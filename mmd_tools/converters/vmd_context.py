"""Context objects passed from VmdConverter into split VMD helper modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, MutableMapping, MutableSet, Optional, Tuple


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
class VmdBoneAnimationContext:
    """State and scene operations needed by legacy VMD bone keying."""

    logger: Any
    bone_name_mapping: Mapping[str, str]
    bone_bind_poses: Mapping[str, Tuple[float, float, float]]
    failed_bones: MutableSet[str]
    use_animation_layers: bool
    anim_layer: Optional[str]
    motion_scale: float
    use_quaternion_interpolation: bool
    set_bone_keyframes: Callable[[str, List[Any], str, Optional[dict]], None]
    build_legacy_bone_key_routes: Callable[[], Mapping[str, dict]]
    collect_ik_link_joints: Callable[[], Mapping[str, dict]]
    add_objects_to_layer: Callable[[List[str]], None]
    add_attrs_to_anim_layer: Callable[[str, List[str]], None]
    vmd_frame_to_maya_time: Callable[[float], float]
    vmd_interp_channel_for_attr: Callable[[str], Optional[str]]
    convert_vmd_quat_to_joint_rotate: Callable[[str, float, float, float, float], Tuple[float, float, float]]
    samples_as_anim_layer_deltas: Callable[[str, Mapping[str, List[Tuple[float, float]]]], Mapping[str, List[Tuple[float, float]]]]
    batch_key_scalar_channels: Callable[[str, Mapping[str, List[Tuple[float, float]]], Optional[str]], bool]
    apply_vmd_bezier_tangents: Callable[[str, List[Any], Any, Mapping[str, str]], None]


@dataclass(frozen=True)
class VmdRuntimeLocalDecomposeContext:
    """Mutable runtime-local decomposition state shared with VmdConverter wrappers."""

    logger: Any
    bone_index_to_joint: Mapping[int, str]
    bone_name_to_index: Mapping[str, int]
    bone_bind_poses: Mapping[str, Tuple[float, float, float]]
    bone_parent_map: MutableMapping[int, Optional[int]]
    bone_rotate_orders: MutableMapping[int, int]
    runtime_bind_world_matrices: MutableMapping[int, Any]
    runtime_no_orient_bind_world_matrices: MutableMapping[int, Any]
    native_local_decompose_cache: MutableMapping[str, Any]
    convert_mmd_world_matrix_to_maya: Callable[[List[float]], List[float]]
    get_joint_orient_cache: Callable[[str], Tuple[Any, int]]


@dataclass(frozen=True)
class VmdRuntimeRigContext:
    """Minimal state needed by runtime-rig scene helpers."""

    logger: Any
    bone_name_mapping: Mapping[str, str]
    bone_bind_poses: Mapping[str, Tuple[float, float, float]]
    runtime_joint_attrs: Callable[[], Tuple[str, str, str, str, str, str]]
