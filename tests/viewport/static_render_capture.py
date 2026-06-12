"""Static render capture: import PMX fixture and capture with fixed camera/light.

This script is a report-only capture that:
- Runs under mayapy (standalone, no GUI required)
- Initializes Maya standalone
- Imports a PMX model via mmd_tools.io.mmd_importer.import_mmd_file()
- Computes the model's world-space bounding box and frames the camera
  to show the entire model with margin (~70% fill)
- Sets up a fixed camera and directional light using GoldenOracle-derived defaults
  (FOV 25°, light direction [0.5,-1,0.5], white light)
- Leaves the mayapy batch-mode viewport background unchanged
- Captures one frame to PNG via cmds.playblast with offScreen=True
- Detects the actual output file (handles frame-padded names)
- Verifies the PNG exists, has non-zero size, and is NOT near-black
  (parses PNG with stdlib struct+zlib, checks pixel max >= 10)

Bounding-box-based camera framing:
- Camera target: bounding box center of all mesh nodes under the imported root
- Camera position: center + view_direction * distance, where distance is
  computed from the diagonal radius and the camera FOV so the model fills
  roughly 70% of the viewport (whichever dimension is tighter).
- Near/far clipping adjusted dynamically.

This is a GoldenOracle-style fixed-camera capture baseline, NOT an image-comparison gate.
No FLIP or pixel-diff comparison is performed.

Color management:
Before capture, the script explicitly sets View Transform, Display, and
Rendering Space via cmds.colorManagementPrefs().  The defaults are:
  --view-transform 'Un-tone-mapped (sRGB)'
  --display 'sRGB'
  --rendering-space 'ACEScg'

These are validated against the available lists queried from Maya before
setting.  If a requested value is unavailable, a RuntimeError is raised
with the full list of available values logged in the error message.

Launched by the `maya_static_render` Nox session (or directly via mayapy ...).

Usage (direct):
    mayapy tests/viewport/static_render_capture.py --out build/captures/static_render_1bone_cube.png --model tests/data/for_unit_test/test_1bone_cube.pmx --frame 0 --width 1024 --height 1024
    mayapy tests/viewport/static_render_capture.py --out out.png --model model.pmx --shader --view-transform "ACES 1.0 SDR-video (sRGB)" --display sRGB --rendering-space ACEScg
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import zlib
from pathlib import Path

import maya.cmds as cmds
import maya.standalone

# GoldenOracle static-render defaults (from manifests/static-render.json)
# Camera target/position are computed from the model's bounding box at runtime;
# only the FOV and light direction/color are kept as constants.
GOLDEN_CAMERA_FOV = 25  # horizontal field of view in degrees
GOLDEN_LIGHT_DIRECTION = [0.5, -1, 0.5]  # direction from light toward scene
GOLDEN_LIGHT_COLOR = [1, 1, 1]
# View direction for the camera (world-space unit vector from camera to target).
# GoldenOracle-like: slightly above and to the right of the model front.
CAMERA_VIEW_DIR = [0.4, 0.2, 0.9]  # X=right, Y=up, Z=toward viewer (Maya -Z = into screen)


def _parse_args() -> argparse.Namespace:
    """Parse command line options for the static render capture."""
    parser = argparse.ArgumentParser(
        description="Static render capture of a PMX fixture with fixed camera/light."
    )
    parser.add_argument(
        "--out",
        default="build/captures/static_render_1bone_cube.png",
        help="Output PNG path. Default: build/captures/static_render_1bone_cube.png",
    )
    parser.add_argument(
        "--model",
        default="tests/data/for_unit_test/test_1bone_cube.pmx",
        help="PMX model file to import. Default: tests/data/for_unit_test/test_1bone_cube.pmx",
    )
    parser.add_argument(
        "--frame",
        type=int,
        default=0,
        help="Frame number to capture. Default: 0",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1024,
        help="Capture width in pixels. Default: 1024",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=1024,
        help="Capture height in pixels. Default: 1024",
    )
    parser.add_argument(
        "--shader",
        action="store_true",
        dest="shader",
        default=False,
        help="Enable MMD dx11Shader assignment via PMX importer (default: disabled)",
    )
    parser.add_argument(
        "--no-shader",
        action="store_false",
        dest="shader",
        help="Disable MMD dx11Shader assignment, use basic lambert fallback",
    )
    parser.add_argument(
        "--shader-backend",
        default="auto",
        choices=["auto", "dx11", "glsl", "standard"],
        help="Shader backend for MMD material import when --shader is enabled. "
        "Allowed: auto (default), dx11, glsl, standard",
    )
    parser.add_argument(
        "--view-transform",
        default="Un-tone-mapped (sRGB)",
        help="View Transform name for color management. Default: Un-tone-mapped (sRGB)",
    )
    parser.add_argument(
        "--display",
        default="sRGB",
        help="Display name for color management. Default: sRGB",
    )
    parser.add_argument(
        "--rendering-space",
        default="ACEScg",
        help="Rendering space name for color management. Default: ACEScg",
    )
    parser.add_argument(
        "--diagnostics-out",
        default=None,
        help="Optional JSON path for structured shader/capture diagnostics.",
    )
    parser.add_argument(
        "--allow-blank",
        action="store_true",
        help="Diagnostic mode: write diagnostics and exit 0 even if the PNG is blank.",
    )
    return parser.parse_args()


def _resolve_actual_png(requested: Path, frame: int) -> Path:
    """Return the actual written PNG path.

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

    # Fallback: any PNG with the requested stem that appeared in our controlled output dir.
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
    """Compute XYZ Euler rotation (degrees) so that -Z points from *from_pos* toward *to_pos*.

    Maya camera transform order is XYZ (rotateX then rotateY then rotateZ):
      R = Rx(φ) * Ry(θ) * Rz(0)
      view_dir = R * (0, 0, -1) = (-sin(θ), sin(φ)*cos(θ), -cos(φ)*cos(θ))

    Solving for θ, φ given view_dir = normalized(to_pos - from_pos) = (dx,dy,dz)/len:
      sin(θ) = -dx/len      →  θ = asin(-dx/len)
      sin(φ)*cos(θ) = dy/len  →  from atan2(dy/len, -dz/len) we get φ = atan2(dy, -dz)
      cos(φ)*cos(θ) = -dz/len

    Returns [rotateX, rotateY, rotateZ] in degrees.
    """
    dx = to_pos[0] - from_pos[0]
    dy = to_pos[1] - from_pos[1]
    dz = to_pos[2] - from_pos[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1e-12:
        return [0.0, 0.0, 0.0]

    # Y rotation (yaw): -sin(θ) = dx/len  →  sin(θ) = -dx/len
    sin_theta = -dx / length
    sin_theta = max(-1.0, min(1.0, sin_theta))
    yaw = math.degrees(math.asin(sin_theta))

    # X rotation (pitch): from atan2(dy, -dz) = atan2(sin(φ)*cos(θ), cos(φ)*cos(θ))
    pitch = math.degrees(math.atan2(dy, -dz))

    return [pitch, yaw, 0.0]


def _compute_model_bounds(root_node: str) -> tuple[list[float], float]:
    """Compute world-space bounding box center and diagonal radius of mesh nodes.

    Args:
        root_node: Root transform of the imported model.

    Returns:
        (center [x,y,z], diagonal_radius) where diagonal_radius is half the
        diagonal length of the combined bounding box of all mesh nodes.
        Falls back to center=[0,0,0], radius=5.0 if no meshes are found.
    """
    meshes = cmds.listRelatives(root_node, allDescendents=True, type="mesh")
    if not meshes:
        print("Warning: no mesh nodes found under root, falling back to default bounds")
        return [0.0, 0.0, 0.0], 5.0

    try:
        bbox = cmds.exactWorldBoundingBox(meshes)
    except Exception as exc:
        print(f"Warning: exactWorldBoundingBox failed ({exc}), using fallback bounds")
        return [0.0, 0.0, 0.0], 5.0

    # bbox is [minX, minY, minZ, maxX, maxY, maxZ]
    if any(not isinstance(v, (int, float)) for v in bbox):
        print("Warning: invalid bounding box, using fallback")
        return [0.0, 0.0, 0.0], 5.0

    center = [
        (bbox[0] + bbox[3]) * 0.5,
        (bbox[1] + bbox[4]) * 0.5,
        (bbox[2] + bbox[5]) * 0.5,
    ]
    dx = bbox[3] - bbox[0]
    dy = bbox[4] - bbox[1]
    dz = bbox[5] - bbox[2]
    radius = math.sqrt(dx * dx + dy * dy + dz * dz) * 0.5

    print(
        f"Model bounds: min=({bbox[0]:.3f},{bbox[1]:.3f},{bbox[2]:.3f}), "
        f"max=({bbox[3]:.3f},{bbox[4]:.3f},{bbox[5]:.3f}), "
        f"center=({center[0]:.3f},{center[1]:.3f},{center[2]:.3f}), "
        f"radius={radius:.3f}"
    )
    return center, radius


def _check_png_not_blank(png_path: Path, threshold: int = 10, allow_blank: bool = False) -> dict:
    """Verify PNG is not effectively blank (all pixels near zero).

    Uses only stdlib (struct + zlib) to parse the PNG, decompress IDAT
    data, and sample pixel values. Raises RuntimeError if the maximum
    pixel value across any sampled RGB channel is below *threshold*.

    Returns a stats dict with keys: min, max, avg, samples, width, height.
    """
    with open(png_path, "rb") as f:
        raw = f.read()

    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a valid PNG file: {png_path}")

    # Read IHDR
    ihdr_len = struct.unpack_from(">I", raw, 8)[0]
    if raw[12:16] != b"IHDR":
        raise ValueError(f"Expected IHDR chunk, got {raw[12:16]!r}")
    ihdr = raw[16 : 16 + ihdr_len]
    img_w = struct.unpack_from(">I", ihdr, 0)[0]
    img_h = struct.unpack_from(">I", ihdr, 4)[0]
    bit_depth = ihdr[8]
    color_type = ihdr[9]

    # Collect IDAT data
    pos = 16 + ihdr_len + 4  # skip IHDR data + CRC
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
    if color_type == 0:  # Grayscale
        channels = 1
    elif color_type == 2:  # RGB
        channels = 3
    elif color_type == 4:  # Grayscale + alpha
        channels = 2
    elif color_type == 6:  # RGBA
        channels = 4
    else:
        raise ValueError(f"Unsupported PNG color type for blank check: {color_type}")

    pixel_bytes = channels
    row_bytes = img_w * pixel_bytes
    row_stride = 1 + row_bytes  # 1 filter byte per row

    min_val = 255
    max_val = 0
    total = 0
    count = 0
    prev_row = bytearray(row_bytes)

    # Sample rows evenly across the full image height (not just the top).
    # The camera framing may place the model below row 200 (e.g. rows 235-753
    # as observed with FOV=25\u00b0 at 139-unit distance), so checking only the
    # first 200 rows would miss it entirely.  We step through the whole height
    # to catch the model wherever it appears.
    total_rows = max(1, len(decompressed) // row_stride)
    row_step = max(1, total_rows // 400)  # sample ~400 rows across full height
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
            if filter_type == 1:  # Sub
                row[idx] = (row[idx] + left) & 0xFF
            elif filter_type == 2:  # Up
                row[idx] = (row[idx] + up) & 0xFF
            elif filter_type == 3:  # Average
                row[idx] = (row[idx] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:  # Paeth
                predictor = left + up - up_left
                pa = abs(predictor - left)
                pb = abs(predictor - up)
                pc = abs(predictor - up_left)
                paeth = left if pa <= pb and pa <= pc else up if pb <= pc else up_left
                row[idx] = (row[idx] + paeth) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"Unsupported PNG row filter: {filter_type}")

        if row_idx % row_step == 0:
            # Sample every 4th pixel horizontally for speed.
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

    stats = {
        "min": min_val,
        "max": max_val,
        "avg": round(avg, 3),
        "samples": count,
        "width": img_w,
        "height": img_h,
    }

    if max_val < threshold:
        blank_error = (
            f"Captured PNG is effectively blank (max pixel={max_val} < {threshold}): "
            f"min={min_val} avg={avg:.2f} samples={count}. "
            "Model may be out of frame, unlit, or the viewport background is black."
        )
        stats["blank_error"] = blank_error
        if not allow_blank:
            raise RuntimeError(blank_error)

    return stats


def _write_glsl_diagnostics(
    png_path: Path,
    root_node: str,
    png_stats: dict | None = None,
    diagnostics_path: Path | None = None,
    context: dict | None = None,
    error: str | None = None,
) -> dict:
    """Collect structured GLSL/dx11 diagnostics and write {png_path}.diag.json.

    Returns the diagnostics dict (also saved to disk).
    """
    diag: dict = {
        "png_path": str(png_path),
        "png_stats": png_stats,
        "shader_backend": "<unknown>",
        "shader_requested": None,
        "glsl_shaders": [],
        "dx11_shaders": [],
        "meshes": [],
        "color_management": {},
        "plugin_info": {},
        "errors": [],
    }
    if context:
        diag.update(context)
    if error:
        diag["error"] = error
    if png_stats and png_stats.get("blank_error"):
        diag["blank_error"] = png_stats["blank_error"]

    # -- Shader backend detection
    try:
        import mmd_tools.core.settings as _mms
        diag["shader_backend"] = str(_mms.get("import.model.mmd_shader_backend", "<unset>"))
    except Exception as exc:
        diag["shader_backend"] = f"<error: {exc}>"

    # -- GLSL shader nodes
    glsl_nodes = cmds.ls(type="GLSLShader") or []
    diag["GLSLShader_count"] = len(glsl_nodes)
    for shader in glsl_nodes:
        info: dict = {"name": shader}
        for attr_path, attr_name in [("shader", "shader_path"), ("technique", "technique")]:
            try:
                info[attr_name] = str(cmds.getAttr(f"{shader}.{attr_path}"))
            except Exception as exc:
                info[attr_name] = f"<error: {exc}>"

        # File existence check
        fp = info.get("shader_path", "")
        if fp and not fp.startswith("<"):
            info["shader_file_exists"] = Path(fp).exists()
            if not info["shader_file_exists"]:
                diag.setdefault("errors", []).append(f"Shader file not found: {fp}")

        # Compilation/status attributes
        for attr_name in [
            "status", "compileStatus", "compilationMessage", "error", "log",
            "outColor", "outColorR", "outColorG", "outColorB", "outColorA",
        ]:
            try:
                if cmds.attributeQuery(attr_name, node=shader, exists=True):
                    val = cmds.getAttr(f"{shader}.{attr_name}")
                    if isinstance(val, (list, tuple)):
                        val = list(val)
                    info[f"attr:{attr_name}"] = val
            except Exception as exc:
                info[f"attr:{attr_name}:error"] = str(exc)

        # Debug attributes (error/warn/status/log/message/compile)
        try:
            all_attrs = cmds.listAttr(shader) or []
            dbg_keys = ["error", "warn", "status", "log", "message", "compile"]
            for attr_name in all_attrs:
                if any(k in attr_name.lower() for k in dbg_keys):
                    try:
                        if cmds.attributeQuery(attr_name, node=shader, exists=True):
                            val = cmds.getAttr(f"{shader}.{attr_name}")
                            if isinstance(val, (list, tuple)):
                                val = list(val)
                            info[f"dbg_attr:{attr_name}"] = val
                    except Exception as exc:
                        info[f"dbg_attr:{attr_name}:error"] = str(exc)
        except Exception as exc:
            info["debug_attr_scan_error"] = str(exc)

        # Material uniforms
        for attr_name in [
            "DiffuseColor", "SpecularColor", "AmbientColor", "Shininess",
            "EdgeColor", "EdgeSize", "SphereMode", "Opacity",
        ]:
            try:
                val = cmds.getAttr(f"{shader}.{attr_name}")
                info[f"uniform:{attr_name}"] = str(val)
            except Exception as exc:
                info[f"uniform:{attr_name}:error"] = str(exc)

        # ShadingEngine connections
        sgs = cmds.listConnections(shader, type="shadingEngine") or []
        info["shading_engine_count"] = len(sgs)
        info["shading_engines"] = []
        for sg in sgs:
            sg_info: dict = {"name": sg}
            try:
                members = cmds.sets(sg, q=True) or []
                sg_info["member_count"] = len(members)
                sg_info["members"] = members[:20]
                # Connected surfaceShader
                src = cmds.listConnections(f"{sg}.surfaceShader", source=True, destination=False) or []
                sg_info["surfaceShader_inputs"] = src
            except Exception as exc:
                sg_info["error"] = str(exc)
            info["shading_engines"].append(sg_info)

        diag["glsl_shaders"].append(info)

    # -- dx11 shader nodes
    dx11_nodes = cmds.ls(type="dx11Shader") or []
    diag["dx11Shader_count"] = len(dx11_nodes)
    for shader in dx11_nodes:
        info: dict = {"name": shader}
        for attr_path, attr_name in [("shader", "shader_path"), ("technique", "technique")]:
            try:
                info[attr_name] = str(cmds.getAttr(f"{shader}.{attr_path}"))
            except Exception as exc:
                info[attr_name] = f"<error: {exc}>"
        fp = info.get("shader_path", "")
        if fp and not fp.startswith("<"):
            info["shader_file_exists"] = Path(fp).exists()
        sgs = cmds.listConnections(shader, type="shadingEngine") or []
        info["shading_engine_count"] = len(sgs)
        info["shading_engines"] = [{"name": sg} for sg in sgs]
        diag["dx11_shaders"].append(info)

    # -- Mesh info
    meshes = cmds.listRelatives(root_node, allDescendents=True, type="mesh") or []
    diag["mesh_count"] = len(meshes)
    for m in meshes:
        try:
            face_count = cmds.polyEvaluate(m, face=True)
            diag["meshes"].append({"name": m, "face_count": face_count})
        except Exception:
            diag["meshes"].append({"name": m})

    # -- Color management
    for attr_name in ["viewTransformName", "displayName", "renderingSpaceName"]:
        try:
            diag["color_management"][attr_name] = str(
                cmds.colorManagementPrefs(q=True, **{attr_name: True})
            )
        except Exception as exc:
            diag["color_management"][attr_name] = f"<error: {exc}>"

    # -- Plugin info
    for plugin_name in ["glslShader", "dx11Shader"]:
        try:
            loaded = cmds.pluginInfo(plugin_name, q=True, loaded=True)
            version = cmds.pluginInfo(plugin_name, q=True, version=True)
            path = cmds.pluginInfo(plugin_name, q=True, path=True)
            diag["plugin_info"][plugin_name] = {
                "loaded": loaded,
                "version": version,
                "path": path,
            }
        except Exception as exc:
            diag["plugin_info"][plugin_name] = f"<error: {exc}>"

    # -- VP2 device diagnostics
    vp2: dict = {}
    import os as _os
    vp2["MAYA_VP2_DEVICE_OVERRIDE"] = _os.environ.get("MAYA_VP2_DEVICE_OVERRIDE", None)
    try:
        device_info = cmds.ogs(deviceInformation=True)
        vp2["deviceInformation"] = device_info
    except Exception as exc:
        vp2["deviceInformation"] = f"<error: {exc}>"
    try:
        vp2["vp2RenderingEngine"] = str(
            cmds.optionVar(query="vp2RenderingEngine")
        )
    except Exception as exc:
        vp2["vp2RenderingEngine"] = f"<error: {exc}>"
    diag["vp2"] = vp2

    # -- Write to disk
    diag_path = diagnostics_path or png_path.with_name(png_path.name + ".diag.json")
    diag_path.parent.mkdir(parents=True, exist_ok=True)
    with open(diag_path, "w", encoding="utf-8") as f:
        json.dump(diag, f, indent=2, default=str)
    print(f"Structured diagnostics written to: {diag_path}")
    return diag


def _validate_shader_assignment(root_node: str, expected_backend: str) -> None:
    """Fail shader captures when the requested backend did not create usable shaders."""
    if expected_backend == "standard":
        return

    if expected_backend == "dx11":
        node_type = "dx11Shader"
        shader_attr = "shader"
    elif expected_backend == "glsl":
        node_type = "GLSLShader"
        shader_attr = "shader"
    else:
        raise RuntimeError(f"Unsupported resolved shader backend: {expected_backend}")

    shader_nodes = cmds.ls(type=node_type) or []
    if not shader_nodes:
        raise RuntimeError(f"--shader requested backend {expected_backend}, but no {node_type} nodes were created.")

    bad_nodes: list[str] = []
    for shader in shader_nodes:
        try:
            shader_path = str(cmds.getAttr(f"{shader}.{shader_attr}") or "")
        except Exception as exc:
            bad_nodes.append(f"{shader}: cannot read {shader_attr}: {exc}")
            continue
        if not shader_path:
            bad_nodes.append(f"{shader}: empty {shader_attr}")
        elif not Path(shader_path).is_file():
            bad_nodes.append(f"{shader}: shader file does not exist: {shader_path}")

        shading_engines = cmds.listConnections(shader, type="shadingEngine") or []
        if not shading_engines:
            bad_nodes.append(f"{shader}: no shadingEngine connection")

    meshes = cmds.listRelatives(root_node, allDescendents=True, type="mesh") or []
    assigned_shading_engines: set[str] = set()
    for mesh in meshes:
        for sg in cmds.listConnections(mesh, type="shadingEngine") or []:
            assigned_shading_engines.add(sg)
    if not assigned_shading_engines:
        bad_nodes.append(f"{root_node}: no mesh shadingEngine assignments")

    if bad_nodes:
        raise RuntimeError(
            f"--shader backend {expected_backend} created invalid shader assignments:\n"
            + "\n".join(f"- {item}" for item in bad_nodes)
        )


def _diagnose_shaders(root_node: str) -> None:
    """Print diagnostics about MMD shader nodes created by the importer."""
    dx11_nodes = cmds.ls(type="dx11Shader") or []
    glsl_nodes = cmds.ls(type="GLSLShader") or []
    print(f"dx11Shader nodes found: {len(dx11_nodes)}")
    print(f"GLSLShader nodes found: {len(glsl_nodes)}")
    for shader in dx11_nodes:
        shader_path = "<unreadable>"
        technique = "<unreadable>"
        try:
            shader_path = cmds.getAttr(f"{shader}.shader")
        except Exception as exc:
            shader_path = f"<error: {exc}>"
        try:
            technique = cmds.getAttr(f"{shader}.technique")
        except Exception as exc:
            technique = f"<error: {exc}>"
        sgs = cmds.listConnections(shader, type="shadingEngine") or []
        print(f"  Shader: {shader}")
        print(f"    shader path: {shader_path}")
        print(f"    technique: {technique}")
        print(f"    shadingEngines ({len(sgs)}): {sgs}")

    for shader in glsl_nodes:
        shader_path = "<unreadable>"
        technique = "<unreadable>"
        material_uniforms: dict[str, str] = {}
        try:
            shader_path = cmds.getAttr(f"{shader}.shader")
        except Exception as exc:
            shader_path = f"<error: {exc}>"
        try:
            technique = cmds.getAttr(f"{shader}.technique")
        except Exception as exc:
            technique = f"<error: {exc}>"
        for attr in [
            "DiffuseColor",
            "SpecularColor",
            "AmbientColor",
            "Shininess",
            "EdgeColor",
            "EdgeSize",
            "SphereMode",
            "Opacity",
        ]:
            try:
                material_uniforms[attr] = str(cmds.getAttr(f"{shader}.{attr}"))
            except Exception as exc:
                material_uniforms[attr] = f"<error: {exc}>"
        for attr in [
            "glslShader",
            "shader",
            "technique",
            "status",
            "compileStatus",
            "compilationMessage",
            "error",
            "log",
            "outColor",
            "outColorR",
            "outColorG",
            "outColorB",
            "outColorA",
        ]:
            try:
                if cmds.attributeQuery(attr, node=shader, exists=True):
                    material_uniforms[f"attr:{attr}"] = str(cmds.getAttr(f"{shader}.{attr}"))
            except Exception as exc:
                material_uniforms[f"attr:{attr}"] = f"<error: {exc}>"
        try:
            all_attrs = cmds.listAttr(shader) or []
            dbg_attrs = [
                a
                for a in all_attrs
                if any(key in a.lower() for key in ["error", "warn", "status", "log", "message", "compile"])
            ]
            if dbg_attrs:
                print(f"    GLSL debug attrs: {dbg_attrs}")
                for dbg_attr in dbg_attrs:
                    try:
                        material_uniforms[f"dbg_attr:{dbg_attr}"] = str(
                            cmds.getAttr(f"{shader}.{dbg_attr}")
                        )
                    except Exception as exc:
                        material_uniforms[f"dbg_attr:{dbg_attr}"] = f"<error: {exc}>"
        except Exception as exc:
            print(f"    Failed to list GLSL debug attrs: {exc}")
        sgs = cmds.listConnections(shader, type="shadingEngine") or []
        print(f"  Shader: {shader}")
        print(f"    shader path: {shader_path}")
        print(f"    technique: {technique}")
        print(f"    material uniforms: {material_uniforms}")
        print(f"    shadingEngines ({len(sgs)}): {sgs}")

    if not dx11_nodes and not glsl_nodes:
        print("  (no dx11/GLSL shader nodes created; MMD shader creation may have failed)")


def main() -> int:
    """Execute the static render capture and return process exit code."""
    args = _parse_args()

    out_path = Path(args.out).resolve()
    model_path = Path(args.model).resolve()
    frame = args.frame
    width = args.width
    height = args.height
    shader_backend = args.shader_backend
    diagnostics_path = Path(args.diagnostics_out).resolve() if args.diagnostics_out else None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not model_path.exists():
        raise FileNotFoundError(f"PMX model not found: {model_path}")

    # Ensure project root is importable.
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    maya.standalone.initialize(name="python")
    try:
        cmds.file(new=True, force=True)

        # ---------------------------------------------------------------
        # 1. Import PMX model
        # ---------------------------------------------------------------
        from mmd_tools.core import settings
        from mmd_tools.io.mmd_importer import import_mmd_file

        # Determine shader mode: --shader requests dx11Shader path via the
        # PMX importer.  If requested backend is unavailable in this mayapy
        # environment we fail fast to avoid silently dropping to lambert.
        use_shader = args.shader
        resolved_shader_backend = shader_backend
        settings.set("import.model.create_mmd_shaders", use_shader)
        if use_shader:
            settings.set("import.model.mmd_shader_backend", shader_backend)
            if shader_backend in {"auto", "dx11"}:
                # Plugin name is "dx11Shader", not DirectX11Shader.
                _dx11_ok = False
                try:
                    cmds.loadPlugin("dx11Shader", quiet=True)
                except Exception:
                    _dx11_ok = False
                else:
                    try:
                        probe = cmds.shadingNode(
                            "dx11Shader", asShader=True, name="_dx11_avail_probe"
                        )
                        _dx11_ok = cmds.attributeQuery("outColor", node=probe, exists=True)
                        cmds.delete(probe)
                    except Exception:
                        _dx11_ok = False

                if _dx11_ok:
                    resolved_shader_backend = "dx11"
                    settings.set("import.model.mmd_shader_backend", "dx11")
                    print("dx11Shader node type is available and fully functional.")
                elif shader_backend == "dx11":
                    raise RuntimeError(
                        "--shader-backend dx11 requested but dx11Shader is unavailable. "
                        "Use --shader-backend auto to fall back to glslShader."
                    )
                else:
                    # auto: fall through to glslShader
                    print("dx11Shader unavailable; falling back to glslShader probe.")
                    try:
                        cmds.loadPlugin("glslShader", quiet=True)
                    except Exception as plug_exc:
                        raise RuntimeError(
                            f"--shader-backend auto: glslShader plugin could not be loaded: {plug_exc}."
                        )
                    try:
                        probe = cmds.shadingNode(
                            "GLSLShader", asShader=True, name="_glsl_avail_probe"
                        )
                        _glsl_ok = cmds.attributeQuery("outColor", node=probe, exists=True)
                        cmds.delete(probe)
                        if not _glsl_ok:
                            raise RuntimeError(
                                "GLSLShader node type exists but has no outColor attribute."
                            )
                        settings.set("import.model.mmd_shader_backend", "glsl")
                        resolved_shader_backend = "glsl"
                        print("GLSLShader node type is available and fully functional (auto fallback).")
                    except RuntimeError:
                        raise
                    except Exception as probe_exc:
                        raise RuntimeError(
                            f"--shader-backend auto: glslShader probe failed: {probe_exc}."
                        )
            elif shader_backend == "glsl":
                try:
                    cmds.loadPlugin("glslShader", quiet=True)
                except Exception as plug_exc:
                    raise RuntimeError(
                        f"--shader-backend glsl requested but glslShader plugin could not be loaded: {plug_exc}."
                    )
                try:
                    probe = cmds.shadingNode(
                        "GLSLShader", asShader=True, name="_glsl_avail_probe"
                    )
                    has_out = cmds.attributeQuery("outColor", node=probe, exists=True)
                    cmds.delete(probe)
                    if not has_out:
                        raise RuntimeError(
                            "GLSLShader node type exists but has no outColor attribute. "
                            "The glslShader plugin may be damaged or incompatible."
                        )
                    print("GLSLShader node type is available and fully functional.")
                    resolved_shader_backend = "glsl"
                except RuntimeError:
                    raise
                except Exception as probe_exc:
                    raise RuntimeError(
                        f"--shader-backend glsl requested but GLSLShader probe failed: {probe_exc}."
                    )
            elif shader_backend == "standard":
                resolved_shader_backend = "standard"
                print("shader_backend=standard: skipping dx11/glsl probe.")

        root_node = import_mmd_file(str(model_path))
        if root_node is None:
            raise RuntimeError(f"Failed to import PMX model: {model_path}")
        print(f"Imported PMX model, root node: {root_node}")

        # Re-read what actually happened (the importer may have fallen back
        # internally despite create_mmd_shaders=True).
        actual_shader_mode = settings.get("import.model.create_mmd_shaders")
        if use_shader and not actual_shader_mode:
            raise RuntimeError(
                "--shader enabled but PMX importer did not create MMD shaders. "
                "The dx11Shader plugin is loaded but the importer's shader creation failed. "
                "Check the PMX material definitions or use --no-shader."
            )
        if use_shader:
            _validate_shader_assignment(root_node, resolved_shader_backend)

        # ---------------------------------------------------------------
        # 2. Compute model bounding box and set up camera
        #
        # Camera target = bbox center. Camera position = center + view_dir
        # * distance, where distance is chosen so the model fills ~70% of
        # the viewport (using the tighter of horizontal/vertical fits).
        # We repurpose the default 'persp' camera to avoid the playblast
        # 'camera' kwarg that can raise "invalid flag" in stand-alone.
        # ---------------------------------------------------------------
        model_center, model_radius = _compute_model_bounds(root_node)

        # Compute camera distance so the model fills ~70% of the viewport.
        aspect_ratio = width / height
        fov_h = math.radians(GOLDEN_CAMERA_FOV)
        tan_half_fov = math.tan(fov_h * 0.5)
        # Half of the visible world at distance d: horizontal = d*tan(fov/2),
        # vertical = d*tan(fov/2) / aspect.
        d_h = model_radius / (0.7 * tan_half_fov) if tan_half_fov > 1e-9 else model_radius * 3.0
        d_v = model_radius / (0.7 * tan_half_fov / aspect_ratio) if tan_half_fov > 1e-9 else model_radius * 3.0
        camera_distance = max(d_h, d_v, model_radius * 2.0, 5.0)

        view_dir = _normalize(CAMERA_VIEW_DIR)
        cam_pos = [
            model_center[0] + view_dir[0] * camera_distance,
            model_center[1] + view_dir[1] * camera_distance,
            model_center[2] + view_dir[2] * camera_distance,
        ]

        persp_shape = cmds.listRelatives("persp", shapes=True)
        if not persp_shape:
            raise RuntimeError("No shape found under persp camera")
        persp_shape = persp_shape[0]

        cmds.setAttr("persp.translateX", cam_pos[0])
        cmds.setAttr("persp.translateY", cam_pos[1])
        cmds.setAttr("persp.translateZ", cam_pos[2])

        # Compute Euler rotation directly (no aimConstraint, which can fail
        # in mayapy standalone batch mode without a model panel).
        euler = _direction_to_euler_rotation(cam_pos, model_center)
        cmds.setAttr("persp.rotateX", euler[0])
        cmds.setAttr("persp.rotateY", euler[1])
        cmds.setAttr("persp.rotateZ", euler[2])

        # Maya camera uses focalLength (not a writable horizontalFieldOfView attr).
        # For a 36 mm film back: FOV = 2 * atan(36 / (2 * focalLength)).
        focal_length = 18.0 / tan_half_fov if tan_half_fov > 1e-9 else 35.0
        cmds.setAttr(f"{persp_shape}.focalLength", focal_length)

        # Set near/far clipping planes based on distance to model.
        clip_near = max(0.01, camera_distance * 0.01)
        clip_far = camera_distance + model_radius * 4.0 + 100.0
        cmds.setAttr(f"{persp_shape}.nearClipPlane", clip_near)
        cmds.setAttr(f"{persp_shape}.farClipPlane", clip_far)
        print(
            f"Camera: pos=({cam_pos[0]:.2f},{cam_pos[1]:.2f},{cam_pos[2]:.2f}), "
            f"target=({model_center[0]:.2f},{model_center[1]:.2f},{model_center[2]:.2f}), "
            f"distance={camera_distance:.2f}, focalLength={focal_length:.2f}, "
            f"rotation=({euler[0]:.2f},{euler[1]:.2f},{euler[2]:.2f})"
        )

        # ---------------------------------------------------------------
        # 3. Mesh rendering: shader mode vs. basic lambert fallback
        #
        # When --shader is active the PMX importer creates dx11Shader nodes
        # (which require a DX11-capable viewport).  We diagnose them and do
        # NOT assign a lambert override so the native shader path is tested.
        #
        # When --no-shader (the default) the importer creates standardSurface
        # materials which may not render in mayapy batch mode.  We replace
        # the surfaceShader inside each existing shadingEngine with a visible
        # lambert, keeping face-level assignments intact, and turn off vertex
        # colours.
        # ---------------------------------------------------------------
        if use_shader:
            _diagnose_shaders(root_node)
        else:
            meshes = cmds.listRelatives(root_node, allDescendents=True, type="mesh")
            if meshes:
                debug_shader = cmds.shadingNode("lambert", asShader=True, name="viewportDebug_lambert")
                cmds.setAttr(f"{debug_shader}.color", 0.6, 0.7, 0.9, type="double3")
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
                print(
                    f"Assigned basic lambert to {len(sgs_seen)} "
                    f"shadingEngine(s) for {len(meshes)} mesh(es)"
                )

        # ---------------------------------------------------------------
        # 4. Set up directional light (GoldenOracle defaults)
        #
        # The light direction [0.5,-1,0.5] points from upper-right-front
        # toward the scene. We point the light at the model center so the
        # illumination is centered on the imported model.
        # Use direct rotation (no aimConstraint) for batch-mode compat.
        # ---------------------------------------------------------------
        light_shape = cmds.directionalLight(
            name="staticRenderLight",
            intensity=1.0,
            rgb=GOLDEN_LIGHT_COLOR,
        )
        light_xform = cmds.listRelatives(light_shape, parent=True)[0]
        # The golden direction vector [0.5,-1,0.5] is the direction from the
        # light toward the scene. Since local -Z points at the target, placing
        # the transform at center - direction makes -Z align with direction.
        light_pos = [
            model_center[0] - GOLDEN_LIGHT_DIRECTION[0],
            model_center[1] - GOLDEN_LIGHT_DIRECTION[1],
            model_center[2] - GOLDEN_LIGHT_DIRECTION[2],
        ]
        light_euler = _direction_to_euler_rotation(light_pos, model_center)
        cmds.setAttr(f"{light_xform}.rotateX", light_euler[0])
        cmds.setAttr(f"{light_xform}.rotateY", light_euler[1])
        cmds.setAttr(f"{light_xform}.rotateZ", light_euler[2])

        # ---------------------------------------------------------------
        # 5. Background
        #
        # In mayapy standalone batch mode, displayRGBColor / displayPref are
        # not available and calling them can corrupt VP2.0 internal state,
        # causing the entire playblast output to be black.  We therefore skip
        # background colour calls entirely and accept the default medium-gray
        # viewport background.  The non-blank PNG check below verifies that
        # the mesh renders on top of the background.
        # ---------------------------------------------------------------
        print("Note: viewport background colour not changed (batch mode: no display manager)")

        # ---------------------------------------------------------------
        # 6. Color Management: View Transform / Display / Rendering Space
        #
        # Query available lists, validate the requested values, then set
        # them explicitly via colorManagementPrefs.  Log the resulting
        # configuration to stdout.
        # ---------------------------------------------------------------
        def _validate_cm_prefs(
            query_attr: str, setting_attr: str, requested: str, label: str
        ) -> None:
            """Query available *Names, validate requested value, set it."""
            available = cmds.colorManagementPrefs(q=True, **{query_attr: True})
            if not isinstance(available, list):
                available = [available] if available else []
            if requested not in available:
                raise RuntimeError(
                    f"{label} '{requested}' is not available. "
                    f"Available {label}s: {available}"
                )
            cmds.colorManagementPrefs(e=True, **{setting_attr: requested})
            # Read back and log
            actual = cmds.colorManagementPrefs(q=True, **{setting_attr: True})
            print(f"  {label}: {actual}")

        print("Color Management settings:")
        _validate_cm_prefs(
            "viewTransformNames", "viewTransformName",
            args.view_transform, "View Transform"
        )
        _validate_cm_prefs(
            "displayNames", "displayName",
            args.display, "Display"
        )
        _validate_cm_prefs(
            "renderingSpaceNames", "renderingSpaceName",
            args.rendering_space, "Rendering Space"
        )

        # ---------------------------------------------------------------
        # 7. Set current time and capture
        # ---------------------------------------------------------------
        cmds.currentTime(frame)
        try:
            cmds.playbackOptions(minTime=frame, maxTime=frame)
        except Exception:
            pass

        try:
            cmds.refresh()
        except Exception:
            pass

        # Clean prior outputs for this stem.
        for old_png in out_path.parent.glob(f"{out_path.stem}*.png"):
            try:
                old_png.unlink()
            except Exception:
                pass

        playblast_result = cmds.playblast(
            filename=str(out_path.with_suffix("")),
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
        print(f"playblast returned: {playblast_result!r}")

        actual = _resolve_actual_png(out_path, frame)
        if not actual.exists():
            try:
                contents = list(actual.parent.iterdir()) if actual.parent.exists() else []
            except Exception as e:
                contents = [f"<iterdir failed: {e}>"]
            print(f"Output dir contents: {contents}")
            raise FileNotFoundError(
                f"Static render capture PNG not produced. "
                f"Requested base: {out_path}, frame: {frame}"
            )

        size = actual.stat().st_size
        if size <= 0:
            raise RuntimeError(f"Captured PNG is zero bytes: {actual}")

        # ---------------------------------------------------------------
        # 7. Non-blank self-check (stdlib PNG parsing, no PIL/NumPy)
        # ---------------------------------------------------------------
        png_stats = None
        diagnostics_context = {
            "model": str(model_path),
            "out": str(out_path),
            "actual_png": str(actual),
            "frame": frame,
            "width": width,
            "height": height,
            "shader_requested": bool(use_shader),
            "shader_backend": shader_backend,
        }
        diagnostics_enabled = diagnostics_path is not None or args.allow_blank
        try:
            png_stats = _check_png_not_blank(actual, allow_blank=args.allow_blank)
            if "blank_error" in png_stats:
                print(f"Warning: {png_stats['blank_error']}")
            else:
                print(
                    f"PNG pixel check: min={png_stats['min']} max={png_stats['max']} "
                    f"avg={png_stats['avg']} samples={png_stats['samples']} "
                    f"dims={png_stats['width']}x{png_stats['height']}"
                )
        except RuntimeError as exc:
            try:
                png_stats = _check_png_not_blank(actual, allow_blank=True)
            except Exception:
                pass
            if diagnostics_enabled:
                _write_glsl_diagnostics(
                    actual,
                    root_node,
                    png_stats,
                    diagnostics_path,
                    diagnostics_context,
                    str(exc),
                )
            raise

        # -- Structured diagnostics artifact for shader-mode captures
        if diagnostics_enabled:
            _write_glsl_diagnostics(
                actual,
                root_node,
                png_stats,
                diagnostics_path,
                diagnostics_context,
                png_stats.get("blank_error") if png_stats else None,
            )

        print(f"OK: static render capture -> {actual} (size={size} bytes, frame={frame}, {width}x{height})")
        return 0

    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
