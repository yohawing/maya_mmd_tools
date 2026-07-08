"""Context objects passed from VmdConverter into split VMD helper modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, MutableSet, Optional, Tuple, Union


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


@dataclass(frozen=True)
class VmdRuntimeCacheCollectContext:
    """State and operations needed to evaluate runtime frames into channel arrays."""

    logger: Any
    bone_index_to_joint: Mapping[int, str]
    outer_refresh_suspended: bool
    get_anim_layer: Callable[[], Optional[str]]
    set_anim_layer: Callable[[Optional[str]], None]
    create_runtime_joint_channel_arrays: Callable[[], Dict[str, Dict[str, Any]]]
    create_runtime_joint_channel_static_state: Callable[[], Dict[str, Dict[str, dict]]]
    compute_native_local_channel_batch: Callable[[Any], Optional[dict]]
    runtime_batch_morph_weights_for_frame: Callable[[Any, int], list]
    ensure_bone_hierarchy_maps: Callable[[], None]
    native_local_channel_batch_for_frame: Callable[[dict, int], Dict[int, Tuple[float, float, float, float, float, float]]]
    runtime_batch_world_matrices_for_frame: Callable[[Any, int], list]
    compute_all_bone_locals: Callable[[list], Dict[int, Tuple[float, float, float, float, float, float]]]
    append_bone_locals_to_channel_arrays: Callable[
        [Dict[int, Tuple[float, float, float, float, float, float]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, dict]]],
        None,
    ]


@dataclass(frozen=True)
class VmdRuntimeSceneApplyContext:
    """State and scene operations needed to key collected runtime channel arrays."""

    logger: Any
    outer_refresh_suspended: bool
    collect_append_info: Callable[[], Dict[str, dict]]
    collect_mmd_ik_passthrough_info: Callable[[], Dict[str, Dict[str, Union[str, int]]]]
    decompose_append_rotations_for_scene: Callable[
        [Dict[str, Dict[str, Any]], Dict[str, Dict[str, dict]], Dict[str, dict], int],
        Dict[str, Dict[str, Any]],
    ]
    decompose_append_translations_for_scene: Callable[
        [Dict[str, Dict[str, Any]], Dict[str, Dict[str, dict]], Dict[str, dict], int],
        Dict[str, Dict[str, Any]],
    ]
    key_mmd_ik_passthrough_rotation: Callable[
        [Dict[str, Union[str, int]], Dict[str, Any], Dict[str, dict], Any, List[float], bool],
        int,
    ]
    batch_create_and_key_curve_arrays: Callable[
        [str, Dict[str, Any], Dict[str, dict], Any, List[float]],
        Tuple[int, int],
    ]
    bake_morph_weight_cache_from_runtime: Callable[[List[Tuple[float, list]], List[str]], None]
    apply_runtime_channel_arrays: Optional[
        Callable[
            [Dict[str, Dict[str, Any]], Dict[str, Dict[str, dict]], Any, List[float], List[Tuple[float, list]], List[str]],
            None,
        ]
    ] = None


@dataclass(frozen=True)
class VmdCameraAnimationContext:
    """State and keying operations needed by VMD camera import."""

    motion_scale: float
    anim_layer: Optional[str]
    use_animation_layers: bool
    get_or_create_camera: Callable[[], str]
    vmd_frame_to_maya_time: Callable[[float], float]
    maya_time_to_vmd_frame: Callable[[float], float]
    add_attrs_to_anim_layer: Callable[[str, List[str]], None]
    samples_as_anim_layer_deltas: Callable[[str, Dict[str, List[Tuple[float, float]]]], Dict[str, List[Tuple[float, float]]]]
    batch_key_scalar_channels: Callable[[str, Dict[str, List[Tuple[float, float]]], Optional[str]], bool]
    apply_vmd_bezier_tangents: Callable[..., None]
    get_frame_number: Callable[[Any], float]
