"""Local GoldenOracle camera-motion verifier for Maya VMD camera import.

This runner is intentionally local-only: the GoldenOracle manifest and oracle
JSONL files live outside this repository and may reference private local assets.
It compares Maya-imported VMD camera channels against GoldenOracle camera dumps.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.standalone


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path("F:/Develop/MMDDev/GoldenOracle/manifests/camera_motion.json")
DEFAULT_OUT = ROOT / "build" / "local-camera-motion-oracle" / "report.json"
DEFAULT_CURRENT_EPSILON = 3.0e-2
ATTR_MMD_CAMERA_TARGET_NODE = "mmd_camera_target_node"
GENERATED_CAMERA_CASE_MARKERS = ("generated", "interpolation-isolated")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--case", dest="case_name", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mode", choices=["sparse", "bake", "both"], default="sparse")
    parser.add_argument("--max-current-frames", type=int, default=240)
    parser.add_argument("--all-frames", action="store_true")
    parser.add_argument("--epsilon", type=float, default=None)
    parser.add_argument(
        "--current-epsilon",
        type=float,
        default=None,
        help="Tolerance for camera.current playback checks; defaults to mmd-anim parity tolerance.",
    )
    parser.add_argument(
        "--current-report-only",
        action="store_true",
        help="Report camera.current mismatches without failing the run.",
    )
    parser.add_argument(
        "--current-frame-zero",
        choices=["auto", "include", "skip"],
        default="auto",
        help=(
            "camera.current frame 0 policy. auto skips frame 0 for non-generated "
            "GoldenOracle dumps and keeps it for generated synthetic cases."
        ),
    )
    parser.add_argument(
        "--parity-vmd",
        default=None,
        help="Run oracle-free Bake-vs-Rig camera.current parity for this VMD instead of manifest cases.",
    )
    parser.add_argument("--parity-case-name", default="camera-bake-rig-parity")
    parser.add_argument("--parity-epsilon", type=float, default=DEFAULT_CURRENT_EPSILON)
    parser.add_argument("--parity-interpolation-eye-max", type=float, default=None)
    parser.add_argument("--parity-interpolation-forward-max-deg", type=float, default=None)
    parser.add_argument("--parity-interpolation-up-max-deg", type=float, default=None)
    parser.add_argument("--parity-interpolation-rotation-max-deg", type=float, default=None)
    parser.add_argument(
        "--parity-interpolation-report-only",
        action="store_true",
        help="Report sparse-vs-bake interpolation drift without failing the parity case.",
    )
    parser.add_argument(
        "--parity-current-report-only",
        action="store_true",
        help="For Bake-vs-Rig parity mode, report playback deltas without failing when sparse Rig is editability-first.",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    return parser.parse_args()


def _initialize(repo_root: Path) -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    root = str(repo_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_path(manifest_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return (manifest_dir / path).resolve()


def _load_manifest(path: Path) -> tuple[Path, list[dict[str, Any]]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    defaults = manifest.get("defaults") or {}
    cases = []
    for case in manifest.get("cases") or []:
        merged = _deep_merge(defaults, case)
        cases.append(merged)
    return path.parent, cases


def _iter_oracle_records(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _write_json_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")


def _select_records(case: dict[str, Any], records: list[dict[str, Any]], max_frames: int, all_frames: bool) -> list[dict[str, Any]]:
    if all_frames:
        return records
    explicit_frames = case.get("frames")
    if explicit_frames:
        wanted = {int(frame) for frame in explicit_frames}
        return [record for record in records if int(record.get("frame", -1)) in wanted]
    if max_frames <= 0:
        return records
    if len(records) <= max_frames:
        return records
    step = max(1, math.floor(len(records) / max_frames))
    selected = records[::step][:max_frames]
    if records[-1] not in selected:
        selected.append(records[-1])
    return selected


def _select_frame_numbers(frame_numbers: list[int], max_frames: int, all_frames: bool) -> list[int]:
    keyframes = sorted({int(frame) for frame in frame_numbers})
    if not keyframes:
        return []
    start = keyframes[0]
    end = keyframes[-1]
    if all_frames or max_frames <= 0:
        return list(range(start, end + 1))
    span = end - start + 1
    if span <= max_frames:
        return list(range(start, end + 1))
    if max_frames <= 1:
        return [start, end] if start != end else [start]
    step = (span - 1) / float(max_frames - 1)
    return sorted({int(round(start + index * step)) for index in range(max_frames)} | {start, end})


def _camera_keyframes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for record in records:
        keyframes = (((record.get("camera") or {}).get("keyframes")) or [])
        if keyframes:
            return keyframes
    return []


def _import_camera(vmd_path: Path, *, bake_mode: bool) -> str:
    from mmd_tools.core.vmd_data import VmdData
    from mmd_tools.converters.vmd_converter import VmdConverter

    cmds.file(new=True, force=True)
    vmd_data = VmdData().parse_file(str(vmd_path))
    vmd_bytes = vmd_path.read_bytes()
    converter = VmdConverter()
    converter.use_animation_layers = False
    if not converter.convert(
        vmd_data,
        bake_mode=bake_mode,
        vmd_bytes=vmd_bytes,
        scene_animation_only=True,
    ):
        raise RuntimeError(f"VMD camera import failed: {vmd_path}")
    camera = converter._get_or_create_camera()
    if not cmds.objExists(camera):
        raise RuntimeError("MMD camera was not created")
    return camera


def _plug_float(node: str, attr: str, frame: float) -> float:
    value = cmds.getAttr(f"{node}.{attr}", time=frame)
    if isinstance(value, (list, tuple)):
        if len(value) == 1 and isinstance(value[0], (list, tuple)):
            value = value[0][0]
        else:
            value = value[0]
    return float(value or 0.0)


def _current_plug_float(node: str, attr: str) -> float:
    value = cmds.getAttr(f"{node}.{attr}")
    if isinstance(value, (list, tuple)):
        if len(value) == 1 and isinstance(value[0], (list, tuple)):
            value = value[0][0]
        else:
            value = value[0]
    return float(value or 0.0)


def _normalize_vector(vector: om.MVector) -> list[float]:
    if vector.length() <= 1.0e-12:
        return [0.0, 0.0, 0.0]
    vector.normalize()
    return [float(vector.x), float(vector.y), float(vector.z)]


def _maya_point_to_mmd(point: om.MVector) -> list[float]:
    return [float(point.x), float(point.y), -float(point.z)]


def _mmd_point_to_maya(point: list[float] | tuple[float, float, float]) -> list[float]:
    return [float(point[0]), float(point[1]), -float(point[2])]


def _expected_maya_forward_up(rotation: list[float] | tuple[float, float, float]) -> tuple[list[float], list[float]]:
    matrix = om.MEulerRotation(
        -float(rotation[0]),
        -float(rotation[1]),
        -float(rotation[2]),
        om.MEulerRotation.kZXY,
    ).asMatrix()
    look = om.MVector(0.0, 0.0, 1.0) * matrix
    up = om.MVector(0.0, 1.0, 0.0) * matrix
    return _normalize_vector(om.MVector(look.x, look.y, -look.z)), _normalize_vector(om.MVector(up.x, up.y, -up.z))


def _expected_camera_transform_state(expected: dict[str, Any]) -> dict[str, list[float]]:
    forward, up = _expected_maya_forward_up(expected["rotation"])
    return {
        "transformPosition": list(expected["position"]),
        "transformForward": forward,
        "transformUp": up,
    }


def _camera_shape(camera: str) -> str | None:
    shapes = cmds.listRelatives(camera, shapes=True, type="camera") or []
    return shapes[0] if shapes else None


def _camera_shape_vertical_fov(camera: str, frame: float) -> float:
    shape = _camera_shape(camera)
    if not shape:
        return _plug_float(camera, "mmd_camera_viewing_angle", frame)
    focal_length = _current_plug_float(shape, "focalLength")
    if abs(focal_length) <= 1e-9:
        return 0.0
    aperture_inch = _current_plug_float(shape, "verticalFilmAperture")
    aperture_mm = aperture_inch * 25.4
    return math.degrees(2.0 * math.atan(aperture_mm / (2.0 * focal_length)))


def _camera_shape_perspective(camera: str, frame: float) -> bool:
    shape = _camera_shape(camera)
    if shape and cmds.attributeQuery("orthographic", node=shape, exists=True):
        return not bool(round(_current_plug_float(shape, "orthographic")))
    if cmds.attributeQuery("mmd_camera_perspective", node=camera, exists=True):
        return bool(round(_plug_float(camera, "mmd_camera_perspective", frame))) == 0
    return True


def _camera_target_node(camera: str) -> str | None:
    if not cmds.attributeQuery(ATTR_MMD_CAMERA_TARGET_NODE, node=camera, exists=True):
        return None
    targets = cmds.listConnections(
        f"{camera}.{ATTR_MMD_CAMERA_TARGET_NODE}",
        source=True,
        destination=False,
    ) or []
    return targets[0] if targets else None


def _evaluate_camera_expression(camera: str) -> None:
    from mmd_tools.converters.vmd_camera_animation import (
        MMD_CAMERA_EXPR_ID_ATTR,
        evaluate_mmd_camera_expression,
    )

    for expression in (
        cmds.listConnections(
            f"{camera}.message",
            source=False,
            destination=True,
            type="expression",
        )
        or []
    ):
        if not cmds.attributeQuery(MMD_CAMERA_EXPR_ID_ATTR, node=expression, exists=True):
            continue
        expression_id = cmds.getAttr(f"{expression}.{MMD_CAMERA_EXPR_ID_ATTR}")
        if expression_id:
            evaluate_mmd_camera_expression(str(expression_id))


def _signed_camera_distance(eye: om.MVector, target: om.MVector, forward: om.MVector) -> float:
    """Recover MMD signed distance from the camera eye, target, and world forward."""
    target_from_eye = target - eye
    distance = target_from_eye.length()
    if distance <= 1.0e-12:
        return 0.0
    forward_normal = om.MVector(forward.x, forward.y, forward.z)
    if forward_normal.length() <= 1.0e-12:
        return -distance
    forward_normal.normalize()
    return -distance if target_from_eye * forward_normal >= 0.0 else distance


def _maya_camera_state(camera: str, frame: float) -> dict[str, Any]:
    from mmd_tools.converters.vmd_camera_animation import mmd_camera_rotation_from_maya_forward_up

    cmds.currentTime(frame, edit=True)
    _evaluate_camera_expression(camera)
    world_position = cmds.xform(camera, query=True, worldSpace=True, translation=True)
    matrix_values = [float(value) for value in cmds.getAttr(f"{camera}.worldMatrix[0]")]
    matrix = om.MMatrix(matrix_values)
    forward = om.MVector(0.0, 0.0, -1.0) * matrix
    up = om.MVector(0.0, 1.0, 0.0) * matrix
    forward_list = _normalize_vector(forward)
    up_list = _normalize_vector(up)
    target_node = _camera_target_node(camera)
    if target_node:
        eye = om.MVector(*world_position)
        maya_target = om.MVector(*cmds.xform(target_node, query=True, worldSpace=True, translation=True))
        position = _maya_point_to_mmd(maya_target)
        distance = _signed_camera_distance(eye, maya_target, forward)
    else:
        distance = _plug_float(camera, "mmd_camera_distance", frame)
        position = [
            _plug_float(camera, "mmd_camera_target_x", frame),
            _plug_float(camera, "mmd_camera_target_y", frame),
            _plug_float(camera, "mmd_camera_target_z", frame),
        ]
        maya_target = om.MVector(*_mmd_point_to_maya(position))
    rotation = list(mmd_camera_rotation_from_maya_forward_up(tuple(forward_list), tuple(up_list)))
    state = {
        "distance": distance,
        "position": position,
        "rotation": rotation,
        "fov": _camera_shape_vertical_fov(camera, frame),
        "perspective": _camera_shape_perspective(camera, frame),
        "transformPosition": _maya_point_to_mmd(maya_target),
        "transformForward": forward_list,
        "transformUp": up_list,
        "worldMatrix": matrix_values,
    }
    shape = _camera_shape(camera)
    if shape:
        state.update(
            {
                "shapeFocalLength": _current_plug_float(shape, "focalLength"),
                "shapeOrthographicWidth": _current_plug_float(shape, "orthographicWidth"),
                "shapeOrthographic": bool(round(_current_plug_float(shape, "orthographic"))),
            }
        )
    return state


def _diff_scalar(actual: float, expected: float) -> float:
    return abs(float(actual) - float(expected))


def _diff_vector(actual: list[float], expected: list[float]) -> float:
    return max(_diff_scalar(left, right) for left, right in zip(actual, expected))


def _diff_angle(actual: float, expected: float) -> float:
    delta = (float(actual) - float(expected) + math.pi) % (2.0 * math.pi) - math.pi
    return abs(delta)


def _diff_rotation_vector(actual: list[float], expected: list[float]) -> float:
    return max(_diff_angle(left, right) for left, right in zip(actual, expected))


def _vector_euclidean(actual: list[float], expected: list[float]) -> float:
    return math.sqrt(sum((float(left) - float(right)) ** 2 for left, right in zip(actual, expected)))


def _vector_angle_degrees(actual: list[float], expected: list[float]) -> float:
    left = [float(value) for value in actual]
    right = [float(value) for value in expected]
    left_length = math.sqrt(sum(value * value for value in left))
    right_length = math.sqrt(sum(value * value for value in right))
    if left_length <= 1.0e-12 or right_length <= 1.0e-12:
        return 0.0
    dot = sum(l_value * r_value for l_value, r_value in zip(left, right)) / (left_length * right_length)
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(float(value) for value in values)
    index = (len(sorted_values) - 1) * float(percentile) / 100.0
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[int(index)]
    return sorted_values[lower] * (upper - index) + sorted_values[upper] * (index - lower)


def _summarize_drift_values(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [float(row[field]) for row in rows]
    worst = max(rows, key=lambda row: float(row[field])) if rows else None
    return {
        "mean": sum(values) / len(values) if values else 0.0,
        "p50": _percentile(values, 50.0),
        "p95": _percentile(values, 95.0),
        "p99": _percentile(values, 99.0),
        "max": max(values) if values else 0.0,
        "maxFrame": int(worst["frame"]) if worst else None,
    }


def _camera_eye_from_state(state: dict[str, Any]) -> list[float]:
    matrix = state.get("worldMatrix") or []
    if len(matrix) >= 15:
        return [float(matrix[12]), float(matrix[13]), float(matrix[14])]
    return [float(value) for value in state.get("transformPosition", [0.0, 0.0, 0.0])]


def _parity_drift_rows(
    frames: list[int],
    keyframes: set[int],
    sparse_states: dict[int, dict[str, Any]],
    bake_states: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for frame in frames:
        sparse = sparse_states[frame]
        bake = bake_states[frame]
        rows.append(
            {
                "frame": int(frame),
                "isKey": int(frame) in keyframes,
                "targetMaxAbs": _diff_vector(list(sparse["position"]), list(bake["position"])),
                "distanceAbs": _diff_scalar(float(sparse["distance"]), float(bake["distance"])),
                "rotationMaxDeg": math.degrees(_diff_rotation_vector(list(sparse["rotation"]), list(bake["rotation"]))),
                "fovAbsDeg": _diff_scalar(float(sparse["fov"]), float(bake["fov"])),
                "eyeEuclidean": _vector_euclidean(_camera_eye_from_state(sparse), _camera_eye_from_state(bake)),
                "eyeMaxAbs": _diff_vector(_camera_eye_from_state(sparse), _camera_eye_from_state(bake)),
                "forwardAngleDeg": _vector_angle_degrees(list(sparse["transformForward"]), list(bake["transformForward"])),
                "upAngleDeg": _vector_angle_degrees(list(sparse["transformUp"]), list(bake["transformUp"])),
                "worldMatrixMaxAbs": _diff_vector(list(sparse["worldMatrix"]), list(bake["worldMatrix"])),
            }
        )
    return rows


def _summarize_parity_drift(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "targetMaxAbs",
        "distanceAbs",
        "rotationMaxDeg",
        "fovAbsDeg",
        "eyeEuclidean",
        "eyeMaxAbs",
        "forwardAngleDeg",
        "upAngleDeg",
        "worldMatrixMaxAbs",
    )
    inbetween = [row for row in rows if not row["isKey"]]
    keyframes = [row for row in rows if row["isKey"]]
    return {
        "frames": len(rows),
        "keyframes": len(keyframes),
        "inbetweenFrames": len(inbetween),
        "all": {field: _summarize_drift_values(rows, field) for field in fields},
        "inbetweenOnly": {field: _summarize_drift_values(inbetween, field) for field in fields},
        "keyframesOnly": {field: _summarize_drift_values(keyframes, field) for field in fields},
        "topEyeFrames": sorted(rows, key=lambda row: float(row["eyeEuclidean"]), reverse=True)[:10],
        "topForwardFrames": sorted(rows, key=lambda row: float(row["forwardAngleDeg"]), reverse=True)[:10],
    }


def _interpolation_drift_mismatches(summary: dict[str, Any], thresholds: dict[str, float | None]) -> list[dict[str, Any]]:
    fields = {
        "eyeEuclidean": "parity-interpolation-eye-max",
        "forwardAngleDeg": "parity-interpolation-forward-max-deg",
        "upAngleDeg": "parity-interpolation-up-max-deg",
        "rotationMaxDeg": "parity-interpolation-rotation-max-deg",
    }
    mismatches = []
    inbetween = summary.get("inbetweenOnly") or {}
    for field, threshold_name in fields.items():
        threshold = thresholds.get(threshold_name)
        if threshold is None:
            continue
        stats = inbetween.get(field) or {}
        actual = float(stats.get("max", 0.0))
        if actual > float(threshold):
            mismatches.append(
                {
                    "field": field,
                    "actual": actual,
                    "expected": f"<= {float(threshold)}",
                    "maxFrame": stats.get("maxFrame"),
                }
            )
    return mismatches


def _check_field(
    mismatches: list[dict[str, Any]],
    *,
    frame: int,
    mode: str,
    field: str,
    actual: Any,
    expected: Any,
    epsilon: float,
) -> float:
    if isinstance(expected, list) and field.endswith(".rotation"):
        delta = _diff_rotation_vector(list(actual), list(expected))
    elif isinstance(expected, list):
        delta = _diff_vector(list(actual), list(expected))
    elif isinstance(expected, bool):
        delta = 0.0 if bool(actual) == bool(expected) else 1.0
    else:
        delta = _diff_scalar(float(actual), float(expected))
    if delta > epsilon:
        mismatches.append(
            {
                "mode": mode,
                "frame": frame,
                "field": field,
                "actual": actual,
                "expected": expected,
                "delta": delta,
            }
        )
    return delta


def _is_generated_camera_case(case: dict[str, Any]) -> bool:
    name = str(case.get("name") or "").lower()
    return any(marker in name for marker in GENERATED_CAMERA_CASE_MARKERS)


def _skip_current_frame_zero(case: dict[str, Any], policy: str) -> bool:
    if policy == "include":
        return False
    if policy == "skip":
        return True
    return not _is_generated_camera_case(case)


def _compare_current(
    camera: str,
    records: list[dict[str, Any]],
    *,
    mode: str,
    epsilon: float,
    gate: bool,
    skip_frame_zero: bool,
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    skipped_frames: list[dict[str, Any]] = []
    worst = 0.0
    compared = 0
    compared_frames = 0
    for record in records:
        frame = int(record["frame"])
        expected = ((record.get("camera") or {}).get("current")) or {}
        if not expected:
            continue
        if skip_frame_zero and frame == 0:
            skipped_frames.append(
                {
                    "frame": frame,
                    "reason": (
                        "frame 0 camera.current is a keepInitialFrameZero/current-view dump, "
                        "not VMD raw playback"
                    ),
                }
            )
            continue
        compared_frames += 1
        actual = _maya_camera_state(camera, frame)
        for field in ("distance", "position", "rotation", "fov", "perspective"):
            if field not in expected:
                continue
            worst = max(
                worst,
                _check_field(
                    mismatches,
                    frame=frame,
                    mode=mode,
                    field=f"camera.current.{field}",
                    actual=actual[field],
                    expected=expected[field],
                    epsilon=epsilon,
                ),
            )
            compared += 1
        for field, expected_value in _expected_camera_transform_state(expected).items():
            worst = max(
                worst,
                _check_field(
                    mismatches,
                    frame=frame,
                    mode=mode,
                    field=f"camera.current.{field}",
                    actual=actual[field],
                    expected=expected_value,
                    epsilon=epsilon,
                ),
            )
            compared += 1
    return {
        "kind": "playbackCurrent",
        "compared": compared,
        "comparedFrames": compared_frames,
        "skipped": len(skipped_frames),
        "skippedFrames": skipped_frames,
        "worstDelta": worst,
        "mismatches": mismatches[:50],
        "gate": gate,
    }


def _compare_keyframes(camera: str, keyframes: list[dict[str, Any]], *, mode: str, epsilon: float) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    worst = 0.0
    compared = 0
    for expected in keyframes:
        frame = int(expected["frame"])
        actual = _maya_camera_state(camera, frame)
        expected_values = {
            "distance": expected["distance"],
            "position": expected["position"],
            "rotation": expected["rotation"],
            "fov": float(expected["fov"]),
            "perspective": bool(expected["perspective"]),
        }
        expected_values.update(_expected_camera_transform_state(expected))
        for field, expected_value in expected_values.items():
            worst = max(
                worst,
                _check_field(
                    mismatches,
                    frame=frame,
                    mode=mode,
                    field=f"camera.keyframes.{field}",
                    actual=actual[field],
                    expected=expected_value,
                    epsilon=epsilon,
                ),
            )
            compared += 1
    return {
        "kind": "rawKeyframes",
        "compared": compared,
        "worstDelta": worst,
        "mismatches": mismatches[:50],
        "gate": True,
    }


def _camera_frame_numbers_from_vmd(vmd_path: Path) -> list[int]:
    from mmd_tools.core.vmd_data import VmdData

    vmd_data = VmdData().parse_file(str(vmd_path))
    return [int(frame.frame_number) for frame in getattr(vmd_data, "camera_frames", [])]


def _check_sparse_camera_rig_structure(camera: str) -> dict[str, Any]:
    raw_attrs = (
        "mmd_camera_distance",
        "mmd_camera_viewing_angle",
        "mmd_camera_perspective",
        "mmd_camera_target_x",
        "mmd_camera_target_y",
        "mmd_camera_target_z",
        "mmd_camera_rotation_x",
        "mmd_camera_rotation_y",
        "mmd_camera_rotation_z",
        "mmd_camera_motion_scale",
    )
    mismatches: list[dict[str, Any]] = []
    present_raw_attrs = [attr for attr in raw_attrs if cmds.attributeQuery(attr, node=camera, exists=True)]
    if present_raw_attrs:
        mismatches.append({"field": "customAnimationAttrs", "actual": present_raw_attrs, "expected": []})
    expressions = cmds.listConnections(f"{camera}.message", source=False, destination=True, type="expression") or []
    if expressions:
        mismatches.append({"field": "expressions", "actual": expressions, "expected": []})
    target = _camera_target_node(camera)
    if not target:
        mismatches.append({"field": "target", "actual": None, "expected": "connected target locator"})
    elif not cmds.keyframe(f"{target}.translateX", query=True):
        mismatches.append({"field": "target.translateX.keys", "actual": 0, "expected": ">0"})
    elif (cmds.listRelatives(camera, parent=True) or [None])[0] != target:
        mismatches.append({"field": "camera.parent", "actual": (cmds.listRelatives(camera, parent=True) or [None])[0], "expected": target})
    if target and not cmds.keyframe(f"{target}.rotateX", query=True):
        mismatches.append({"field": "target.rotateX.keys", "actual": 0, "expected": ">0"})
    if not cmds.keyframe(f"{camera}.translateZ", query=True):
        mismatches.append({"field": "camera.translateZ.keys", "actual": 0, "expected": ">0"})
    if not cmds.keyframe(f"{camera}.rotateZ", query=True):
        mismatches.append({"field": "camera.rotateZ.keys", "actual": 0, "expected": ">0"})
    aim_constraints = cmds.listConnections(f"{camera}.rotateX", source=True, destination=False, type="aimConstraint") or []
    if aim_constraints:
        mismatches.append({"field": "camera.aimConstraint", "actual": aim_constraints, "expected": []})
    shape = _camera_shape(camera)
    if shape and not cmds.keyframe(f"{shape}.focalLength", query=True):
        mismatches.append({"field": "cameraShape.focalLength.keys", "actual": 0, "expected": ">0"})
    return {
        "kind": "sparseRigEditability",
        "gate": True,
        "mismatches": mismatches,
    }


def _run_parity_vmd(vmd_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": args.parity_case_name,
        "vmd": str(vmd_path),
        "epsilon": float(args.parity_epsilon),
        "status": "pending",
        "checks": {},
    }
    if not vmd_path.exists():
        result.update({"status": "skipped", "reason": "camera VMD is missing"})
        return result

    frame_numbers = _camera_frame_numbers_from_vmd(vmd_path)
    frames = _select_frame_numbers(frame_numbers, args.max_current_frames, args.all_frames)
    result["vmdCameraFrames"] = len(frame_numbers)
    result["selectedCurrentFrames"] = len(frames)
    if not frames:
        result.update({"status": "skipped", "reason": "camera VMD has no camera frames"})
        return result

    sparse_camera = _import_camera(vmd_path, bake_mode=False)
    rig_structure = _check_sparse_camera_rig_structure(sparse_camera)
    sparse_states = {frame: _maya_camera_state(sparse_camera, frame) for frame in frames}
    bake_camera = _import_camera(vmd_path, bake_mode=True)
    bake_states = {frame: _maya_camera_state(bake_camera, frame) for frame in frames}
    drift_rows = _parity_drift_rows(frames, set(frame_numbers), sparse_states, bake_states)
    drift_summary = _summarize_parity_drift(drift_rows)
    drift_mismatches = _interpolation_drift_mismatches(
        drift_summary,
        {
            "parity-interpolation-eye-max": args.parity_interpolation_eye_max,
            "parity-interpolation-forward-max-deg": args.parity_interpolation_forward_max_deg,
            "parity-interpolation-up-max-deg": args.parity_interpolation_up_max_deg,
            "parity-interpolation-rotation-max-deg": args.parity_interpolation_rotation_max_deg,
        },
    )

    mismatches: list[dict[str, Any]] = []
    worst = 0.0
    compared = 0
    fields = (
        "distance",
        "position",
        "rotation",
        "fov",
        "perspective",
        "transformPosition",
        "transformForward",
        "transformUp",
        "worldMatrix",
        "shapeFocalLength",
        "shapeOrthographicWidth",
        "shapeOrthographic",
    )
    for frame in frames:
        sparse = sparse_states[frame]
        bake = bake_states[frame]
        for field in fields:
            if field not in sparse or field not in bake:
                continue
            worst = max(
                worst,
                _check_field(
                    mismatches,
                    frame=frame,
                    mode="bake-vs-rig",
                    field=f"camera.current.{field}",
                    actual=sparse[field],
                    expected=bake[field],
                    epsilon=float(args.parity_epsilon),
                ),
            )
            compared += 1

    result["checks"]["current"] = {
        "kind": "bakeRigParity",
        "compared": compared,
        "comparedFrames": len(frames),
        "worstDelta": worst,
        "mismatches": mismatches[:50],
        "gate": not bool(args.parity_current_report_only),
    }
    result["checks"]["interpolationDrift"] = {
        "kind": "sparseBakeInterpolationDrift",
        "gate": not bool(args.parity_interpolation_report_only),
        "summary": drift_summary,
        "mismatches": drift_mismatches,
    }
    result["checks"]["rigStructure"] = rig_structure
    result["status"] = (
        "failed"
        if rig_structure["mismatches"]
        or (mismatches and not args.parity_current_report_only)
        or (drift_mismatches and not args.parity_interpolation_report_only)
        else "passed"
    )
    return result


def _run_case(manifest_dir: Path, case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    assets = case.get("assets") or {}
    vmd_path = _resolve_path(manifest_dir, assets.get("cameraMotion") or assets.get("motion"))
    oracle_path = _resolve_path(manifest_dir, ((case.get("oracle") or {}).get("path")))
    epsilon = float(args.epsilon if args.epsilon is not None else (case.get("compare") or {}).get("epsilon", 5e-4))
    current_epsilon = float(args.current_epsilon if args.current_epsilon is not None else DEFAULT_CURRENT_EPSILON)
    is_generated_case = _is_generated_camera_case(case)
    skip_current_frame_zero = _skip_current_frame_zero(case, args.current_frame_zero)
    result: dict[str, Any] = {
        "name": case.get("name"),
        "vmd": str(vmd_path) if vmd_path else None,
        "oracle": str(oracle_path) if oracle_path else None,
        "epsilon": epsilon,
        "currentEpsilon": current_epsilon,
        "currentFrameZeroPolicy": args.current_frame_zero,
        "currentFrameZeroGeneratedCase": is_generated_case,
        "currentFrameZeroSkipEnabled": skip_current_frame_zero,
        "status": "pending",
        "checks": {},
    }
    if vmd_path is None or not vmd_path.exists():
        result.update({"status": "skipped", "reason": "camera VMD is missing"})
        return result
    if oracle_path is None or not oracle_path.exists():
        result.update({"status": "skipped", "reason": "oracle JSONL is missing"})
        return result

    records = _iter_oracle_records(oracle_path)
    selected_records = _select_records(case, records, args.max_current_frames, args.all_frames)
    keyframes = _camera_keyframes(records)
    result["oracleFrames"] = len(records)
    result["selectedCurrentFrames"] = len(selected_records)
    result["oracleKeyframes"] = len(keyframes)

    modes = ["sparse", "bake"] if args.mode == "both" else [args.mode]
    failed = False
    for mode in modes:
        camera = _import_camera(vmd_path, bake_mode=(mode == "bake"))
        mode_checks: dict[str, Any] = {}
        if keyframes:
            mode_checks["keyframes"] = _compare_keyframes(camera, keyframes, mode=mode, epsilon=epsilon)
        mode_checks["current"] = _compare_current(
            camera,
            selected_records,
            mode=mode,
            epsilon=current_epsilon,
            gate=not args.current_report_only,
            skip_frame_zero=skip_current_frame_zero,
        )
        result["checks"][mode] = mode_checks
        for check in mode_checks.values():
            if check.get("gate", True) and check["mismatches"]:
                failed = True

    result["status"] = "failed" if failed else "passed"
    return result


def main() -> int:
    args = _parse_args()
    repo_root = Path(args.repo_root).resolve()
    _initialize(repo_root)
    if args.parity_vmd:
        case = _run_parity_vmd(Path(args.parity_vmd).resolve(), args)
        report = {
            "mode": "bake-rig-parity",
            "allFrames": bool(args.all_frames),
            "maxCurrentFrames": args.max_current_frames,
            "cases": [case],
        }
        failed = [item for item in report["cases"] if item["status"] == "failed"]
        runnable = [item for item in report["cases"] if item["status"] != "skipped"]
        report["summary"] = {
            "cases": len(report["cases"]),
            "runnable": len(runnable),
            "passed": sum(1 for item in report["cases"] if item["status"] == "passed"),
            "failed": len(failed),
            "skipped": sum(1 for item in report["cases"] if item["status"] == "skipped"),
        }

        out = Path(args.out).resolve()
        _write_json_report(out, report)
        print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
        print(f"report: {out}", flush=True)
        return 1 if failed else 0

    manifest_path = Path(args.manifest).resolve()
    manifest_dir, cases = _load_manifest(manifest_path)
    if args.case_name:
        cases = [case for case in cases if case.get("name") == args.case_name]
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        raise ValueError("No GoldenOracle camera-motion cases selected")

    report = {
        "manifest": str(manifest_path),
        "mode": args.mode,
        "allFrames": bool(args.all_frames),
        "maxCurrentFrames": args.max_current_frames,
        "currentFrameZeroPolicy": args.current_frame_zero,
        "cases": [_run_case(manifest_dir, case, args) for case in cases],
    }
    failed = [case for case in report["cases"] if case["status"] == "failed"]
    runnable = [case for case in report["cases"] if case["status"] != "skipped"]
    report["summary"] = {
        "cases": len(report["cases"]),
        "runnable": len(runnable),
        "passed": sum(1 for case in report["cases"] if case["status"] == "passed"),
        "failed": len(failed),
        "skipped": sum(1 for case in report["cases"] if case["status"] == "skipped"),
    }

    out = Path(args.out).resolve()
    _write_json_report(out, report)
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    print(f"report: {out}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
