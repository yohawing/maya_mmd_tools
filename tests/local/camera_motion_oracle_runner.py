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
    if not converter.convert(vmd_data, bake_mode=bake_mode, vmd_bytes=vmd_bytes):
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
    matrix = om.MMatrix(cmds.getAttr(f"{camera}.worldMatrix[0]"))
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
    return {
        "distance": distance,
        "position": position,
        "rotation": rotation,
        "fov": _camera_shape_vertical_fov(camera, frame),
        "perspective": _camera_shape_perspective(camera, frame),
        "transformPosition": _maya_point_to_mmd(maya_target),
        "transformForward": forward_list,
        "transformUp": up_list,
    }


def _diff_scalar(actual: float, expected: float) -> float:
    return abs(float(actual) - float(expected))


def _diff_vector(actual: list[float], expected: list[float]) -> float:
    return max(_diff_scalar(left, right) for left, right in zip(actual, expected))


def _diff_angle(actual: float, expected: float) -> float:
    delta = (float(actual) - float(expected) + math.pi) % (2.0 * math.pi) - math.pi
    return abs(delta)


def _diff_rotation_vector(actual: list[float], expected: list[float]) -> float:
    return max(_diff_angle(left, right) for left, right in zip(actual, expected))


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
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    print(f"report: {out}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
