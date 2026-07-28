"""Action boundary for VMD animation import execution."""

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
    "ImportVmdAction",
    "ImportVmdRequest",
    "ImportVmdResult",
    "VMD_TARGET_AUTO",
    "VMD_TARGET_CAMERA",
]

# Tagged target choices used by the VMD target combo.  Model choices use their
# concrete Maya root string instead of either sentinel.
VMD_TARGET_AUTO = "__vmd_target_auto__"
VMD_TARGET_CAMERA = "__vmd_target_camera__"


@dataclass
class ImportVmdRequest:
    """Request data for importing a VMD animation."""

    file_path: str
    options: Dict[str, Any]
    create_new_scene: bool = False
    progress_callback: Optional[Callable[[int], None]] = None


@dataclass
class ImportVmdResult:
    """Result data returned by VMD animation import."""

    root_node: Any = None
    succeeded: bool = False
    error: Optional[Exception] = None
    warnings: List[Any] = field(default_factory=list)
    # success | partial | fatal. None means callers should derive from succeeded/error/warnings.
    outcome: Optional[str] = None


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
            scene_only = bool(request.options.get("scene_animation_only", False))
            if scene_only:
                if "target_model" in request.options:
                    raise ValueError("Camera Motion must not specify target_model")
            elif request.create_new_scene:
                raise ValueError("VMD model motion cannot create a new scene")
            elif not request.options.get("target_model"):
                raise ValueError("VMD model motion requires an explicit target model")
            if request.create_new_scene:
                self._new_scene()
            importer = self._importer or import_mmd_file
            kwargs = {"options": request.options}
            if request.progress_callback is not None:
                kwargs["progress_callback"] = request.progress_callback
            root_node = importer(request.file_path, **kwargs)
        except Exception as exc:
            return ImportVmdResult(error=exc, outcome=OUTCOME_FATAL)
        warnings = warnings_from_options(request.options)
        return ImportVmdResult(
            root_node=root_node,
            succeeded=bool(root_node),
            warnings=warnings,
            outcome=classify_import_outcome(root_node, warnings),
        )

    def _create_new_scene(self) -> None:
        adapter = self._maya_adapter or MayaCmdsAdapter()
        adapter.new_scene(force=True)
