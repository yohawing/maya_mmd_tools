"""
ctypes type definitions shared by the mmd-anim runtime wrapper.

This module keeps C ABI structures and lightweight batch result containers
separate from the runtime loader so other native helpers can import them
without pulling in library discovery or function binding side effects.
"""

from __future__ import annotations

from ctypes import POINTER, Structure, c_bool, c_float, c_int32, c_size_t, c_uint8, c_uint32
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
