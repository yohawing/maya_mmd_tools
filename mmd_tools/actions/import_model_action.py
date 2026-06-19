"""Action boundary for PMX/PMD model import execution."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from ..io.mmd_importer import import_mmd_file


@dataclass
class ImportModelRequest:
    """Request data for importing a PMX/PMD model."""

    file_path: str
    options: Dict[str, Any]
    create_new_scene: bool = False


@dataclass
class ImportModelResult:
    """Result data returned by PMX/PMD model import."""

    root_node: Any = None
    succeeded: bool = False
    error: Optional[Exception] = None


class ImportModelAction:
    """Execute the Maya-side PMX/PMD model import."""

    def __init__(
        self,
        importer: Optional[Callable[..., Any]] = None,
        new_scene: Optional[Callable[[], None]] = None,
    ):
        self._importer = importer
        self._new_scene = new_scene or self._create_new_scene

    def execute(self, request: ImportModelRequest) -> ImportModelResult:
        """Run model import and convert backend failures into a result object."""
        try:
            if request.create_new_scene:
                self._new_scene()
            importer = self._importer or import_mmd_file
            root_node = importer(request.file_path, options=request.options)
        except Exception as exc:
            return ImportModelResult(error=exc)
        return ImportModelResult(root_node=root_node, succeeded=bool(root_node))

    @staticmethod
    def _create_new_scene() -> None:
        from maya import cmds

        cmds.file(new=True, force=True)
