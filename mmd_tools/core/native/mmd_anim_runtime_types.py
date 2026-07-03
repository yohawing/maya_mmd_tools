"""
ctypes type definitions shared by the mmd-anim runtime wrapper.

This module keeps C ABI structures and lightweight batch result containers
separate from the runtime loader so other native helpers can import them
without pulling in library discovery or function binding side effects.
"""

from __future__ import annotations

from ctypes import POINTER, Structure, c_bool, c_float, c_int32, c_size_t, c_uint8, c_uint32
from typing import Any, NamedTuple


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
