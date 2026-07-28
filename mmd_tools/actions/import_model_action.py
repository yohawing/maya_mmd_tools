"""Action boundary for PMX/PMD model import execution."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..adapters import MayaCmdsAdapter
from ..io.mmd_importer import import_mmd_file
from .import_result import (
    OUTCOME_FATAL,
    OUTCOME_PARTIAL,
    OUTCOME_SUCCESS,
    classify_import_outcome,
    warnings_from_options,
)

__all__ = [
    "OUTCOME_FATAL",
    "OUTCOME_PARTIAL",
    "OUTCOME_SUCCESS",
    "ImportModelAction",
    "ImportModelRequest",
    "ImportModelResult",
]


@dataclass
class ImportModelRequest:
    """Request data for importing a PMX/PMD model."""

    file_path: str
    options: Dict[str, Any]
    create_new_scene: bool = False
    progress_callback: Optional[Callable[[int], None]] = None


@dataclass
class ImportModelResult:
    """Result data returned by PMX/PMD model import."""

    root_node: Any = None
    succeeded: bool = False
    error: Optional[Exception] = None
    warnings: List[Any] = field(default_factory=list)
    # success | partial | fatal. None means callers should derive from succeeded/error/warnings.
    outcome: Optional[str] = None


class ImportModelAction:
    """Execute the Maya-side PMX/PMD model import."""

    def __init__(
        self,
        importer: Optional[Callable[..., Any]] = None,
        new_scene: Optional[Callable[[], None]] = None,
        maya_adapter: Optional[MayaCmdsAdapter] = None,
    ):
        self._importer = importer
        self._maya_adapter = maya_adapter
        self._new_scene = new_scene or self._create_new_scene

    def execute(self, request: ImportModelRequest) -> ImportModelResult:
        """Run model import and convert backend failures into a result object."""
        try:
            if request.create_new_scene:
                self._new_scene()
            importer = self._importer or import_mmd_file
            kwargs = {"options": request.options}
            if request.progress_callback is not None:
                kwargs["progress_callback"] = request.progress_callback
            root_node = importer(request.file_path, **kwargs)
        except Exception as exc:
            return ImportModelResult(error=exc, outcome=OUTCOME_FATAL)
        warnings = warnings_from_options(request.options)
        return ImportModelResult(
            root_node=root_node,
            succeeded=bool(root_node),
            warnings=warnings,
            outcome=classify_import_outcome(root_node, warnings),
        )

    def _create_new_scene(self) -> None:
        adapter = self._maya_adapter or MayaCmdsAdapter()
        adapter.new_scene(force=True)
