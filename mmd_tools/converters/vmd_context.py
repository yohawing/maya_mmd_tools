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
    target_model: Optional[str]
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
    create_mmd_control_rig: bool = False
    # Explicit opt-in only (default OFF). Requires bake_mode=True.
    use_native_physics_bake: bool = False
    # Explicit programmatic opt-in only (default OFF). Requires bake_mode=True.
    reduce_bake_keys: bool = False
    reduce_translate_tolerance: float = 5.0e-4
    reduce_rotate_tolerance: float = 1.0e-4
    reduce_morph_tolerance: float = 1.0e-3


@dataclass(frozen=True)
class VmdBoneAnimationContext:
    """State and scene operations needed by legacy VMD bone keying."""

    logger: Any
    bone_name_mapping: Mapping[str, str]
    bone_index_to_joint: Mapping[int, str]
    bone_bind_poses: Mapping[str, Tuple[float, float, float]]
    failed_bones: MutableSet[str]
    use_animation_layers: bool
    anim_layer: Optional[str]
    motion_scale: float
    use_quaternion_interpolation: bool
    use_vmd_rotation_time_curve: bool
    rotation_time_curve_records: List[Dict[str, Any]]
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


@dataclass(frozen=True)
class VmdLightAnimationContext:
    """State and keying operations needed by VMD light import."""

    logger: Any
    anim_layer: Optional[str]
    use_animation_layers: bool
    get_or_create_light: Callable[[], str]
    vmd_frame_to_maya_time: Callable[[float], float]
    maya_time_to_vmd_frame: Callable[[float], float]
    add_attrs_to_anim_layer: Callable[[str, List[str]], None]
    samples_as_anim_layer_deltas: Callable[[str, Dict[str, List[Tuple[float, float]]]], Dict[str, List[Tuple[float, float]]]]
    batch_key_scalar_channels: Callable[[str, Dict[str, List[Tuple[float, float]]], Optional[str]], bool]


@dataclass(frozen=True)
class VmdTimelineContext:
    """State and operations needed by VMD timeline setup."""

    logger: Any
    fps: float
    vmd_frame_to_maya_time: Callable[[float], float]


@dataclass(frozen=True)
class VmdIkEnabledAnimationContext:
    """State and scene queries needed by VMD IK enabled-state keying."""

    logger: Any
    collect_ik_nodes_by_bone_name: Callable[[Optional[str], Optional[str]], Dict[str, str]]
    get_animation_frame_range: Callable[[Any], Tuple[int, int]]
    vmd_frame_to_maya_time: Callable[[float], float]


@dataclass(frozen=True)
class VmdNameMappingContext:
    """Mutable mapping state needed to bind VMD names to Maya scene nodes."""

    logger: Any
    bone_name_mapping: MutableMapping[str, str]
    bone_name_to_index: MutableMapping[str, int]
    bone_index_to_joint: MutableMapping[int, str]
    build_morph_mappings: Callable[..., None]


@dataclass(frozen=True)
class VmdImportStateContext:
    """Mutable state and callbacks needed by VMD import cleanup helpers."""

    logger: Any
    bone_name_mapping: Mapping[str, str]
    bone_bind_poses: MutableMapping[str, Tuple[float, float, float]]
    morph_name_mapping: Mapping[str, Any]
    collect_append_info: Callable[[], Dict[str, dict]]
    iter_morph_mappings: Callable[[Any], List[Tuple[str, str, str]]]
    set_refresh_suspended: Callable[[bool], None]


@dataclass(frozen=True)
class VmdMorphAnimationContext:
    """State and keying operations needed by VMD morph import and runtime morph bake."""

    logger: Any
    morph_name_mapping: Mapping[str, Any]
    anim_layer: Optional[str]
    use_animation_layers: bool
    iter_morph_mappings: Callable[[Any], List[Tuple[str, str, str]]]
    vmd_frame_to_maya_time: Callable[[float], float]
    samples_as_anim_layer_deltas: Callable[[str, Dict[str, List[Tuple[float, float]]]], Dict[str, List[Tuple[float, float]]]]
    batch_key_scalar_channels: Callable[[str, Dict[str, List[Tuple[float, float]]], Optional[str]], bool]
