"""One-operation export orchestration for UI and headless callers."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional

from ..actions.export_model_action import ExportModelAction, ExportModelRequest
from ..actions.bake_timeline_vmd_export_action import BakeTimelineVmdExportCancelled
from ..validation.export_validator import (
    ExportValidationAcknowledgementRequired,
    ExportValidationIssue,
    ExportValidationReport,
    structured_export_failure_report,
)
from ..validation.scene_preflight import ScenePreflight
from ..validation.report_artifacts import (
    ValidationReportArtifactPaths,
    write_validation_report_artifacts,
)
from ..validation.vmd_validator import VMD_EXPORT_BAKE_TIMELINE


STATE_EDITING = "Editing"
STATE_VALIDATING = "Validating"
STATE_BLOCKED = "Blocked"
STATE_READY = "Ready"
STATE_EXPORTING = "Exporting"
STATE_SUCCEEDED = "Succeeded"
STATE_FAILED = "Failed"
STATE_CANCELLED = "Cancelled"


@dataclass
class ExportWorkflowRequest:
    """Format-neutral export request accepted by the shared workflow."""

    file_path: str
    options: Dict[str, Any]
    model_data: Any = None
    animation_data: Any = None


@dataclass
class ExportWorkflowResult:
    """Terminal result of one complete export operation."""

    state: str
    report: ExportValidationReport
    metadata: Dict[str, Any]
    payload: Any = None
    action_result: Any = None
    error: Optional[Exception] = None
    phase_timings: Dict[str, float] = field(default_factory=dict)
    active_phase: Optional[str] = None
    completed_phases: List[str] = field(default_factory=list)
    validation_report_artifacts: Optional[ValidationReportArtifactPaths] = None

    @property
    def succeeded(self) -> bool:
        """Return whether writer, verifier, and atomic publication succeeded."""

        return self.state == STATE_SUCCEEDED

    @property
    def warnings(self):
        """Expose action-compatible warning entries."""

        return list(self.report.issues)


def _combine_reports(
    first: ExportValidationReport,
    second: Optional[ExportValidationReport],
    *,
    export_format: Optional[str],
    export_strategy: str,
) -> ExportValidationReport:
    """Merge scene and action findings without changing their order."""

    if second is None or not second.issues:
        return first
    return first.merged_with(
        second,
        export_format=export_format,
        mode=export_strategy,
    )


def _collect_failure_report(
    export_format: Optional[str], export_strategy: str, error: Exception
) -> ExportValidationReport:
    """Keep a collector failure diagnosable without losing its lower report."""

    wrapper = ExportValidationIssue(
        "COLLECTION_FAILED",
        "fatal",
        True,
        "collector",
        f"scene collector failed: {type(error).__name__}: {error}",
        details={
            "phase": "collection",
            "exception_type": type(error).__name__,
            "aggregation_discriminator": "collection",
        },
    )
    lower = structured_export_failure_report(error, export_format, mode=export_strategy)
    if lower is not None and any(issue.code == wrapper.code for issue in lower.issues):
        return lower
    wrapper_report = ExportValidationReport(
        export_format, (wrapper,), mode=export_strategy
    )
    if lower is None:
        return wrapper_report
    return wrapper_report.merged_with(
        lower,
        export_format=export_format,
        mode=export_strategy,
    )


def _report_output_failure(
    report: ExportValidationReport,
    error: Exception,
    *,
    export_format: Optional[str],
    export_strategy: str,
) -> ExportValidationReport:
    """Expose an unreported output failure in the validation console."""

    if report.is_blocking or any(issue.code == "OUTPUT_WRITE_FAILED" for issue in report.issues):
        return report
    failure = ExportValidationReport(
        export_format,
        (
            ExportValidationIssue(
                "OUTPUT_WRITE_FAILED",
                "fatal",
                True,
                "file_path",
                "export output could not be written: "
                f"{type(error).__name__}: {error}. "
                "Choose a writable output path and verify its permissions and length.",
                details={
                    "phase": "write",
                    "exception_type": type(error).__name__,
                    "aggregation_discriminator": "write",
                },
            ),
        ),
        mode=export_strategy,
    )
    return _combine_reports(
        report, failure, export_format=export_format, export_strategy=export_strategy
    )


def _scene_report_for_control_rig(
    report: ExportValidationReport, options: Mapping[str, Any], vmd_action: Any
) -> ExportValidationReport:
    """Allow ownership paths the one-shot action can collect safely."""

    if str(options.get("export_format") or "").lower() != "vmd":
        return report
    if str(options.get("export_strategy") or "").lower() != VMD_EXPORT_BAKE_TIMELINE:
        return report
    can_collect = getattr(vmd_action, "can_prepare_for_collection", None)
    if not callable(can_collect):
        return report
    try:
        if not bool(can_collect(options)):
            return report
    except Exception:
        return report
    return report.filtered(
        lambda issue: not (
                issue.code == "OWNERSHIP_CONFLICT"
                and issue.details.get("owner") == "control_rig"
            ),
    )


class _PhaseTracker:
    """Record only phases that actually crossed an export boundary."""

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._phase_started: Optional[float] = None
        self.active_phase: Optional[str] = None
        self.timings: Dict[str, float] = {}
        self.completed: List[str] = []

    def transition(self, phase: str, started: bool) -> None:
        if started:
            if self.active_phase is not None:
                # A failed boundary deliberately has no completion marker.
                # Cleanup is still a real later boundary and must never be
                # prevented from removing the private sibling.
                self.active_phase = None
                self._phase_started = None
            self.active_phase = phase
            self._phase_started = time.perf_counter()
            return
        if self.active_phase != phase or self._phase_started is None:
            return
        self.timings[phase] = round(time.perf_counter() - self._phase_started, 6)
        self.completed.append(phase)
        self.active_phase = None
        self._phase_started = None

    def attach(self, result: ExportWorkflowResult) -> ExportWorkflowResult:
        result.phase_timings = dict(
            self.timings, total=round(time.perf_counter() - self._started, 6)
        )
        result.active_phase = self.active_phase
        result.completed_phases = list(self.completed)
        return result


class ExportWorkflowService:
    """Run one scene preflight and one format-specific export action."""

    def __init__(
        self,
        *,
        scene_preflight: Optional[ScenePreflight] = None,
        scene_service: Any = None,
        model_action: Optional[ExportModelAction] = None,
        vmd_action: Any = None,
    ):
        self.model_action = model_action or ExportModelAction()
        self.vmd_action = vmd_action
        self.scene_preflight = scene_preflight or ScenePreflight(scene_service=scene_service)

    @staticmethod
    def _options(request: ExportWorkflowRequest) -> Dict[str, Any]:
        options = dict(request.options or {})
        options.setdefault("file_path", request.file_path)
        export_format = str(
            options.get("export_format") or Path(request.file_path).suffix.lstrip(".")
        ).lower().lstrip(".")
        if export_format == "vmd":
            options["export_format"] = "vmd"
            options["export_strategy"] = VMD_EXPORT_BAKE_TIMELINE
        return options

    @staticmethod
    def _target_options(options: Mapping[str, Any], metadata: Mapping[str, Any]) -> Dict[str, Any]:
        enriched = dict(options)
        if metadata.get("format") in {"pmx", "vmd"} and enriched.get("current_model_root"):
            enriched["target_model"] = str(enriched["current_model_root"])
        if metadata.get("target_identity") is not None:
            enriched.setdefault("target_identity", metadata["target_identity"])
        if metadata.get("scene_revision") is not None:
            enriched.setdefault("scene_revision", metadata["scene_revision"])
        return enriched

    @staticmethod
    def _emit_progress(progress_callback: Optional[Callable[[str], None]], stage: str) -> None:
        if not callable(progress_callback):
            return
        try:
            progress_callback(stage)
        except Exception:
            return

    @staticmethod
    def _write_vmd_report(
        request: ExportWorkflowRequest,
        options: Mapping[str, Any],
        metadata: Mapping[str, Any],
        report: ExportValidationReport,
    ) -> Optional[ValidationReportArtifactPaths]:
        """Persist the terminal one-shot VMD report when evidence was requested."""

        report_directory = options.get("validation_report_dir")
        if report_directory in (None, ""):
            return None
        configured_evidence = options.get("validation_report_evidence") or {}
        if not isinstance(configured_evidence, Mapping):
            raise TypeError("validation_report_evidence must be a mapping")
        evidence = dict(configured_evidence)
        evidence.setdefault("target_path", str(Path(request.file_path).resolve(strict=False)))
        return write_validation_report_artifacts(
            report,
            report_directory,
            target_identity=options.get("target_identity", metadata.get("target_identity")),
            provenance=options.get(
                "validation_report_provenance", "ExportWorkflowService one-shot VMD"
            ),
            evidence=evidence,
        )

    def _scene_preflight(self, request: ExportWorkflowRequest):
        options = self._options(request)
        scene = self.scene_preflight.run(options)
        metadata = dict(scene.metadata)
        if metadata.get("format") == "vmd":
            metadata["export_strategy"] = VMD_EXPORT_BAKE_TIMELINE
        # GUI requests intentionally name only ``current_model_root``.  The
        # production VMD backend requires the equivalent ``target_model`` at
        # every action boundary, including this read-only Control Rig
        # capability check.  Use the same enriched options that collection
        # receives so EDIT / CONTROL_OWNED does not remain falsely blocked.
        action_options = self._target_options(options, metadata)
        report = _scene_report_for_control_rig(
            scene.report, action_options, self.vmd_action
        )
        return options, metadata, report

    def _collect_model(self, request: ExportWorkflowRequest, options: Mapping[str, Any]) -> Any:
        if request.model_data is not None:
            return request.model_data
        for option_name in ("model_data", "maya_data"):
            if options.get(option_name) is not None:
                return options[option_name]
        collector = getattr(self.model_action, "_collector", None)
        if collector is None:
            raise ValueError("model export requires model_data or a collector")
        return collector(dict(options))

    def validate(
        self,
        request: ExportWorkflowRequest,
        *,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> ExportWorkflowResult:
        """Run the non-writing scene validation API for headless callers."""

        self._emit_progress(progress_callback, "scene_preflight")
        options, metadata, report = self._scene_preflight(request)
        export_format = metadata.get("format")
        strategy = metadata.get("export_strategy") or "model"
        if report.is_blocking:
            self._emit_progress(progress_callback, "report_ready")
            return ExportWorkflowResult(STATE_BLOCKED, report, metadata)
        if export_format != "pmx":
            self._emit_progress(progress_callback, "report_ready")
            return ExportWorkflowResult(STATE_READY, report, metadata)
        try:
            self._emit_progress(progress_callback, "payload_collection")
            payload = self._collect_model(request, self._target_options(options, metadata))
            validator = getattr(self.model_action, "_validator", None)
            if not callable(validator):
                raise ValueError("model action does not expose a validator")
            payload_report = validator(payload, export_format)
        except Exception as exc:
            report = _combine_reports(
                report,
                _collect_failure_report(export_format, strategy, exc),
                export_format=export_format,
                export_strategy=strategy,
            )
            self._emit_progress(progress_callback, "report_ready")
            return ExportWorkflowResult(STATE_BLOCKED, report, metadata, error=exc)
        report = _combine_reports(
            report, payload_report, export_format=export_format, export_strategy=strategy
        )
        self._emit_progress(progress_callback, "report_ready")
        return ExportWorkflowResult(
            STATE_BLOCKED if report.is_blocking else STATE_READY,
            report,
            metadata,
            payload=payload,
        )

    def prepare_vmd(
        self,
        request: ExportWorkflowRequest,
        *,
        acknowledge_warnings: bool = False,
        warning_callback: Optional[Callable[[ExportValidationReport], bool]] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> ExportWorkflowResult:
        """Synchronously complete one VMD export and return its terminal result.

        This retained headless seam deliberately prepares no reusable payload,
        token, watch, or temporary file.  It is the VMD half of ``execute()``
        and returns only after the one-shot action has published or cleaned up.
        """

        tracker = _PhaseTracker()
        self._emit_progress(progress_callback, "scene_preflight")
        options, metadata, report = self._scene_preflight(request)
        export_format = metadata.get("format")
        strategy = metadata.get("export_strategy") or VMD_EXPORT_BAKE_TIMELINE

        def finish(result: ExportWorkflowResult) -> ExportWorkflowResult:
            result = tracker.attach(result)
            if result.action_result is not None:
                artifacts = getattr(
                    result.action_result, "validation_report_artifacts", None
                )
                result.validation_report_artifacts = artifacts
                return result
            try:
                artifacts = self._write_vmd_report(request, options, metadata, result.report)
            except Exception as exc:
                result.state = STATE_FAILED
                result.error = exc
                result.report = _report_output_failure(
                    result.report,
                    exc,
                    export_format=export_format,
                    export_strategy=strategy,
                )
                artifacts = None
            result.validation_report_artifacts = artifacts
            return result

        if export_format != "vmd":
            raise ValueError("prepare_vmd requires a VMD export request")
        if report.is_blocking:
            self._emit_progress(progress_callback, "report_ready")
            return finish(ExportWorkflowResult(STATE_BLOCKED, report, metadata))

        try:
            execute_one_shot = getattr(self.vmd_action, "execute_one_shot", None)
            if not callable(execute_one_shot):
                raise ValueError("VMD export requires a one-shot Bake Timeline action")
            self._emit_progress(progress_callback, "payload_collection")
            action_options = self._target_options(options, metadata)

            def phase_callback(name: str, started: bool) -> None:
                tracker.transition(name, started)
                if not started:
                    return
                if name in {"encode", "flush"}:
                    self._emit_progress(progress_callback, "writer")
                elif name in {"output_verify", "warning_decision", "replace", "cleanup"}:
                    self._emit_progress(progress_callback, "report_ready")

            if str(action_options.get("export_target") or "").lower() in {
                "camera",
                "light",
                "camera+light",
                "camera_light",
            }:
                action_options["_progress_callback"] = progress_callback
            action_result = execute_one_shot(
                ExportWorkflowRequest(
                    request.file_path,
                    action_options,
                    animation_data=request.animation_data,
                ),
                acknowledge_warnings=acknowledge_warnings,
                warning_callback=warning_callback,
                phase_callback=phase_callback,
                initial_report=report,
            )
        except Exception as exc:
            report = _report_output_failure(
                report, exc, export_format=export_format, export_strategy=strategy
            )
            return finish(
                ExportWorkflowResult(STATE_FAILED, report, metadata, error=exc)
            )

        action_report = getattr(action_result, "validation_report", None)
        if isinstance(action_report, ExportValidationReport):
            report = action_report
        action_error = getattr(action_result, "error", None)
        succeeded = bool(getattr(action_result, "succeeded", False)) and action_error is None
        live_cancelled = bool(getattr(action_result, "cancelled", False)) or isinstance(
            action_error,
            BakeTimelineVmdExportCancelled,
        )
        warning_declined = isinstance(action_error, ExportValidationAcknowledgementRequired)
        if not succeeded and action_error is not None and not live_cancelled and not warning_declined:
            report = _report_output_failure(
                report, action_error, export_format=export_format, export_strategy=strategy
            )
        self._emit_progress(progress_callback, "report_ready")
        return finish(
            ExportWorkflowResult(
                STATE_SUCCEEDED
                if succeeded
                else (
                    STATE_CANCELLED
                    if live_cancelled
                    else (STATE_BLOCKED if warning_declined else STATE_FAILED)
                ),
                report,
                metadata,
                action_result=action_result,
                error=action_error,
            )
        )

    def execute(
        self,
        request: ExportWorkflowRequest,
        *,
        acknowledge_warnings: bool = False,
        warning_callback: Optional[Callable[[ExportValidationReport], bool]] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> ExportWorkflowResult:
        """Complete one export; no payload, watch, or stage outlives this call."""

        if self._options(request).get("export_format") == "vmd":
            return self.prepare_vmd(
                request,
                acknowledge_warnings=acknowledge_warnings,
                warning_callback=warning_callback,
                progress_callback=progress_callback,
            )

        tracker = _PhaseTracker()
        self._emit_progress(progress_callback, "scene_preflight")
        options, metadata, report = self._scene_preflight(request)
        export_format = metadata.get("format")
        strategy = metadata.get("export_strategy") or "model"
        if report.is_blocking:
            self._emit_progress(progress_callback, "report_ready")
            return tracker.attach(ExportWorkflowResult(STATE_BLOCKED, report, metadata))

        action_result = None
        try:
            action_options = self._target_options(options, metadata)
            if acknowledge_warnings:
                action_options["ack_warnings"] = True
            action_options["_warning_callback"] = warning_callback
            action_options["_phase_callback"] = tracker.transition
            if export_format == "pmx":
                self._emit_progress(progress_callback, "payload_collection")
                tracker.transition("collect", True)
                try:
                    action_options["model_data"] = self._collect_model(request, action_options)
                except Exception:
                    raise
                else:
                    tracker.transition("collect", False)
                self._emit_progress(progress_callback, "writer")
                action_result = self.model_action.execute(
                    ExportModelRequest(request.file_path, action_options)
                )
            else:
                return tracker.attach(ExportWorkflowResult(STATE_BLOCKED, report, metadata))
        except Exception as exc:
            report = _report_output_failure(
                report, exc, export_format=export_format, export_strategy=strategy
            )
            return tracker.attach(
                ExportWorkflowResult(STATE_FAILED, report, metadata, error=exc)
            )

        report = _combine_reports(
            report,
            getattr(action_result, "validation_report", None),
            export_format=export_format,
            export_strategy=strategy,
        )
        action_error = getattr(action_result, "error", None)
        succeeded = bool(getattr(action_result, "succeeded", False)) and action_error is None
        if not succeeded and action_error is not None:
            report = _report_output_failure(
                report, action_error, export_format=export_format, export_strategy=strategy
            )
        self._emit_progress(progress_callback, "report_ready")
        return tracker.attach(
            ExportWorkflowResult(
                STATE_SUCCEEDED if succeeded else STATE_FAILED,
                report,
                metadata,
                action_result=action_result,
                error=action_error,
            )
        )


__all__ = [
    "ExportWorkflowRequest",
    "ExportWorkflowResult",
    "ExportWorkflowService",
    "STATE_BLOCKED",
    "STATE_EDITING",
    "STATE_EXPORTING",
    "STATE_FAILED",
    "STATE_CANCELLED",
    "STATE_READY",
    "STATE_SUCCEEDED",
    "STATE_VALIDATING",
]
