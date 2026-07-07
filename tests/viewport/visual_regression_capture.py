"""Manifest-driven Maya GUI / DX11 viewport visual regression harness.

This script launches a fresh Maya GUI process, opens a Python commandPort,
imports selected GoldenOracle render-manifest fixtures with dx11Shader
materials, captures Viewport 2.0 PNGs, and writes report-only diagnostics.

The harness intentionally copies MMDShader.fx to a unique path per run before
assigning it to dx11Shader nodes. Maya can cache effects by .fx path inside a
long-running process, so unique shader paths make captures reproducible across
working-tree edits.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport


DEFAULT_MAYA_VERSION = "2024"
DEFAULT_PORT = 7721
DEFAULT_TIMEOUT = 420
COMPLETION_MARKER = "//-- MAYA VISUAL REGRESSION FINISHED --//"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
LOGGER = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default=DEFAULT_MAYA_VERSION)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--manifest",
        required=True,
        help="GoldenOracle-compatible render manifest path.",
    )
    parser.add_argument(
        "--out",
        default="build/visual-regression/maya-dx11",
        help="Output directory under build/ by convention.",
    )
    parser.add_argument(
        "--shader-fx",
        default="",
        help="Optional .fx file to copy into this run instead of mmd_tools/shaders/MMDShader.fx.",
    )
    parser.add_argument("--case", action="append", default=[], help="Case name to capture. Repeatable.")
    parser.add_argument("--tag", action="append", default=[], help="Capture cases containing all tags. Repeatable.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of cases after filtering.")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--launch-mode",
        choices=["direct", "powershell"],
        default="powershell" if os.name == "nt" else "direct",
        help="How to launch Maya when not attaching. powershell uses Start-Process on Windows.",
    )
    parser.add_argument(
        "--attach-existing",
        action="store_true",
        help="Use an already-open commandPort instead of launching a fresh Maya process.",
    )
    parser.add_argument(
        "--keep-maya",
        action="store_true",
        help="Leave Maya GUI running after capture for manual inspection.",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="Skip actual-vs-oracle pixel summary even when oracle PNG exists.",
    )
    parser.add_argument(
        "--debug-lambert-control",
        action="store_true",
        help="Temporarily assign a red lambert to visible mesh transforms before capture.",
    )
    parser.add_argument(
        "--hide-orig-shapes",
        action="store_true",
        help="Temporarily mark *Orig mesh shapes as intermediate before capture.",
    )
    return parser.parse_args()


def _load_cases(manifest_path: Path, names: list[str], tags: list[str], limit: int) -> tuple[dict, list[dict]]:
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    defaults = manifest.get("defaults", {})
    selected: list[dict] = []
    wanted = set(names)
    wanted_tags = set(tags)
    manifest_dir = manifest_path.parent

    for case in manifest.get("cases", []):
        name = case.get("name")
        if wanted and name not in wanted:
            continue
        case_tags = set(case.get("metadata", {}).get("tags", []))
        if wanted_tags and not wanted_tags.issubset(case_tags):
            continue

        camera = dict(defaults.get("camera", {}))
        camera.update(case.get("metadata", {}).get("camera", {}))
        image = dict(defaults.get("image", {}))
        image.update(case.get("image", {}))
        light = dict(defaults.get("light", {}))
        light.update(case.get("metadata", {}).get("light", {}))

        model = (manifest_dir / case["assets"]["model"]).resolve()
        oracle_rel = case.get("oracle", {}).get("path")
        oracle_dir = (manifest_dir / oracle_rel).resolve().parent if oracle_rel else None
        oracle_png = oracle_dir / f"frame-{case.get('frames', [0])[0]}.png" if oracle_dir else None

        selected.append(
            {
                "name": name,
                "model": str(model),
                "frame": int(case.get("frames", [defaults.get("frame", 0)])[0]),
                "camera": camera,
                "image": image,
                "light": light,
                "oracle_png": str(oracle_png) if oracle_png else None,
                "metadata": case.get("metadata", {}),
            }
        )

    if limit > 0:
        selected = selected[:limit]

    return manifest, selected


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _prepare_shader(project_root: Path, output_dir: Path, shader_fx: str = "") -> Path:
    source = Path(shader_fx) if shader_fx else project_root / "mmd_tools/shaders/MMDShader.fx"
    if not source.is_absolute():
        source = (project_root / source).resolve()
    digest = _sha256_file(source)[:16]
    dest = output_dir / "shaders" / f"MMDShader.{digest}.{int(time.time())}.fx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest


def _monitor_log(log_path: Path, timeout: int) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)
    start = time.time()
    with open(log_path, encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        while time.time() - start < timeout:
            line = f.readline()
            if line:
                print(line, end="")
                if COMPLETION_MARKER in line:
                    return
            else:
                time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for Maya-side completion marker in {log_path}")


def _build_maya_code(
    *,
    project_root: Path,
    cases: list[dict],
    shader_fx: Path,
    output_dir: Path,
    log_path: Path,
    width: int,
    height: int,
    compare: bool,
    debug_lambert_control: bool,
    hide_orig_shapes: bool,
) -> str:
    payload = {
        "project_root": str(project_root),
        "cases": cases,
        "shader_fx": str(shader_fx),
        "output_dir": str(output_dir),
        "log_path": str(log_path),
        "width": width,
        "height": height,
        "compare": compare,
        "debug_lambert_control": debug_lambert_control,
        "hide_orig_shapes": hide_orig_shapes,
        "completion_marker": COMPLETION_MARKER,
    }
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return f"""
import base64, json, math, os, sys, traceback, zlib, struct
from pathlib import Path

import maya.cmds as cmds

_payload = json.loads(base64.b64decode({encoded!r}).decode("utf-8"))
_project_root = Path(_payload["project_root"])
_output_dir = Path(_payload["output_dir"])
_log_path = Path(_payload["log_path"])
_shader_fx = Path(_payload["shader_fx"])
_cases = _payload["cases"]
_width = int(_payload["width"])
_height = int(_payload["height"])
_compare = bool(_payload["compare"])
_debug_lambert_control = bool(_payload["debug_lambert_control"])
_hide_orig_shapes = bool(_payload["hide_orig_shapes"])
_completion_marker = _payload["completion_marker"]

_output_dir.mkdir(parents=True, exist_ok=True)
_log_path.parent.mkdir(parents=True, exist_ok=True)

def _log(message):
    text = str(message)
    print(text)
    with open(_log_path, "a", encoding="utf-8", errors="replace") as f:
        f.write(text + "\\n")
        f.flush()

def _png_stats(path):
    path = Path(path)
    data = path.read_bytes()
    if data[:8] != b"\\x89PNG\\r\\n\\x1a\\n":
        return {{"error": "not png", "path": str(path)}}
    pos = 8
    width = height = bit_depth = color_type = None
    compressed = b""
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos+4])[0]
        ctype = data[pos+4:pos+8]
        chunk = data[pos+8:pos+8+length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            compressed += chunk
        elif ctype == b"IEND":
            break
    if bit_depth != 8 or color_type not in (2, 6):
        return {{"width": width, "height": height, "unsupported": f"bit_depth={{bit_depth}} color_type={{color_type}}"}}
    channels = 4 if color_type == 6 else 3
    raw = zlib.decompress(compressed)
    stride = width * channels
    prev = [0] * stride
    offset = 0
    min_v = 255
    max_v = 0
    total = 0
    count = 0
    for _y in range(height):
        filt = raw[offset]
        offset += 1
        row = list(raw[offset:offset+stride])
        offset += stride
        recon = [0] * stride
        for i, x in enumerate(row):
            left = recon[i - channels] if i >= channels else 0
            up = prev[i]
            up_left = prev[i - channels] if i >= channels else 0
            if filt == 0:
                val = x
            elif filt == 1:
                val = (x + left) & 255
            elif filt == 2:
                val = (x + up) & 255
            elif filt == 3:
                val = (x + ((left + up) // 2)) & 255
            elif filt == 4:
                p = left + up - up_left
                pa = abs(p - left); pb = abs(p - up); pc = abs(p - up_left)
                pr = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                val = (x + pr) & 255
            else:
                val = x
            recon[i] = val
        for i, v in enumerate(recon):
            if channels == 4 and i % 4 == 3:
                continue
            min_v = min(min_v, v)
            max_v = max(max_v, v)
            total += v
            count += 1
        prev = recon
    return {{"width": width, "height": height, "min": min_v, "max": max_v, "mean": total / max(count, 1), "samples": count}}

def _png_center_sample(path):
    path = Path(path)
    data = path.read_bytes()
    if data[:8] != b"\\x89PNG\\r\\n\\x1a\\n":
        return {{"error": "not png"}}
    pos = 8
    width = height = bit_depth = color_type = None
    compressed = b""
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos+4])[0]
        ctype = data[pos+4:pos+8]
        chunk = data[pos+8:pos+8+length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            compressed += chunk
        elif ctype == b"IEND":
            break
    if bit_depth != 8 or color_type not in (2, 6):
        return {{"width": width, "height": height, "unsupported": f"bit_depth={{bit_depth}} color_type={{color_type}}"}}
    channels = 4 if color_type == 6 else 3
    raw = zlib.decompress(compressed)
    stride = width * channels
    prev = [0] * stride
    offset = 0
    target_y = height // 2
    target_x = width // 2
    sample = None
    for y in range(height):
        filt = raw[offset]
        offset += 1
        row = list(raw[offset:offset+stride])
        offset += stride
        recon = [0] * stride
        for i, x in enumerate(row):
            left = recon[i - channels] if i >= channels else 0
            up = prev[i]
            up_left = prev[i - channels] if i >= channels else 0
            if filt == 0:
                val = x
            elif filt == 1:
                val = (x + left) & 255
            elif filt == 2:
                val = (x + up) & 255
            elif filt == 3:
                val = (x + ((left + up) // 2)) & 255
            elif filt == 4:
                p = left + up - up_left
                pa = abs(p - left); pb = abs(p - up); pc = abs(p - up_left)
                pr = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                val = (x + pr) & 255
            else:
                val = x
            recon[i] = val
        if y == target_y:
            start = target_x * channels
            sample = recon[start:start+channels]
            break
        prev = recon
    rgb = sample[:3] if sample else []
    green_suspected = bool(len(rgb) >= 3 and rgb[1] > 180 and rgb[0] < 40 and rgb[2] < 80)
    return {{"x": target_x, "y": target_y, "rgba": sample, "rgb": rgb, "vp2_unassigned_green_suspected": green_suspected}}

def _png_diff(a_path, b_path):
    a_path = Path(a_path)
    b_path = Path(b_path)
    if not a_path.is_file() or not b_path.is_file():
        return {{"available": False, "reason": "missing png"}}
    try:
        from PIL import Image, ImageChops, ImageStat
        a = Image.open(a_path).convert("RGBA")
        b = Image.open(b_path).convert("RGBA")
        if a.size != b.size:
            return {{"available": True, "size_mismatch": [a.size, b.size]}}
        diff = ImageChops.difference(a, b)
        stat = ImageStat.Stat(diff)
        extrema = diff.getextrema()
        bbox = diff.getbbox()
        return {{"available": True, "mean": stat.mean, "extrema": extrema, "bbox": bbox}}
    except Exception as exc:
        return {{"available": False, "reason": str(exc)}}

def _make_camera(camera):
    cam, shape = cmds.camera(name="visualRegressionCam")
    cmds.xform(cam, ws=True, t=camera["position"])
    loc = cmds.spaceLocator(name="__visual_regression_target__")[0]
    cmds.xform(loc, ws=True, t=camera["target"])
    con = cmds.aimConstraint(loc, cam, aimVector=(0, 0, -1), upVector=(0, 1, 0), worldUpType="scene")[0]
    cmds.delete(con, loc)
    fov = float(camera.get("fov", 25))
    aperture = cmds.getAttr(shape + ".horizontalFilmAperture")
    focal = (aperture * 25.4 * 0.5) / math.tan(math.radians(fov) * 0.5)
    cmds.setAttr(shape + ".focalLength", focal)
    cmds.setAttr(shape + ".nearClipPlane", float(camera.get("near", 0.1)))
    cmds.setAttr(shape + ".farClipPlane", float(camera.get("far", 1000)))
    return cam

def _setup_panel(camera):
    visible = cmds.getPanel(visiblePanels=True) or []
    visible_model_panels = [p for p in visible if cmds.getPanel(typeOf=p) == "modelPanel"]
    focused = cmds.getPanel(withFocus=True)
    if focused in visible_model_panels:
        panel = focused
    elif visible_model_panels:
        panel = visible_model_panels[0]
    else:
        panels = cmds.getPanel(type="modelPanel") or []
        panel = panels[0] if panels else cmds.modelPanel()
    cmds.modelEditor(
        panel,
        e=True,
        rendererName="vp2Renderer",
        displayAppearance="smoothShaded",
        displayTextures=True,
        wireframeOnShaded=False,
        useDefaultMaterial=False,
        selectionHiliteDisplay=False,
        grid=False,
        headsUpDisplay=False,
        cameras=False,
        lights=False,
        locators=False,
        joints=False,
        ikHandles=False,
        deformers=False,
        dynamics=False,
        nurbsCurves=False,
    )
    cmds.lookThru(panel, camera)
    try:
        cmds.setFocus(panel)
    except Exception:
        pass
    return panel

def _panel_diag(capture_panel=None):
    panels = cmds.getPanel(type="modelPanel") or []
    visible = cmds.getPanel(visiblePanels=True) or []
    focused = cmds.getPanel(withFocus=True)
    items = []
    for panel in panels:
        item = {{"panel": panel, "visible": panel in visible, "focused": panel == focused, "capturePanel": panel == capture_panel}}
        for flag in ["rendererName", "displayAppearance", "displayTextures", "wireframeOnShaded", "useDefaultMaterial", "selectionHiliteDisplay"]:
            try:
                item[flag] = cmds.modelEditor(panel, q=True, **{{flag: True}})
            except Exception as exc:
                item[flag] = "ERR: " + str(exc)
        try:
            item["camera"] = cmds.modelPanel(panel, q=True, camera=True)
        except Exception as exc:
            item["camera"] = "ERR: " + str(exc)
        items.append(item)
    return {{"focusedPanel": focused, "visiblePanels": visible, "capturePanel": capture_panel, "modelPanels": items}}

def _setup_color_management():
    # The MMD shader reproduces MMD's gamma-space look under CM-on (linear texture
    # input) + an sRGB / Un-tone-mapped view transform -- the same path a normal
    # GUI import targets. Validate against that exact pipeline (previously this
    # disabled CM, which no longer matches the shader's assumptions).
    result = {{}}
    try:
        cmds.colorManagementPrefs(e=True, cmEnabled=True)
        result["cmEnabled"] = cmds.colorManagementPrefs(q=True, cmEnabled=True)
    except Exception as exc:
        result["cmEnabled_error"] = str(exc)
    # Rendering space must be sRGB-primary linear so the view transform is a pure
    # sRGB encode that the shader's output de-gamma cancels exactly. The default
    # ACEScg adds an AP1->Rec.709 primaries matrix the shader cannot undo.
    for query, edit, value in [
        ("renderingSpaceName", "renderingSpaceName", "scene-linear Rec.709-sRGB"),
        ("viewTransformName", "viewTransformName", "Un-tone-mapped (sRGB)"),
        ("displayName", "displayName", "sRGB"),
    ]:
        try:
            cmds.colorManagementPrefs(e=True, **{{edit: value}})
            result[query] = cmds.colorManagementPrefs(q=True, **{{query: True}})
        except Exception as exc:
            result[query + "_error"] = str(exc)
    return result

def _shader_diag():
    items = []
    for shader in cmds.ls(type="dx11Shader") or []:
        shader_attrs = cmds.listAttr(shader) or []
        item = {{
            "name": shader,
            "attrs": {{}},
            "incoming": {{}},
            "outputs": {{}},
            "assignments": [],
            "listTechniques": [],
            "listTechniquesError": None,
            "hardware_attrs": [a for a in shader_attrs if "hardware" in a.lower() or "effect" in a.lower() or "error" in a.lower()],
        }}
        try:
            item["listTechniques"] = cmds.dx11Shader(shader, q=True, listTechniques=True) or []
        except Exception as exc:
            item["listTechniquesError"] = str(exc)
        for attr in [
            "shader", "technique", "DiffuseColorRGB", "DiffuseColorA",
            "diagnostics", "EffectParameters",
            "AmbientColor", "SpecularColor", "Shininess", "Opacity",
            "SphereMode", "EdgeColorRGB", "EdgeSize",
            "HasMainTexture", "HasSphereTexture", "HasToonTexture",
            "mmd_texture_path", "mmd_sphere_path", "mmd_draw_flags",
        ]:
            if cmds.attributeQuery(attr, node=shader, exists=True):
                try:
                    item["attrs"][attr] = cmds.getAttr(shader + "." + attr)
                except Exception as exc:
                    item["attrs"][attr] = "ERR: " + str(exc)
        for attr in ["MainTexture", "SphereTexture", "ToonTexture"]:
            if cmds.attributeQuery(attr, node=shader, exists=True):
                try:
                    item["incoming"][attr] = cmds.listConnections(shader + "." + attr, s=True, d=False, plugs=True) or []
                except Exception as exc:
                    item["incoming"][attr] = "ERR: " + str(exc)
        for attr in ["outColor", "outTransparency", "message"]:
            if cmds.attributeQuery(attr, node=shader, exists=True):
                try:
                    item["outputs"][attr] = cmds.listConnections(shader + "." + attr, s=False, d=True, plugs=True) or []
                except Exception as exc:
                    item["outputs"][attr] = "ERR: " + str(exc)
        engines = cmds.listConnections(shader, type="shadingEngine") or []
        for engine in sorted(set(engines)):
            engine_attrs = cmds.listAttr(engine) or []
            assignment = {{
                "shadingEngine": engine,
                "members": [],
                "hardware_attrs": [a for a in engine_attrs if "hardware" in a.lower() or "shader" in a.lower()],
                "surfaceShader_inputs": cmds.listConnections(engine + ".surfaceShader", s=True, d=False, plugs=True) or [],
            }}
            try:
                assignment["members"] = cmds.sets(engine, q=True) or []
            except Exception as exc:
                assignment["members"] = "ERR: " + str(exc)
            item["assignments"].append(assignment)
        items.append(item)
    return items

def _scene_diag(capture_panel=None):
    meshes = []
    no_intermediate = cmds.ls(type="mesh", long=True, noIntermediate=True) or []
    for shape in cmds.ls(type="mesh", long=True) or []:
        item = {{"shape": shape}}
        try:
            item["transform"] = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]
        except Exception:
            item["transform"] = None
        for attr in ["intermediateObject", "visibility", "displayColors"]:
            try:
                item[attr] = cmds.getAttr(shape + "." + attr)
            except Exception as exc:
                item[attr] = "ERR: " + str(exc)
        if item.get("transform"):
            try:
                item["transformVisibility"] = cmds.getAttr(item["transform"] + ".visibility")
            except Exception as exc:
                item["transformVisibility"] = "ERR: " + str(exc)
        try:
            item["faceCount"] = cmds.polyEvaluate(shape, face=True)
        except Exception as exc:
            item["faceCount"] = "ERR: " + str(exc)
        try:
            item["shadingEngines"] = cmds.listConnections(shape, type="shadingEngine") or []
        except Exception as exc:
            item["shadingEngines"] = "ERR: " + str(exc)
        try:
            item["shadingEnginePlugs"] = cmds.listConnections(shape, type="shadingEngine", plugs=True) or []
        except Exception as exc:
            item["shadingEnginePlugs"] = "ERR: " + str(exc)
        meshes.append(item)
    return {{
        "selection": cmds.ls(selection=True, long=True) or [],
        "colorManagement": _color_management_diag(),
        "panels": _panel_diag(capture_panel),
        "noIntermediateMeshes": no_intermediate,
        "meshes": meshes,
    }}

def _mark_orig_shapes_intermediate():
    changed = []
    for shape in cmds.ls(type="mesh", long=True) or []:
        short_name = shape.split("|")[-1]
        if short_name.endswith("Orig") and cmds.attributeQuery("intermediateObject", node=shape, exists=True):
            try:
                previous = cmds.getAttr(shape + ".intermediateObject")
                cmds.setAttr(shape + ".intermediateObject", True)
                changed.append({{"shape": shape, "previous": previous, "current": cmds.getAttr(shape + ".intermediateObject")}})
            except Exception as exc:
                changed.append({{"shape": shape, "error": str(exc)}})
    return changed

def _assign_debug_lambert():
    shader = cmds.shadingNode("lambert", asShader=True, name="visualRegressionDebugRedLambert")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shader + "SG")
    cmds.setAttr(shader + ".color", 1.0, 0.0, 0.0, type="double3")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    assigned = []
    for shape in cmds.ls(type="mesh", long=True, noIntermediate=True) or []:
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        target = parents[0] if parents else shape
        try:
            cmds.sets(target, edit=True, forceElement=sg)
            assigned.append(target)
        except Exception as exc:
            assigned.append("ERR: " + target + ": " + str(exc))
    return {{"shader": shader, "shadingEngine": sg, "assigned": assigned}}

def _color_management_diag():
    result = {{}}
    for query in ["cmEnabled", "viewTransformName", "displayName", "renderingSpaceName"]:
        try:
            result[query] = cmds.colorManagementPrefs(q=True, **{{query: True}})
        except Exception as exc:
            result[query] = "ERR: " + str(exc)
    return result

def _apply_unique_shader_path():
    from mmd_tools.converters.mesh_converter import sync_dx11_generated_uniforms
    shaders = cmds.ls(type="dx11Shader") or []
    techniques = {{}}
    for shader in shaders:
        if cmds.attributeQuery("technique", node=shader, exists=True):
            techniques[shader] = cmds.getAttr(shader + ".technique") or "MMDTechnique"
        cmds.setAttr(shader + ".shader", str(_shader_fx), type="string")
    cmds.refresh(force=True)
    sync_dx11_generated_uniforms(shaders)
    for shader in shaders:
        if cmds.attributeQuery("technique", node=shader, exists=True):
            cmds.setAttr(shader + ".technique", techniques.get(shader, "MMDTechnique"), type="string")
    return shaders

def _apply_mmd_light(case):
    light = case.get("light") or {{}}
    source_direction = light.get("direction") or [0.5, -1.0, 0.5]
    # GoldenOracle/three fixture lights are authored in the source MMD/three
    # coordinate frame. The imported Maya mesh path mirrors X/Z for the DX11
    # viewport comparison, so the MMD light direction must be mirrored too.
    direction = [-float(source_direction[0]), float(source_direction[1]), -float(source_direction[2])]
    color = light.get("color") or [1.0, 1.0, 1.0]
    # The import wires each shader's MMDLightDirection/MMDLightColor to the
    # `mmd_light` controller, so those attrs are connected and cannot be
    # setAttr'd directly. Drive the controller instead (the real runtime path):
    # its world -Z feeds MMDLightDirection through the wired vectorProduct.
    from mmd_tools.converters import light_converter
    ctrl = light_converter.set_mmd_light_direction(direction, color)
    return {{"sourceDirection": source_direction, "mayaDirection": direction, "color": color, "controller": ctrl}}

def _capture_case(case):
    import importlib
    import mmd_tools.converters as converters
    import mmd_tools.converters.mesh_converter as mesh_converter
    import mmd_tools.core.mmd_parser as mmd_parser
    import mmd_tools.core.pmx_data as pmx_data
    import mmd_tools.core.pmx_data.vertex as pmx_vertex
    import mmd_tools.core.maya_utils as maya_utils
    import mmd_tools.io.mmd_importer as mmd_importer
    import mmd_tools.io.pmx_importer as pmx_importer
    import mmd_tools.io.vmd_importer as vmd_importer
    from mmd_tools.core.settings import settings

    maya_utils = importlib.reload(maya_utils)
    pmx_vertex = importlib.reload(pmx_vertex)
    pmx_data = importlib.reload(pmx_data)
    mmd_parser = importlib.reload(mmd_parser)
    mesh_converter = importlib.reload(mesh_converter)
    converters = importlib.reload(converters)
    pmx_importer = importlib.reload(pmx_importer)
    vmd_importer = importlib.reload(vmd_importer)
    mmd_importer = importlib.reload(mmd_importer)

    cmds.file(new=True, force=True)
    settings.set("import.model.create_mmd_shaders", True)
    settings.set("import.model.mmd_shader_backend", "dx11")
    try:
        cmds.loadPlugin("dx11Shader", quiet=True)
    except Exception:
        pass

    root = mmd_importer.import_mmd_file(case["model"])
    if root is None:
        raise RuntimeError("import_mmd_file returned None: " + case["model"])
    _apply_unique_shader_path()
    debug_actions = {{"mmdLight": _apply_mmd_light(case)}}
    if _hide_orig_shapes:
        debug_actions["hideOrigShapes"] = _mark_orig_shapes_intermediate()
    if _debug_lambert_control:
        debug_actions["lambertControl"] = _assign_debug_lambert()

    camera = _make_camera(case["camera"])
    _setup_color_management()
    capture_panel = _setup_panel(camera)
    frame = int(case.get("frame", 0))
    cmds.currentTime(frame)
    cmds.select(clear=True)
    for name in ["background", "backgroundTop", "backgroundBottom"]:
        try:
            cmds.displayRGBColor(name, 1, 1, 1)
        except Exception:
            pass
    cmds.refresh(force=True)

    case_dir = _output_dir / case["name"]
    case_dir.mkdir(parents=True, exist_ok=True)
    actual = case_dir / ("actual-frame-" + str(frame) + ".png")
    cmds.playblast(
        completeFilename=str(actual),
        forceOverwrite=True,
        format="image",
        compression="png",
        width=_width,
        height=_height,
        percent=100,
        showOrnaments=False,
        viewer=False,
        frame=frame,
        editorPanelName=capture_panel,
    )

    stats = _png_stats(actual)
    center_sample = _png_center_sample(actual)
    oracle = case.get("oracle_png")
    diff = _png_diff(actual, oracle) if _compare and oracle else {{"available": False, "reason": "comparison disabled or no oracle"}}
    shader_diag = _shader_diag()
    dx11_issues = []
    for shader in shader_diag:
        if not shader.get("listTechniques"):
            dx11_issues.append({{"shader": shader.get("name"), "issue": "empty dx11Shader technique list"}})
        diagnostics = shader.get("attrs", {{}}).get("diagnostics")
        if diagnostics:
            dx11_issues.append({{"shader": shader.get("name"), "issue": "dx11Shader diagnostics not empty", "diagnostics": diagnostics}})
    if center_sample.get("vp2_unassigned_green_suspected"):
        dx11_issues.append({{"issue": "center pixel resembles VP2 unassigned-material green", "center_sample": center_sample}})
    ok = int(stats.get("max", 0)) > 10 and not dx11_issues
    diag = {{
        "case": case,
        "actual_png": str(actual),
        "actual_png_stats": stats,
        "actual_png_center_sample": center_sample,
        "oracle_png": oracle,
        "diff": diff,
        "dx11_issues": dx11_issues,
        "debug_actions": debug_actions,
        "scene": _scene_diag(capture_panel),
        "shaders": shader_diag,
    }}
    diag_path = case_dir / "diagnostics.json"
    with open(diag_path, "w", encoding="utf-8") as f:
        json.dump(diag, f, ensure_ascii=False, indent=2)
    return {{
        "name": case["name"],
        "ok": ok,
        "actual_png": str(actual),
        "diagnostics": str(diag_path),
        "stats": stats,
        "center_sample": center_sample,
        "dx11_issues": dx11_issues,
        "diff": diff,
    }}

def _main():
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    _log("Visual regression cases: " + str(len(_cases)))
    report = {{
        "schemaVersion": 1,
        "kind": "maya-dx11-visual-regression-report",
        "shader_fx": str(_shader_fx),
        "output_dir": str(_output_dir),
        "deviceInformation": None,
        "dx11_device_valid": False,
        "results": [],
        "errors": [],
    }}
    try:
        dev = cmds.ogs(deviceInformation=True)
        report["deviceInformation"] = dev
        report["dx11_device_valid"] = "DirectX" in str(dev) or "DX11" in str(dev)
    except Exception as exc:
        report["deviceInformation"] = "ERR: " + str(exc)

    for case in _cases:
        _log("CAPTURE " + case["name"])
        try:
            result = _capture_case(case)
            report["results"].append(result)
            _log("  -> " + str(result["actual_png"]))
        except Exception as exc:
            error = {{"name": case.get("name"), "error": str(exc), "traceback": traceback.format_exc()}}
            report["errors"].append(error)
            _log("  ERROR " + str(exc))

    report_path = _output_dir / "visual-regression-report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    _log("REPORT " + str(report_path))
    _log(_completion_marker)

_main()
"""


def main() -> int:
    args = _parse_args()
    project_root = _project_root()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = (project_root / manifest_path).resolve()
    output_dir = Path(args.out)
    if not output_dir.is_absolute():
        output_dir = (project_root / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    _, cases = _load_cases(manifest_path, args.case, args.tag, args.limit)
    if not cases:
        raise RuntimeError("No manifest cases selected.")

    shader_fx = _prepare_shader(project_root, output_dir, args.shader_fx)
    log_path = output_dir / "maya_visual_regression.log"
    report_path = output_dir / "visual-regression-report.json"

    LOGGER.info("Manifest: %s", manifest_path)
    LOGGER.info("Cases: %d", len(cases))
    LOGGER.info("Unique shader: %s", shader_fx)

    proc: subprocess.Popen | None = None
    try:
        if args.attach_existing:
            LOGGER.info("Attaching to existing Maya commandPort :%d", args.port)
        else:
            LOGGER.info("Maya executable: %s", maya_commandport.maya_exe(args.maya))
            proc = maya_commandport.launch_maya(
                version=args.maya,
                project_root=project_root,
                output_dir=output_dir,
                port=args.port,
                launch_mode=args.launch_mode,
                env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11"},
            )
        maya_commandport.wait_for_port(args.port, args.timeout, proc)
        code = _build_maya_code(
            project_root=project_root,
            cases=cases,
            shader_fx=shader_fx,
            output_dir=output_dir,
            log_path=log_path,
            width=args.width,
            height=args.height,
            compare=not args.no_compare,
            debug_lambert_control=args.debug_lambert_control,
            hide_orig_shapes=args.hide_orig_shapes,
        )
        maya_commandport.send_python(args.port, code, label="<maya-visual-regression>")
        _monitor_log(log_path, args.timeout)
    finally:
        if proc is None and not args.attach_existing and not args.keep_maya:
            maya_commandport.quit_maya(args.port)
        if proc is not None and not args.keep_maya:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=20)
            maya_commandport.close_process_logs(proc)

    if not report_path.is_file():
        raise RuntimeError(f"Maya-side report was not written: {report_path}")
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)
    if report.get("errors"):
        raise RuntimeError(f"Visual regression capture had {len(report['errors'])} error(s): {report['errors'][:2]}")
    if not report.get("dx11_device_valid"):
        raise RuntimeError("Viewport device did not report DirectX/DX11. See report diagnostics.")
    blank = [r for r in report.get("results", []) if not r.get("ok")]
    if blank:
        raise RuntimeError(f"Blank-like captures detected: {[r.get('name') for r in blank]}")

    LOGGER.info("Visual regression report: %s", report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
