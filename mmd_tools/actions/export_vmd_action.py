"""Action boundary for VMD animation export execution."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from ..converters.vmd_scene_collector import VmdSceneCollector
from ..io.vmd_exporter import VmdExporter

_DEFAULT_COLLECTOR = object()


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
    error: Optional[Exception] = None


class ExportVmdAction:
    """Execute VMD export from already-collected animation data.

    The scene collector is injectable so UI/Maya integration can be added
    without coupling this action to Maya commands.
    """

    def __init__(
        self,
        exporter: Optional[VmdExporter] = None,
        collector: Optional[Callable[[Dict[str, Any]], Any]] = _DEFAULT_COLLECTOR,
    ):
        self._exporter = exporter or VmdExporter()
        if collector is _DEFAULT_COLLECTOR:
            self._collector = VmdSceneCollector().collect
        else:
            self._collector = collector

    def execute(self, request: ExportVmdRequest) -> ExportVmdResult:
        """Run VMD export and convert backend failures into a result object."""
        try:
            animation_data = request.animation_data
            if animation_data is None:
                if self._collector is None:
                    raise ValueError("VMD export requires animation_data or a collector")
                animation_data = self._collector(request.options)
            self._exporter.export_vmd_animation(request.file_path, animation_data)
        except Exception as exc:
            return ExportVmdResult(error=exc)
        return ExportVmdResult(exported_path=request.file_path, succeeded=True)
