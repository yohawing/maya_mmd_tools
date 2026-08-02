"""Action boundary for PMX/PMD model export execution."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Dict, List, Optional

from ..core.logger import get_logger
from ..io.pmd_exporter import PmdExporter
from ..io.pmx_exporter import PmxExporter
from ..validation.export_validator import (
    ExportValidationAcknowledgementRequired,
    ExportValidationError,
    ExportValidationIssue,
    ExportValidationReport,
    validate_model_data,
)
from ..validation.output_verifier import verify_model_output
from ..validation.mmd_anim_verifier import verify_mmd_anim_asset
from ..validation.snapshot import ExportValidationSnapshot

logger = get_logger(__name__)

_DEFAULT_COLLECTOR = object()
_DEFAULT_OUTPUT_VERIFIER = object()
_DEFAULT_VALIDATOR = object()


@dataclass
class ExportModelRequest:
    """Request data for exporting a PMX/PMD model."""

    file_path: str
    options: Dict[str, Any]


@dataclass
class ExportModelResult:
    """Result data returned by PMX/PMD model export."""

    exported_path: Optional[str] = None
    succeeded: bool = False
    status_message: str = ""
    error: Optional[Exception] = None
    warnings: List[Any] = field(default_factory=list)
    validation_report: Optional[ExportValidationReport] = None
    payload_fingerprint: Optional[str] = None


def _default_collect_model_data(options: Dict[str, Any]) -> dict:
    """Collect minimum PMX/PMD-compatible model data from the requested mesh."""
    from maya import cmds

    from ..converters.export_scene_collector import ExportSceneCollector

    collector = ExportSceneCollector()
    if (
        options.get("target_model")
        or options.get("model_root")
        or options.get("target_mesh")
        or options.get("export_mesh")
        or options.get("mesh")
    ):
        return collector.collect(options)

    target = None
    if target is None:
        selection = cmds.ls(selection=True, long=True) or []
        target = selection[0] if selection else None
    if target is None:
        raise ValueError("Model export requires model_data, target_model, target_mesh, or a selected mesh")
    return collector.collect({"target_mesh": target})


class ExportModelAction:
    """Execute PMX/PMD model export from collected or scene-collected data."""

    def __init__(
        self,
        pmx_exporter: Optional[PmxExporter] = None,
        pmd_exporter: Optional[PmdExporter] = None,
        collector: Optional[Callable[[Dict[str, Any]], dict]] = _DEFAULT_COLLECTOR,
        output_verifier: Any = _DEFAULT_OUTPUT_VERIFIER,
        validator: Any = _DEFAULT_VALIDATOR,
    ):
        self._pmx_exporter = pmx_exporter or PmxExporter()
        self._pmd_exporter = pmd_exporter or PmdExporter()
        if collector is _DEFAULT_COLLECTOR:
            self._collector = _default_collect_model_data
        else:
            self._collector = collector
        self._output_verifier = (
            verify_model_output if output_verifier is _DEFAULT_OUTPUT_VERIFIER else output_verifier
        )
        self._validator = validate_model_data if validator is _DEFAULT_VALIDATOR else validator

    def execute(self, request: ExportModelRequest) -> ExportModelResult:
        """Export a PMX/PMD model and return a small result object."""
        validation_report: Optional[ExportValidationReport] = None
        payload_fingerprint: Optional[str] = None
        temporary_path: Optional[str] = None
        try:
            export_format = (request.options.get("export_format") or "").lower()
            if not export_format:
                export_format = Path(request.file_path).suffix.lower().lstrip(".") or "pmx"

            model_data = request.options.get("model_data")
            if model_data is None:
                model_data = request.options.get("maya_data")
            if model_data is None:
                if self._collector is None:
                    raise ValueError("Model export requires model_data or a collector")
                model_data = self._collector(request.options)

            if export_format not in ("pmx", "pmd"):
                raise ValueError(f"Unsupported model export format: {export_format}")

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
                    return ExportModelResult(
                        status_message=f"Export failed: {validation_error}",
                        error=validation_error,
                        warnings=list(validation_report.issues),
                        validation_report=validation_report,
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
                    return ExportModelResult(
                        status_message=f"Export failed: {validation_error}",
                        error=validation_error,
                        warnings=list(validation_report.issues),
                        validation_report=validation_report,
                        payload_fingerprint=snapshot.payload_fingerprint,
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
                return ExportModelResult(
                    status_message=f"Export failed: {validation_error}",
                    error=validation_error,
                    warnings=list(validation_report.issues),
                    validation_report=validation_report,
                    payload_fingerprint=payload_fingerprint,
                )

            model_data = snapshot.model_data
            validation_report = self._validator(model_data, export_format)
            if validation_report.is_blocking:
                validation_error = ExportValidationError(validation_report)
                logger.error("Model export preflight failed: %s", validation_error)
                return ExportModelResult(
                    status_message=f"Export failed: {validation_error}",
                    error=validation_error,
                    warnings=list(validation_report.issues),
                    validation_report=validation_report,
                    payload_fingerprint=payload_fingerprint,
                )

            if validation_report.requires_warning_ack and request.options.get("ack_warnings") is not True:
                acknowledgement_error = ExportValidationAcknowledgementRequired(validation_report)
                logger.error("Model export is waiting for warning acknowledgement: %s", acknowledgement_error)
                return ExportModelResult(
                    status_message=f"Export failed: {acknowledgement_error}",
                    error=acknowledgement_error,
                    warnings=list(validation_report.issues),
                    validation_report=validation_report,
                    payload_fingerprint=payload_fingerprint,
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
                self._pmx_exporter.export_pmx_model(temporary_path, writer_model_data)
            else:
                self._pmd_exporter.export_pmd_model(temporary_path, writer_model_data)

            if self._output_verifier is not None:
                output_report = self._output_verifier(temporary_path, export_format, writer_model_data)
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

            if validation_report.requires_warning_ack and request.options.get("ack_warnings") is not True:
                raise ExportValidationAcknowledgementRequired(validation_report)

            if not os.path.isfile(temporary_path) or os.path.getsize(temporary_path) == 0:
                raise FileNotFoundError(
                    f"Export writer did not create a non-empty temporary output: {temporary_path}"
                )
            os.replace(temporary_path, request.file_path)
            temporary_path = None

            logger.info("Exported %s model: %s", export_format.upper(), request.file_path)
            return ExportModelResult(
                exported_path=request.file_path,
                succeeded=True,
                status_message=f"Export complete: {request.file_path}",
                validation_report=validation_report,
                payload_fingerprint=payload_fingerprint,
            )
        except Exception as exc:
            logger.error("Model export failed: %s", exc, exc_info=True)
            return ExportModelResult(
                status_message=f"Export failed: {exc}",
                error=exc,
                warnings=list(validation_report.issues) if validation_report else [],
                validation_report=validation_report,
                payload_fingerprint=payload_fingerprint,
            )
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("Failed to remove temporary export file: %s", temporary_path)
