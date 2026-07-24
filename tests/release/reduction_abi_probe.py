"""Probe the mmd-anim dense-pose reduction ABI from the Maya repository.

This script intentionally uses the public C header contract through ``ctypes``
instead of importing a Python wrapper.  It supplies a deterministic two-bone,
31-sample hierarchy and records the reduction report plus every Unity scalar
curve descriptor/key.  The output is evidence for the Maya bake integration
slice; it does not modify the external mmd-anim checkout.

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
EXPECTED_ABI_VERSION = 2
TARGET_DCC_CUBIC = 2
SEMANTIC_TRANSLATION = 0
SEMANTIC_EULER = 1
SEMANTIC_MORPH = 2
AXIS_NONE = 3


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
    """C layout of mmd_runtime_ffi_unity_curve_descriptor_t."""

    _fields_ = [
        ("semantic", ctypes.c_uint32),
        ("target_index", ctypes.c_uint32),
        ("axis", ctypes.c_uint32),
        ("key_count", ctypes.c_size_t),
    ]


class CurveKey(ctypes.Structure):
    """C layout of mmd_runtime_ffi_unity_curve_key_t."""

    _fields_ = [
        ("time_seconds", ctypes.c_float),
        ("value", ctypes.c_float),
        ("in_tangent", ctypes.c_float),
        ("out_tangent", ctypes.c_float),
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
    if abi_version != EXPECTED_ABI_VERSION:
        raise RuntimeError(
            f"mmd-anim runtime ABI mismatch: expected={EXPECTED_ABI_VERSION}, actual={abi_version}"
        )
    library.mmd_runtime_model_create.restype = c_void_p
    library.mmd_runtime_model_create.argtypes = [
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_size_t,
    ]
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
    library.mmd_runtime_reduced_pose_free.argtypes = [c_void_p]
    library.mmd_runtime_reduced_pose_report.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_report.argtypes = [c_void_p, ctypes.POINTER(ReductionReport)]
    library.mmd_runtime_reduced_pose_bone_count.restype = ctypes.c_size_t
    library.mmd_runtime_reduced_pose_bone_count.argtypes = [c_void_p]
    library.mmd_runtime_reduced_pose_morph_count.restype = ctypes.c_size_t
    library.mmd_runtime_reduced_pose_morph_count.argtypes = [c_void_p]
    library.mmd_runtime_reduced_pose_unity_curve_count.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_unity_curve_count.argtypes = [
        c_void_p,
        ctypes.c_float,
        ctypes.c_bool,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    library.mmd_runtime_reduced_pose_unity_curve_descriptor.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_unity_curve_descriptor.argtypes = [
        c_void_p,
        ctypes.c_float,
        ctypes.c_bool,
        ctypes.c_size_t,
        ctypes.POINTER(CurveDescriptor),
    ]
    library.mmd_runtime_reduced_pose_unity_curve_keys.restype = ctypes.c_uint32
    library.mmd_runtime_reduced_pose_unity_curve_keys.argtypes = [
        c_void_p,
        ctypes.c_float,
        ctypes.c_bool,
        ctypes.c_size_t,
        ctypes.POINTER(CurveKey),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    return library


def _curve_keys(
    library: ctypes.CDLL,
    pose: ctypes.c_void_p,
    frames_per_second: float,
    flip_z: bool,
    curve_index: int,
) -> Tuple[int, int, List[CurveKey]]:
    """Retrieve one curve with the ABI's required two-call protocol."""
    required = ctypes.c_size_t()
    first_status = library.mmd_runtime_reduced_pose_unity_curve_keys(
        pose,
        frames_per_second,
        flip_z,
        curve_index,
        None,
        0,
        ctypes.byref(required),
    )
    if first_status != STATUS_BUFFER_TOO_SMALL:
        return first_status, -1, []
    keys = (CurveKey * required.value)()
    second_status = library.mmd_runtime_reduced_pose_unity_curve_keys(
        pose,
        frames_per_second,
        flip_z,
        curve_index,
        keys,
        required.value,
        ctypes.byref(required),
    )
    return first_status, second_status, list(keys) if second_status == STATUS_OK else []


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
    curve_count = ctypes.c_size_t()
    curve_status = library.mmd_runtime_reduced_pose_unity_curve_count(
        reduced,
        fixture["frames_per_second"],
        False,
        ctypes.byref(curve_count),
    )
    descriptors: List[Dict[str, Any]] = []
    all_keys: List[List[CurveKey]] = []
    curve_key_statuses: List[Dict[str, int]] = []
    for curve_index in range(curve_count.value if curve_status == STATUS_OK else 0):
        descriptor = CurveDescriptor()
        descriptor_status = library.mmd_runtime_reduced_pose_unity_curve_descriptor(
            reduced,
            fixture["frames_per_second"],
            False,
            curve_index,
            ctypes.byref(descriptor),
        )
        first_key_status, key_status, keys = _curve_keys(
            library,
            reduced,
            fixture["frames_per_second"],
            False,
            curve_index,
        )
        descriptors.append(
            {
                "index": curve_index,
                "status": descriptor_status,
                "semantic": int(descriptor.semantic),
                "target_index": int(descriptor.target_index),
                "axis": int(descriptor.axis),
                "key_count": int(descriptor.key_count),
            }
        )
        curve_key_statuses.append({"first_call": first_key_status, "second_call": key_status})
        all_keys.append(keys)

    library.mmd_runtime_model_free(model)
    # The reduced handle owns its skeleton snapshot; post-free access is part of
    # the documented ABI and mirrors the real bake ownership boundary.
    post_free_report = ReductionReport()
    post_free_report_status = library.mmd_runtime_reduced_pose_report(
        reduced,
        ctypes.byref(post_free_report),
    )
    post_free_curve_count = ctypes.c_size_t()
    post_free_status = library.mmd_runtime_reduced_pose_unity_curve_count(
        reduced,
        fixture["frames_per_second"],
        False,
        ctypes.byref(post_free_curve_count),
    )
    reduced_bone_count = int(library.mmd_runtime_reduced_pose_bone_count(reduced))
    reduced_morph_count = int(library.mmd_runtime_reduced_pose_morph_count(reduced))

    finite_tangents = all(
        math.isfinite(key.in_tangent) and math.isfinite(key.out_tangent)
        for keys in all_keys
        for key in keys
    )
    nonzero_tangent = any(
        abs(key.in_tangent) > 1.0e-6 or abs(key.out_tangent) > 1.0e-6
        for keys in all_keys
        for key in keys
    )
    expected_first_time = fixture["start_frame"] / fixture["frames_per_second"]
    expected_last_time = (
        fixture["start_frame"] + fixture["frame_step"] * (fixture["frame_count"] - 1)
    ) / fixture["frames_per_second"]
    curve_times_are_frame_based = bool(all_keys) and all(
        keys
        and abs(keys[0].time_seconds - expected_first_time) <= 1.0e-5
        and abs(keys[-1].time_seconds - expected_last_time) <= 1.0e-5
        and all(
            math.isfinite(key.time_seconds)
            and abs(
                (
                    (key.time_seconds * fixture["frames_per_second"])
                    - fixture["start_frame"]
                )
                / fixture["frame_step"]
                - round(
                    (
                        (key.time_seconds * fixture["frames_per_second"])
                        - fixture["start_frame"]
                    )
                    / fixture["frame_step"]
                )
            )
            < 1.0e-4
            for key in keys
        )
        for keys in all_keys
    )
    frame_times = [key.time_seconds for keys in all_keys for key in keys]
    euler_steps = [
        abs(right.value - left.value)
        for descriptor, keys in zip(descriptors, all_keys)
        if descriptor["semantic"] == SEMANTIC_EULER
        for left, right in zip(keys, keys[1:])
    ]
    max_euler_step = max(euler_steps, default=0.0)

    # The root Z translation is curve index 2 (translation XYZ per bone).
    flip_first_status, flip_keys_status, flip_keys = _curve_keys(
        library,
        reduced,
        fixture["frames_per_second"],
        True,
        2,
    )
    normal_keys = all_keys[2] if len(all_keys) > 2 else []
    flip_z_pairs = list(zip(normal_keys, flip_keys))
    flip_z_signs = [
        abs(flipped.time_seconds - normal.time_seconds) <= 1.0e-6
        and abs(flipped.value + normal.value) <= 2.0e-5
        for normal, flipped in flip_z_pairs
    ]
    flip_z_conversion_ok = (
        flip_first_status == STATUS_BUFFER_TOO_SMALL
        and flip_keys_status == STATUS_OK
        and len(flip_keys) == len(normal_keys)
        and bool(flip_z_signs)
        and all(flip_z_signs)
    )

    library.mmd_runtime_reduced_pose_free(reduced)
    tolerance_checks = {
        "local_position": report.max_local_position_error <= tolerances.local_position,
        "local_rotation_radians": report.max_local_rotation_error_radians <= tolerances.local_rotation_radians,
        "world_position": report.max_world_position_error <= tolerances.world_position,
        "world_rotation_radians": report.max_world_rotation_error_radians <= tolerances.world_rotation_radians,
        "morph_weight": report.max_morph_weight_error <= tolerances.morph_weight,
    }
    expected_descriptors = [
        (semantic, bone_index, axis)
        for bone_index in range(2)
        for semantic in (SEMANTIC_TRANSLATION, SEMANTIC_EULER)
        for axis in range(3)
    ]
    expected_descriptors.append((SEMANTIC_MORPH, 0, AXIS_NONE))
    descriptor_order_ok = len(descriptors) == len(expected_descriptors) and all(
        descriptor["status"] == STATUS_OK
        and descriptor["key_count"] == len(keys)
        and (
            descriptor["semantic"],
            descriptor["target_index"],
            descriptor["axis"],
        )
        == expected
        for descriptor, keys, expected in zip(descriptors, all_keys, expected_descriptors)
    )
    checks = {
        "abi_version_ok": abi_version == EXPECTED_ABI_VERSION,
        "dcc_cubic_status_ok": status == STATUS_OK,
        "report_status_ok": report_status == STATUS_OK,
        "curve_count_status_ok": curve_status == STATUS_OK,
        "curve_count_matches_layout": curve_count.value == 2 * 6 + 1,
        "curve_descriptor_order_and_counts": descriptor_order_ok,
        "curve_two_call_status_ok": all(
            item["first_call"] == STATUS_BUFFER_TOO_SMALL
            and item["second_call"] == STATUS_OK
            for item in curve_key_statuses
        ),
        "post_model_free_report_and_curve_access": post_free_report_status == STATUS_OK
        and _report_to_dict(post_free_report) == report_data
        and post_free_status == STATUS_OK
        and post_free_curve_count.value == curve_count.value,
        "report_within_tolerances": all(tolerance_checks.values()),
        "reduced_key_count": report.reduced_bone_key_count < report.source_bone_key_count
        and report.reduced_morph_key_count < report.source_morph_key_count,
        "frame_times_preserve_endpoints_and_sample_grid": curve_times_are_frame_based,
        "hermite_tangents_finite_and_nonzero": finite_tangents and nonzero_tangent,
        "euler_unwrap_continuity": max_euler_step <= 180.0 + 1.0e-3,
        "flip_z_translation_conversion": flip_z_conversion_ok,
    }
    overall = all(checks.values())
    resolved_library = _resolve_library(library_path)
    return {
        "status": "pass" if overall else "fail",
        "library": str(resolved_library),
        "library_sha256": hashlib.sha256(resolved_library.read_bytes()).hexdigest(),
        "abi_version": abi_version,
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
            "curve_count": int(curve_count.value),
            "curve_descriptors": descriptors,
            "curve_key_statuses": curve_key_statuses,
            "first_key_time_seconds": min(frame_times, default=None),
            "last_key_time_seconds": max(frame_times, default=None),
            "expected_first_time_seconds": expected_first_time,
            "expected_last_time_seconds": expected_last_time,
            "max_euler_step_degrees": max_euler_step,
            "flip_z_root_translation_sample": {
                "normal": [key.value for key in normal_keys[:2]],
                "flip_z": [key.value for key in flip_keys[:2]],
            },
        },
        "checks": checks,
        "maya_adaptation": {
            "hierarchy": {
                "status": "available",
                "evidence": "parent_indices are accepted and child world matrices are decomposed relative to the parent.",
            },
            "frame_time": {
                "status": "available",
                "evidence": "start_frame/frame_step are preserved and curve keys are seconds at caller-supplied FPS.",
            },
            "hermite": {
                "status": "available",
                "evidence": "DCC_CUBIC curve keys expose finite in_tangent/out_tangent values; tangents are per-second.",
            },
            "euler_unwrap": {
                "status": "available_for_unity_curves",
                "evidence": "Unity curve output applies Euler filtering and degree conversion; no Maya rotate-order output is exposed.",
            },
            "coordinate_conversion": {
                "status": "partial",
                "evidence": "flip_z converts Unity curve handedness; no Maya-specific axis/unit conversion is in this reduction ABI.",
            },
            "joint_orient_and_bind": {
                "status": "not_mapped_to_maya_channels",
                "evidence": "the model ABI can accept inverse-bind matrices, but Unity curve enumeration exposes no jointOrient, rotateOrder, bind-basis semantic, or Maya local-channel adapter.",
            },
        },
        "abi_verdict": "blocked_for_generic_maya_bake_until_maya_channel_adapter_exists",
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
