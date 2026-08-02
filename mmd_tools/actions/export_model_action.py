"""Action boundary for PMX/PMD model export execution."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Dict, List, Optional

from ..core.logger import get_logger
from ..io.pmd_exporter import PmdExporter
from ..io.pmx_exporter import PmxExporter
from ..validation.export_validator import ExportValidationError, ExportValidationReport, validate_model_data
from ..validation.output_verifier import verify_model_output

logger = get_logger(__name__)

_DEFAULT_COLLECTOR = object()
_DEFAULT_OUTPUT_VERIFIER = object()


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

    def execute(self, request: ExportModelRequest) -> ExportModelResult:
        """Export a PMX/PMD model and return a small result object."""
        validation_report: Optional[ExportValidationReport] = None
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

            validation_report = validate_model_data(model_data, export_format)
            if validation_report.is_blocking:
                validation_error = ExportValidationError(validation_report)
                logger.error("Model export preflight failed: %s", validation_error)
                return ExportModelResult(
                    status_message=f"Export failed: {validation_error}",
                    error=validation_error,
                    warnings=list(validation_report.issues),
                    validation_report=validation_report,
                )

            target_path = Path(request.file_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_fd, temporary_path = tempfile.mkstemp(
                prefix=f".{target_path.stem}.",
                suffix=f".{export_format}",
                dir=str(target_path.parent),
            )
            os.close(temporary_fd)

            if export_format == "pmx":
                self._pmx_exporter.export_pmx_model(temporary_path, model_data)
            else:
                self._pmd_exporter.export_pmd_model(temporary_path, model_data)

            if self._output_verifier is not None:
                output_report = self._output_verifier(temporary_path, export_format, model_data)
                if output_report is not None and output_report.issues:
                    validation_report = ExportValidationReport(
                        export_format,
                        tuple(validation_report.issues) + tuple(output_report.issues),
                    )
                if output_report is not None and output_report.is_blocking:
                    raise ExportValidationError(validation_report)

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
            )
        except Exception as exc:
            logger.error("Model export failed: %s", exc, exc_info=True)
            return ExportModelResult(
                status_message=f"Export failed: {exc}",
                error=exc,
                warnings=list(validation_report.issues) if validation_report else [],
                validation_report=validation_report,
            )
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("Failed to remove temporary export file: %s", temporary_path)
