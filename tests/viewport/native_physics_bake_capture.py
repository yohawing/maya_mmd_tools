"""mayapy runner: native physics bake VMD import + offscreen PNG + JSON report.

Default mode (single-import capture):
    Imports a PMX model, applies VMD with ``bake_mode=True`` and
    ``use_native_physics_bake=True``, captures one frame via playblast, and
    writes a machine-readable JSON report describing feature flags, routing
    outcome, joint matrix samples, PNG pixel stats, and output paths.

``--verify-bake-route`` mode (dual-import E2E gate):
    Runs two clean-scene imports of a real physics PMX + VMD:

    1. baseline: ``bake_mode=True``, ``use_native_physics_bake=False``
    2. native:   ``bake_mode=True``, ``use_native_physics_bake=True``

    Fails (non-zero exit) unless:
    - native profile reports ``native_physics_bake.used == True``
    - at least one physics-controlled bone shows a measurable local-transform
      delta between native and baseline across explicit evaluation frames

Capture path reuses the known-good mayapy offscreen pattern from
``static_render_capture`` / track6 capture (bbox-framed persp camera, lambert
surfaceShader replacement, oriented directional light, stdlib non-blank PNG
check). Does not launch Maya GUI or require DX11.

Usage:
    mayapy tests/viewport/native_physics_bake_capture.py \\
        --pmx tests/data/mmt_test_model.pmx \\
        --vmd tests/data/mmt_test_model_test_motion.vmd \\
        --out build/captures/native_physics_bake.png \\
        --report build/reports/native_physics_bake_capture.json \\
        --frame 0

    mayapy tests/viewport/native_physics_bake_capture.py \\
        --verify-bake-route \\
        --pmx tests/data/physics/test_hair_physics.pmx \\
        --vmd tests/data/mmt_test_model_test_motion.vmd \\
        --report build/reports/native_physics_bake_route_e2e.json \\
        --eval-frames 0,1,2,3,4,5
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.standalone

DEFAULT_ROOT = Path(__file__).resolve().parents[2]

# GoldenOracle-style defaults (same as static_render_capture / track6).
CAMERA_FOV_DEG = 25.0
CAMERA_VIEW_DIR = [0.4, 0.2, 0.9]
LIGHT_DIRECTION = [0.5, -1.0, 0.5]
PNG_BLANK_THRESHOLD = 10

# Dual-import route gate defaults.
DEFAULT_VERIFY_PMX = "tests/data/physics/test_hair_physics.pmx"
DEFAULT_VERIFY_VMD = "tests/data/mmt_test_model_test_motion.vmd"
DEFAULT_EVAL_FRAMES = (0, 1, 2, 3, 4, 5)
# Local TR channels: translation units are scene cm; rotations are degrees.
# 1e-3 is large enough to ignore float noise, small enough to catch real physics writeback.
DEFAULT_DELTA_EPSILON = 1.0e-3
LOCAL_CHANNELS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(DEFAULT_ROOT))
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--out", default="build/captures/native_physics_bake.png")
    parser.add_argument("--report", default="build/reports/native_physics_bake_capture.json")
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--fps", type=int, default=30, choices=(30, 60))
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument(
        "--verify-bake-route",
        action="store_true",
        help=(
            "Run dual-import E2E gate (baseline vs native physics bake) instead of "
            "single-import PNG capture. Preferred fixtures: "
            f"{DEFAULT_VERIFY_PMX} + {DEFAULT_VERIFY_VMD}. "
            "Does not change default capture behaviour."
        ),
    )
    parser.add_argument(
        "--eval-frames",
        default=",".join(str(frame) for frame in DEFAULT_EVAL_FRAMES),
        help="Comma-separated Maya frames to sample in --verify-bake-route mode.",
    )
    parser.add_argument(
        "--delta-epsilon",
        type=float,
        default=DEFAULT_DELTA_EPSILON,
        help="Minimum abs local-channel delta required between native and baseline.",
    )
    return parser.parse_args()


def _parse_eval_frames(raw: str) -> list[int]:
    frames: list[int] = []
    for part in str(raw).split(","):
        text = part.strip()
        if not text:
            continue
        frames.append(int(text))
    if not frames:
        raise ValueError("--eval-frames must list at least one integer frame")
    # Preserve order but drop duplicates so reports stay stable.
    seen: set[int] = set()
    ordered: list[int] = []
    for frame in frames:
        if frame in seen:
            continue
        seen.add(frame)
        ordered.append(frame)
    return ordered


def _initialize(repo_root: Path) -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    current_root = str(DEFAULT_ROOT.resolve())
    sys.path[:] = [
        entry
        for entry in sys.path
        if str(Path(entry).resolve()) != current_root
        and str(Path(entry).resolve()) != str(DEFAULT_ROOT.resolve() / "tests" / "viewport")
    ]
    sys.path.insert(0, str(repo_root))


def _resolve(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (repo_root / path).resolve()


def _mesh_points(root: str) -> list[list[float]]:
    points: list[list[float]] = []
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        sel = om.MSelectionList()
        sel.add(shape)
        fn = om.MFnMesh(sel.getDagPath(0))
        points.extend([[p.x, p.y, p.z] for p in fn.getPoints(om.MSpace.kWorld)])
    return points


def _bbox(points: list[list[float]]) -> dict[str, list[float] | float]:
    if not points:
        return {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0], "center": [0.0, 0.0, 0.0], "diag": 0.0}
    mins = [min(point[i] for point in points) for i in range(3)]
    maxs = [max(point[i] for point in points) for i in range(3)]
    center = [(mins[i] + maxs[i]) * 0.5 for i in range(3)]
    diag = math.sqrt(sum((maxs[i] - mins[i]) ** 2 for i in range(3)))
    return {
        "min": [round(value, 6) for value in mins],
        "max": [round(value, 6) for value in maxs],
        "center": [round(value, 6) for value in center],
        "diag": round(diag, 6),
    }


def _normalize(v: list[float]) -> list[float]:
    length = math.sqrt(sum(x * x for x in v))
    if length < 1e-12:
        return [1.0, 0.0, 0.0]
    return [x / length for x in v]


def _direction_to_euler_rotation(from_pos: list[float], to_pos: list[float]) -> list[float]:
    """XYZ Euler degrees so local -Z points from *from_pos* toward *to_pos*."""
    dx = to_pos[0] - from_pos[0]
    dy = to_pos[1] - from_pos[1]
    dz = to_pos[2] - from_pos[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1e-12:
        return [0.0, 0.0, 0.0]
    sin_theta = max(-1.0, min(1.0, -dx / length))
    yaw = math.degrees(math.asin(sin_theta))
    pitch = math.degrees(math.atan2(dy, -dz))
    return [pitch, yaw, 0.0]


def _assign_debug_material(root: str) -> int:
    """Replace surfaceShader on existing mesh SGs with a bright lambert.

    standardSurface (and similar) often renders black/blank under mayapy
    offscreen playblast; lambert replacement is the proven visibility path.
    """
    meshes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    if not meshes:
        return 0
    shader = cmds.shadingNode("lambert", asShader=True, name="nativePhysicsBake_lambert")
    cmds.setAttr(f"{shader}.color", 0.85, 0.9, 1.0, type="double3")
    cmds.setAttr(f"{shader}.ambientColor", 0.35, 0.35, 0.35, type="double3")
    cmds.setAttr(f"{shader}.diffuse", 1.0)
    sgs_seen: set[str] = set()
    for mesh in meshes:
        try:
            if cmds.getAttr(f"{mesh}.intermediateObject"):
                continue
        except Exception:
            pass
        try:
            cmds.setAttr(f"{mesh}.displayColors", 0)
        except Exception:
            pass
        for sg in cmds.listConnections(mesh, type="shadingEngine") or []:
            if sg in sgs_seen:
                continue
            sgs_seen.add(sg)
            existing = cmds.listConnections(f"{sg}.surfaceShader", source=True, destination=False) or []
            for node in existing:
                try:
                    cmds.disconnectAttr(f"{node}.outColor", f"{sg}.surfaceShader")
                except Exception:
                    pass
            cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
    # Fallback: no SG connection yet — create one and force-assign transforms.
    if not sgs_seen:
        shading_group = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{shader}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{shading_group}.surfaceShader", force=True)
        transforms = sorted(
            {
                cmds.listRelatives(mesh, parent=True, fullPath=True)[0]
                for mesh in meshes
                if cmds.listRelatives(mesh, parent=True, fullPath=True)
            }
        )
        if transforms:
            cmds.sets(transforms, edit=True, forceElement=shading_group)
            return 1
    return len(sgs_seen)


def _setup_camera_and_light(
    bounds: dict[str, list[float] | float],
    width: int,
    height: int,
) -> dict[str, float]:
    """Frame default persp on mesh bbox and light the scene (no GUI panel needed)."""
    center = [float(v) for v in bounds["center"]]  # type: ignore[arg-type]
    diag = float(bounds["diag"])
    model_radius = max(diag * 0.5, 0.5)

    aspect_ratio = width / max(height, 1)
    tan_half_fov = math.tan(math.radians(CAMERA_FOV_DEG) * 0.5)
    d_h = model_radius / (0.7 * tan_half_fov) if tan_half_fov > 1e-9 else model_radius * 3.0
    d_v = (
        model_radius / (0.7 * tan_half_fov / aspect_ratio)
        if tan_half_fov > 1e-9
        else model_radius * 3.0
    )
    camera_distance = max(d_h, d_v, model_radius * 2.0, 5.0)

    view_dir = _normalize(CAMERA_VIEW_DIR)
    cam_pos = [
        center[0] + view_dir[0] * camera_distance,
        center[1] + view_dir[1] * camera_distance,
        center[2] + view_dir[2] * camera_distance,
    ]
    euler = _direction_to_euler_rotation(cam_pos, center)

    cmds.setAttr("persp.translateX", cam_pos[0])
    cmds.setAttr("persp.translateY", cam_pos[1])
    cmds.setAttr("persp.translateZ", cam_pos[2])
    cmds.setAttr("persp.rotateX", euler[0])
    cmds.setAttr("persp.rotateY", euler[1])
    cmds.setAttr("persp.rotateZ", euler[2])

    shapes = cmds.listRelatives("persp", shapes=True) or []
    if shapes:
        persp_shape = shapes[0]
        focal_length = 18.0 / tan_half_fov if tan_half_fov > 1e-9 else 35.0
        cmds.setAttr(f"{persp_shape}.focalLength", focal_length)
        cmds.setAttr(f"{persp_shape}.nearClipPlane", max(0.01, camera_distance * 0.01))
        cmds.setAttr(
            f"{persp_shape}.farClipPlane",
            camera_distance + model_radius * 4.0 + 100.0,
        )

    light_shape = cmds.directionalLight(
        name="nativePhysicsBakeLight",
        intensity=2.0,
        rgb=[1.0, 1.0, 1.0],
    )
    light_xform = cmds.listRelatives(light_shape, parent=True)[0]
    # GOLDEN direction is light→scene; place transform at center - dir so -Z aims at center.
    light_pos = [
        center[0] - LIGHT_DIRECTION[0],
        center[1] - LIGHT_DIRECTION[1],
        center[2] - LIGHT_DIRECTION[2],
    ]
    light_euler = _direction_to_euler_rotation(light_pos, center)
    cmds.setAttr(f"{light_xform}.rotateX", light_euler[0])
    cmds.setAttr(f"{light_xform}.rotateY", light_euler[1])
    cmds.setAttr(f"{light_xform}.rotateZ", light_euler[2])

    fill_shape = cmds.ambientLight(name="nativePhysicsBakeFill", intensity=0.8, rgb=(1.0, 1.0, 1.0))
    fill_xform = cmds.listRelatives(fill_shape, parent=True)[0]
    cmds.setAttr(f"{fill_xform}.translateX", center[0])
    cmds.setAttr(f"{fill_xform}.translateY", center[1] + model_radius)
    cmds.setAttr(f"{fill_xform}.translateZ", center[2] + model_radius)

    return {
        "camera_distance": round(camera_distance, 6),
        "model_radius": round(model_radius, 6),
        "cam_x": round(cam_pos[0], 6),
        "cam_y": round(cam_pos[1], 6),
        "cam_z": round(cam_pos[2], 6),
    }


def _apply_color_management() -> None:
    """Prefer Un-tone-mapped sRGB when available (batch-safe, no displayPref)."""
    try:
        available = cmds.colorManagementPrefs(q=True, viewTransformNames=True) or []
        if not isinstance(available, list):
            available = [available]
        preferred = "Un-tone-mapped (sRGB)"
        if preferred in available:
            cmds.colorManagementPrefs(e=True, viewTransformName=preferred)
        display_names = cmds.colorManagementPrefs(q=True, displayNames=True) or []
        if not isinstance(display_names, list):
            display_names = [display_names]
        if "sRGB" in display_names:
            cmds.colorManagementPrefs(e=True, displayName="sRGB")
    except Exception:
        pass


def _check_png_not_blank(
    png_path: Path,
    threshold: int = PNG_BLANK_THRESHOLD,
    *,
    allow_blank: bool = False,
) -> dict[str, Any]:
    """Parse PNG with stdlib only; reject near-black frames.

    Returns compact stats (min/max/avg/samples/width/height). Raises RuntimeError
    when max sampled channel value is below *threshold* unless *allow_blank*.
    """
    with open(png_path, "rb") as handle:
        raw = handle.read()

    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a valid PNG file: {png_path}")

    ihdr_len = struct.unpack_from(">I", raw, 8)[0]
    if raw[12:16] != b"IHDR":
        raise ValueError(f"Expected IHDR chunk, got {raw[12:16]!r}")
    ihdr = raw[16 : 16 + ihdr_len]
    img_w = struct.unpack_from(">I", ihdr, 0)[0]
    img_h = struct.unpack_from(">I", ihdr, 4)[0]
    bit_depth = ihdr[8]
    color_type = ihdr[9]

    pos = 16 + ihdr_len + 4
    idat_parts: list[bytes] = []
    while pos + 8 <= len(raw):
        chunk_len = struct.unpack_from(">I", raw, pos)[0]
        chunk_type = raw[pos + 4 : pos + 8]
        if chunk_type == b"IDAT":
            idat_parts.append(raw[pos + 8 : pos + 8 + chunk_len])
        elif chunk_type == b"IEND":
            break
        pos += 12 + chunk_len

    if not idat_parts:
        raise ValueError(f"No IDAT data in PNG: {png_path}")

    decompressed = zlib.decompress(b"".join(idat_parts))
    if bit_depth != 8:
        raise ValueError(f"Unsupported PNG bit depth for blank check: {bit_depth}")
    if color_type == 0:
        channels = 1
    elif color_type == 2:
        channels = 3
    elif color_type == 4:
        channels = 2
    elif color_type == 6:
        channels = 4
    else:
        raise ValueError(f"Unsupported PNG color type for blank check: {color_type}")

    pixel_bytes = channels
    row_bytes = img_w * pixel_bytes
    row_stride = 1 + row_bytes
    min_val = 255
    max_val = 0
    total = 0
    count = 0
    prev_row = bytearray(row_bytes)
    total_rows = max(1, len(decompressed) // row_stride)
    row_step = max(1, total_rows // 400)

    for row_idx in range(total_rows):
        offset = row_idx * row_stride
        if offset + row_stride > len(decompressed):
            break
        filter_type = decompressed[offset]
        row = bytearray(decompressed[offset + 1 : offset + 1 + row_bytes])
        for idx in range(row_bytes):
            left = row[idx - pixel_bytes] if idx >= pixel_bytes else 0
            up = prev_row[idx]
            up_left = prev_row[idx - pixel_bytes] if idx >= pixel_bytes else 0
            if filter_type == 1:
                row[idx] = (row[idx] + left) & 0xFF
            elif filter_type == 2:
                row[idx] = (row[idx] + up) & 0xFF
            elif filter_type == 3:
                row[idx] = (row[idx] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                predictor = left + up - up_left
                pa = abs(predictor - left)
                pb = abs(predictor - up)
                pc = abs(predictor - up_left)
                paeth = left if pa <= pb and pa <= pc else up if pb <= pc else up_left
                row[idx] = (row[idx] + paeth) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"Unsupported PNG row filter: {filter_type}")

        if row_idx % row_step == 0:
            for px in range(0, row_bytes, pixel_bytes * 4):
                if color_type in (0, 4):
                    val = row[px]
                else:
                    val = max(row[px], row[px + 1], row[px + 2])
                min_val = min(min_val, val)
                max_val = max(max_val, val)
                total += val
                count += 1
        prev_row = row

    avg = total / count if count else 0.0
    stats: dict[str, Any] = {
        "min": min_val if count else 0,
        "max": max_val if count else 0,
        "avg": round(avg, 3),
        "samples": count,
        "width": img_w,
        "height": img_h,
        "threshold": threshold,
    }
    if max_val < threshold:
        blank_error = (
            f"Captured PNG is effectively blank (max pixel={max_val} < {threshold}): "
            f"min={min_val} avg={avg:.2f} samples={count}. "
            "Model may be out of frame, unlit, or the viewport is black."
        )
        stats["blank_error"] = blank_error
        if not allow_blank:
            raise RuntimeError(blank_error)
    return stats


def _capture(root: str, out: Path, frame: int, width: int, height: int, bounds: dict) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    for old_png in out.parent.glob(f"{out.stem}*.png"):
        try:
            old_png.unlink()
        except Exception:
            pass

    _assign_debug_material(root)
    _setup_camera_and_light(bounds, width, height)
    _apply_color_management()

    cmds.currentTime(frame, edit=True)
    try:
        cmds.playbackOptions(minTime=frame, maxTime=frame)
    except Exception:
        pass
    try:
        cmds.refresh(force=True)
    except Exception:
        try:
            cmds.refresh()
        except Exception:
            pass

    # Minimal playblast flag set proven under mayapy standalone (no camera kwarg).
    result = cmds.playblast(
        frame=frame,
        format="image",
        filename=str(out.with_suffix("")),
        compression="png",
        width=width,
        height=height,
        percent=100,
        quality=90,
        viewer=False,
        showOrnaments=False,
        forceOverwrite=True,
        offScreen=True,
        offScreenViewportUpdate=True,
    )
    candidates = [
        out,
        out.with_suffix(".png"),
        out.parent / f"{out.stem}.{frame:04d}.png",
        out.parent / f"{out.stem}.{frame:03d}.png",
        out.parent / f"{out.stem}.{frame:02d}.png",
        out.parent / f"{out.stem}.{frame}.png",
        Path(result) if result else out,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate.resolve()
    pngs = sorted(out.parent.glob(f"{out.stem}*.png"), key=lambda path: path.stat().st_mtime, reverse=True)
    if pngs:
        return pngs[0].resolve()
    raise RuntimeError(f"playblast did not create non-empty PNG: {out}")


def _joint_matrix_samples(root: str, limit: int = 8) -> list[dict[str, Any]]:
    """Sample a few joint world matrices for machine-readable verification."""
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    samples: list[dict[str, Any]] = []
    for joint in joints[:limit]:
        try:
            matrix = cmds.xform(joint, query=True, matrix=True, worldSpace=True)
            translate = cmds.xform(joint, query=True, translation=True, worldSpace=True)
        except Exception:
            continue
        samples.append(
            {
                "joint": joint,
                "worldTranslate": [round(float(v), 6) for v in translate],
                "worldMatrix": [round(float(v), 6) for v in matrix],
            }
        )
    return samples


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _long_path(node: str) -> str:
    paths = cmds.ls(node, long=True) or []
    return paths[0] if paths else node


def _extract_native_physics_routing(profile: dict[str, Any]) -> dict[str, Any]:
    converter = profile.get("vmd_converter")
    if not isinstance(converter, dict):
        return {}
    routing = converter.get("native_physics_bake")
    return dict(routing) if isinstance(routing, dict) else {}


def _physics_controlled_joints(root: str, pmx_path: Path) -> list[dict[str, Any]]:
    """Return joints driven by non-static rigid bodies declared in the PMX."""
    from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX
    from mmd_tools.core.mmd_parser import parse_pmx_file

    root_path = _long_path(root)
    bone_by_index: dict[int, str] = {}
    for joint in cmds.listRelatives(root_path, allDescendents=True, type="joint", fullPath=True) or []:
        if not cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
            continue
        try:
            bone_by_index[int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))] = joint
        except Exception:
            continue

    controlled: list[dict[str, Any]] = []
    seen_joints: set[str] = set()
    pmx = parse_pmx_file(str(pmx_path))
    for rb_index, rigid_body in enumerate(pmx.rigid_bodies):
        mode = int(rigid_body.physics_mode)
        bone_index = int(rigid_body.related_bone_index)
        if mode == 0 or bone_index < 0:
            continue
        joint = bone_by_index.get(bone_index)
        if not joint or joint in seen_joints:
            continue
        seen_joints.add(joint)
        controlled.append(
            {
                "joint": joint,
                "boneIndex": bone_index,
                "rigidBodyIndex": rb_index,
                "physicsMode": mode,
            }
        )
    controlled.sort(key=lambda item: (int(item["boneIndex"]), str(item["joint"])))
    return controlled


def _sample_local_channels(joint: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for channel in LOCAL_CHANNELS:
        attr = f"{joint}.{channel}"
        try:
            values[channel] = float(cmds.getAttr(attr))
        except Exception:
            values[channel] = float("nan")
    return values


def _sample_bone_transform(joint: str) -> dict[str, Any]:
    local = _sample_local_channels(joint)
    try:
        matrix = [float(value) for value in cmds.xform(joint, query=True, matrix=True, worldSpace=True)]
        world = [matrix[12], matrix[13], matrix[14]]
        scale = [
            math.sqrt(sum(matrix[row * 4 + column] ** 2 for column in range(3)))
            for row in range(3)
        ]
    except Exception:
        matrix = [float("nan")] * 16
        world = [float("nan")] * 3
        scale = [float("nan")] * 3
    parents = cmds.listRelatives(joint, parent=True, fullPath=True) or []
    try:
        parent_world = (
            [float(value) for value in cmds.xform(parents[0], query=True, translation=True, worldSpace=True)]
            if parents
            else [0.0, 0.0, 0.0]
        )
    except Exception:
        parent_world = [float("nan")] * 3
    finite = all(
        math.isfinite(value)
        for value in (*local.values(), *world, *parent_world, *scale, *matrix)
    )
    return {
        **local,
        "joint": joint,
        "worldTranslate": world,
        "parentWorldTranslate": parent_world,
        "worldMatrixScale": scale,
        "worldMatrix": matrix,
        "finite": finite,
    }


def _sample_physics_bones(
    controlled: list[dict[str, Any]],
    frames: list[int],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Sample local TR and world-space diagnostics at each evaluation frame.

    Layout: samples[bone_index][frame_str][channel_or_world_field] = value.
    """
    samples: dict[str, dict[str, dict[str, Any]]] = {
        str(item["boneIndex"]): {} for item in controlled
    }
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        try:
            cmds.dgdirty(allPlugs=True)
        except Exception:
            pass
        try:
            cmds.refresh(force=True)
        except Exception:
            try:
                cmds.refresh()
            except Exception:
                pass
        frame_key = str(frame)
        for item in controlled:
            bone_key = str(item["boneIndex"])
            sample = _sample_bone_transform(item["joint"])
            samples[bone_key][frame_key] = sample
    return samples


def _capture_physics_bind_pairs(
    controlled: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Capture the PMX-import bind relation before VMD changes the joints."""
    pairs: dict[str, dict[str, Any]] = {}
    for item in controlled:
        sample = _sample_bone_transform(item["joint"])
        pairs[str(item["boneIndex"])] = {
            "joint": item["joint"],
            "physicsMode": item["physicsMode"],
            "worldMatrix": sample["worldMatrix"],
        }
    return pairs


def _compare_local_samples(
    baseline: dict[str, dict[str, dict[str, Any]]],
    native: dict[str, dict[str, dict[str, Any]]],
    frames: list[int],
    epsilon: float,
) -> dict[str, Any]:
    """Find the largest abs local-channel delta between two bake runs."""
    max_abs = 0.0
    winner: dict[str, Any] | None = None
    compared_bones = 0
    compared_channels = 0
    per_bone_max: dict[str, float] = {}

    bone_indices = sorted(set(baseline.keys()) & set(native.keys()), key=lambda value: int(value))
    for bone_index in bone_indices:
        compared_bones += 1
        bone_max = 0.0
        for frame in frames:
            frame_key = str(frame)
            base_frame = baseline.get(bone_index, {}).get(frame_key)
            native_frame = native.get(bone_index, {}).get(frame_key)
            if not base_frame or not native_frame:
                continue
            for channel in LOCAL_CHANNELS:
                base_val = base_frame.get(channel)
                native_val = native_frame.get(channel)
                if base_val is None or native_val is None:
                    continue
                if math.isnan(base_val) or math.isnan(native_val):
                    continue
                delta = abs(float(native_val) - float(base_val))
                compared_channels += 1
                bone_max = max(bone_max, delta)
                if delta > max_abs:
                    max_abs = delta
                    winner = {
                        "boneIndex": int(bone_index),
                        "joint": base_frame.get("joint"),
                        "frame": frame,
                        "channel": channel,
                        "baseline": float(base_val),
                        "native": float(native_val),
                        "absDelta": float(delta),
                    }
        per_bone_max[bone_index] = round(bone_max, 9)

    return {
        "epsilon": float(epsilon),
        "passed": bool(winner is not None and max_abs > float(epsilon)),
        "maxAbsDelta": round(max_abs, 9),
        "winner": winner,
        "comparedBones": compared_bones,
        "comparedChannels": compared_channels,
        "perBoneMaxAbsDelta": per_bone_max,
    }


def _configure_import_settings() -> None:
    from mmd_tools.core import settings

    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.rig.add_semi_standard_bones", False)
    settings.set("logging.level", "INFO")


def _import_pmx_with_physics(pmx_path: Path) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    root = import_mmd_file(
        str(pmx_path),
        options={
            "setup_rig": False,
            "setup_bone_orientation": False,
            "import_physics": True,
            "create_physics_joints": True,
            "create_mmd_shaders": False,
            "use_namespace": False,
            "cpp_fast_load": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx_path}")
    return _long_path(root)


def _import_vmd_bake(
    *,
    vmd_path: Path,
    pmx_path: Path,
    root: str,
    fps: int,
    use_native_physics_bake: bool,
) -> dict[str, Any]:
    from mmd_tools.io.mmd_importer import import_mmd_file

    profile: dict[str, Any] = {}
    ok = import_mmd_file(
        str(vmd_path),
        options={
            "target_model": root,
            "pmx_path": str(pmx_path),
            "bake_mode": True,
            "use_native_physics_bake": bool(use_native_physics_bake),
            "vmd_fps": int(fps),
            "profile": profile,
        },
    )
    if not ok:
        raise RuntimeError(
            f"VMD bake import failed (use_native_physics_bake={use_native_physics_bake}): {vmd_path}"
        )
    return profile


def _run_bake_scene(
    *,
    pmx_path: Path,
    vmd_path: Path,
    fps: int,
    use_native_physics_bake: bool,
    eval_frames: list[int],
) -> dict[str, Any]:
    root = _import_pmx_with_physics(pmx_path)
    bind_pairs = _capture_physics_bind_pairs(_physics_controlled_joints(root, pmx_path))
    profile = _import_vmd_bake(
        vmd_path=vmd_path,
        pmx_path=pmx_path,
        root=root,
        fps=fps,
        use_native_physics_bake=use_native_physics_bake,
    )
    routing = _extract_native_physics_routing(profile)
    controlled = _physics_controlled_joints(root, pmx_path)
    samples = _sample_physics_bones(controlled, eval_frames)
    return {
        "root": root,
        "bake_mode": True,
        "use_native_physics_bake": bool(use_native_physics_bake),
        "profile": profile,
        "physics_routing": routing,
        "physics_bones": controlled,
        "bind_pairs": bind_pairs,
        "samples": samples,
        "eval_frames": list(eval_frames),
    }


def _assert_route_gate(report: dict[str, Any], epsilon: float) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []

    native = report.get("native") or {}
    routing = native.get("physics_routing") or {}
    used = bool(routing.get("used"))
    assertions.append(
        {
            "name": "native_physics_bake_used",
            "pass": used,
            "details": {
                "used": used,
                "requested": routing.get("requested"),
                "reason": routing.get("reason"),
                "routing": routing,
            },
        }
    )

    delta = report.get("delta") or {}
    delta_pass = bool(delta.get("passed"))
    assertions.append(
        {
            "name": "physics_bone_local_transform_delta",
            "pass": delta_pass,
            "details": {
                "epsilon": epsilon,
                "maxAbsDelta": delta.get("maxAbsDelta"),
                "winner": delta.get("winner"),
                "comparedBones": delta.get("comparedBones"),
                "comparedChannels": delta.get("comparedChannels"),
            },
        }
    )


    report["assertions"] = assertions
    failed = [item for item in assertions if not item["pass"]]
    report["failed_assertions"] = [item["name"] for item in failed]
    if failed:
        report["status"] = "failed"
        report["gate"] = failed[0]["name"]
        report["error"] = (
            "native physics bake route gate failed: "
            + ", ".join(item["name"] for item in failed)
        )
    else:
        report["status"] = "passed"
        report["gate"] = "ok"
        report.pop("error", None)
    return assertions


def main_verify_bake_route(args: argparse.Namespace) -> int:
    """Dual-import E2E: baseline runtime bake vs native physics bake."""
    repo_root = Path(args.repo_root).resolve()
    _initialize(repo_root)

    import mmd_tools
    from mmd_tools.core.native.mmd_anim_runtime import (
        get_runtime_feature_flags,
        get_runtime_library_path,
        is_mmd_runtime_available,
        is_native_physics_available,
    )

    pmx_path = _resolve(repo_root, args.pmx)
    vmd_path = _resolve(repo_root, args.vmd)
    report_path = _resolve(repo_root, args.report)
    try:
        eval_frames = _parse_eval_frames(args.eval_frames)
    except ValueError as exc:
        report = {
            "status": "failed",
            "mode": "verify_bake_route",
            "error": str(exc),
            "gate": "invalid_eval_frames",
            "report": str(report_path),
        }
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    epsilon = float(args.delta_epsilon)
    feature_flags = int(get_runtime_feature_flags()) if is_mmd_runtime_available() else 0
    physics_available = bool(is_native_physics_available())
    runtime_path = str(get_runtime_library_path() or "")

    report: dict[str, Any] = {
        "status": "failed",
        "mode": "verify_bake_route",
        "repo_root": str(repo_root),
        "mmd_tools": str(Path(mmd_tools.__file__).resolve()),
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "fps": int(args.fps),
        "eval_frames": list(eval_frames),
        "delta_epsilon": epsilon,
        "feature_flags": feature_flags,
        "feature_flags_hex": hex(feature_flags),
        "native_physics_available": physics_available,
        "runtime_library_path": runtime_path,
        "baseline": {},
        "native": {},
        "delta": {},
        "assertions": [],
        "report": str(report_path),
    }

    if not pmx_path.is_file():
        report["error"] = f"PMX not found: {pmx_path}"
        report["gate"] = "pmx_missing"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    if not vmd_path.is_file():
        report["error"] = f"VMD not found: {vmd_path}"
        report["gate"] = "vmd_missing"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    if not physics_available:
        report["error"] = (
            "native physics unavailable: require feature-enabled mmd-anim-ffi "
            f"(feature_flags={hex(feature_flags)}, path={runtime_path or 'None'}). "
            "Pass --ffi-path to a physics-bullet-native build directory."
        )
        report["gate"] = "native_physics_unavailable"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 3
    _configure_import_settings()

    try:
        baseline = _run_bake_scene(
            pmx_path=pmx_path,
            vmd_path=vmd_path,
            fps=int(args.fps),
            use_native_physics_bake=False,
            eval_frames=eval_frames,
        )
        report["baseline"] = {
            "bake_mode": True,
            "use_native_physics_bake": False,
            "root": baseline["root"],
            "profile": baseline["profile"],
            "physics_routing": baseline["physics_routing"],
            "physics_bones": baseline["physics_bones"],
            "samples": baseline["samples"],
            "eval_frames": baseline["eval_frames"],
        }
    except Exception as exc:
        report["error"] = f"baseline import failed: {type(exc).__name__}: {exc}"
        report["gate"] = "baseline_import_failed"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 4

    try:
        native = _run_bake_scene(
            pmx_path=pmx_path,
            vmd_path=vmd_path,
            fps=int(args.fps),
            use_native_physics_bake=True,
            eval_frames=eval_frames,
        )
        report["native"] = {
            "bake_mode": True,
            "use_native_physics_bake": True,
            "root": native["root"],
            "profile": native["profile"],
            "physics_routing": native["physics_routing"],
            "physics_bones": native["physics_bones"],
            "samples": native["samples"],
            "eval_frames": native["eval_frames"],
        }
    except Exception as exc:
        report["error"] = f"native import failed: {type(exc).__name__}: {exc}"
        report["gate"] = "native_import_failed"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 5

    if not baseline["physics_bones"] or not native["physics_bones"]:
        report["error"] = (
            "no physics-controlled bones found after import "
            f"(baseline={len(baseline['physics_bones'])}, native={len(native['physics_bones'])})"
        )
        report["gate"] = "no_physics_bones"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 6

    report["delta"] = _compare_local_samples(
        baseline["samples"],
        native["samples"],
        eval_frames,
        epsilon,
    )
    _assert_route_gate(report, epsilon)
    _write_report(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("status") == "passed":
        return 0
    # Stable non-zero codes for gate failures.
    gate = report.get("gate")
    if gate == "native_physics_bake_used":
        return 6
    if gate == "physics_bone_local_transform_delta":
        return 7
    return 10


def main_capture(args: argparse.Namespace) -> int:
    """Single-import PNG capture (default / existing behaviour)."""
    repo_root = Path(args.repo_root).resolve()
    _initialize(repo_root)

    import mmd_tools
    from mmd_tools.core.native.mmd_anim_runtime import (
        get_runtime_feature_flags,
        get_runtime_library_path,
        is_mmd_runtime_available,
        is_native_physics_available,
    )
    from mmd_tools.io.mmd_importer import import_mmd_file

    pmx_path = _resolve(repo_root, args.pmx)
    vmd_path = _resolve(repo_root, args.vmd)
    out_path = _resolve(repo_root, args.out)
    report_path = _resolve(repo_root, args.report)

    feature_flags = int(get_runtime_feature_flags()) if is_mmd_runtime_available() else 0
    physics_available = bool(is_native_physics_available())
    runtime_path = str(get_runtime_library_path() or "")

    report: dict[str, Any] = {
        "status": "failed",
        "mode": "capture",
        "repo_root": str(repo_root),
        "mmd_tools": str(Path(mmd_tools.__file__).resolve()),
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "frame": int(args.frame),
        "fps": int(args.fps),
        "feature_flags": feature_flags,
        "feature_flags_hex": hex(feature_flags),
        "native_physics_available": physics_available,
        "runtime_library_path": runtime_path,
        "use_native_physics_bake": True,
        "bake_mode": True,
        "physics_routing": {},
        "joint_matrix_samples": [],
        "png": "",
        "png_bytes": 0,
        "png_stats": {},
        "report": str(report_path),
    }

    if not pmx_path.is_file():
        report["error"] = f"PMX not found: {pmx_path}"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    if not vmd_path.is_file():
        report["error"] = f"VMD not found: {vmd_path}"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    if not physics_available:
        report["error"] = (
            "native physics unavailable: require feature-enabled mmd-anim-ffi "
            f"(feature_flags={hex(feature_flags)}, path={runtime_path or 'None'}). "
            "Pass --ffi-path to a physics-bullet-native build directory."
        )
        report["gate"] = "native_physics_unavailable"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 3

    _configure_import_settings()

    cmds.file(new=True, force=True)
    root = import_mmd_file(
        str(pmx_path),
        options={
            "setup_rig": False,
            "setup_bone_orientation": False,
            "import_physics": False,
            "create_mmd_shaders": False,
        },
    )
    if not root:
        report["error"] = f"PMX import failed: {pmx_path}"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 4

    profile: dict[str, Any] = {}
    ok = import_mmd_file(
        str(vmd_path),
        options={
            "target_model": root,
            "pmx_path": str(pmx_path),
            "bake_mode": True,
            "use_native_physics_bake": True,
            "vmd_fps": int(args.fps),
            "profile": profile,
        },
    )
    if not ok:
        report["error"] = f"VMD native physics bake import failed: {vmd_path}"
        report["import_profile"] = profile
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 5

    routing = _extract_native_physics_routing(profile)
    report["physics_routing"] = routing
    report["import_profile"] = profile

    if not routing.get("used"):
        report["error"] = (
            "native physics bake path was not used "
            f"(reason={routing.get('reason', 'unknown')}); refusing to claim success"
        )
        report["gate"] = "physics_routing_not_used"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 6

    cmds.currentTime(args.frame, edit=True)
    cmds.refresh(force=True)
    points = _mesh_points(root)
    bounds = _bbox(points)
    report["joint_matrix_samples"] = _joint_matrix_samples(root)
    report["bbox"] = bounds
    report["vertices"] = len(points)

    if not points or float(bounds["diag"]) <= 0.0:
        report["error"] = "No mesh geometry to capture (empty bbox)"
        report["gate"] = "empty_geometry"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 7

    try:
        png = _capture(root, out_path, args.frame, args.width, args.height, bounds)
    except Exception as exc:
        report["error"] = f"capture failed: {type(exc).__name__}: {exc}"
        report["gate"] = "capture_failed"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 7

    png_size = png.stat().st_size if png.exists() else 0
    report["png"] = str(png)
    report["png_bytes"] = int(png_size)
    if png_size <= 0:
        report["error"] = f"PNG missing or empty: {png}"
        report["gate"] = "png_empty"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 7

    try:
        png_stats = _check_png_not_blank(png)
    except Exception as exc:
        try:
            report["png_stats"] = _check_png_not_blank(png, allow_blank=True)
        except Exception as parse_exc:
            report["png_stats"] = {"error": str(parse_exc)}
        if "blank_error" not in report["png_stats"]:
            report["png_stats"]["blank_error"] = str(exc)
        report["error"] = str(exc)
        report["gate"] = "png_not_visible"
        report["status"] = "failed"
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 8

    report["png_stats"] = png_stats
    report["status"] = "passed"
    _write_report(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = _parse_args()
    if args.verify_bake_route:
        return main_verify_bake_route(args)
    return main_capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
