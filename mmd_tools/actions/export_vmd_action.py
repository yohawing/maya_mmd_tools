"""Action boundary for validated, atomic VMD animation export."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import inspect
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Dict, List, Optional

from ..converters.vmd_scene_collector import VmdSceneCollector
from ..core.logger import get_logger
from ..io.vmd_exporter import VmdExporter
from ..validation.export_validator import (
    ExportValidationAcknowledgementRequired,
    ExportValidationError,
    ExportValidationReport,
)
from ..validation.mmd_anim_verifier import verify_mmd_anim_asset
from ..validation.report_artifacts import (
    ValidationReportArtifactPaths,
    write_validation_report_artifacts,
)
from ..validation.snapshot import fingerprint_payload
from ..validation.vmd_validator import VMD_MODE_C, validate_vmd_data, verify_vmd_output


logger = get_logger(__name__)

_DEFAULT_COLLECTOR = object()
_DEFAULT_OUTPUT_VERIFIER = object()
_DEFAULT_VALIDATOR = object()


@dataclass
class ExportVmdRequest:
    """Request data for exporting a VMD animation."""

    file_path: str
    options: Dict[str, Any]
    animation_data: Any = None


@dataclass
class ExportVmdResult:
    """Result data returned by VMD animation export."""

    exported_path: Optional[str] = None
    succeeded: bool = False
    status_message: str = ""
    error: Optional[Exception] = None
    warnings: List[Any] = field(default_factory=list)
    validation_report: Optional[ExportValidationReport] = None
    payload_fingerprint: Optional[str] = None
    validation_report_artifacts: Optional[ValidationReportArtifactPaths] = None


class ExportVmdAction:
    """Validate, write, and verify VMD data without replacing bad output.

    The scene collector, payload validator, and output verifier are injectable
    so the same orchestration can be exercised by headless tests and Maya UI.
    """

    def __init__(
        self,
        exporter: Optional[VmdExporter] = None,
        collector: Optional[Callable[[Dict[str, Any]], Any]] = _DEFAULT_COLLECTOR,
        output_verifier: Any = _DEFAULT_OUTPUT_VERIFIER,
        validator: Any = _DEFAULT_VALIDATOR,
    ):
        self._exporter = exporter or VmdExporter()
        if collector is _DEFAULT_COLLECTOR:
            self._collector = VmdSceneCollector().collect
        else:
            self._collector = collector
        self._output_verifier = (
            verify_vmd_output
            if output_verifier is _DEFAULT_OUTPUT_VERIFIER
            else output_verifier
        )
        self._validator = validate_vmd_data if validator is _DEFAULT_VALIDATOR else validator

    @staticmethod
    def _write_requested_report(
        request: ExportVmdRequest,
        report: Optional[ExportValidationReport],
        payload_fingerprint: Optional[str],
        mode: str,
    ) -> Optional[ValidationReportArtifactPaths]:
        """Write report artifacts only when the caller supplied a run directory."""
        report_directory = request.options.get("validation_report_dir")
        if report is None or report_directory in (None, ""):
            return None

        configured_evidence = request.options.get("validation_report_evidence") or {}
        if not isinstance(configured_evidence, Mapping):
            raise TypeError("validation_report_evidence must be a mapping")
        evidence = dict(configured_evidence)
        evidence.setdefault("target_path", str(request.file_path))
        evidence.setdefault("mode", mode)
        evidence.setdefault(
            "raw_provenance_supplied",
            bool(request.options.get("raw_provenance", request.options.get("vmd_raw_provenance"))),
        )
        return write_validation_report_artifacts(
            report,
            report_directory,
            target_identity=request.options.get("target_identity"),
            snapshot_fingerprint=payload_fingerprint,
            provenance=request.options.get("validation_report_provenance", "VmdPayloadValidator"),
            evidence=evidence,
        )

    def _to_vmd_data(self, animation_data: Any):
        """Normalize collected data using the exporter or the local converter."""
        converter = getattr(self._exporter, "to_vmd_data", None)
        if callable(converter):
            return converter(animation_data)
        return VmdExporter(native_exporter=None).to_vmd_data(animation_data)

    @staticmethod
    def _append_report(
        report: ExportValidationReport,
        additional: Optional[ExportValidationReport],
        mode: str,
    ) -> ExportValidationReport:
        """Combine validator reports while preserving the selected VMD mode."""
        if additional is None or not additional.issues:
            return report
        return ExportValidationReport(
            "vmd",
            tuple(report.issues) + tuple(additional.issues),
            mode=mode,
        )

    def _validate(self, vmd_data: Any, mode: str, options: Mapping[str, Any]) -> ExportValidationReport:
        """Run the configured validator with the VMD workflow options."""
        frame_range = options.get("frame_range")
        if frame_range is None and "frame_start" in options and "frame_end" in options:
            frame_range = (options.get("frame_start"), options.get("frame_end"))
        raw_provenance = options.get("raw_provenance", options.get("vmd_raw_provenance"))
        try:
            parameters = inspect.signature(self._validator).parameters
        except (TypeError, ValueError):
            parameters = {}
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        kwargs = {}
        if accepts_kwargs or "raw_provenance" in parameters:
            kwargs["raw_provenance"] = raw_provenance
        if accepts_kwargs or "frame_range" in parameters:
            kwargs["frame_range"] = frame_range
        return self._validator(vmd_data, mode, **kwargs)

    def execute(self, request: ExportVmdRequest) -> ExportVmdResult:
        """Run VMD validation/export and convert failures into a result object."""
        validation_report: Optional[ExportValidationReport] = None
        payload_fingerprint: Optional[str] = None
        validation_report_artifacts: Optional[ValidationReportArtifactPaths] = None
        temporary_path: Optional[str] = None
        mode = str(request.options.get("vmd_mode", request.options.get("mode", VMD_MODE_C)) or "").upper()

        def build_result(
            *,
            exported_path: Optional[str] = None,
            succeeded: bool = False,
            status_message: str = "",
            error: Optional[Exception] = None,
            warnings: Optional[List[Any]] = None,
        ) -> ExportVmdResult:
            """Build a result and persist the final report when requested."""
            nonlocal validation_report_artifacts
            validation_report_artifacts = self._write_requested_report(
                request,
                validation_report,
                payload_fingerprint,
                mode,
            )
            return ExportVmdResult(
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
            animation_data = request.animation_data
            if animation_data is None:
                if self._collector is None:
                    raise ValueError("VMD export requires animation_data or a collector")
                animation_data = self._collector(request.options)

            vmd_data = self._to_vmd_data(animation_data)
            validation_report = self._validate(vmd_data, mode, request.options)
            if validation_report.is_blocking:
                validation_error = ExportValidationError(validation_report)
                logger.error("VMD export preflight failed: %s", validation_error)
                return build_result(
                    status_message=f"Export failed: {validation_error}",
                    error=validation_error,
                    warnings=list(validation_report.issues),
                )

            try:
                payload = VmdExporter(native_exporter=None).to_native_json_payload(vmd_data)
                payload_fingerprint = fingerprint_payload(payload)
            except (TypeError, ValueError, OverflowError):
                # Structural validation remains the authoritative failure. If
                # a custom validator omitted a field, do not invent a digest.
                payload_fingerprint = None

            if validation_report.requires_warning_ack and request.options.get("ack_warnings") is not True:
                acknowledgement_error = ExportValidationAcknowledgementRequired(validation_report)
                logger.error("VMD export is waiting for warning acknowledgement: %s", acknowledgement_error)
                return build_result(
                    status_message=f"Export failed: {acknowledgement_error}",
                    error=acknowledgement_error,
                    warnings=list(validation_report.issues),
                )

            target_path = Path(request.file_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_fd, temporary_path = tempfile.mkstemp(
                prefix=f".{target_path.stem}.",
                suffix=".vmd",
                dir=str(target_path.parent),
            )
            os.close(temporary_fd)

            self._exporter.export_vmd_animation(temporary_path, animation_data)

            if self._output_verifier is not None:
                output_report = self._output_verifier(temporary_path, mode)
                validation_report = self._append_report(validation_report, output_report, mode)
                if output_report is not None and output_report.is_blocking:
                    raise ExportValidationError(validation_report)

            if request.options.get("verify_mmd_anim") or request.options.get("mmd_anim_cli"):
                mmd_anim_report = verify_mmd_anim_asset(
                    temporary_path,
                    cli_path=request.options.get("mmd_anim_cli") or "mmd-anim",
                    timeout=float(request.options.get("mmd_anim_timeout", 60.0)),
                )
                validation_report = self._append_report(validation_report, mmd_anim_report, mode)
                if mmd_anim_report.is_blocking:
                    raise ExportValidationError(validation_report)

            if validation_report.requires_warning_ack and request.options.get("ack_warnings") is not True:
                raise ExportValidationAcknowledgementRequired(validation_report)

            if not os.path.isfile(temporary_path) or os.path.getsize(temporary_path) == 0:
                raise FileNotFoundError(
                    f"Export writer did not create a non-empty temporary output: {temporary_path}"
                )

            validation_report_artifacts = self._write_requested_report(
                request,
                validation_report,
                payload_fingerprint,
                mode,
            )
            os.replace(temporary_path, request.file_path)
            temporary_path = None

            logger.info("Exported VMD animation (%s): %s", mode, request.file_path)
            return ExportVmdResult(
                exported_path=request.file_path,
                succeeded=True,
                status_message=f"Export complete: {request.file_path}",
                validation_report=validation_report,
                payload_fingerprint=payload_fingerprint,
                validation_report_artifacts=validation_report_artifacts,
            )
        except Exception as exc:
            logger.error("VMD export failed: %s", exc, exc_info=True)
            if validation_report is not None and validation_report_artifacts is None:
                try:
                    validation_report_artifacts = self._write_requested_report(
                        request,
                        validation_report,
                        payload_fingerprint,
                        mode,
                    )
                except Exception as artifact_error:
                    logger.error(
                        "Failed to write VMD validation report artifacts: %s",
                        artifact_error,
                        exc_info=True,
                    )
            return ExportVmdResult(
                status_message=f"Export failed: {exc}",
                error=exc,
                warnings=list(validation_report.issues) if validation_report else [],
                validation_report=validation_report,
                payload_fingerprint=payload_fingerprint,
                validation_report_artifacts=validation_report_artifacts,
            )
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("Failed to remove temporary VMD export file: %s", temporary_path)


__all__ = ["ExportVmdAction", "ExportVmdRequest", "ExportVmdResult"]
