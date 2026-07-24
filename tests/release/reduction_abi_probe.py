"""Probe the mmd-anim dense-pose reduction ABI from the Maya repository.

This script intentionally uses the public C header contract through ``ctypes``
instead of importing a Python wrapper.  It supplies a deterministic two-bone,
31-sample hierarchy and records the reduction report plus every runtime-neutral
generic curve descriptor/key.  The output is evidence for the Maya bake
integration slice; it does not modify the external mmd-anim checkout.

Examples:
    python tests/release/reduction_abi_probe.py
    python tests/release/reduction_abi_probe.py --ffi-path build/mmd-anim-unlocked-target/release --out-json build/reports/reduction_abi.json --out-md build/reports/reduction_abi.md
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


STATUS_OK = 0
STATUS_BUFFER_TOO_SMALL = 3
CURRENT_ABI_VERSION = 3
SUPPORTED_ABI_VERSIONS = (2, CURRENT_ABI_VERSION)
# Compatibility alias for downstream probe consumers that imported the old
# constant.  Validation uses SUPPORTED_ABI_VERSIONS, not this singular value.
EXPECTED_ABI_VERSION = CURRENT_ABI_VERSION
TARGET_DCC_CUBIC = 2
FEATURE_REDUCED_POSE_GENERIC_CURVES = 1 << 4
GENERIC_CURVE_BONE_LOCAL = 0
GENERIC_CURVE_MORPH_WEIGHT = 1
GENERIC_VALUE_TRANSLATION = 1 << 0
GENERIC_VALUE_QUATERNION = 1 << 1
GENERIC_VALUE_SCALAR = 1 << 2
GENERIC_ROTATION_BASIS_NONE = 0
GENERIC_ROTATION_BASIS_RUNTIME_QUATERNION = 1
GENERIC_ABI_VERSION = 1
SEMANTIC_TRANSLATION = 0
SEMANTIC_EULER = 1
SEMANTIC_MORPH = 2
AXIS_NONE = 3


def _f32(value: float) -> float:
    """Quantize a probe expectation exactly as the native c_float ABI does."""
    return ctypes.c_float(value).value


class ReductionTolerances(ctypes.Structure):
    """C layout of mmd_runtime_ffi_reduction_tolerances_t."""

    _fields_ = [
        ("local_position", ctypes.c_float),
        ("local_rotation_radians", ctypes.c_float),
        ("world_position", ctypes.c_float),
        ("world_rotation_radians", ctypes.c_float),
        ("morph_weight", ctypes.c_float),
    ]


class ReductionReport(ctypes.Structure):
    """C layout of mmd_runtime_ffi_pose_reduction_report_t."""

    _fields_ = [
        ("source_bone_key_count", ctypes.c_size_t),
        ("reduced_bone_key_count", ctypes.c_size_t),
        ("source_morph_key_count", ctypes.c_size_t),
        ("reduced_morph_key_count", ctypes.c_size_t),
        ("max_local_position_error", ctypes.c_float),
        ("max_local_rotation_error_radians", ctypes.c_float),
        ("max_world_position_error", ctypes.c_float),
        ("max_world_rotation_error_radians", ctypes.c_float),
        ("max_morph_weight_error", ctypes.c_float),
    ]


class CurveDescriptor(ctypes.Structure):
    """Legacy C layout retained for fixture compatibility only.

    ABI 3 removed the Unity curve enumeration functions.  The probe no longer
    binds or calls those symbols; generic curve structures below are the only
    reduction output consumed at runtime.
    """

    _fields_ = [
        ("semantic", ctypes.c_uint32),
        ("target_index", ctypes.c_uint32),
        ("axis", ctypes.c_uint32),
        ("key_count", ctypes.c_size_t),
    ]


class CurveKey(ctypes.Structure):
    """Legacy Unity key layout retained for old fixture imports only."""

    _fields_ = [
        ("time_seconds", ctypes.c_float),
        ("value", ctypes.c_float),
        ("in_tangent", ctypes.c_float),
        ("out_tangent", ctypes.c_float),
    ]


class GenericCurveInfo(ctypes.Structure):
    """C layout of mmd_runtime_ffi_generic_curve_info_t."""

    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("abi_version", ctypes.c_uint32),
        ("reduction_target", ctypes.c_uint32),
        ("coordinate_system", ctypes.c_uint32),
        ("length_unit", ctypes.c_uint32),
        ("angle_unit", ctypes.c_uint32),
        ("time_unit", ctypes.c_uint32),
        ("tangent_unit", ctypes.c_uint32),
        ("model_identity", ctypes.c_uint64),
        ("start_frame", ctypes.c_float),
        ("frame_step", ctypes.c_float),
        ("frame_count", ctypes.c_size_t),
        ("bone_count", ctypes.c_size_t),
        ("morph_count", ctypes.c_size_t),
    ]


class GenericCurveDescriptor(ctypes.Structure):
    """C layout of mmd_runtime_ffi_generic_curve_descriptor_t."""

    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("abi_version", ctypes.c_uint32),
        ("kind", ctypes.c_uint32),
        ("target_index", ctypes.c_uint32),
        ("parent_index", ctypes.c_int32),
        ("value_flags", ctypes.c_uint32),
        ("interpolation", ctypes.c_uint32),
        ("rotation_basis", ctypes.c_uint32),
        ("key_count", ctypes.c_size_t),
    ]


class GenericCurveKey(ctypes.Structure):
    """C layout of mmd_runtime_ffi_generic_curve_key_t."""

    _fields_ = [
        ("sample_index", ctypes.c_size_t),
        ("frame", ctypes.c_float),
        ("translation_xyz", ctypes.c_float * 3),
        ("rotation_xyzw", ctypes.c_float * 4),
        ("scalar", ctypes.c_float),
        ("segment_prev_out_translation_xyz", ctypes.c_float * 3),
        ("segment_current_in_translation_xyz", ctypes.c_float * 3),
        ("segment_from_previous_start_euler_xyz", ctypes.c_float * 3),
        ("segment_from_previous_end_euler_xyz", ctypes.c_float * 3),
        ("segment_prev_out_rotation_xyz", ctypes.c_float * 3),
        ("segment_current_in_rotation_xyz", ctypes.c_float * 3),
        ("segment_prev_out_scalar", ctypes.c_float),
        ("segment_current_in_scalar", ctypes.c_float),
    ]


def _matrix_multiply(left: Sequence[float], right: Sequence[float]) -> List[float]:
    """Multiply two column-major 4x4 matrices."""
    return [
        sum(left[k * 4 + row] * right[column * 4 + k] for k in range(4))
        for column in range(4)
        for row in range(4)
    ]


def _transform(tx: float, ty: float, tz: float, angle_z: float) -> List[float]:
    """Return a rigid column-major transform with a Z-axis rotation."""
    cosine = math.cos(angle_z)
    sine = math.sin(angle_z)
    return [
        cosine,
        sine,
        0.0,
        0.0,
        -sine,
        cosine,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        tx,
        ty,
        tz,
        1.0,
    ]


def make_dense_fixture() -> Dict[str, Any]:
    """Build a deterministic hierarchy containing acceleration and morph motion."""
    frame_count = 31
    world: List[float] = []
    morph: List[float] = []
    for frame in range(frame_count):
        normalized = frame / (frame_count - 1)
        root = _transform(
            0.5 * math.sin(normalized * math.pi),
            0.1 * normalized,
            0.3 * normalized,
            math.radians(-170.0 + 340.0 * normalized),
        )
        child_local = _transform(
            0.15 * math.sin(normalized * math.pi * 2.0),
            1.0 + 0.1 * math.sin(normalized * math.pi),
            0.0,
            math.radians(170.0 - 340.0 * normalized),
        )
        world.extend(root)
        world.extend(_matrix_multiply(root, child_local))
        morph.append(0.5 + 0.5 * math.sin(normalized * math.pi * 2.0))
    return {
        "parents": [-1, 0],
        "rest_positions": [0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        "world_matrices": world,
        "morph_weights": morph,
        "frame_count": frame_count,
        "start_frame": 10.0,
        "frame_step": 1.0,
        "frames_per_second": 30.0,
    }


def _resolve_library(path: Path) -> Path:
    """Resolve a DLL/dylib path or a directory containing the runtime library."""
    if path.is_file():
        return path.resolve()
    if os.name == "nt":
        name = "mmd_runtime_ffi.dll"
    elif sys.platform == "darwin":
        name = "libmmd_runtime_ffi.dylib"
    else:
        name = "libmmd_runtime_ffi.so"
    candidate = path / name
    if candidate.is_file():
        return candidate.resolve()
    raise FileNotFoundError(f"mmd-anim runtime library not found: {candidate}")


def _load_library(path: Path) -> ctypes.CDLL:
    """Load the exact runtime module and bind the reduction symbols."""
    resolved = _resolve_library(path)
    library = ctypes.CDLL(str(resolved))
    c_void_p = ctypes.c_void_p
    library.mmd_runtime_abi_version.restype = ctypes.c_uint32
    abi_version = int(library.mmd_runtime_abi_version())
    if abi_version not in SUPPORTED_ABI_VERSIONS:
        raise RuntimeError(
            "mmd-anim runtime ABI unsupported: "
            f"current={CURRENT_ABI_VERSION}, supported={SUPPORTED_ABI_VERSIONS}, actual={abi_version}"
        )
    library.mmd_runtime_model_create.restype = c_void_p
    library.mmd_runtime_model_create.argtypes = [
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_size_t,
    ]
    library.mmd_runtime_model_free.restype = None
    library.mmd_runtime_model_free.argtypes = [c_void_p]
    library.mmd_runtime_reduced_pose_create_from_dense.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_create_from_dense.argtypes = [
        c_void_p,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_float,
        ctypes.c_float,
        ctypes.c_uint32,
        ReductionTolerances,
        ctypes.POINTER(c_void_p),
    ]
    library.mmd_runtime_reduced_pose_free.restype = None
    library.mmd_runtime_reduced_pose_free.argtypes = [c_void_p]
    library.mmd_runtime_reduced_pose_report.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_report.argtypes = [c_void_p, ctypes.POINTER(ReductionReport)]
    library.mmd_runtime_reduced_pose_bone_count.restype = ctypes.c_size_t
    library.mmd_runtime_reduced_pose_bone_count.argtypes = [c_void_p]
    library.mmd_runtime_reduced_pose_morph_count.restype = ctypes.c_size_t
    library.mmd_runtime_reduced_pose_morph_count.argtypes = [c_void_p]
    library.mmd_runtime_feature_flags.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_generic_curve_info.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_generic_curve_info.argtypes = [
        c_void_p,
        ctypes.POINTER(GenericCurveInfo),
    ]
    library.mmd_runtime_reduced_pose_generic_curve_count.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_generic_curve_count.argtypes = [
        c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    library.mmd_runtime_reduced_pose_generic_curve_descriptor.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_generic_curve_descriptor.argtypes = [
        c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(GenericCurveDescriptor),
    ]
    library.mmd_runtime_reduced_pose_generic_curve_keys.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_generic_curve_keys.argtypes = [
        c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(GenericCurveKey),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    return library


def _generic_curve_keys(
    library: ctypes.CDLL,
    pose: ctypes.c_void_p,
    curve_index: int,
) -> Tuple[int, int, int, List[GenericCurveKey], bool]:
    """Retrieve one runtime-neutral curve using its stride-aware two-call ABI."""
    required = ctypes.c_size_t()
    first_status = library.mmd_runtime_reduced_pose_generic_curve_keys(
        pose,
        curve_index,
        None,
        0,
        ctypes.sizeof(GenericCurveKey),
        ctypes.byref(required),
    )
    if first_status != STATUS_BUFFER_TOO_SMALL:
        return int(first_status), -1, -1, [], False
    # A short buffer must fail closed without partially writing keys.
    short_status = -1
    short_buffer_unchanged = True
    if required.value > 1:
        short_keys = (GenericCurveKey * (required.value - 1))()
        ctypes.memset(ctypes.addressof(short_keys), 0xA5, ctypes.sizeof(short_keys))
        sentinel = ctypes.string_at(ctypes.addressof(short_keys), ctypes.sizeof(short_keys))
        short_required = ctypes.c_size_t()
        short_status = int(
            library.mmd_runtime_reduced_pose_generic_curve_keys(
                pose,
                curve_index,
                short_keys,
                required.value - 1,
                ctypes.sizeof(GenericCurveKey),
                ctypes.byref(short_required),
            )
        )
        short_buffer_unchanged = (
            ctypes.string_at(ctypes.addressof(short_keys), ctypes.sizeof(short_keys)) == sentinel
        )
        if (
            short_status != STATUS_BUFFER_TOO_SMALL
            or short_required.value != required.value
            or not short_buffer_unchanged
        ):
            return int(first_status), short_status, short_status, [], short_buffer_unchanged
    keys = (GenericCurveKey * required.value)()
    second_status = library.mmd_runtime_reduced_pose_generic_curve_keys(
        pose,
        curve_index,
        keys,
        required.value,
        ctypes.sizeof(GenericCurveKey),
        ctypes.byref(required),
    )
    return (
        int(first_status),
        int(second_status),
        short_status,
        list(keys) if second_status == STATUS_OK else [],
        short_buffer_unchanged,
    )


def _report_to_dict(report: ReductionReport) -> Dict[str, Any]:
    """Convert the ABI report struct to JSON-compatible values."""
    return {name: getattr(report, name) for name, _ in report._fields_}


def run_probe(library_path: Path) -> Dict[str, Any]:
    """Run the deterministic reduction probe and return an evidence payload."""
    fixture = make_dense_fixture()
    library = _load_library(library_path)
    abi_version = int(library.mmd_runtime_abi_version())
    parents = (ctypes.c_int32 * len(fixture["parents"]))(*fixture["parents"])
    rest = (ctypes.c_float * len(fixture["rest_positions"]))(\
        *fixture["rest_positions"]
    )
    world = (ctypes.c_float * len(fixture["world_matrices"]))(\
        *fixture["world_matrices"]
    )
    morph = (ctypes.c_float * len(fixture["morph_weights"]))(\
        *fixture["morph_weights"]
    )
    model = library.mmd_runtime_model_create(parents, rest, len(fixture["parents"]))
    if not model:
        raise RuntimeError("mmd_runtime_model_create returned NULL")
    tolerances = ReductionTolerances(1.0e-3, 1.0e-3, 1.0e-3, 1.0e-3, 1.0e-3)
    reduced = ctypes.c_void_p()
    status = library.mmd_runtime_reduced_pose_create_from_dense(
        model,
        0xBA6E0001,
        world,
        len(fixture["world_matrices"]),
        morph,
        len(fixture["morph_weights"]),
        fixture["frame_count"],
        fixture["start_frame"],
        fixture["frame_step"],
        TARGET_DCC_CUBIC,
        tolerances,
        ctypes.byref(reduced),
    )
    if status != STATUS_OK or not reduced:
        library.mmd_runtime_model_free(model)
        raise RuntimeError(f"DCC_CUBIC reduction failed with status={status}")

    report = ReductionReport()
    report_status = library.mmd_runtime_reduced_pose_report(reduced, ctypes.byref(report))
    report_data = _report_to_dict(report)

    library.mmd_runtime_model_free(model)
    # The reduced handle owns its skeleton snapshot; post-free access is part of
    # the documented ABI and mirrors the real bake ownership boundary.
    post_free_report = ReductionReport()
    post_free_report_status = library.mmd_runtime_reduced_pose_report(
        reduced,
        ctypes.byref(post_free_report),
    )
    reduced_bone_count = int(library.mmd_runtime_reduced_pose_bone_count(reduced))
    reduced_morph_count = int(library.mmd_runtime_reduced_pose_morph_count(reduced))

    feature_flags = int(library.mmd_runtime_feature_flags())
    generic_info = GenericCurveInfo()
    generic_info.struct_size = ctypes.sizeof(GenericCurveInfo)
    generic_info_status = int(
        library.mmd_runtime_reduced_pose_generic_curve_info(
            reduced,
            ctypes.byref(generic_info),
        )
    )
    generic_curve_count = ctypes.c_size_t()
    generic_curve_count_status = int(
        library.mmd_runtime_reduced_pose_generic_curve_count(
            reduced,
            ctypes.byref(generic_curve_count),
        )
    )
    generic_descriptors: List[Dict[str, Any]] = []
    generic_all_keys: List[List[GenericCurveKey]] = []
    generic_key_statuses: List[Dict[str, Any]] = []
    for curve_index in range(generic_curve_count.value if generic_curve_count_status == STATUS_OK else 0):
        descriptor = GenericCurveDescriptor()
        descriptor.struct_size = ctypes.sizeof(GenericCurveDescriptor)
        descriptor_status = int(
            library.mmd_runtime_reduced_pose_generic_curve_descriptor(
                reduced,
                curve_index,
                ctypes.byref(descriptor),
            )
        )
        first_status, second_status, short_status, keys, short_buffer_unchanged = _generic_curve_keys(
            library,
            reduced,
            curve_index,
        )
        generic_descriptors.append(
            {
                "index": curve_index,
                "status": descriptor_status,
                "struct_size": int(descriptor.struct_size),
                "abi_version": int(descriptor.abi_version),
                "kind": int(descriptor.kind),
                "target_index": int(descriptor.target_index),
                "parent_index": int(descriptor.parent_index),
                "value_flags": int(descriptor.value_flags),
                "interpolation": int(descriptor.interpolation),
                "rotation_basis": int(descriptor.rotation_basis),
                "key_count": int(descriptor.key_count),
            }
        )
        generic_key_statuses.append(
            {
                "first_call": first_status,
                "short_call": short_status,
                "short_buffer_unchanged": short_buffer_unchanged,
                "second_call": second_status,
            }
        )
        generic_all_keys.append(keys)

    expected_start_frame = _f32(fixture["start_frame"])
    expected_frame_step = _f32(fixture["frame_step"])
    expected_last_frame = _f32(
        expected_start_frame + _f32(expected_frame_step * (fixture["frame_count"] - 1))
    )
    generic_frame_contract_ok = bool(generic_all_keys) and all(
        keys
        and keys[0].frame >= expected_start_frame
        and keys[-1].frame <= expected_last_frame
        and all(
            key.sample_index < fixture["frame_count"]
            and math.isfinite(key.frame)
            and abs(
                key.frame
                - _f32(expected_start_frame + _f32(expected_frame_step * key.sample_index))
            )
            <= 1.0e-4
            for key in keys
        )
        for keys in generic_all_keys
    )
    generic_quaternions_normalized = all(
        abs(sum(value * value for value in key.rotation_xyzw) - 1.0) <= 2.0e-3
        for descriptor, keys in zip(generic_descriptors, generic_all_keys)
        if descriptor["kind"] == GENERIC_CURVE_BONE_LOCAL
        for key in keys
    )
    expected_generic_descriptors = [
        (GENERIC_CURVE_BONE_LOCAL, bone_index, parent_index, GENERIC_VALUE_TRANSLATION | GENERIC_VALUE_QUATERNION, GENERIC_ROTATION_BASIS_RUNTIME_QUATERNION)
        for bone_index, parent_index in enumerate(fixture["parents"])
    ]
    expected_generic_descriptors.append(
        (GENERIC_CURVE_MORPH_WEIGHT, 0, -1, GENERIC_VALUE_SCALAR, GENERIC_ROTATION_BASIS_NONE)
    )
    generic_descriptor_order_ok = len(generic_descriptors) == len(expected_generic_descriptors) and all(
        descriptor["status"] == STATUS_OK
        and descriptor["struct_size"] >= ctypes.sizeof(GenericCurveDescriptor)
        and descriptor["abi_version"] == GENERIC_ABI_VERSION
        and descriptor["key_count"] == len(keys)
        and (
            descriptor["kind"],
            descriptor["target_index"],
            descriptor["parent_index"],
            descriptor["value_flags"],
            descriptor["rotation_basis"],
        )
        == expected
        for descriptor, keys, expected in zip(generic_descriptors, generic_all_keys, expected_generic_descriptors)
    )
    generic_info_ok = (
        generic_info_status == STATUS_OK
        and int(generic_info.struct_size) >= ctypes.sizeof(GenericCurveInfo)
        and int(generic_info.abi_version) == GENERIC_ABI_VERSION
        and int(generic_info.reduction_target) == TARGET_DCC_CUBIC
        and int(generic_info.coordinate_system) == 0
        and int(generic_info.length_unit) == 0
        and int(generic_info.angle_unit) == 0
        and int(generic_info.time_unit) == 0
        and int(generic_info.tangent_unit) == 0
        and int(generic_info.model_identity) == 0xBA6E0001
        and abs(float(generic_info.start_frame) - expected_start_frame) <= 1.0e-4
        and abs(float(generic_info.frame_step) - expected_frame_step) <= 1.0e-4
        and int(generic_info.frame_count) == fixture["frame_count"]
        and int(generic_info.bone_count) == 2
        and int(generic_info.morph_count) == 1
    )

    library.mmd_runtime_reduced_pose_free(reduced)
    tolerance_checks = {
        "local_position": report.max_local_position_error <= tolerances.local_position,
        "local_rotation_radians": report.max_local_rotation_error_radians <= tolerances.local_rotation_radians,
        "world_position": report.max_world_position_error <= tolerances.world_position,
        "world_rotation_radians": report.max_world_rotation_error_radians <= tolerances.world_rotation_radians,
        "morph_weight": report.max_morph_weight_error <= tolerances.morph_weight,
    }
    checks = {
        "abi_version_supported": abi_version in SUPPORTED_ABI_VERSIONS,
        "abi_version_current_or_compat": abi_version in (CURRENT_ABI_VERSION, 2),
        "generic_feature_bit_ok": bool(feature_flags & FEATURE_REDUCED_POSE_GENERIC_CURVES),
        "dcc_cubic_status_ok": status == STATUS_OK,
        "report_status_ok": report_status == STATUS_OK,
        "post_model_free_report": post_free_report_status == STATUS_OK
        and _report_to_dict(post_free_report) == report_data
        ,
        "report_within_tolerances": all(tolerance_checks.values()),
        "reduced_key_count": report.reduced_bone_key_count < report.source_bone_key_count
        and report.reduced_morph_key_count < report.source_morph_key_count,
        "generic_info_contract_ok": generic_info_ok,
        "generic_curve_count_status_ok": generic_curve_count_status == STATUS_OK
        and generic_curve_count.value == 3,
        "generic_curve_descriptor_order_and_counts": generic_descriptor_order_ok,
        "generic_curve_two_call_status_ok": all(
            item["first_call"] == STATUS_BUFFER_TOO_SMALL
            and item["second_call"] == STATUS_OK
            and (item["short_call"] in (-1, STATUS_BUFFER_TOO_SMALL))
            and item["short_buffer_unchanged"]
            for item in generic_key_statuses
        ),
        "generic_curve_frame_contract": generic_frame_contract_ok,
        "generic_curve_quaternions_normalized": generic_quaternions_normalized,
    }
    overall = all(checks.values())
    resolved_library = _resolve_library(library_path)
    return {
        "status": "pass" if overall else "fail",
        "library": str(resolved_library),
        "library_sha256": hashlib.sha256(resolved_library.read_bytes()).hexdigest(),
        "abi_version": abi_version,
        "abi_compatibility": {
            "actual": abi_version,
            "current": CURRENT_ABI_VERSION,
            "supported": list(SUPPORTED_ABI_VERSIONS),
            "is_current": abi_version == CURRENT_ABI_VERSION,
            "is_compatible": abi_version in SUPPORTED_ABI_VERSIONS,
        },
        "fixture": {
            "bone_count": 2,
            "morph_count": 1,
            "frame_count": fixture["frame_count"],
            "start_frame": fixture["start_frame"],
            "frame_step": fixture["frame_step"],
            "frames_per_second": fixture["frames_per_second"],
        },
        "reduction": {
            "target": "DCC_CUBIC",
            "tolerances": {name: getattr(tolerances, name) for name, _ in tolerances._fields_},
            "report": report_data,
            "bone_count": reduced_bone_count,
            "morph_count": reduced_morph_count,
            "generic_curve_info": {
                name: getattr(generic_info, name) for name, _ in generic_info._fields_
            },
            "generic_curve_count": int(generic_curve_count.value),
            "generic_curve_descriptors": generic_descriptors,
            "generic_curve_key_statuses": generic_key_statuses,
        },
        "checks": checks,
        "maya_adaptation": {
            "hierarchy": {
                "status": "available",
                "evidence": "parent_indices are accepted and child world matrices are decomposed relative to the parent.",
            },
            "frame_time": {
                "status": "available",
                "evidence": "start_frame/frame_step are preserved as source sample-frame coordinates.",
            },
            "hermite": {
                "status": "available",
                "evidence": "Generic curve keys expose finite DCC cubic segment tangent fields in runtime units.",
            },
            "euler_unwrap": {
                "status": "diagnostic_only",
                "evidence": "Generic curve Euler segment fields remain runtime diagnostics; Maya channels are adapted by the host.",
            },
            "coordinate_conversion": {
                "status": "runtime_neutral",
                "evidence": "Generic curves preserve runtime-native model units and radians for the Maya adapter.",
            },
            "joint_orient_and_bind": {
                "status": "host_adapter_required",
                "evidence": "The generic ABI exposes parent indices and local quaternions; Maya jointOrient/rotateOrder conversion remains host-owned.",
            },
        },
        "abi_verdict": "generic_runtime_curves_available_for_maya_adapter",
    }


def write_reports(payload: Dict[str, Any], out_json: Path | None, out_md: Path | None) -> None:
    """Write optional machine-readable and concise Markdown evidence."""
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if out_md:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        checks = payload.get("checks", {})
        lines = [
            "# mmd-anim Reduction ABI Probe",
            "",
            f"- Status: `{payload.get('status')}`",
            f"- ABI version: `{payload.get('abi_version')}`",
            f"- ABI compatibility: current=`{payload.get('abi_compatibility', {}).get('current')}`, supported=`{payload.get('abi_compatibility', {}).get('supported')}`",
            f"- Verdict: `{payload.get('abi_verdict')}`",
            "",
            "| Check | Result |",
            "| --- | --- |",
        ]
        lines.extend(f"| `{name}` | `{value}` |" for name, value in checks.items())
        lines.extend(
            [
                "",
                "## Maya adaptation boundary",
                "",
            ]
        )
        for name, item in payload.get("maya_adaptation", {}).items():
            lines.append(f"- **{name}**: `{item['status']}` — {item['evidence']}")
        out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    """Run the probe command and return a process status."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ffi-path",
        type=Path,
        default=Path("external/mmd-anim/target/release"),
        help="mmd_runtime_ffi DLL/dylib or containing directory",
    )
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-md", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = run_probe(args.ffi_path)
    except Exception as exc:  # pragma: no cover - exercised by the nox gate
        payload = {
            "status": "fail",
            "error": str(exc),
            "library": str(args.ffi_path),
            "abi_verdict": "probe_unavailable",
        }
    write_reports(payload, args.out_json, args.out_md)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
