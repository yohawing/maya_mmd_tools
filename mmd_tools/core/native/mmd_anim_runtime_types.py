"""
ctypes type definitions shared by the mmd-anim runtime wrapper.

This module keeps C ABI structures and lightweight batch result containers
separate from the runtime loader so other native helpers can import them
without pulling in library discovery or function binding side effects.
"""

from __future__ import annotations

from ctypes import POINTER, Structure, c_bool, c_float, c_int32, c_size_t, c_uint8, c_uint16, c_uint32
from typing import Any, NamedTuple


MMD_RUNTIME_STATUS_OK = 0
MMD_RUNTIME_STATUS_INVALID_INPUT = 1
MMD_RUNTIME_STATUS_UNSUPPORTED = 2
MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL = 3
MMD_RUNTIME_STATUS_ERROR = 4

MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION = 1 << 0
MMD_RUNTIME_FEATURE_PHYSICS_BULLET_NATIVE = 1 << 1
MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS = (
    MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION
    | MMD_RUNTIME_FEATURE_PHYSICS_BULLET_NATIVE
)

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
