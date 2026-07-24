"""
ctypes type definitions shared by the mmd-anim runtime wrapper.

This module keeps C ABI structures and lightweight batch result containers
separate from the runtime loader so other native helpers can import them
without pulling in library discovery or function binding side effects.
"""

from __future__ import annotations

from ctypes import POINTER, Structure, c_bool, c_float, c_int32, c_size_t, c_uint8, c_uint16, c_uint32, c_uint64
from typing import Any, NamedTuple, Tuple


MMD_RUNTIME_STATUS_OK = 0
MMD_RUNTIME_STATUS_INVALID_INPUT = 1
MMD_RUNTIME_STATUS_UNSUPPORTED = 2
MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL = 3
MMD_RUNTIME_STATUS_ERROR = 4

MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION = 1 << 0
MMD_RUNTIME_FEATURE_PHYSICS_BULLET_NATIVE = 1 << 1
MMD_RUNTIME_FEATURE_MODEL_DESCRIPTOR = 1 << 2
MMD_RUNTIME_FEATURE_HOST_POSE_NATIVE_MORPHS = 1 << 3
MMD_RUNTIME_FEATURE_REDUCED_POSE_GENERIC_CURVES = 1 << 4
MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS = (
    MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION
    | MMD_RUNTIME_FEATURE_PHYSICS_BULLET_NATIVE
)

MMD_RUNTIME_REDUCED_POSE_GENERIC_CURVE_ABI_VERSION_V1 = 1

MMD_RUNTIME_REDUCTION_TARGET_LINEAR_SLERP = 0
MMD_RUNTIME_REDUCTION_TARGET_VMD_BEZIER = 1
MMD_RUNTIME_REDUCTION_TARGET_DCC_CUBIC = 2

MMD_RUNTIME_GENERIC_CURVE_BONE_LOCAL = 0
MMD_RUNTIME_GENERIC_CURVE_MORPH_WEIGHT = 1
MMD_RUNTIME_GENERIC_VALUE_TRANSLATION = 1 << 0
MMD_RUNTIME_GENERIC_VALUE_QUATERNION = 1 << 1
MMD_RUNTIME_GENERIC_VALUE_SCALAR = 1 << 2
MMD_RUNTIME_GENERIC_COORDINATE_MMD_RUNTIME_NATIVE = 0
MMD_RUNTIME_GENERIC_LENGTH_MODEL_UNITS = 0
MMD_RUNTIME_GENERIC_ANGLE_RADIANS = 0
MMD_RUNTIME_GENERIC_TIME_SAMPLE_FRAMES = 0
MMD_RUNTIME_GENERIC_TANGENT_VALUE_PER_SAMPLE_FRAME = 0
MMD_RUNTIME_GENERIC_ROTATION_BASIS_NONE = 0
MMD_RUNTIME_GENERIC_ROTATION_BASIS_RUNTIME_QUATERNION = 1
MMD_RUNTIME_GENERIC_ROTATION_BASIS_EULER_XYZ_RADIANS_PER_FRAME = 2

MMD_RUNTIME_PHYSICS_MODE_OFF = 0
MMD_RUNTIME_PHYSICS_MODE_TRACE = 1
MMD_RUNTIME_PHYSICS_MODE_LIVE = 2

MMD_RUNTIME_PHYSICS_FRAME_ACTION_SEED = 0
MMD_RUNTIME_PHYSICS_FRAME_ACTION_STEP = 1


class MmdRuntimeFfiByteBuffer(Structure):
    """
    Rust MmdRuntimeFfiByteBuffer (repr(C)) mirrored as a ctypes Structure.

    Fields:
        data: Byte pointer (uint8_t*).
        len: Byte length (size_t).
    """

    _fields_ = [
        ("data", POINTER(c_uint8)),
        ("len", c_size_t),
    ]


class MmdRuntimeBatchEvaluation(NamedTuple):
    """Contiguous frame batch evaluation result."""

    frame_count: int
    bone_count: int
    morph_count: int
    world_matrices: Any
    morph_weights: Any


class MmdRuntimeLocalChannelBatch(NamedTuple):
    """Batch local channel result from native Maya local decomposition."""

    frame_count: int
    bone_count: int
    local_channels: Any


class MmdRuntimeFfiReductionTolerances(Structure):
    """Reduction tolerances matching ``mmd_runtime_ffi_reduction_tolerances_t``."""

    _fields_ = [
        ("local_position", c_float),
        ("local_rotation_radians", c_float),
        ("world_position", c_float),
        ("world_rotation_radians", c_float),
        ("morph_weight", c_float),
    ]


class MmdRuntimeFfiPoseReductionReport(Structure):
    """Pose reduction report matching the native ABI."""

    _fields_ = [
        ("source_bone_key_count", c_size_t),
        ("reduced_bone_key_count", c_size_t),
        ("source_morph_key_count", c_size_t),
        ("reduced_morph_key_count", c_size_t),
        ("max_local_position_error", c_float),
        ("max_local_rotation_error_radians", c_float),
        ("max_world_position_error", c_float),
        ("max_world_rotation_error_radians", c_float),
        ("max_morph_weight_error", c_float),
    ]


class MmdRuntimeFfiGenericCurveInfo(Structure):
    """Generic reduced-curve metadata matching ABI version 1."""

    _fields_ = [
        ("struct_size", c_uint32),
        ("abi_version", c_uint32),
        ("reduction_target", c_uint32),
        ("coordinate_system", c_uint32),
        ("length_unit", c_uint32),
        ("angle_unit", c_uint32),
        ("time_unit", c_uint32),
        ("tangent_unit", c_uint32),
        ("model_identity", c_uint64),
        ("start_frame", c_float),
        ("frame_step", c_float),
        ("frame_count", c_size_t),
        ("bone_count", c_size_t),
        ("morph_count", c_size_t),
    ]


class MmdRuntimeFfiGenericCurveDescriptor(Structure):
    """Generic reduced-curve descriptor matching ABI version 1."""

    _fields_ = [
        ("struct_size", c_uint32),
        ("abi_version", c_uint32),
        ("kind", c_uint32),
        ("target_index", c_uint32),
        ("parent_index", c_int32),
        ("value_flags", c_uint32),
        ("interpolation", c_uint32),
        ("rotation_basis", c_uint32),
        ("key_count", c_size_t),
    ]


class MmdRuntimeFfiGenericCurveKey(Structure):
    """One generic reduced-curve key, including diagnostic segment fields."""

    _fields_ = [
        ("sample_index", c_size_t),
        ("frame", c_float),
        ("translation_xyz", c_float * 3),
        ("rotation_xyzw", c_float * 4),
        ("scalar", c_float),
        ("segment_prev_out_translation_xyz", c_float * 3),
        ("segment_current_in_translation_xyz", c_float * 3),
        ("segment_from_previous_start_euler_xyz", c_float * 3),
        ("segment_from_previous_end_euler_xyz", c_float * 3),
        ("segment_prev_out_rotation_xyz", c_float * 3),
        ("segment_current_in_rotation_xyz", c_float * 3),
        ("segment_prev_out_scalar", c_float),
        ("segment_current_in_scalar", c_float),
    ]


class MmdRuntimeReductionTolerances(NamedTuple):
    """Python-friendly reduction tolerances in runtime-native units."""

    local_position: float = 5.0e-4
    local_rotation_radians: float = 1.0e-3
    world_position: float = 5.0e-4
    world_rotation_radians: float = 1.0e-3
    morph_weight: float = 1.0e-3


class MmdRuntimePoseReductionReport(NamedTuple):
    """Owned Python copy of the native pose reduction report."""

    source_bone_key_count: int
    reduced_bone_key_count: int
    source_morph_key_count: int
    reduced_morph_key_count: int
    max_local_position_error: float
    max_local_rotation_error_radians: float
    max_world_position_error: float
    max_world_rotation_error_radians: float
    max_morph_weight_error: float


class MmdRuntimeGenericCurveInfo(NamedTuple):
    """Owned Python copy of generic reduced-curve metadata."""

    struct_size: int
    abi_version: int
    reduction_target: int
    coordinate_system: int
    length_unit: int
    angle_unit: int
    time_unit: int
    tangent_unit: int
    model_identity: int
    start_frame: float
    frame_step: float
    frame_count: int
    bone_count: int
    morph_count: int


class MmdRuntimeGenericCurveDescriptor(NamedTuple):
    """Owned Python copy of one generic reduced-curve descriptor."""

    struct_size: int
    abi_version: int
    kind: int
    target_index: int
    parent_index: int
    value_flags: int
    interpolation: int
    rotation_basis: int
    key_count: int


class MmdRuntimeGenericCurveKey(NamedTuple):
    """Owned Python copy of one generic curve key.

    Euler segment fields are diagnostic fit data.  They intentionally remain
    separate from ``rotation_xyzw`` and must not be treated as Maya channels.
    """

    sample_index: int
    frame: float
    translation_xyz: Tuple[float, float, float]
    rotation_xyzw: Tuple[float, float, float, float]
    scalar: float
    segment_prev_out_translation_xyz: Tuple[float, float, float]
    segment_current_in_translation_xyz: Tuple[float, float, float]
    segment_from_previous_start_euler_xyz: Tuple[float, float, float]
    segment_from_previous_end_euler_xyz: Tuple[float, float, float]
    segment_prev_out_rotation_xyz: Tuple[float, float, float]
    segment_current_in_rotation_xyz: Tuple[float, float, float]
    segment_prev_out_scalar: float
    segment_current_in_scalar: float


class MmdRuntimeGenericCurve(NamedTuple):
    """Owned generic curve DTO pairing a descriptor with copied keys."""

    descriptor: MmdRuntimeGenericCurveDescriptor
    keys: Tuple[MmdRuntimeGenericCurveKey, ...]


class MmdRuntimeReducedPoseResult(NamedTuple):
    """Owned generic curves and report detached from a native reduction handle."""

    info: MmdRuntimeGenericCurveInfo
    curves: Tuple[MmdRuntimeGenericCurve, ...]
    report: MmdRuntimePoseReductionReport


class MmdRuntimeFfiPhysicsStepStats(Structure):
    """Physics fixed-step statistics returned by the native FFI."""

    _fields_ = [
        ("input_dt_seconds", c_float),
        ("clamped_dt_seconds", c_float),
        ("substeps", c_uint32),
        ("accumulator_seconds", c_float),
    ]


class MmdRuntimeFfiPhysicsWorldStepReport(Structure):
    """Physics world step report returned by the native FFI."""

    _fields_ = [
        ("tick", MmdRuntimeFfiPhysicsStepStats),
        ("kinematic_rigidbodies_fed", c_size_t),
        ("bones_written_back", c_size_t),
    ]


class MmdRuntimeFfiPhysicsRigidbodyBinding(Structure):
    """Rigid body to bone binding returned by the host physics ABI."""

    _fields_ = [
        ("bone_index", c_int32),
        ("mode", c_uint32),
    ]


class MmdRuntimeFfiHostPoseView(Structure):
    """Borrowed caller-owned host pose buffers for one atomic evaluation."""

    _fields_ = [
        ("local_position_offsets_xyz", POINTER(c_float)),
        ("local_rotation_xyzw", POINTER(c_float)),
        ("local_scales_xyz", POINTER(c_float)),
        ("bone_count", c_size_t),
        ("morph_weights", POINTER(c_float)),
        ("morph_count", c_size_t),
        ("ik_enabled", POINTER(c_uint8)),
        ("ik_count", c_size_t),
    ]


class MmdRuntimeFfiRigBone(Structure):
    """Runtime rig bone descriptor passed to the native FFI."""

    _fields_ = [
        ("parent_slot", c_int32),
        ("rest_position_xyz", c_float * 3),
        ("flags", c_uint32),
        ("fixed_axis_xyz", c_float * 3),
    ]


MMD_RUNTIME_RIG_BONE_FIXED_AXIS = 1 << 0


class MmdRuntimeFfiRigIkLink(Structure):
    """Runtime IK link descriptor passed to the native FFI."""

    _fields_ = [
        ("bone_slot", c_uint32),
        ("has_angle_limit", c_bool),
        ("angle_limit_min_xyz", c_float * 3),
        ("angle_limit_max_xyz", c_float * 3),
    ]


class MmdRuntimeFfiRigBoneLocalAxisV2(Structure):
    """Optional PMX local-axis descriptor for v2 IK-chain creation."""

    _fields_ = [
        ("has_local_axis", c_bool),
        ("local_axis_x_xyz", c_float * 3),
        ("local_axis_z_xyz", c_float * 3),
    ]


class MmdRuntimeModelBoneDescriptor(Structure):
    """Version 1 typed model bone descriptor."""

    _fields_ = [
        ("parent_index", c_int32),
        ("rest_position_xyz", c_float * 3),
        ("transform_order", c_int32),
        ("flags", c_uint32),
        ("fixed_axis_xyz", c_float * 3),
        ("local_axis_x_xyz", c_float * 3),
        ("local_axis_z_xyz", c_float * 3),
    ]


MMD_RUNTIME_MODEL_BONE_TRANSFORM_AFTER_PHYSICS = 1 << 0
MMD_RUNTIME_MODEL_BONE_FIXED_AXIS = 1 << 1
MMD_RUNTIME_MODEL_BONE_LOCAL_AXIS = 1 << 2


class MmdRuntimeModelIkSolverDescriptor(Structure):
    _fields_ = [
        ("ik_bone_index", c_uint32),
        ("target_bone_index", c_uint32),
        ("link_offset", c_size_t),
        ("link_count", c_size_t),
        ("iteration_count", c_uint32),
        ("limit_angle", c_float),
    ]


class MmdRuntimeModelIkLinkDescriptor(Structure):
    _fields_ = [
        ("bone_index", c_uint32),
        ("flags", c_uint32),
        ("angle_limit_min_xyz", c_float * 3),
        ("angle_limit_max_xyz", c_float * 3),
    ]


MMD_RUNTIME_MODEL_IK_LINK_ANGLE_LIMIT = 1


class MmdRuntimeModelAppendDescriptor(Structure):
    _fields_ = [
        ("target_bone_index", c_uint32),
        ("source_bone_index", c_uint32),
        ("ratio", c_float),
        ("flags", c_uint32),
    ]


MMD_RUNTIME_MODEL_APPEND_ROTATION = 1
MMD_RUNTIME_MODEL_APPEND_TRANSLATION = 1 << 1
MMD_RUNTIME_MODEL_APPEND_LOCAL = 1 << 2


class MmdRuntimeModelBoneMorphOffsetDescriptor(Structure):
    _fields_ = [
        ("morph_index", c_uint32),
        ("target_bone_index", c_uint32),
        ("position_offset_xyz", c_float * 3),
        ("rotation_offset_xyzw", c_float * 4),
    ]


class MmdRuntimeModelGroupMorphOffsetDescriptor(Structure):
    _fields_ = [
        ("morph_index", c_uint32),
        ("child_morph_index", c_uint32),
        ("ratio", c_float),
    ]


class MmdRuntimeModelDescriptor(Structure):
    """Top-level version 1 model descriptor passed to the native FFI."""

    _fields_ = [
        ("struct_size", c_uint32),
        ("descriptor_version", c_uint32),
        ("flags", c_uint32),
        ("reserved", c_uint32),
        ("bones", POINTER(MmdRuntimeModelBoneDescriptor)),
        ("bone_count", c_size_t),
        ("ik_solvers", POINTER(MmdRuntimeModelIkSolverDescriptor)),
        ("ik_solver_count", c_size_t),
        ("ik_links", POINTER(MmdRuntimeModelIkLinkDescriptor)),
        ("ik_link_count", c_size_t),
        ("append_transforms", POINTER(MmdRuntimeModelAppendDescriptor)),
        ("append_transform_count", c_size_t),
        ("morph_count", c_uint32),
        ("bone_morph_offsets", POINTER(MmdRuntimeModelBoneMorphOffsetDescriptor)),
        ("bone_morph_offset_count", c_size_t),
        ("group_morph_offsets", POINTER(MmdRuntimeModelGroupMorphOffsetDescriptor)),
        ("group_morph_offset_count", c_size_t),
    ]


MMD_RUNTIME_MODEL_DESCRIPTOR_VERSION_V1 = 1
MMD_RUNTIME_MODEL_DESCRIPTOR_FLAGS_NONE = 0


class MmdRuntimeFfiIkSolveStats(Structure):
    """IK solver statistics returned by the native FFI."""

    _fields_ = [
        ("executed_iterations", c_uint32),
        ("link_steps", c_uint32),
        ("final_distance", c_float),
        ("break_reason", c_uint32),
    ]


class MmdRuntimeFfiAppendConfig(Structure):
    """Append transform configuration passed to the native FFI."""

    _fields_ = [
        ("ratio", c_float),
        ("affect_rotation", c_bool),
        ("affect_translation", c_bool),
    ]


MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_SPHERE = 0
MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_BOX = 1
MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_CAPSULE = 2

MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_STATIC = 0
MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC = 1
MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC_BONE = 2
MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_UNKNOWN = 3

MMD_RUNTIME_PHYSICS_JOINT_KIND_GENERIC_6DOF_SPRING = 0
MMD_RUNTIME_PHYSICS_JOINT_KIND_UNSUPPORTED = 1


class MmdRuntimeFfiPhysicsTickConfig(Structure):
    """Physics tick configuration for the native FFI."""

    _fields_ = [
        ("fixed_substep_seconds", c_float),
        ("max_substeps_per_tick", c_uint32),
    ]


class MmdRuntimeFfiPhysicsRigidbodyDesc(Structure):
    """Canonical rigid body descriptor matching mmd_runtime_ffi_physics_rigidbody_desc_t."""

    _fields_ = [
        ("shape", c_uint32),
        ("shape_size", c_float * 3),
        ("position_xyz", c_float * 3),
        ("rotation_euler_xyz", c_float * 3),
        ("mass", c_float),
        ("linear_damping", c_float),
        ("angular_damping", c_float),
        ("friction", c_float),
        ("restitution", c_float),
        ("collision_group", c_uint16),
        ("collision_mask", c_uint16),
        ("bone_index", c_int32),
        ("mode", c_uint32),
        ("body_from_bone_position_xyz", c_float * 3),
        ("body_from_bone_rotation_xyzw", c_float * 4),
        ("bone_from_body_position_xyz", c_float * 3),
        ("bone_from_body_rotation_xyzw", c_float * 4),
    ]


class MmdRuntimeFfiPhysicsJointDesc(Structure):
    """Canonical joint descriptor matching mmd_runtime_ffi_physics_joint_desc_t."""

    _fields_ = [
        ("kind", c_uint32),
        ("rigidbody_a", c_size_t),
        ("rigidbody_b", c_size_t),
        ("position_xyz", c_float * 3),
        ("rotation_euler_xyz", c_float * 3),
        ("translation_lower_limit_xyz", c_float * 3),
        ("translation_upper_limit_xyz", c_float * 3),
        ("rotation_lower_limit_xyz", c_float * 3),
        ("rotation_upper_limit_xyz", c_float * 3),
        ("spring_translation_factor_xyz", c_float * 3),
        ("spring_rotation_factor_xyz", c_float * 3),
    ]
