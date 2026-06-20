"""Action boundary for VMD animation import execution."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from ..adapters import MayaCmdsAdapter
from ..io.mmd_importer import import_mmd_file


@dataclass
class ImportVmdRequest:
    """Request data for importing a VMD animation."""

    file_path: str
    options: Dict[str, Any]
    create_new_scene: bool = False


@dataclass
class ImportVmdResult:
    """Result data returned by VMD animation import."""

    root_node: Any = None
    succeeded: bool = False
    error: Optional[Exception] = None


class ImportVmdAction:
    """Execute the Maya-side VMD animation import."""

    def __init__(
        self,
        importer: Optional[Callable[..., Any]] = None,
        new_scene: Optional[Callable[[], None]] = None,
        maya_adapter: Optional[MayaCmdsAdapter] = None,
    ):
        self._importer = importer
        self._maya_adapter = maya_adapter
        self._new_scene = new_scene or self._create_new_scene

    def execute(self, request: ImportVmdRequest) -> ImportVmdResult:
        """Run VMD import and convert backend failures into a result object."""
        try:
            if request.create_new_scene:
                self._new_scene()
            importer = self._importer or import_mmd_file
            root_node = importer(request.file_path, options=request.options)
        except Exception as exc:
            return ImportVmdResult(error=exc)
        return ImportVmdResult(root_node=root_node, succeeded=bool(root_node))

    def _create_new_scene(self) -> None:
        adapter = self._maya_adapter or MayaCmdsAdapter()
        adapter.new_scene(force=True)
