"""Action boundary for VMD animation import execution."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..adapters import MayaCmdsAdapter
from ..io.mmd_importer import import_mmd_file

# Explicit import result outcomes (backwards-compatible with succeeded/error/warnings).
OUTCOME_SUCCESS = "success"
OUTCOME_PARTIAL = "partial"
OUTCOME_FATAL = "fatal"

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
        warnings = _warnings_from_options(request.options)
        return ImportVmdResult(
            root_node=root_node,
            succeeded=bool(root_node),
            warnings=warnings,
            outcome=_classify_import_outcome(root_node, warnings),
        )

    def _create_new_scene(self) -> None:
        adapter = self._maya_adapter or MayaCmdsAdapter()
        adapter.new_scene(force=True)


def _classify_import_outcome(root_node: Any, warnings: List[Any]) -> str:
    """Classify import into success, partial, or fatal."""
    if not root_node:
        return OUTCOME_FATAL
    if warnings:
        return OUTCOME_PARTIAL
    return OUTCOME_SUCCESS


def _warnings_from_options(options: Dict[str, Any]) -> List[Any]:
    """Collect warning records accumulated in an import profile."""
    profile = options.get("profile") if isinstance(options, dict) else None
    if not isinstance(profile, dict):
        return []
    warnings = list(profile.get("warnings") or [])
    warnings.extend(profile.get("vmd_converter", {}).get("warnings") or [])
    warnings.extend(profile.get("bone_converter", {}).get("warnings") or [])
    warnings.extend(profile.get("bone_converter", {}).get("rig_converter", {}).get("warnings") or [])
    # Bone morph runtime fail-soft (e.g. node_type_unavailable) is partial, not fatal.
    warnings.extend(profile.get("bone_morph_runtime", {}).get("warnings") or [])
    warnings.extend(profile.get("texture_issues") or [])
    warnings.extend(profile.get("mesh_converter", {}).get("unresolved_textures") or [])
    return warnings
