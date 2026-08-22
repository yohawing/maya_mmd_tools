"""Action boundary for PMX model export execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from ..core.logger import get_logger
from ..validation.export_validator import (
    ExportValidationAcknowledgementRequired,
    ExportValidationError,
    ExportValidationIssue,
    ExportValidationReport,
    validate_model_data,
)
from ..validation.output_verifier import verify_model_output
from ..validation.mmd_anim_verifier import verify_mmd_anim_asset
from ..validation.mmd_anim_binding_verifier import verify_mmd_anim_binding_asset
from ..validation.report_artifacts import (
    ValidationReportArtifactPaths,
    write_validation_report_artifacts,
)
from ..validation.snapshot import ExportValidationSnapshot

if TYPE_CHECKING:
    from ..io.pmx_exporter import PmxExporter

logger = get_logger(__name__)

_DEFAULT_COLLECTOR = object()
_DEFAULT_OUTPUT_VERIFIER = object()
_DEFAULT_VALIDATOR = object()


def _find_authoring_model_root(options: Mapping[str, Any], adapter: Any) -> tuple[str | None, bool]:
    """Resolve an explicit or selected MMD model root for authoring export.

    The boolean marks an explicit ``target_model``/``model_root`` request;
    explicit roots are never silently redirected to another DAG ancestor.
    """
    explicit_root = options.get("target_model") or options.get("model_root")
    if explicit_root:
        return str(explicit_root), True

    selected = options.get("target_mesh") or options.get("export_mesh") or options.get("mesh")
    if selected and not options.get("_selected_target"):
        # An explicitly supplied mesh is intentionally the legacy single-mesh
        # route.  Only a Maya selection may be promoted to an ancestor root.
        return None, False
    if not selected:
        return None, False
    current = str(selected)
    for _ in range(64):
        try:
            if adapter.attribute_exists("mmd_model_name", current) or adapter.attribute_exists(
                "mmd_model_name_en", current
            ):
                return current, False
            parents = adapter.list_relatives(current, parent=True, fullPath=True) or []
        except Exception:
            return None, False
        if not parents:
            return None, False
        current = str(parents[0])
    return None, False


def _has_authoring_markers(root: str, adapter: Any) -> bool:
    """Return whether a legacy root contains semantic metadata worth reading."""
    model_fields = (
        "mmd_model_name",
        "mmd_model_name_en",
        "mmd_comment",
        "mmd_comment_en",
    )
    try:
        if any(adapter.attribute_exists(field, root) for field in model_fields):
            return True
        joints = adapter.list_relatives(root, allDescendents=True, fullPath=True, type="joint") or []
        for joint in joints:
            if adapter.attribute_exists("mmd_bone_index", joint):
                return True
        # Registry presence is itself a strict authoring marker.  A malformed
        # connection must reach the backend and fail closed, never downgrade.
        return bool(adapter.attribute_exists("mmd_model_registry", root))
    except Exception:
        # A failed marker probe is not evidence of an ordinary mesh-only root;
        # let strict authoring read surface the backend error when applicable.
        return True


def _project_authoring_payload(
    options: Mapping[str, Any],
    oracle_payload: dict,
    *,
    adapter: Any,
) -> dict:
    """Read and project semantic Spec when a model-root route is applicable."""
    semantics = options.get("authoring_semantics", "auto")
    if semantics not in {"auto", "legacy"}:
        raise ValueError("authoring_semantics must be 'auto' or 'legacy'")
    if semantics == "legacy":
        return oracle_payload
    root, _ = _find_authoring_model_root(options, adapter)
    if root is None:
        return oracle_payload

    from ..adapters.maya_scene_metadata_backend import MayaSceneMetadataBackend
    from ..adapters.scene_metadata_adapter import SceneMetadataAdapter
    from ..converters.authoring_export_bridge import project_authoring_spec

    registry_present = bool(adapter.attribute_exists("mmd_model_registry", root))
    if not registry_present and not _has_authoring_markers(root, adapter):
        return oracle_payload
    backend = MayaSceneMetadataBackend(adapter)
    spec = SceneMetadataAdapter(backend).read_spec(root)
    return project_authoring_spec(spec, oracle_payload)


@dataclass
class ExportModelRequest:
    """Request data for exporting a PMX model."""

    file_path: str
    options: Dict[str, Any]


@dataclass
class ExportModelResult:
    """Result data returned by PMX model export."""

    exported_path: Optional[str] = None
    succeeded: bool = False
    status_message: str = ""
    error: Optional[Exception] = None
    warnings: List[Any] = field(default_factory=list)
    validation_report: Optional[ExportValidationReport] = None
    payload_fingerprint: Optional[str] = None
    validation_report_artifacts: Optional[ValidationReportArtifactPaths] = None


def _default_collect_model_data(options: Dict[str, Any]) -> dict:
    """Collect minimum PMX-compatible model data from the requested model."""
    from ..converters.export_scene_collector import ExportSceneCollector

    options = dict(options or {})
    export_format = str(options.get("export_format") or Path(str(options.get("file_path") or "")).suffix)
    export_format = export_format.lower().lstrip(".")
    if export_format != "pmx":
        raise ValueError(f"model export format {export_format or 'empty'} is not supported")

    # The Export UI's Current Model is the only implicit scene authority.  Do
    # not derive an export target from Maya's selection: connection nodes such
    # as the MMD light vectorProduct can be selected without being exportable
    # geometry, and selection is not part of the validation snapshot.
    if "current_model_root" in options:
        current_model_root = options.get("current_model_root")
        if not current_model_root:
            raise ValueError("Model export requires a Current Model")
        options["target_model"] = str(current_model_root)

    collector = ExportSceneCollector()
    if (
        options.get("target_model")
        or options.get("model_root")
        or options.get("target_mesh")
        or options.get("export_mesh")
        or options.get("mesh")
    ):
        oracle_payload = collector.collect(options)
        semantics = options.get("authoring_semantics", "auto")
        if semantics not in {"auto", "legacy"}:
            raise ValueError("authoring_semantics must be 'auto' or 'legacy'")
        if semantics == "legacy":
            return oracle_payload
        from ..adapters.maya_cmds_adapter import MayaCmdsAdapter

        return _project_authoring_payload(options, oracle_payload, adapter=MayaCmdsAdapter())

    raise ValueError(
        "Model export requires current_model_root, target_model, model_root, or a target mesh"
    )


class ExportModelAction:
    """Execute PMX model export from collected or scene-collected data."""

    def __init__(
        self,
        pmx_exporter: Optional[PmxExporter] = None,
        collector: Optional[Callable[[Dict[str, Any]], dict]] = _DEFAULT_COLLECTOR,
        output_verifier: Any = _DEFAULT_OUTPUT_VERIFIER,
        validator: Any = _DEFAULT_VALIDATOR,
    ):
        self._pmx_exporter = pmx_exporter
        if collector is _DEFAULT_COLLECTOR:
            self._collector = _default_collect_model_data
        else:
            self._collector = collector
        self._output_verifier = (
            verify_model_output if output_verifier is _DEFAULT_OUTPUT_VERIFIER else output_verifier
        )
        self._validator = validate_model_data if validator is _DEFAULT_VALIDATOR else validator

    @staticmethod
    def _write_requested_report(
        request: ExportModelRequest,
        report: Optional[ExportValidationReport],
        payload_fingerprint: Optional[str],
    ) -> Optional[ValidationReportArtifactPaths]:
        """Write a report only when the caller supplied an explicit run directory."""
        report_directory = request.options.get("validation_report_dir")
        if report is None or report_directory in (None, ""):
            return None

        configured_evidence = request.options.get("validation_report_evidence") or {}
        if not isinstance(configured_evidence, Mapping):
            raise TypeError("validation_report_evidence must be a mapping")
        evidence = dict(configured_evidence)
        evidence.setdefault("target_path", str(request.file_path))
        return write_validation_report_artifacts(
            report,
            report_directory,
            target_identity=request.options.get("target_identity"),
            snapshot_fingerprint=payload_fingerprint,
            provenance=request.options.get("validation_report_provenance", "PayloadValidator"),
            evidence=evidence,
        )

    def execute(self, request: ExportModelRequest) -> ExportModelResult:
        """Export a PMX model and return a small result object."""
        # Resolve once before invoking collectors, writers, or warning UI.
        # Warning handling can enter a nested host event loop, so retaining a
        # relative path here could otherwise publish into a later ``chdir``.
        request = ExportModelRequest(
            str(Path(request.file_path).expanduser().resolve(strict=False)),
            request.options,
        )
        validation_report: Optional[ExportValidationReport] = None
        payload_fingerprint: Optional[str] = None
        validation_report_artifacts: Optional[ValidationReportArtifactPaths] = None
        temporary_path: Optional[str] = None
        phase_callback = request.options.get("_phase_callback")

        def phase(name: str, started: bool) -> None:
            if callable(phase_callback):
                phase_callback(name, started)

        def build_result(
            *,
            exported_path: Optional[str] = None,
            succeeded: bool = False,
            status_message: str = "",
            error: Optional[Exception] = None,
            warnings: Optional[List[Any]] = None,
        ) -> ExportModelResult:
            """Build a result and persist its final validation report if requested."""
            nonlocal validation_report_artifacts
            validation_report_artifacts = self._write_requested_report(
                request,
                validation_report,
                payload_fingerprint,
            )
            return ExportModelResult(
                exported_path=exported_path,
                succeeded=succeeded,
                status_message=status_message,
                error=error,
                warnings=list(warnings or []),
                validation_report=validation_report,
                payload_fingerprint=payload_fingerprint,
                validation_report_artifacts=validation_report_artifacts,
            )

        try:
            export_format = (request.options.get("export_format") or "").lower()
            if not export_format:
                export_format = Path(request.file_path).suffix.lower().lstrip(".") or "pmx"

            if export_format != "pmx":
                validation_report = ExportValidationReport(
                    export_format or None,
                    (
                        ExportValidationIssue(
                            "EXPORT_FORMAT_UNSUPPORTED",
                            "fatal",
                            True,
                            "export_format",
                            f"model export format {export_format or 'empty'} is not supported",
                        ),
                    ),
                )
                validation_error = ExportValidationError(validation_report)
                logger.error("Model export preflight failed: %s", validation_error)
                return build_result(
                    status_message=f"Export failed: {validation_error}",
                    error=validation_error,
                    warnings=list(validation_report.issues),
                )

            model_data = request.options.get("model_data")
            if model_data is None:
                model_data = request.options.get("maya_data")
            if model_data is None:
                if self._collector is None:
                    raise ValueError("Model export requires model_data or a collector")
                collector_options = dict(request.options)
                collector_options["export_format"] = export_format
                model_data = self._collector(collector_options)

            source_model_data = model_data
            scene_revision = request.options.get("scene_revision")
            target_identity = request.options.get("target_identity")
            provided_snapshot = request.options.get("validation_snapshot")
            if provided_snapshot is None:
                try:
                    snapshot = ExportValidationSnapshot.capture(
                        source_model_data,
                        export_format,
                        scene_revision=scene_revision,
                        target_identity=target_identity,
                    )
                except (TypeError, ValueError):
                    validation_report = validate_model_data(source_model_data, export_format)
                    if not validation_report.is_blocking:
                        raise
                    validation_error = ExportValidationError(validation_report)
                    logger.error("Model export preflight failed: %s", validation_error)
                    return build_result(
                        status_message=f"Export failed: {validation_error}",
                        error=validation_error,
                        warnings=list(validation_report.issues),
                    )
            elif isinstance(provided_snapshot, ExportValidationSnapshot):
                snapshot = provided_snapshot
                if not snapshot.matches(
                    source_model_data,
                    export_format,
                    scene_revision=scene_revision,
                    target_identity=target_identity,
                ):
                    validation_report = ExportValidationReport(
                        export_format,
                        (
                            ExportValidationIssue(
                                "STALE_VALIDATION_SNAPSHOT",
                                "fatal",
                                True,
                                "validation_snapshot",
                                "validation snapshot does not match the current payload or scene revision",
                            ),
                        ),
                    )
                    validation_error = ExportValidationError(validation_report)
                    logger.error("Model export preflight failed: %s", validation_error)
                    return build_result(
                        status_message=f"Export failed: {validation_error}",
                        error=validation_error,
                        warnings=list(validation_report.issues),
                    )
            else:
                raise TypeError("validation_snapshot must be an ExportValidationSnapshot")

            payload_fingerprint = snapshot.payload_fingerprint
            expected_payload_fingerprint = request.options.get("expected_payload_fingerprint")
            if (
                expected_payload_fingerprint is not None
                and expected_payload_fingerprint != payload_fingerprint
            ):
                validation_report = ExportValidationReport(
                    export_format,
                    (
                        ExportValidationIssue(
                            "STALE_VALIDATION_SNAPSHOT",
                            "fatal",
                            True,
                            "validation_snapshot.payload_fingerprint",
                            "current payload fingerprint does not match the expected validation snapshot",
                        ),
                    ),
                )
                validation_error = ExportValidationError(validation_report)
                logger.error("Model export preflight failed: %s", validation_error)
                return build_result(
                    status_message=f"Export failed: {validation_error}",
                    error=validation_error,
                    warnings=list(validation_report.issues),
                )

            model_data = snapshot.model_data
            validation_report = self._validator(model_data, export_format)
            if validation_report.is_blocking:
                validation_error = ExportValidationError(validation_report)
                logger.error("Model export preflight failed: %s", validation_error)
                return build_result(
                    status_message=f"Export failed: {validation_error}",
                    error=validation_error,
                    warnings=list(validation_report.issues),
                )

            if not snapshot.matches(
                source_model_data,
                export_format,
                scene_revision=scene_revision,
                target_identity=target_identity,
            ):
                validation_report = ExportValidationReport(
                    export_format,
                    (
                        ExportValidationIssue(
                            "STALE_VALIDATION_SNAPSHOT",
                            "fatal",
                            True,
                            "validation_snapshot",
                            "payload or scene revision changed after validation",
                        ),
                    ),
                )
                raise ExportValidationError(validation_report)

            writer_model_data = snapshot.copy_for_export()

            target_path = Path(request.file_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_fd, temporary_path = tempfile.mkstemp(
                prefix=f".{target_path.stem}.",
                suffix=f".{export_format}",
                dir=str(target_path.parent),
            )
            os.close(temporary_fd)

            if export_format == "pmx":
                if self._pmx_exporter is None:
                    # Import the Maya-aware writer only after validation has
                    # succeeded and a PMX dispatch is genuinely required.
                    from ..io.pmx_exporter import PmxExporter

                    self._pmx_exporter = PmxExporter()
                phase("encode", True)
                try:
                    self._pmx_exporter.export_pmx_model(temporary_path, writer_model_data)
                except Exception:
                    raise
                else:
                    phase("encode", False)
            else:  # pragma: no cover - guarded by the format preflight above
                raise ValueError(f"Unsupported model export format: {export_format}")

            phase("flush", True)
            try:
                with open(temporary_path, "rb") as output_handle:
                    os.fsync(output_handle.fileno())
            except OSError:
                # A successful writer still owns the output contract on filesystems
                # without an fsync-capable read handle.
                pass
            finally:
                phase("flush", False)

            if self._output_verifier is not None:
                phase("output_verify", True)
                try:
                    output_report = self._output_verifier(
                        temporary_path, export_format, writer_model_data
                    )
                except Exception:
                    raise
                else:
                    phase("output_verify", False)
                if output_report is not None and output_report.issues:
                    validation_report = ExportValidationReport(
                        export_format,
                        tuple(validation_report.issues) + tuple(output_report.issues),
                    )
                if output_report is not None and output_report.is_blocking:
                    raise ExportValidationError(validation_report)

            if request.options.get("verify_mmd_anim") or request.options.get("mmd_anim_cli"):
                mmd_anim_report = verify_mmd_anim_asset(
                    temporary_path,
                    model_data=writer_model_data,
                    cli_path=request.options.get("mmd_anim_cli") or "mmd-anim",
                    timeout=float(request.options.get("mmd_anim_timeout", 60.0)),
                )
                if mmd_anim_report.issues:
                    validation_report = ExportValidationReport(
                        export_format,
                        tuple(validation_report.issues) + tuple(mmd_anim_report.issues),
                    )
                if mmd_anim_report.is_blocking:
                    raise ExportValidationError(validation_report)

            if request.options.get("verify_mmd_anim_binding"):
                expected_binding_counts = {}
                if isinstance(writer_model_data, Mapping):
                    bones = writer_model_data.get("bones")
                    if bones is None:
                        expected_binding_counts["bones"] = 1
                    elif not isinstance(bones, (str, bytes, bytearray)):
                        expected_binding_counts["bones"] = len(bones)
                    morphs = writer_model_data.get("morphs")
                    if morphs is not None and not isinstance(morphs, (str, bytes, bytearray)):
                        expected_binding_counts["morphs"] = len(morphs)
                configured_counts = request.options.get("mmd_anim_binding_expected_counts")
                if isinstance(configured_counts, Mapping):
                    expected_binding_counts.update(configured_counts)
                binding_report = verify_mmd_anim_binding_asset(
                    temporary_path,
                    motion_path=request.options.get("mmd_anim_binding_motion_path"),
                    binding_root=request.options.get("mmd_anim_binding_root"),
                    runtime_library=request.options.get("mmd_anim_binding_library"),
                    frame=request.options.get("mmd_anim_binding_frame", 0.0),
                    expected_counts=expected_binding_counts,
                )
                if binding_report.issues:
                    validation_report = ExportValidationReport(
                        export_format,
                        tuple(validation_report.issues) + tuple(binding_report.issues),
                    )
                if binding_report.is_blocking:
                    raise ExportValidationError(validation_report)

            if validation_report.requires_warning_ack and request.options.get("ack_warnings") is not True:
                warning_callback = request.options.get("_warning_callback")
                phase("warning_decision", True)
                try:
                    approved = (
                        bool(warning_callback(validation_report))
                        if callable(warning_callback)
                        else False
                    )
                finally:
                    phase("warning_decision", False)
                if not approved:
                    raise ExportValidationAcknowledgementRequired(validation_report)

            if not os.path.isfile(temporary_path) or os.path.getsize(temporary_path) == 0:
                raise FileNotFoundError(
                    f"Export writer did not create a non-empty temporary output: {temporary_path}"
                )

            validation_report_artifacts = self._write_requested_report(
                request,
                validation_report,
                payload_fingerprint,
            )
            phase("replace", True)
            try:
                os.replace(temporary_path, request.file_path)
            except Exception:
                raise
            else:
                phase("replace", False)
            temporary_path = None

            logger.info("Exported %s model: %s", export_format.upper(), request.file_path)
            return ExportModelResult(
                exported_path=request.file_path,
                succeeded=True,
                status_message=f"Export complete: {request.file_path}",
                validation_report=validation_report,
                payload_fingerprint=payload_fingerprint,
                validation_report_artifacts=validation_report_artifacts,
            )
        except Exception as exc:
            if validation_report is None:
                exception_report = getattr(exc, "report", None)
                if isinstance(exception_report, ExportValidationReport):
                    validation_report = exception_report
            logger.error("Model export failed: %s", exc, exc_info=True)
            if validation_report is not None and validation_report_artifacts is None:
                try:
                    validation_report_artifacts = self._write_requested_report(
                        request,
                        validation_report,
                        payload_fingerprint,
                    )
                except Exception as artifact_error:
                    logger.error(
                        "Failed to write validation report artifacts: %s",
                        artifact_error,
                        exc_info=True,
                    )
            return ExportModelResult(
                status_message=f"Export failed: {exc}",
                error=exc,
                warnings=list(validation_report.issues) if validation_report else [],
                validation_report=validation_report,
                payload_fingerprint=payload_fingerprint,
                validation_report_artifacts=validation_report_artifacts,
            )
        finally:
            if temporary_path is not None:
                phase("cleanup", True)
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("Failed to remove temporary export file: %s", temporary_path)
                finally:
                    phase("cleanup", False)
