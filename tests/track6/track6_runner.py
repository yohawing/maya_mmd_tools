#!/usr/bin/env python
"""Manifest-driven Maya batch import runner for Track 6.

This runner is intentionally local-data friendly:
- it reads PMX/PMD/VMD files from an external asset root such as F:/MMD;
- it writes manifests, result logs, and optional Maya scenes only under build/;
- it can rerun a single failing case with --case.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import struct
import sys
import time
import traceback
import zlib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOT = (ROOT / "build").resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _CaseLogHandler(logging.Handler):
    """Collect Python logging records emitted while one batch case runs."""

    def __init__(self, records: list[dict[str, str]]):
        super().__init__(level=logging.WARNING)
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        self._records.append(
            {
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
            }
        )


def _require_build_path(value: str | Path, option_name: str) -> Path:
    """Resolve an output path and require it to stay under build/."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if path != BUILD_ROOT and BUILD_ROOT not in path.parents:
        raise ValueError(f"{option_name} must resolve under {BUILD_ROOT}: {path}")
    return path


def _case_name(raw: str) -> str:
    """Return a filesystem-safe case name."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return safe[:80] or "case"


def _rel_or_abs(path: Path) -> str:
    """Return a stable absolute path string for local manifests."""
    return str(path.resolve())


def generate_manifest(
    scan_root: str | Path,
    write_manifest: str | Path,
    max_models: int,
    max_motions: int,
    max_cases: int,
) -> Path:
    """Scan an asset root and write a small manifest under build/."""
    root = Path(scan_root)
    if not root.is_dir():
        raise NotADirectoryError(f"scan root not found: {root}")
    resolved_root = root.resolve()

    def _is_inside_scan_root(path: Path) -> bool:
        resolved = path.resolve()
        return resolved == resolved_root or resolved_root in resolved.parents

    out_path = _require_build_path(write_manifest, "--write-manifest")
    models = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in {".pmx", ".pmd"} and _is_inside_scan_root(p)
    )
    motions = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() == ".vmd" and _is_inside_scan_root(p)
    )
    models = models[:max_models]

    # Avoid manifests that spend every case on corrupt/truncated VMD files.
    # This is a parse-only preflight; actual Maya import is still checked by run_manifest().
    from mmd_tools.core.mmd_parser import parse_mmd_file

    valid_motions: list[Path] = []
    skipped_motions: list[str] = []
    if max_motions > 0:
        for motion in motions:
            try:
                parse_mmd_file(str(motion))
            except Exception as exc:  # noqa: BLE001 - manifest generation records local data quality.
                skipped_motions.append(f"{motion}: {type(exc).__name__}: {exc}")
                continue
            valid_motions.append(motion)
            if len(valid_motions) >= max_motions:
                break
    motions = valid_motions

    cases: list[dict[str, Any]] = []
    used_names: set[str] = set()

    def _unique_name(raw: str) -> str:
        base = _case_name(raw)
        if base not in used_names:
            used_names.add(base)
            return base
        suffix = 2
        while f"{base}_{suffix}" in used_names:
            suffix += 1
        name = f"{base}_{suffix}"
        used_names.add(name)
        return name

    if motions:
        for index, model in enumerate(models):
            if len(cases) >= max_cases:
                break
            motion = motions[index % len(motions)]
            cases.append(
                {
                    "name": _unique_name(f"{model.stem}__{motion.stem}"),
                    "model": _rel_or_abs(model),
                    "motion": _rel_or_abs(motion),
                }
            )
    else:
        for model in models[:max_cases]:
            cases.append({"name": _unique_name(model.stem), "model": _rel_or_abs(model)})

    manifest = {
        "version": 1,
        "description": f"Generated from {root.resolve()}",
        "models": [_rel_or_abs(p) for p in models],
        "motions": [_rel_or_abs(p) for p in motions],
        "cases": cases,
        "skipped_motions": skipped_motions[:100],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def load_cases(manifest_path: str | Path) -> list[dict[str, Any]]:
    """Load manifest cases and resolve relative paths from the manifest directory."""
    path = Path(manifest_path)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest_dir = path.parent
    cases = data.get("cases", [])
    if not cases:
        raise ValueError(f"manifest has no cases: {path}")

    resolved_cases: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        model = Path(case["model"])
        if not model.is_absolute():
            model = (manifest_dir / model).resolve()
        motion = case.get("motion")
        motion_path = None
        if motion:
            motion_path = Path(motion)
            if not motion_path.is_absolute():
                motion_path = (manifest_dir / motion_path).resolve()
        resolved_cases.append(
            {
                "name": case.get("name") or f"case_{index:03d}",
                "model": str(model),
                "motion": str(motion_path) if motion_path else None,
            }
        )
    return resolved_cases


def _resolve_actual_png(requested: Path, frame: int) -> Path:
    """Return the actual written PNG path from a playblast call.

    playblast with format=image + compression=png can emit:
      - exact requested name + .png
      - <stem>.<frame>.png (padding varies: 1 or 4 digits common)
      - <stem>.png
    Fall back to the most recent *.png in the output dir.
    """
    requested = requested.resolve()
    out_dir = requested.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = requested.stem
    candidates: list[Path] = [
        requested,
        requested.with_suffix(".png"),
        out_dir / f"{stem}.png",
        out_dir / f"{stem}.{frame:04d}.png",
        out_dir / f"{stem}.{frame:03d}.png",
        out_dir / f"{stem}.{frame:02d}.png",
        out_dir / f"{stem}.{frame}.png",
    ]

    for cand in candidates:
        if cand.exists() and cand.stat().st_size > 0:
            return cand

    png_files = sorted(
        out_dir.glob(f"{stem}*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if png_files:
        return png_files[0]

    return requested.with_suffix(".png")


def _normalize(v: list[float]) -> list[float]:
    """Return unit vector in the same direction."""
    length = math.sqrt(sum(x * x for x in v))
    if length < 1e-12:
        return [1.0, 0.0, 0.0]
    return [x / length for x in v]


def _direction_to_euler_rotation(from_pos: list[float], to_pos: list[float]) -> list[float]:
    """Compute XYZ Euler rotation (degrees) so that -Z points from *from_pos* toward *to_pos*."""
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


def _compute_model_bounds(root_node: str) -> tuple[list[float], float]:
    """Compute world-space bounding box center and diagonal radius of mesh nodes."""
    from maya import cmds

    meshes = cmds.listRelatives(root_node, allDescendents=True, type="mesh")
    if not meshes:
        print("  Warning: no mesh nodes found, using fallback bounds")
        return [0.0, 0.0, 0.0], 5.0

    try:
        bbox = cmds.exactWorldBoundingBox(meshes)
    except Exception as exc:
        print(f"  Warning: exactWorldBoundingBox failed ({exc}), using fallback")
        return [0.0, 0.0, 0.0], 5.0

    center = [
        (bbox[0] + bbox[3]) * 0.5,
        (bbox[1] + bbox[4]) * 0.5,
        (bbox[2] + bbox[5]) * 0.5,
    ]
    radius = math.sqrt(
        (bbox[3] - bbox[0]) ** 2
        + (bbox[4] - bbox[1]) ** 2
        + (bbox[5] - bbox[2]) ** 2
    ) * 0.5
    print(
        f"  Model bounds: center=({center[0]:.3f},{center[1]:.3f},{center[2]:.3f}), "
        f"radius={radius:.3f}"
    )
    return center, radius


def _check_png_not_blank(png_path: Path, threshold: int = 10) -> dict:
    """Verify PNG is not effectively blank (all pixels near zero).

    Uses only stdlib (struct + zlib) to parse the PNG, decompress IDAT
    data, and sample pixel values. Raises RuntimeError if the maximum
    pixel value across any sampled RGB channel is below *threshold*.
    """
    with open(png_path, "rb") as f:
        raw = f.read()

    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a valid PNG file: {png_path}")

    ihdr_len = struct.unpack_from(">I", raw, 8)[0]
    if raw[12:16] != b"IHDR":
        raise ValueError(f"Expected IHDR chunk, got {raw[12:16]!r}")
    ihdr = raw[16 : 16 + ihdr_len]
    img_w = struct.unpack_from(">I", ihdr, 0)[0]
    _ = struct.unpack_from(">I", ihdr, 4)[0]  # img_h (unused)
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
        raise ValueError(f"Unsupported PNG color type: {color_type}")

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
                if val < min_val:
                    min_val = val
                if val > max_val:
                    max_val = val
                total += val
                count += 1
        prev_row = row

    avg = total / count if count else 0.0
    stats = {"min": min_val, "max": max_val, "avg": round(avg, 3), "samples": count}

    if max_val < threshold:
        raise RuntimeError(
            f"Captured PNG is effectively blank (max pixel={max_val} < {threshold}): "
            f"min={min_val} avg={avg:.2f} samples={count}."
        )
    return stats


def _capture_case(
    root_node: str,
    captures_dir: Path,
    case_name: str,
    frame: int,
    width: int,
    height: int,
    fov_deg: float,
    verbose: bool = True,
) -> dict:
    """Capture one frame of the imported model via playblast.

    Computes the model bounding box, positions the camera to fill ~70% of
    the viewport, sets up a directional light, and runs offscreen playblast.
    Returns a dict with keys: capture_path, error (or None), png_stats (or None).
    """
    from maya import cmds

    result: dict = {"capture_path": None, "error": None, "png_stats": None}

    try:
        captures_dir.mkdir(parents=True, exist_ok=True)
        model_center, model_radius = _compute_model_bounds(root_node)

        # --- Camera ---
        aspect_ratio = width / height
        tan_half_fov = math.tan(math.radians(fov_deg) * 0.5)
        d_h = model_radius / (0.7 * tan_half_fov) if tan_half_fov > 1e-9 else model_radius * 3.0
        d_v = model_radius / (0.7 * tan_half_fov / aspect_ratio) if tan_half_fov > 1e-9 else model_radius * 3.0
        camera_distance = max(d_h, d_v, model_radius * 2.0, 5.0)

        view_dir = _normalize([0.4, 0.2, 0.9])
        cam_pos = [
            model_center[0] + view_dir[0] * camera_distance,
            model_center[1] + view_dir[1] * camera_distance,
            model_center[2] + view_dir[2] * camera_distance,
        ]

        persp_shape = cmds.listRelatives("persp", shapes=True)
        if persp_shape:
            persp_shape = persp_shape[0]
            cmds.setAttr("persp.translateX", cam_pos[0])
            cmds.setAttr("persp.translateY", cam_pos[1])
            cmds.setAttr("persp.translateZ", cam_pos[2])
            euler = _direction_to_euler_rotation(cam_pos, model_center)
            cmds.setAttr("persp.rotateX", euler[0])
            cmds.setAttr("persp.rotateY", euler[1])
            cmds.setAttr("persp.rotateZ", euler[2])
            focal_length = 18.0 / tan_half_fov if tan_half_fov > 1e-9 else 35.0
            cmds.setAttr(f"{persp_shape}.focalLength", focal_length)
            clip_near = max(0.01, camera_distance * 0.01)
            clip_far = camera_distance + model_radius * 4.0 + 100.0
            cmds.setAttr(f"{persp_shape}.nearClipPlane", clip_near)
            cmds.setAttr(f"{persp_shape}.farClipPlane", clip_far)

            if verbose:
                print(
                    f"  Camera: pos=({cam_pos[0]:.2f},{cam_pos[1]:.2f},{cam_pos[2]:.2f}), "
                    f"distance={camera_distance:.2f}"
                )

        # --- Light ---
        light_shape = cmds.directionalLight(name="captureLight", intensity=1.0, rgb=[1, 1, 1])
        light_xform = cmds.listRelatives(light_shape, parent=True)[0]
        light_dir = [0.5, -1.0, 0.5]
        light_pos = [
            model_center[0] + light_dir[0],
            model_center[1] + light_dir[1],
            model_center[2] + light_dir[2],
        ]
        light_euler = _direction_to_euler_rotation(light_pos, model_center)
        cmds.setAttr(f"{light_xform}.rotateX", light_euler[0])
        cmds.setAttr(f"{light_xform}.rotateY", light_euler[1])
        cmds.setAttr(f"{light_xform}.rotateZ", light_euler[2])

        # --- Basic lambert fallback for meshes so they are visible ---
        meshes = cmds.listRelatives(root_node, allDescendents=True, type="mesh")
        if meshes:
            debug_shader = cmds.shadingNode("lambert", asShader=True, name="captureDebug_lambert")
            cmds.setAttr(f"{debug_shader}.color", 0.6, 0.7, 0.9, type="double3")
            cmds.setAttr(f"{debug_shader}.diffuse", 0.8)
            sgs_seen: set[str] = set()
            for m in meshes:
                cmds.setAttr(f"{m}.displayColors", 0)
                if cmds.getAttr(f"{m}.intermediateObject"):
                    continue
                conn_sgs = cmds.listConnections(m, type="shadingEngine") or []
                for sg in conn_sgs:
                    if sg in sgs_seen:
                        continue
                    sgs_seen.add(sg)
                    existing = cmds.listConnections(
                        sg + ".surfaceShader", source=True, destination=False
                    ) or []
                    for e in existing:
                        try:
                            cmds.disconnectAttr(e + ".outColor", sg + ".surfaceShader")
                        except Exception:
                            pass
                    cmds.connectAttr(f"{debug_shader}.outColor", f"{sg}.surfaceShader", force=True)
            if verbose:
                print(f"  Assigned debug lambert to {len(sgs_seen)} shadingEngine(s)")

        # --- Frame and capture ---
        cmds.currentTime(frame)
        try:
            cmds.playbackOptions(minTime=frame, maxTime=frame)
        except Exception:
            pass

        out_png = captures_dir / f"{_case_name(case_name)}.png"
        for old_png in captures_dir.glob(f"{_case_name(case_name)}*.png"):
            try:
                old_png.unlink()
            except Exception:
                pass

        playblast_result = cmds.playblast(
            filename=str(out_png.with_suffix("")),
            frame=frame,
            format="image",
            compression="png",
            offScreen=True,
            offScreenViewportUpdate=True,
            viewer=False,
            width=width,
            height=height,
            forceOverwrite=True,
            showOrnaments=False,
            percent=100,
        )
        if verbose:
            print(f"  playblast returned: {playblast_result!r}")

        actual = _resolve_actual_png(out_png, frame)
        if not actual.exists() or actual.stat().st_size <= 0:
            raise RuntimeError(f"Capture PNG not produced or empty: {actual}")

        png_stats = _check_png_not_blank(actual)
        if verbose:
            print(
                f"  Capture OK: {actual} ({actual.stat().st_size} bytes, "
                f"{width}x{height}, pixel max={png_stats['max']})"
            )

        result["capture_path"] = str(actual)
        result["png_stats"] = png_stats

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        if verbose:
            print(f"  Capture failed: {result['error']}")

    return result


def _initialize_maya() -> None:
    """Initialize Maya standalone when needed."""
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass


def _collect_audit(
    root_node: str,
    use_shader: bool,
    shader_backend: str = "auto",
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Inspect Maya scene for texture and shader diagnostics.

    Returns an audit dict:
      missing_textures: list of dicts {file_node, texture_path, exists}
      source_file: dict describing the root mmd_source_file attr
      scene_health: compact scene-level sanity checks
      suspicious_string_attrs: non-fatal list of MMD string attrs containing '?'
      shader_errors: list of error strings
      shader_summary: brief string describing what was found
    """
    from maya import cmds

    def _ls_registered_type(node_type: str) -> list[str]:
        try:
            if node_type not in (cmds.allNodeTypes() or []):
                return []
        except Exception:
            return []
        return cmds.ls(type=node_type) or []

    missing_textures: list[dict] = []
    suspicious_string_attrs: list[dict] = []
    shader_errors: list[str] = []
    backend = (shader_backend or "auto").lower()
    dx11_count = 0
    glsl_count = 0

    texture_base_dir = Path(model_dir).resolve() if model_dir else None

    def _path_exists(value: str) -> bool:
        if not value or not value.strip():
            return False
        candidate = Path(value.strip())
        if candidate.exists():
            return True
        if texture_base_dir and not candidate.is_absolute():
            return (texture_base_dir / candidate).exists()
        return False

    def _audit_path_attr(node: str, attr_name: str, source: str) -> dict[str, Any] | None:
        if not cmds.objExists(node) or not cmds.attributeQuery(attr_name, node=node, exists=True):
            return None
        try:
            value = cmds.getAttr(f"{node}.{attr_name}") or ""
        except Exception:
            return None
        item = {
            "source": source,
            "node": node,
            "attr": attr_name,
            "path": value,
            "exists": _path_exists(value),
        }
        if value and not item["exists"]:
            missing_textures.append(
                {
                    "source": source,
                    "node": node,
                    "attr": attr_name,
                    "texture_path": value,
                    "exists": False,
                }
            )
        return item

    # --- Root source path audit ---
    source_file = _audit_path_attr(root_node, "mmd_source_file", "root_attr")

    # --- File node texture audit ---
    file_nodes = cmds.ls(type="file") or []
    for fn in file_nodes:
        try:
            val = cmds.getAttr(fn + ".fileTextureName")
        except Exception:
            continue
        if val and val.strip():
            tex_path = Path(val.strip())
            if not tex_path.exists():
                missing_textures.append(
                    {
                        "source": "file_node",
                        "file_node": fn,
                        "texture_path": str(tex_path),
                        "exists": False,
                    }
                )

    # --- MMD path custom attributes on materials/shaders ---
    for attr_name in ("mmd_texture_path", "mmd_sphere_path"):
        for node in cmds.ls(f"*.{attr_name}", objectsOnly=True) or []:
            _audit_path_attr(node, attr_name, "shader_attr")

    # --- Scene health audit ---
    mesh_shapes = cmds.listRelatives(root_node, allDescendents=True, type="mesh", fullPath=True) or []
    visible_mesh_shapes: list[str] = []
    intermediate_mesh_shapes: list[str] = []
    empty_meshes: list[dict[str, Any]] = []
    meshes_without_uvs: list[str] = []
    for shape in mesh_shapes:
        try:
            is_intermediate = bool(cmds.getAttr(f"{shape}.intermediateObject"))
        except Exception:
            is_intermediate = False
        if is_intermediate:
            intermediate_mesh_shapes.append(shape)
            continue
        visible_mesh_shapes.append(shape)
        try:
            vertex_count = int(cmds.polyEvaluate(shape, vertex=True) or 0)
            face_count = int(cmds.polyEvaluate(shape, face=True) or 0)
        except Exception:
            vertex_count = 0
            face_count = 0
        if vertex_count <= 0 or face_count <= 0:
            empty_meshes.append(
                {"mesh": shape, "vertices": vertex_count, "faces": face_count}
            )
        try:
            uv_names = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        except Exception:
            uv_names = []
        if not uv_names:
            meshes_without_uvs.append(shape)

    scene_health = {
        "root_exists": bool(root_node and cmds.objExists(root_node)),
        "node_count": len(cmds.ls() or []),
        "visible_mesh_shape_count": len(visible_mesh_shapes),
        "intermediate_mesh_shape_count": len(intermediate_mesh_shapes),
        "empty_meshes": empty_meshes,
        "meshes_without_uvs": meshes_without_uvs,
        "joint_count": len(cmds.ls(type="joint") or []),
        "skin_cluster_count": len(cmds.ls(type="skinCluster") or []),
        "blend_shape_count": len(cmds.ls(type="blendShape") or []),
        "file_node_count": len(file_nodes),
    }

    # --- Non-fatal scan for string attrs that look CP932-replaced ---
    for attr_name in (
        "mmd_model_name",
        "mmd_model_name_en",
        "mmd_material_name",
        "mmd_material_name_en",
        "mmd_bone_name",
        "mmd_bone_name_en",
        "mmd_comment",
        "mmd_comment_en",
    ):
        for node in cmds.ls(f"*.{attr_name}", objectsOnly=True) or []:
            try:
                value = cmds.getAttr(f"{node}.{attr_name}") or ""
            except Exception:
                continue
            if isinstance(value, str) and ("?" in value or "\ufffd" in value):
                suspicious_string_attrs.append(
                    {"node": node, "attr": attr_name, "value": value}
                )

    # --- Shader node audit ---
    dx11_nodes = _ls_registered_type("dx11Shader")
    glsl_nodes = _ls_registered_type("GLSLShader")
    dx11_count = len(dx11_nodes)
    glsl_count = len(glsl_nodes)

    if use_shader and backend not in {"standard"}:
        has_dx11 = dx11_count > 0
        has_glsl = glsl_count > 0
        if backend == "glsl":
            if not has_glsl:
                shader_errors.append("shader requested but no GLSLShader node was created")
        elif backend == "dx11":
            if not has_dx11:
                shader_errors.append("shader requested but no dx11Shader node was created")
        else:  # auto
            if not (has_dx11 or has_glsl):
                shader_errors.append("shader requested but neither dx11Shader nor GLSLShader node was created")

    parts: list[str] = []
    if dx11_count:
        parts.append(f"{dx11_count} dx11Shader")
    if glsl_count:
        parts.append(f"{glsl_count} GLSLShader")
    if not parts:
        parts.append("no shader nodes")
    shader_summary = ", ".join(parts)

    return {
        "missing_textures": missing_textures,
        "source_file": source_file,
        "scene_health": scene_health,
        "suspicious_string_attrs": suspicious_string_attrs,
        "shader_errors": shader_errors,
        "shader_summary": shader_summary,
        "log_warnings": [],
        "log_errors": [],
        "warning_count": 0,
        "error_count": 0,
    }


def _apply_log_audit(audit: dict[str, Any], records: list[dict[str, str]], use_shader: bool) -> None:
    """Attach warning/error log summaries to audit and classify shader/texture issues."""
    warnings = [r for r in records if r["level"] == "WARNING"]
    errors = [r for r in records if r["level"] in {"ERROR", "CRITICAL"}]
    audit["log_warnings"] = warnings
    audit["log_errors"] = errors
    audit["warning_count"] = len(warnings)
    audit["error_count"] = len(errors)

    for record in warnings + errors:
        message = record["message"]
        lower = message.lower()
        if "texture" in lower and ("not found" in lower or "missing" in lower):
            audit.setdefault("missing_texture_messages", []).append(message)
        if use_shader and (
            "dx11shader" in lower
            or "shader" in lower
            or "failed to set attribute value" in lower
        ):
            if message not in audit["shader_errors"]:
                audit["shader_errors"].append(message)


def _prepare_shader_mode(use_shader: bool, shader_backend: str = "auto") -> list[str]:
    """Apply shader mode settings and return non-fatal shader setup errors."""
    from maya import cmds
    from mmd_tools.core import settings

    settings.set("import.model.create_mmd_shaders", bool(use_shader))
    backend = (shader_backend or "auto").lower()
    if backend not in {"auto", "dx11", "glsl", "standard"}:
        backend = "auto"
    settings.set("import.model.mmd_shader_backend", backend)

    if not use_shader:
        return []

    if backend == "standard":
        return []

    errors: list[str] = []

    def _probe_node(node_type: str, expect_attr: str) -> tuple[bool, str | None]:
        probe = None
        try:
            probe = cmds.shadingNode(node_type, asShader=True, name=f"_track6_{node_type.lower()}_probe")
            if not cmds.attributeQuery(expect_attr, node=probe, exists=True):
                return False, f"{node_type} probe node has no {expect_attr} attribute"
            return True, None
        except Exception as exc:
            return False, f"{node_type} probe failed: {type(exc).__name__}: {exc}"
        finally:
            if probe and cmds.objExists(probe):
                try:
                    cmds.delete(probe)
                except Exception:
                    pass

    # dx11 (existing stable path)
    if backend in {"auto", "dx11"}:
        try:
            cmds.loadPlugin("dx11Shader", quiet=True)
        except Exception as exc:
            errors.append(f"dx11Shader plugin load failed: {type(exc).__name__}: {exc}")
        else:
            ok, message = _probe_node("dx11Shader", "outColor")
            if ok:
                return errors
            if message:
                errors.append(message)

    # glsl fallback path
    if backend in {"auto", "glsl"}:
        try:
            cmds.loadPlugin("glslShader", quiet=True)
        except Exception as exc:
            if backend == "glsl":
                errors.append(f"glslShader plugin load failed: {type(exc).__name__}: {exc}")
            else:
                errors.append(f"glslShader plugin load failed (fallback check): {type(exc).__name__}: {exc}")
            return errors

        ok, message = _probe_node("GLSLShader", "outColor")
        if ok:
            return errors
        if message:
            errors.append(message)

    return errors


def _collect_profile(
    model_root: str,
    separate_meshes: bool,
    import_elapsed_sec: float,
    parsed_data: Any,
    importer_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect compact profile metrics after a successful model import."""
    from maya import cmds

    mesh_shapes = cmds.listRelatives(model_root, allDescendents=True, type="mesh", fullPath=True) or []
    visible_mesh_shapes = []
    intermediate_mesh_shapes = []
    mesh_transforms = set()
    for shape in mesh_shapes:
        try:
            is_intermediate = bool(cmds.getAttr(f"{shape}.intermediateObject"))
        except Exception:
            is_intermediate = False
        if is_intermediate:
            intermediate_mesh_shapes.append(shape)
            continue
        visible_mesh_shapes.append(shape)
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parents:
            mesh_transforms.add(parents[0])

    material_count_from_parse: int | None = None
    if parsed_data is not None:
        mats = getattr(parsed_data, "materials", None)
        if mats is not None:
            material_count_from_parse = len(mats)

    skin_cluster_count = len(cmds.ls(type="skinCluster") or [])
    blend_shape_count = len(cmds.ls(type="blendShape") or [])

    profile = {
        "separate_meshes_by_material": separate_meshes,
        "import_elapsed_sec": round(import_elapsed_sec, 3),
        "mesh_transform_count": len(mesh_transforms),
        "mesh_shape_count": len(visible_mesh_shapes),
        "intermediate_mesh_shape_count": len(intermediate_mesh_shapes),
        "material_count_from_parse": material_count_from_parse,
        "skin_cluster_count": skin_cluster_count,
        "blend_shape_count": blend_shape_count,
    }
    if importer_profile:
        profile["importer"] = importer_profile
    return profile


def _is_cp932_lossy(text: str) -> bool:
    """Return True when *text* cannot roundtrip through Windows Japanese ACP."""
    if not isinstance(text, str):
        return False
    encoded = text.encode("cp932", errors="replace")
    return encoded.decode("cp932", errors="replace") != text


def _collect_preflight(path: str, parsed_data: Any | None = None) -> dict[str, Any]:
    """Collect model-file facts before Maya scene creation/import."""
    model_path = Path(path)
    preflight: dict[str, Any] = {
        "path": str(model_path),
        "exists": model_path.is_file(),
        "suffix": model_path.suffix.lower(),
        "size_bytes": model_path.stat().st_size if model_path.is_file() else None,
        "path_cp932_lossy": _is_cp932_lossy(str(model_path)),
    }
    if parsed_data is None:
        return preflight

    textures = list(getattr(parsed_data, "textures", []) or [])
    texture_entries = []
    for index, texture in enumerate(textures):
        texture_entries.append(
            {
                "index": index,
                "path": texture,
                "cp932_lossy": _is_cp932_lossy(texture),
            }
        )

    faces = getattr(parsed_data, "faces", []) or []
    vertices = getattr(parsed_data, "vertices", []) or []
    materials = getattr(parsed_data, "materials", []) or []
    bones = getattr(parsed_data, "bones", []) or []
    morphs = getattr(parsed_data, "morphs", []) or []
    preflight.update(
        {
            "parsed_type": type(parsed_data).__name__,
            "vertex_count": len(vertices),
            "face_count": len(faces),
            "material_count": len(materials),
            "bone_count": len(bones),
            "morph_count": len(morphs),
            "texture_count": len(textures),
            "texture_cp932_lossy_count": sum(1 for item in texture_entries if item["cp932_lossy"]),
            "textures": texture_entries[:200],
        }
    )
    return preflight


def run_case(
    case: dict[str, Any],
    out_dir: Path,
    save_scenes: bool,
    capture: bool = False,
    capture_width: int = 640,
    capture_height: int = 480,
    capture_frame: int = 0,
    capture_fov: float = 25.0,
    use_shader: bool = False,
    shader_backend: str = "auto",
    separate_meshes: bool | None = None,
) -> dict[str, Any]:
    """Import one model plus optional motion and return a serializable result.

    When capture=True, also renders one offscreen frame to PNG.
    """
    from maya import cmds
    from mmd_tools.core import settings as mmd_settings
    from mmd_tools.core.mmd_parser import parse_mmd_file
    from mmd_tools.io.mmd_importer import import_mmd_file

    start = time.perf_counter()
    name = str(case["name"])
    model_path = str(case["model"])
    motion_path = case.get("motion")
    scene_path = None
    status = "passed"
    error = None
    traceback_text = None
    capture_result = None
    profile: dict[str, Any] | None = None
    preflight: dict[str, Any] | None = None
    audit: dict[str, Any] = {
        "missing_textures": [],
        "shader_errors": [],
        "shader_summary": "no shader nodes",
        "log_warnings": [],
        "log_errors": [],
        "warning_count": 0,
        "error_count": 0,
    }
    log_records: list[dict[str, str]] = []
    log_handler = _CaseLogHandler(log_records)
    logging.getLogger("mmd_tools").addHandler(log_handler)
    previous_separate_meshes = None
    changed_separate_meshes = False

    try:
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"model not found: {model_path}")
        if motion_path and not Path(str(motion_path)).is_file():
            raise FileNotFoundError(f"motion not found: {motion_path}")

        preflight = _collect_preflight(model_path)
        cmds.file(new=True, force=True)
        shader_setup_errors = _prepare_shader_mode(use_shader, shader_backend)
        previous_separate_meshes = mmd_settings.get("import.model.separate_meshes_by_material", False)
        if separate_meshes is not None:
            mmd_settings.set("import.model.separate_meshes_by_material", separate_meshes)
            changed_separate_meshes = True
        effective_separate_meshes = mmd_settings.get("import.model.separate_meshes_by_material", False)
        parsed_data = parse_mmd_file(model_path)
        preflight = _collect_preflight(model_path, parsed_data)
        import_start = time.perf_counter()
        importer_profile: dict[str, Any] = {}
        model_root = import_mmd_file(
            model_path,
            options={
                "create_mmd_shaders": use_shader,
                "import_physics": False,
                "profile": importer_profile,
            },
        )
        import_elapsed = time.perf_counter() - import_start
        if not model_root:
            raise RuntimeError("model import returned no root node")

        profile = _collect_profile(
            model_root,
            bool(effective_separate_meshes),
            import_elapsed,
            parsed_data,
            importer_profile,
        )

        if motion_path:
            parse_mmd_file(str(motion_path))
            ok = import_mmd_file(
                str(motion_path),
                options={
                    "target_model": model_root,
                    "pmx_path": model_path,
                    "vmd_fps": 30,
                },
            )
            if not ok:
                raise RuntimeError("motion import returned false")

        audit = _collect_audit(model_root, use_shader, shader_backend, Path(model_path).parent)
        audit["shader_errors"].extend(shader_setup_errors)

        # Capture before saving scene (capture modifies the scene with cam/light).
        if capture:
            captures_dir = out_dir / "captures"
            capture_result = _capture_case(
                root_node=model_root,
                captures_dir=captures_dir,
                case_name=name,
                frame=capture_frame,
                width=capture_width,
                height=capture_height,
                fov_deg=capture_fov,
            )
            if capture_result["error"]:
                raise RuntimeError(f"capture failed: {capture_result['error']}")

        if save_scenes:
            scenes_dir = out_dir / "scenes"
            scenes_dir.mkdir(parents=True, exist_ok=True)
            scene_path = scenes_dir / f"{_case_name(name)}.ma"
            cmds.file(rename=str(scene_path))
            cmds.file(save=True, type="mayaAscii")
    except Exception as exc:  # noqa: BLE001 - result JSON must capture all case failures.
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        traceback_text = traceback.format_exc()
    finally:
        logging.getLogger("mmd_tools").removeHandler(log_handler)
        if changed_separate_meshes:
            try:
                mmd_settings.set("import.model.separate_meshes_by_material", previous_separate_meshes)
            except Exception:
                pass
        _apply_log_audit(audit, log_records, use_shader)

    return {
        "name": name,
        "model": model_path,
        "motion": motion_path,
        "status": status,
        "error": error,
        "traceback": traceback_text,
        "scene_path": str(scene_path) if scene_path else None,
        "capture_path": capture_result["capture_path"] if capture_result else None,
        "capture_error": capture_result["error"] if capture_result else None,
        "capture_png_stats": capture_result["png_stats"] if capture_result else None,
        "preflight": preflight,
        "audit": audit,
        "profile": profile,
        "elapsed_sec": round(time.perf_counter() - start, 3),
    }


def _count_results_with(results: list[dict[str, Any]], predicate: Any) -> int:
    """Return how many case results match *predicate*."""
    return sum(1 for result in results if predicate(result))


def _sum_result_values(results: list[dict[str, Any]], getter: Any) -> int:
    """Return the integer sum of *getter(result)* across case results."""
    return sum(int(getter(result) or 0) for result in results)


def _collect_diagnostic_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate broad import diagnostics for quick result triage."""

    def _audit(result: dict[str, Any]) -> dict[str, Any]:
        return result.get("audit") or {}

    def _preflight(result: dict[str, Any]) -> dict[str, Any]:
        return result.get("preflight") or {}

    def _scene_health(result: dict[str, Any]) -> dict[str, Any]:
        return _audit(result).get("scene_health") or {}

    return {
        "preflight": {
            "missing_model_cases": _count_results_with(
                results, lambda r: _preflight(r).get("exists") is False
            ),
            "path_cp932_lossy_cases": _count_results_with(
                results, lambda r: bool(_preflight(r).get("path_cp932_lossy"))
            ),
            "texture_cp932_lossy_cases": _count_results_with(
                results, lambda r: int(_preflight(r).get("texture_cp932_lossy_count") or 0) > 0
            ),
            "texture_cp932_lossy_count": _sum_result_values(
                results, lambda r: _preflight(r).get("texture_cp932_lossy_count")
            ),
            "zero_vertex_cases": _count_results_with(
                results,
                lambda r: "vertex_count" in _preflight(r)
                and int(_preflight(r).get("vertex_count") or 0) <= 0,
            ),
            "zero_material_cases": _count_results_with(
                results,
                lambda r: "material_count" in _preflight(r)
                and int(_preflight(r).get("material_count") or 0) <= 0,
            ),
        },
        "scene": {
            "root_missing_cases": _count_results_with(
                results, lambda r: _scene_health(r).get("root_exists") is False
            ),
            "no_visible_mesh_cases": _count_results_with(
                results,
                lambda r: "visible_mesh_shape_count" in _scene_health(r)
                and int(_scene_health(r).get("visible_mesh_shape_count") or 0) <= 0,
            ),
            "empty_mesh_cases": _count_results_with(
                results, lambda r: bool(_scene_health(r).get("empty_meshes"))
            ),
            "empty_mesh_count": _sum_result_values(
                results, lambda r: len(_scene_health(r).get("empty_meshes") or [])
            ),
            "mesh_without_uv_cases": _count_results_with(
                results, lambda r: bool(_scene_health(r).get("meshes_without_uvs"))
            ),
            "mesh_without_uv_count": _sum_result_values(
                results, lambda r: len(_scene_health(r).get("meshes_without_uvs") or [])
            ),
            "no_skin_cluster_cases": _count_results_with(
                results,
                lambda r: "skin_cluster_count" in _scene_health(r)
                and int(_scene_health(r).get("skin_cluster_count") or 0) <= 0,
            ),
        },
        "paths": {
            "missing_texture_cases": _count_results_with(
                results, lambda r: bool(_audit(r).get("missing_textures"))
            ),
            "missing_texture_count": _sum_result_values(
                results, lambda r: len(_audit(r).get("missing_textures") or [])
            ),
            "source_file_missing_cases": _count_results_with(
                results,
                lambda r: (_audit(r).get("source_file") or {}).get("exists") is False,
            ),
        },
        "strings": {
            "suspicious_string_attr_cases": _count_results_with(
                results, lambda r: bool(_audit(r).get("suspicious_string_attrs"))
            ),
            "suspicious_string_attr_count": _sum_result_values(
                results, lambda r: len(_audit(r).get("suspicious_string_attrs") or [])
            ),
        },
        "logs": {
            "warning_cases": _count_results_with(
                results, lambda r: int(_audit(r).get("warning_count") or 0) > 0
            ),
            "warning_count": _sum_result_values(
                results, lambda r: _audit(r).get("warning_count")
            ),
            "error_log_cases": _count_results_with(
                results, lambda r: int(_audit(r).get("error_count") or 0) > 0
            ),
            "error_log_count": _sum_result_values(
                results, lambda r: _audit(r).get("error_count")
            ),
        },
        "shader": {
            "shader_error_cases": _count_results_with(
                results, lambda r: bool(_audit(r).get("shader_errors"))
            ),
            "shader_error_count": _sum_result_values(
                results, lambda r: len(_audit(r).get("shader_errors") or [])
            ),
        },
    }


def run_manifest(
    manifest_path: str | Path,
    out_dir: str | Path,
    case_filter: str | None,
    limit: int | None,
    save_scenes: bool,
    capture: bool = False,
    capture_width: int = 640,
    capture_height: int = 480,
    capture_frame: int = 0,
    capture_fov: float = 25.0,
    use_shader: bool = False,
    shader_backend: str = "auto",
    separate_meshes: bool | None = None,
) -> int:
    """Run manifest cases in Maya standalone and write result JSON."""
    out_path = _require_build_path(out_dir, "--out-dir")
    out_path.mkdir(parents=True, exist_ok=True)
    cases = load_cases(manifest_path)
    if case_filter:
        cases = [case for case in cases if case_filter.lower() in case["name"].lower()]
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        raise ValueError("no cases selected")

    _initialize_maya()
    results = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['name']}", flush=True)
        result = run_case(
            case,
            out_path,
            save_scenes,
            capture,
            capture_width,
            capture_height,
            capture_frame,
            capture_fov,
            use_shader=use_shader,
            shader_backend=shader_backend,
            separate_meshes=separate_meshes,
        )
        print(f"  {result['status']} ({result['elapsed_sec']}s)", flush=True)
        results.append(result)

    diagnostic_summary = _collect_diagnostic_summary(results)
    path_summary = diagnostic_summary["paths"]
    shader_summary = diagnostic_summary["shader"]
    log_summary = diagnostic_summary["logs"]

    result_doc = {
        "manifest": str(Path(manifest_path).resolve()),
        "out_dir": str(out_path),
        "total": len(results),
        "passed": sum(1 for result in results if result["status"] == "passed"),
        "failed": sum(1 for result in results if result["status"] == "failed"),
        "captured": sum(1 for result in results if result.get("capture_path")),
        "capture_errors": sum(1 for result in results if result.get("capture_error")),
        "missing_texture_cases": path_summary["missing_texture_cases"],
        "missing_texture_count": path_summary["missing_texture_count"],
        "shader_error_cases": shader_summary["shader_error_cases"],
        "shader_error_count": shader_summary["shader_error_count"],
        "warning_cases": log_summary["warning_cases"],
        "warning_count": log_summary["warning_count"],
        "error_log_cases": log_summary["error_log_cases"],
        "error_log_count": log_summary["error_log_count"],
        "diagnostic_summary": diagnostic_summary,
        "results": results,
    }
    result_file = out_path / "results.json"
    result_file.write_text(json.dumps(result_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"results: {result_file}", flush=True)
    return 1 if result_doc["failed"] else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-root", default=None, help="Asset root to scan for PMX/PMD/VMD files.")
    parser.add_argument("--write-manifest", default=None, help="Write generated manifest under build/.")
    parser.add_argument("--max-models", type=int, default=20)
    parser.add_argument("--max-motions", type=int, default=20)
    parser.add_argument("--max-cases", type=int, default=20)
    parser.add_argument("--manifest", default=None, help="Manifest JSON to run.")
    parser.add_argument("--case", default=None, help="Run only cases whose name contains this string.")
    parser.add_argument("--limit", type=int, default=None, help="Limit selected case count.")
    parser.add_argument("--out-dir", default="build/batch-import", help="Result directory under build/.")
    parser.add_argument("--capture", action="store_true", help="Capture one offscreen PNG per case.")
    parser.add_argument("--capture-width", type=int, default=640, help="Capture width (default: 640).")
    parser.add_argument("--capture-height", type=int, default=480, help="Capture height (default: 480).")
    parser.add_argument("--capture-frame", type=int, default=0, help="Frame number to capture (default: 0).")
    parser.add_argument("--capture-fov", type=float, default=25.0, help="Camera FOV in degrees (default: 25.0).")
    parser.add_argument("--save-scenes", action="store_true", help="Save .ma scenes under --out-dir/scenes.")
    parser.add_argument(
        "--shader-backend",
        default="auto",
        choices=["auto", "dx11", "glsl", "standard"],
        help="Shader backend used when --shader is enabled (default: auto).",
    )
    shader_group = parser.add_mutually_exclusive_group()
    shader_group.add_argument("--shader", action="store_true", dest="use_shader", help="Enable MMD shader creation during model import (default: no shader).")
    shader_group.add_argument("--no-shader", action="store_false", dest="use_shader", help="Disable MMD shader creation (default).")
    parser.set_defaults(use_shader=False)
    split_group = parser.add_mutually_exclusive_group()
    split_group.add_argument(
        "--separate-meshes",
        action="store_true",
        dest="separate_meshes",
        help="Enable import.model.separate_meshes_by_material; records per-case profile metrics.",
    )
    split_group.add_argument(
        "--no-separate-meshes",
        action="store_false",
        dest="separate_meshes",
        help="Disable separate_meshes_by_material for this run.",
    )
    parser.set_defaults(separate_meshes=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    if args.scan_root or args.write_manifest:
        if not args.scan_root or not args.write_manifest:
            raise ValueError("--scan-root and --write-manifest must be specified together")
        manifest_path = generate_manifest(
            args.scan_root,
            args.write_manifest,
            args.max_models,
            args.max_motions,
            args.max_cases,
        )
        print(f"manifest: {manifest_path}", flush=True)
        if not args.manifest:
            return 0

    if not args.manifest:
        raise ValueError("--manifest is required unless only generating a manifest")
    return run_manifest(
        args.manifest,
        args.out_dir,
        args.case,
        args.limit,
        args.save_scenes,
        capture=args.capture,
        capture_width=args.capture_width,
        capture_height=args.capture_height,
        capture_frame=args.capture_frame,
        capture_fov=args.capture_fov,
        use_shader=args.use_shader,
        shader_backend=args.shader_backend,
        separate_meshes=args.separate_meshes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
