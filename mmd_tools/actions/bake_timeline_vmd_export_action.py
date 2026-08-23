"""One-shot native Bake Timeline VMD export."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, List, Optional, Protocol

from ..core.logger import get_logger
from ..validation.export_validator import (
    ExportValidationAcknowledgementRequired,
    ExportValidationIssue,
    ExportValidationReport,
    structured_export_failure_report,
)
from ..validation.report_artifacts import (
    ValidationReportArtifactPaths,
    write_validation_report_artifacts,
)
from ..validation.vmd_validator import VMD_EXPORT_BAKE_TIMELINE, verify_vmd_output_streaming
from .vmd_sibling_stage import VmdSiblingStageSession


logger = get_logger(__name__)


class BakeTimelineVmdExportError(ValueError):
    """Raised when one synchronous Bake Timeline export cannot finish safely."""


class BakeTimelineVmdExportRaceError(BakeTimelineVmdExportError):
    """Raised when the authoritative scene changes during collection."""


@dataclass
class ExportVmdResult:
    """Terminal result from the one-shot Bake Timeline VMD export."""

    exported_path: Optional[str] = None
    succeeded: bool = False
    status_message: str = ""
    error: Optional[Exception] = None
    warnings: List[Any] = field(default_factory=list)
    validation_report: Optional[ExportValidationReport] = None
    validation_report_artifacts: Optional[ValidationReportArtifactPaths] = None


class VmdExportPreparationBoundary(Protocol):
    """Maya-owned discovery, collection, and short-lived revision boundary."""

    def discover(self, request: Any) -> Any: ...
    def supports_streaming(self) -> bool: ...
    def collect_to_sink(self, request: Any, sink: Any) -> Mapping[str, Any]: ...
    def arm(self, request: Any, discovery: Any) -> Any: ...
    def current_revision(self, request: Any, discovery: Any) -> Any: ...
    def close(self) -> Any: ...


@dataclass(frozen=True)
class VmdExportDiscovery:
    """Stable route facts needed only for the current export stack frame."""

    scene_session_id: str
    target_uuid: str
    target_identity: str
    dependency_closure_fingerprint: str
    cache_id: str = ""
    schema_version: int = 1
    route: Any = None
    model_name: str = ""


def _read(value: Any, name: str, *aliases: str) -> Any:
    names = (name,) + aliases
    if isinstance(value, Mapping):
        for item in names:
            if item in value:
                return value[item]
        options = value.get("options")
    else:
        for item in names:
            if hasattr(value, item):
                return getattr(value, item)
        options = getattr(value, "options", None)
    if isinstance(options, Mapping):
        for item in names:
            if item in options:
                return options[item]
    return None


def _required(value: Any, name: str) -> str:
    if value is None or not str(value).strip():
        raise BakeTimelineVmdExportError(f"{name} is required for VMD export")
    return str(value)


def _discovery(value: Any, request: Any) -> VmdExportDiscovery:
    if isinstance(value, VmdExportDiscovery):
        result = value
    else:
        result = VmdExportDiscovery(
            scene_session_id=_required(
                _read(value, "scene_session_id", "session_id")
                or _read(request, "scene_session_id", "session_id"),
                "scene_session_id",
            ),
            target_uuid=_required(
                _read(value, "target_uuid", "target_id", "uuid")
                or _read(request, "target_uuid", "target_id", "uuid"),
                "target_uuid",
            ),
            target_identity=_required(
                _read(value, "target_identity", "canonical_identity", "identity")
                or _read(request, "target_identity", "canonical_identity", "identity"),
                "target_identity",
            ),
            dependency_closure_fingerprint=_required(
                _read(value, "dependency_closure_fingerprint", "closure_fingerprint")
                or _read(request, "dependency_closure_fingerprint", "closure_fingerprint"),
                "dependency_closure_fingerprint",
            ),
            cache_id=str(_read(value, "cache_id") or ""),
            schema_version=int(_read(value, "schema_version", "schema") or 1),
            route=_read(value, "route", "target_route"),
            model_name=str(_read(value, "model_name", "vmd_model_name") or _read(request, "model_name", "vmd_model_name") or ""),
        )
    _required(result.scene_session_id, "scene_session_id")
    _required(result.target_uuid, "target_uuid")
    _required(result.target_identity, "target_identity")
    _required(result.dependency_closure_fingerprint, "dependency_closure_fingerprint")
    if result.schema_version <= 0:
        raise BakeTimelineVmdExportError("discovery schema_version must be positive")
    return result


def _augment_dependency_bake_report(
    report: ExportValidationReport, metadata: Mapping[str, Any]
) -> ExportValidationReport:
    diagnostics = metadata.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        return report
    direct = diagnostics.get("control_rig_direct_export")
    if not isinstance(direct, Mapping):
        return report
    rows = direct.get("dependency_baked")
    if not isinstance(rows, (list, tuple)):
        return report
    appended_issues = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        bone = str(row.get("bone") or row.get("joint") or "")
        if not bone:
            continue
        frame_range = row.get("frame_range")
        try:
            key_count = int(row.get("generated_key_count", 0))
        except (TypeError, ValueError):
            key_count = 0
        appended_issues.append(
            ExportValidationIssue(
                "ROUTE_UNRESOLVED",
                "info" if bool(row.get("static")) else "warning",
                False,
                f"scene.control_rig.direct_vmd_export.{bone}.dependency_bake",
                "This bone has no dedicated Control Rig mapping, so its evaluated motion was baked.",
                "Review the baked dependency keys before publishing the VMD.",
                details={
                    "route": "dependency_bake",
                    "bone": bone,
                    "frame_range": list(frame_range) if isinstance(frame_range, (list, tuple)) and len(frame_range) == 2 else None,
                    "generated_key_count": key_count,
                    "aggregation_discriminator": "route",
                },
            )
        )
    return report.with_appended_issues(appended_issues)


def _merge_reports(
    first: Optional[ExportValidationReport], second: Optional[ExportValidationReport]
) -> Optional[ExportValidationReport]:
    """Keep the preflight and one-shot output findings on one final report."""

    if first is None:
        return second
    if second is None or not second.issues:
        return first
    return first.merged_with(
        second,
        export_format=second.export_format or first.export_format,
        mode=second.mode or first.mode,
    )


def _output_write_failure_report(error: Exception) -> ExportValidationReport:
    """Make an unclassified terminal action failure visible to callers."""

    return ExportValidationReport(
        "vmd",
        (
            ExportValidationIssue(
                "OUTPUT_WRITE_FAILED",
                "fatal",
                True,
                "output",
                f"{type(error).__name__}: {error}",
                details={
                    "phase": "write",
                    "exception_type": type(error).__name__,
                    "aggregation_discriminator": "write",
                },
            ),
        ),
        mode=VMD_EXPORT_BAKE_TIMELINE,
    )


class BakeTimelineVmdExportAction:
    """Collect, verify, decide, and atomically publish within one call."""

    def __init__(self, boundary: VmdExportPreparationBoundary):
        required = ("discover", "supports_streaming", "collect_to_sink", "arm", "current_revision", "close")
        if boundary is None or any(not callable(getattr(boundary, name, None)) for name in required):
            raise TypeError("boundary must expose the streaming VMD export lifecycle")
        if not bool(boundary.supports_streaming()):
            raise TypeError("boundary must support streaming VMD export")
        self._boundary = boundary

    def can_prepare_for_collection(self, request: Any) -> bool:
        capability = getattr(self._boundary, "can_prepare_for_collection", None)
        return bool(capability(request)) if callable(capability) else False

    @staticmethod
    def _write_requested_report(
        request: Any,
        report: Optional[ExportValidationReport],
        target: Optional[Path],
    ) -> Optional[ValidationReportArtifactPaths]:
        """Write a direct action report only when a run directory was supplied."""

        options = _read(request, "options")
        options = dict(options) if isinstance(options, Mapping) else {}
        report_directory = options.get("validation_report_dir")
        if report is None or report_directory in (None, ""):
            return None
        configured_evidence = options.get("validation_report_evidence") or {}
        if not isinstance(configured_evidence, Mapping):
            raise TypeError("validation_report_evidence must be a mapping")
        evidence = dict(configured_evidence)
        evidence.setdefault(
            "target_path",
            str(target) if target is not None else str(_read(request, "file_path") or ""),
        )
        return write_validation_report_artifacts(
            report,
            report_directory,
            target_identity=options.get("target_identity"),
            provenance=options.get("validation_report_provenance", "BakeTimelineVmdExportAction"),
            evidence=evidence,
        )

    @staticmethod
    def _stream_counts(metadata: Mapping[str, Any], summary: Any) -> None:
        expected, actual = metadata.get("section_counts"), getattr(summary, "counts", None)
        if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
            raise BakeTimelineVmdExportError("streaming VMD section counts are unavailable")
        for name in ("bones", "morphs", "cameras", "lights", "shadows", "ik"):
            if not isinstance(expected.get(name), int) or expected[name] < 0 or expected[name] != actual.get(name):
                raise BakeTimelineVmdExportError(f"streaming VMD section count mismatch for {name}")

    def execute_one_shot(
        self,
        request: Any,
        *,
        acknowledge_warnings: bool = False,
        warning_callback: Any = None,
        phase_callback: Any = None,
        write_report: bool = True,
        initial_report: Optional[ExportValidationReport] = None,
    ) -> ExportVmdResult:
        """Write a verified sibling and its report before the final replace."""

        def phase(name: str, started: bool) -> None:
            if callable(phase_callback):
                phase_callback(name, started)

        stage: Optional[VmdSiblingStageSession] = None
        collection_context: Any = None
        collection_active = False
        temporary_collection = False
        boundary_open = False
        target: Optional[Path] = None
        report: Optional[ExportValidationReport] = initial_report
        report_artifacts: Optional[ValidationReportArtifactPaths] = None
        artifact_attempted = False
        artifact_written = False
        published = False
        failure: Optional[Exception] = None

        def build_result(
            *,
            exported_path: Optional[str] = None,
            succeeded: bool = False,
            status_message: str = "",
            error: Optional[Exception] = None,
            warnings: Optional[List[Any]] = None,
        ) -> ExportVmdResult:
            return ExportVmdResult(
                exported_path=exported_path,
                succeeded=succeeded,
                status_message=status_message,
                error=error,
                warnings=list(warnings or []),
                validation_report=report,
                validation_report_artifacts=report_artifacts,
            )

        def close_boundary() -> Optional[Exception]:
            nonlocal boundary_open
            if not boundary_open:
                return None
            boundary_open = False
            try:
                self._boundary.close()
            except Exception as exc:
                return exc
            return None

        def restore_collection() -> Optional[Exception]:
            nonlocal collection_active, collection_context
            if not collection_active:
                return None
            context = collection_context
            collection_active = False
            collection_context = None
            close_error = close_boundary()
            restore = getattr(self._boundary, "restore_after_collection", None)
            if not callable(restore):
                return BakeTimelineVmdExportError("collection lifecycle cannot be restored")
            try:
                restore(context)
            except Exception as exc:
                return exc
            return close_error

        try:
            raw_target = _read(request, "file_path", "output_path", "output")
            target = Path(_required(raw_target, "target path")).resolve(strict=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            prepare = getattr(self._boundary, "prepare_for_collection", None)
            if callable(prepare):
                collection_context = prepare(request)
                collection_active = collection_context is not None
                temporary_collection = collection_active
            first = _discovery(self._boundary.discover(request), request)
            self._boundary.arm(request, first)
            boundary_open = True
            revision_before = _required(self._boundary.current_revision(request, first), "revision_before")

            phase("collect", True)
            stage = VmdSiblingStageSession(
                first.model_name,
                target_path=str(target),
                output_verifier=verify_vmd_output_streaming,
            )
            metadata = self._boundary.collect_to_sink(request, stage) or {}
            if not isinstance(metadata, Mapping):
                raise BakeTimelineVmdExportError("streaming VMD backend returned invalid metadata")
            collected_range = metadata.get("validation_frame_range")
            if (
                isinstance(collected_range, (str, bytes))
                or not isinstance(collected_range, Sequence)
                or len(collected_range) != 2
            ):
                raise BakeTimelineVmdExportError("streaming VMD metadata has an invalid frame range")
            try:
                collected_range = (int(collected_range[0]), int(collected_range[1]))
            except (TypeError, ValueError, OverflowError) as exc:
                raise BakeTimelineVmdExportError("streaming VMD metadata has an invalid frame range") from exc
            if collected_range[0] < 0 or collected_range[1] < collected_range[0]:
                raise BakeTimelineVmdExportError("streaming VMD metadata has an invalid frame range")
            stage.set_expected_frame_range(collected_range)
            phase("collect", False)
            summary = stage.finish_collection(phase_callback=phase)
            self._stream_counts(metadata, summary)

            restore_error = restore_collection()
            if restore_error is not None:
                raise BakeTimelineVmdExportError(f"temporary Control Rig restore failed: {restore_error}")
            second = _discovery(self._boundary.discover(request), request)
            if temporary_collection:
                self._boundary.arm(request, second)
                boundary_open = True
            revision_after = _required(self._boundary.current_revision(request, second), "revision_after")
            if not temporary_collection and revision_after != revision_before:
                raise BakeTimelineVmdExportRaceError("scene revision changed during VMD collection")
            if (
                first.scene_session_id != second.scene_session_id
                or first.target_uuid != second.target_uuid
                or first.target_identity != second.target_identity
                or first.dependency_closure_fingerprint != second.dependency_closure_fingerprint
            ):
                raise BakeTimelineVmdExportRaceError("VMD route or dependency closure changed during collection")

            phase("output_verify", True)
            output_report = stage.verify()
            phase("output_verify", False)
            report = _merge_reports(
                initial_report, _augment_dependency_bake_report(output_report, metadata)
            )

            phase("cleanup", True)
            close_error = close_boundary()
            phase("cleanup", False)
            if close_error is not None:
                raise BakeTimelineVmdExportError(f"closing the collection watch failed: {close_error}")
            if report.requires_warning_ack:
                phase("warning_decision", True)
                try:
                    approved = acknowledge_warnings or (bool(warning_callback(report)) if callable(warning_callback) else False)
                finally:
                    phase("warning_decision", False)
                if not approved:
                    raise ExportValidationAcknowledgementRequired(report)

            if write_report:
                artifact_attempted = True
                report_artifacts = self._write_requested_report(request, report, target)
                artifact_written = True

            phase("replace", True)
            os.replace(stage.file_path, str(target))
            phase("replace", False)
            published = True
            return build_result(
                exported_path=str(target),
                succeeded=True,
                status_message=f"Export complete: {target}",
                warnings=list(report.issues),
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            failure = exc
            if stage is not None and not published:
                # Release the exact sibling before constructing a recoverable
                # failure result.  The finalizer still owns the watch close.
                stage.cleanup()
            lower_report = structured_export_failure_report(
                exc, "vmd", mode=VMD_EXPORT_BAKE_TIMELINE
            )
            if isinstance(exc, ExportValidationAcknowledgementRequired):
                # A declined warning is a user decision after successful
                # verification, not an output write failure.
                pass
            elif report is None or report is initial_report:
                report = _merge_reports(
                    initial_report, lower_report or _output_write_failure_report(exc)
                )
            else:
                report = _merge_reports(
                    report, lower_report or _output_write_failure_report(exc)
                )
            if write_report and (not artifact_attempted or artifact_written):
                try:
                    artifact_attempted = True
                    report_artifacts = self._write_requested_report(request, report, target)
                    artifact_written = True
                except Exception as artifact_error:
                    report_artifacts = None
                    logger.error(
                        "Failed to write VMD validation report artifacts: %s",
                        artifact_error,
                        exc_info=True,
                    )
            return build_result(
                status_message=f"Export failed: {exc}",
                error=exc,
                warnings=list(report.issues),
            )
        finally:
            restore_error = restore_collection()
            cleanup_needed = boundary_open or not published
            if cleanup_needed:
                phase("cleanup", True)
            close_error = close_boundary()
            if not published and stage is not None:
                stage_path = Path(stage.file_path)
                stage.cleanup()
                try:
                    if target is not None and stage_path != target and stage_path.exists():
                        stage_path.unlink()
                except OSError:
                    logger.warning("Failed to remove VMD temporary output: %s", stage_path)
            if cleanup_needed:
                phase("cleanup", False)
            if failure is None and (restore_error is not None or close_error is not None):
                logger.error("VMD export cleanup failed: restore=%s close=%s", restore_error, close_error)


__all__ = [
    "BakeTimelineVmdExportAction",
    "BakeTimelineVmdExportError",
    "BakeTimelineVmdExportRaceError",
    "ExportVmdResult",
    "VmdExportDiscovery",
    "VmdExportPreparationBoundary",
]
