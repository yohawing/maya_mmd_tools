"""Context objects passed from VmdConverter into split VMD helper modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, MutableMapping, Optional, Tuple


@dataclass(frozen=True)
class VmdKeyingContext:
    """Minimal state needed by scene keying helpers."""

    logger: Any
    anim_layer: Optional[str]
    use_animation_layers: bool


@dataclass(frozen=True)
class VmdImportContext:
    """Immutable import options for one VMD conversion run."""

    vmd_data: Any
    target_namespace: Optional[str]
    layer_name: str
    bake_mode: bool
    clear_existing_motion: bool
    vmd_bytes: Optional[bytes]
    pmx_bytes: Optional[bytes]
    pmx_path: Optional[str]
    profile: Optional[MutableMapping[str, Any]]
    progress_callback: Optional[Callable[[int], None]]
    import_camera_animation: bool
    import_light_animation: bool


@dataclass(frozen=True)
class VmdRuntimeRigContext:
    """Minimal state needed by runtime-rig scene helpers."""

    logger: Any
    bone_name_mapping: Mapping[str, str]
    bone_bind_poses: Mapping[str, Tuple[float, float, float]]
    runtime_joint_attrs: Callable[[], Tuple[str, str, str, str, str, str]]
