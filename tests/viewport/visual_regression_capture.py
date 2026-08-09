"""Manifest-driven Maya GUI viewport visual regression harness.

This script launches a fresh Maya GUI process, opens a Python commandPort,
imports selected GoldenOracle render-manifest fixtures with hardware shaders
materials, captures Viewport 2.0 PNGs, and writes report-only diagnostics.
It refuses to reuse an already-open commandPort unless ``--attach-existing``
is explicit, and records selected shader plug-in lifecycle state around cases.
The opt-in ``--enable-mmd-self-shadow`` mode records Maya's native shadow
inputs and their post-VP2 values; it is a diagnostic gate, not a parity claim.

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
# Generated PMX GoldenOracle captures use the MMD material-light defaults, not
# the generic host-light values retained in fixture.render.json.  Keep these
# defaults in the Maya capture harness so a manifest without a case-specific
# light does not silently render the same PMX under a different colour model.
_MMD_DEFAULT_LIGHT_TRAVEL_DIRECTION = (0.5, -1.0, 1.0)
_MMD_DEFAULT_LIGHT_COLOR = 154.0 / 255.0
BACKEND_CONFIG = {
    "dx11": {"node_type": "dx11Shader", "plugin": "dx11Shader", "vp2_device": "VirtualDeviceDx11"},
    "glsl": {"node_type": "GLSLShader", "plugin": "glslShader", "vp2_device": "VirtualDeviceGLCore"},
}

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
    parser.add_argument("--shader-backend", choices=sorted(BACKEND_CONFIG), default="dx11")
    parser.add_argument(
        "--display-textures",
        choices=["on", "off"],
        default="on",
        help="Set Maya model-panel displayTextures for the capture (default: on).",
    )
    parser.add_argument(
        "--vp2-device",
        choices=["default", "gl", "glcore", "dx11"],
        default="default",
        help="VP2 device override. default selects the backend-compatible device.",
    )
    parser.add_argument(
        "--launch-mode",
        choices=["direct", "powershell", "explorer"],
        default="explorer" if os.name == "nt" else "direct",
        help="How to launch Maya when not attaching. explorer is the stable detached Windows route.",
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
        "--debug-outline-sentinel",
        action="store_true",
        help="Set a vivid edge color without changing DX11 technique or EdgeSize.",
    )
    parser.add_argument(
        "--enable-mmd-self-shadow",
        action="store_true",
        help=(
            "Opt in to Maya native shadow inputs for MMD receivers; maps "
            "mmd_draw_flags bit 0x08 to UseShadows and enables viewport shadows."
        ),
    )
    parser.add_argument(
        "--hide-orig-shapes",
        action="store_true",
        help="Temporarily mark *Orig mesh shapes as intermediate before capture.",
    )
    return parser.parse_args()


def _load_cases(
    manifest_path: Path,
    names: list[str],
    tags: list[str],
    limit: int,
) -> tuple[dict, list[dict]]:
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    defaults = manifest.get("defaults", {})
    selected: list[dict] = []
    wanted = set(names)
    wanted_tags = set(tags)
    manifest_dir = manifest_path.parent

    def uses_generated_mmd_light(case: dict) -> bool:
        """Identify Three-generated PMX fixtures with baked MMD light defaults."""
        assets = case.get("assets") or {}
        metadata = case.get("metadata") or {}
        model_ref = str(assets.get("model", "")).replace("\\", "/").lower()
        notes = str(metadata.get("notes", "")).lower()
        raw_tags = metadata.get("tags", [])
        tags = {str(tag).lower() for tag in raw_tags} if isinstance(raw_tags, (list, tuple, set)) else set()
        if "light-vmd" in tags:
            return False
        return "/generated/" in f"/{model_ref}" or "three-mmd-loader generated fixture" in notes

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
        case_light = case.get("light") if isinstance(case.get("light"), dict) else {}
        metadata_light = case.get("metadata", {}).get("light", {})
        if not isinstance(metadata_light, dict):
            metadata_light = {}
        light = dict(defaults.get("light", {}))
        light.update(case_light)
        light.update(metadata_light)
        if not case_light and not metadata_light and uses_generated_mmd_light(case):
            # The manifest's top-level light is a legacy host-scene default.
            # Three's generated-PMX baseline keeps the MMD material light
            # (154/255) and its canonical travel direction instead.
            light["direction"] = list(_MMD_DEFAULT_LIGHT_TRAVEL_DIRECTION)
            light["color"] = [_MMD_DEFAULT_LIGHT_COLOR] * 3
            light["source"] = "mmd-default"
        elif case_light or metadata_light:
            light["source"] = "case-override"
        frames = case.get("frames", [defaults.get("frame", 0)])
        assets = case.get("assets") or {}
        camera_motion_rel = (
            assets.get("cameraMotion")
            or assets.get("camera_motion")
            or assets.get("cameraVmd")
            or assets.get("camera_vmd")
        )
        if isinstance(camera_motion_rel, dict):
            camera_motion_rel = (
                camera_motion_rel.get("path")
                or camera_motion_rel.get("file")
                or camera_motion_rel.get("vmd")
            )
        camera_motion = (manifest_dir / camera_motion_rel).resolve() if camera_motion_rel else None

        model = (manifest_dir / case["assets"]["model"]).resolve()
        oracle_rel = case.get("oracle", {}).get("path")
        oracle_dir = (manifest_dir / oracle_rel).resolve().parent if oracle_rel else None
        oracle_png = oracle_dir / f"frame-{case.get('frames', [0])[0]}.png" if oracle_dir else None

        selected.append(
            {
                "name": name,
                "kind": case.get("kind"),
                "model": str(model),
                "frame": int(frames[0]),
                "camera": camera,
                "camera_motion": str(camera_motion) if camera_motion else None,
                "image": image,
                "light": light,
                "oracle_png": str(oracle_png) if oracle_png else None,
                "metadata": case.get("metadata", {}),
            }
        )

    if limit > 0:
        selected = selected[:limit]

    return manifest, selected


def _validate_camera_motion_data(vmd_data: object, vmd_path: Path) -> int:
    """Require at least one camera frame before importing a camera VMD."""
    frames = getattr(vmd_data, "camera_frames", None)
    if not frames:
        raise RuntimeError(f"Camera Motion VMD has no camera frames: {vmd_path}")
    try:
        return len(frames)
    except TypeError as exc:
        raise RuntimeError(f"Camera Motion VMD camera frames are invalid: {vmd_path}") from exc


def _camera_plan_for_case(case: dict) -> dict:
    """Describe whether a case uses its manifest camera or a Maya VMD camera."""
    case_name = case.get("name", "<unnamed>")
    camera_motion = case.get("camera_motion")
    if camera_motion:
        return {
            "source": "maya-vmd-camera-import",
            "vmd": str(camera_motion),
            "frame": int(case.get("frame", 0)),
        }
    camera = case.get("camera")
    if not isinstance(camera, dict):
        raise RuntimeError(f"Manifest camera is missing for case {case_name}")
    return {"source": "manifest", "parameters": dict(camera)}


def _camera_plan(cases: list[dict]) -> dict[str, dict]:
    """Build immutable camera provenance for selected cases."""
    return {case["name"]: _camera_plan_for_case(case) for case in cases}


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


def _vp2_override(backend: str, device: str) -> str:
    if device == "default":
        return BACKEND_CONFIG[backend]["vp2_device"]
    return {"gl": "VirtualDeviceGL", "glcore": "VirtualDeviceGLCore", "dx11": "VirtualDeviceDx11"}[device]


def _device_matches_backend(backend: str, device_information: object) -> bool:
    text = str(device_information).lower()
    if backend == "dx11":
        return "directx" in text or "dx11" in text
    return "opengl" in text or "gl core" in text or "glcore" in text


def _preflight_command_port(port: int, attach_existing: bool) -> None:
    """Refuse to launch against a commandPort owned by another Maya session.

    A fresh capture owns the Maya process and may quit it during cleanup.  An
    already-open port is therefore unsafe unless the caller explicitly opted
    into ``--attach-existing``.
    """
    if attach_existing:
        return
    if maya_commandport.is_port_open(port):
        raise RuntimeError(
            f"commandPort :{port} is already open; refusing to attach without "
            "--attach-existing (choose a free --port or opt in explicitly)"
        )


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
    shader_backend: str,
    display_textures: bool = True,
    debug_outline_sentinel: bool = False,
    enable_mmd_self_shadow: bool = False,
) -> str:
    camera_plans = _camera_plan(cases)
    payload = {
        "project_root": str(project_root),
        "cases": cases,
        "camera_plans": camera_plans,
        "shader_fx": str(shader_fx),
        "output_dir": str(output_dir),
        "log_path": str(log_path),
        "width": width,
        "height": height,
        "compare": compare,
        "debug_lambert_control": debug_lambert_control,
        "debug_outline_sentinel": debug_outline_sentinel,
        "hide_orig_shapes": hide_orig_shapes,
        "shader_backend": shader_backend,
        "shader_node_type": BACKEND_CONFIG[shader_backend]["node_type"],
        "shader_plugin": BACKEND_CONFIG[shader_backend]["plugin"],
        "display_textures": bool(display_textures),
        "enable_mmd_self_shadow": bool(enable_mmd_self_shadow),
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
_camera_plans = _payload["camera_plans"]
_width = int(_payload["width"])
_height = int(_payload["height"])
_compare = bool(_payload["compare"])
_debug_lambert_control = bool(_payload["debug_lambert_control"])
_debug_outline_sentinel = bool(_payload["debug_outline_sentinel"])
_hide_orig_shapes = bool(_payload["hide_orig_shapes"])
_shader_backend = _payload["shader_backend"]
_display_textures = bool(_payload.get("display_textures", True))
_enable_mmd_self_shadow = bool(_payload.get("enable_mmd_self_shadow", False))
_shader_node_type = _payload["shader_node_type"]
_shader_plugin_name = _payload["shader_plugin"]
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
    near_black_pixels = 0
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
        for i in range(0, stride, channels):
            if max(recon[i:i+3]) <= 32:
                near_black_pixels += 1
        prev = recon
    return {{
        "width": width,
        "height": height,
        "min": min_v,
        "max": max_v,
        "mean": total / max(count, 1),
        "samples": count,
        "near_black_rgb_pixels": near_black_pixels,
        "pixel_count": width * height,
    }}

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
        # Maya's bundled Python may not have Pillow.  Reuse the host gate's
        # standard-library decoder so a missing optional package never turns
        # a real Oracle comparison into an inconclusive capture report.
        try:
            from tests.viewport.visual_regression_compare import _image_metrics

            return {{
                "available": True,
                "comparator": "stdlib",
                "pillow_error": str(exc),
                "metrics": _image_metrics(b_path, a_path),
            }}
        except Exception as fallback_exc:
            return {{
                "available": False,
                "reason": f"Pillow: {{exc}}; stdlib fallback: {{fallback_exc}}",
            }}

def _make_camera(camera):
    cam, shape = cmds.camera(name="visualRegressionCam")
    cmds.xform(cam, ws=True, t=camera["position"])
    loc = cmds.spaceLocator(name="__visual_regression_target__")[0]
    cmds.xform(loc, ws=True, t=camera["target"])
    up = camera.get("up")
    if up is None:
        con = cmds.aimConstraint(loc, cam, aimVector=(0, 0, -1), upVector=(0, 1, 0), worldUpType="scene")[0]
    else:
        con = cmds.aimConstraint(
            loc,
            cam,
            aimVector=(0, 0, -1),
            upVector=(0, 1, 0),
            worldUpType="vector",
            worldUpVector=tuple(float(value) for value in up),
        )[0]
    cmds.delete(con, loc)
    fov = float(camera.get("fov", 25))
    aperture = cmds.getAttr(shape + ".horizontalFilmAperture")
    focal = (aperture * 25.4 * 0.5) / math.tan(math.radians(fov) * 0.5)
    cmds.setAttr(shape + ".focalLength", focal)
    cmds.setAttr(shape + ".nearClipPlane", float(camera.get("near", 0.1)))
    cmds.setAttr(shape + ".farClipPlane", float(camera.get("far", 1000)))
    return cam

def _setup_panel(camera, display_textures=True):
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
    editor_flags = dict(
        e=True,
        rendererName="vp2Renderer",
        displayAppearance="smoothShaded",
        displayTextures=bool(display_textures),
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
    if _enable_mmd_self_shadow:
        editor_flags.update(displayLights="all", shadows=True, interactiveDisableShadows=False)
    cmds.modelEditor(panel, **editor_flags)
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
        for flag in [
            "rendererName", "displayAppearance", "displayTextures", "wireframeOnShaded",
            "useDefaultMaterial", "selectionHiliteDisplay", "displayLights", "shadows",
            "interactiveDisableShadows",
        ]:
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
    for shader in cmds.ls(type=_shader_node_type) or []:
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
        if _shader_backend == "dx11":
            try:
                item["listTechniques"] = cmds.dx11Shader(shader, q=True, listTechniques=True) or []
            except Exception as exc:
                item["listTechniquesError"] = str(exc)
        for attr in [
            "shader", "technique", "DiffuseColorRGB", "DiffuseColorA",
            "diagnostics", "EffectParameters",
            "AmbientColor", "SpecularColor", "Shininess", "Opacity",
            "MMDLightDirection", "MMDLightColor",
            "MmdControllerLightVector", "MmdControllerLightRgb",
            "SphereMode", "EdgeColorRGB", "EdgeSize", "DevicePixelRatio",
            "HasMainTexture", "HasSphereTexture", "HasToonTexture",
            "UseShadows", "ShadowStrength", "ShadowBias", "Light0ShadowMap", "Light0Matrix",
            "mmd_texture_path", "mmd_sphere_path", "mmd_draw_flags",
        ]:
            if cmds.attributeQuery(attr, node=shader, exists=True):
                try:
                    item["attrs"][attr] = cmds.getAttr(shader + "." + attr)
                except Exception as exc:
                    item["attrs"][attr] = "ERR: " + str(exc)
        for attr in [
            "MainTexture", "SphereTexture", "ToonTexture",
            "MMDLightDirection", "MMDLightColor",
            "MmdControllerLightVector", "MmdControllerLightRgb",
        ]:
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

def _shader_plugin_diag():
    # Collect selected shader plug-in lifecycle state without changing it.
    state = {{"name": _shader_plugin_name}}
    for key in ["loaded", "autoload", "registered", "path"]:
        try:
            state[key] = cmds.pluginInfo(_shader_plugin_name, query=True, **{{key: True}})
        except Exception as exc:
            state[key] = "ERR: " + str(exc)
    return state

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

def _apply_outline_sentinel():
    # Make accidental edge-pass execution visually unmistakable.
    changed = []
    if _shader_backend != "dx11":
        return changed
    for shader in cmds.ls(type=_shader_node_type) or []:
        item = {{"shader": shader}}
        try:
            item["technique"] = cmds.getAttr(shader + ".technique")
            cmds.setAttr(shader + ".EdgeColorRGB", 1.0, 0.0, 1.0, type="double3")
            cmds.setAttr(shader + ".EdgeColorA", 1.0)
            item["edgeColorRGB"] = [1.0, 0.0, 1.0]
            item["edgeSize"] = cmds.getAttr(shader + ".EdgeSize")
        except Exception as exc:
            item["error"] = str(exc)
        changed.append(item)
    return changed

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
    shaders = cmds.ls(type=_shader_node_type) or []
    if _shader_backend != "dx11":
        sync_dx11_generated_uniforms(shaders)
        return shaders
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

def _apply_case_morph_weights(root, case):
    # Apply optional model-scoped morph input weights requested by a visual case.
    requests = (case.get("metadata") or {{}}).get("morph_weights") or []
    if not requests:
        return None
    controllers = cmds.listConnections(
        root + ".mmd_morph_controller", source=True, destination=False
    ) or []
    if len(controllers) != 1:
        raise RuntimeError(
            "visual morph case requires exactly one model-scoped morph controller: "
            + str(controllers)
        )
    controller = controllers[0]
    applied = []
    for request in requests:
        index = int(request["index"])
        weight = float(request["weight"])
        if index < 0:
            raise ValueError("visual morph weight index must be non-negative")
        plug = controller + ".inputWeight[" + str(index) + "]"
        cmds.setAttr(plug, weight)
        applied.append({{"index": index, "weight": weight, "plug": plug}})
    cmds.refresh(force=True)
    return {{"controller": controller, "weights": applied}}


def _configure_mmd_self_shadow_inputs(light_controller, phase):
    # dx11Shader effect attributes are created lazily by Maya. Keep this probe
    # explicit and fail-closed: a missing UseShadows input is evidence that the
    # imported node is not a shadow consumer, not a reason to mutate the shader
    # graph or claim self-shadow parity.
    result = {{"phase": phase, "enabled": _enable_mmd_self_shadow, "shaders": [], "lightShapes": []}}
    if not _enable_mmd_self_shadow:
        return result
    for shader in cmds.ls(type=_shader_node_type) or []:
        item = {{"shader": shader, "drawFlags": None, "requestedUseShadows": None, "actualUseShadows": None}}
        try:
            if cmds.attributeQuery("mmd_draw_flags", node=shader, exists=True):
                item["drawFlags"] = int(cmds.getAttr(shader + ".mmd_draw_flags"))
            if not cmds.attributeQuery("UseShadows", node=shader, exists=True):
                item["reason"] = "UseShadows attribute unavailable"
            else:
                requested = bool((item["drawFlags"] or 0) & 0x08)
                item["requestedUseShadows"] = requested
                cmds.setAttr(shader + ".UseShadows", requested)
                item["actualUseShadows"] = bool(cmds.getAttr(shader + ".UseShadows"))
        except Exception as exc:
            item["reason"] = str(exc)
        result["shaders"].append(item)
    if light_controller and cmds.objExists(light_controller):
        light_shapes = cmds.listRelatives(light_controller, shapes=True, type="directionalLight") or []
    else:
        light_shapes = []
    result["lightShapes"] = light_shapes
    for shape in light_shapes:
        light_item = {{"shape": shape, "requested": {{}}, "actual": {{}}, "errors": {{}}}}
        for attr in ("useDepthMapShadows", "useRayTraceShadows"):
            if not cmds.attributeQuery(attr, node=shape, exists=True):
                light_item["errors"][attr] = "attribute unavailable"
                continue
            try:
                cmds.setAttr(shape + "." + attr, True)
                light_item["requested"][attr] = True
                light_item["actual"][attr] = bool(cmds.getAttr(shape + "." + attr))
            except Exception as exc:
                light_item["errors"][attr] = str(exc)
        result.setdefault("lights", []).append(light_item)
    return result

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
    return {{
        "source": light.get("source", "manifest"),
        "sourceDirection": source_direction,
        "mayaDirection": direction,
        "color": color,
        "controller": ctrl,
    }}

def _import_vmd_camera(vmd_path):
    # Import a camera-motion VMD through the production Maya converter.
    from mmd_tools.converters.vmd_converter import VmdConverter
    from mmd_tools.core.vmd_data import VmdData

    path = Path(vmd_path)
    if not path.is_file():
        raise RuntimeError("Camera Motion VMD does not exist: " + str(path))
    try:
        vmd_data = VmdData().parse_file(str(path))
        camera_frames = getattr(vmd_data, "camera_frames", None)
        if not camera_frames:
            raise RuntimeError("Camera Motion VMD has no camera frames: " + str(path))
    except Exception as exc:
        raise RuntimeError("Could not parse Camera Motion VMD " + str(path) + ": " + str(exc)) from exc

    try:
        converter = VmdConverter()
        converter.use_animation_layers = False
        converter.import_camera_animation = True
        converter.import_light_animation = False
        converted = converter.convert(
            vmd_data,
            bake_mode=False,
            vmd_bytes=path.read_bytes(),
            scene_animation_only=True,
        )
        if not converted:
            raise RuntimeError("VmdConverter.convert returned false")
        camera = converter._get_or_create_camera()
    except Exception as exc:
        raise RuntimeError("Could not import Camera Motion VMD " + str(path) + ": " + str(exc)) from exc
    if not camera or not cmds.objExists(camera):
        raise RuntimeError("Camera Motion VMD did not create a Maya camera: " + str(path))
    return camera

def _capture_case(case):
    import importlib
    import mmd_tools.converters as converters
    import mmd_tools.converters.mesh_converter as mesh_converter
    import mmd_tools.converters.morph_converter as morph_converter
    import mmd_tools.core.mmd_parser as mmd_parser
    import mmd_tools.core.pmx_data as pmx_data
    import mmd_tools.core.pmx_data.vertex as pmx_vertex
    import mmd_tools.io.mmd_importer as mmd_importer
    import mmd_tools.io.pmx_importer as pmx_importer
    import mmd_tools.io.vmd_importer as vmd_importer
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin
    from mmd_tools.core.settings import settings

    pmx_vertex = importlib.reload(pmx_vertex)
    pmx_data = importlib.reload(pmx_data)
    mmd_parser = importlib.reload(mmd_parser)
    mesh_converter = importlib.reload(mesh_converter)
    morph_converter = importlib.reload(morph_converter)
    converters = importlib.reload(converters)
    pmx_importer = importlib.reload(pmx_importer)
    vmd_importer = importlib.reload(vmd_importer)
    mmd_importer = importlib.reload(mmd_importer)

    cmds.file(new=True, force=True)
    load_mmd_tools_plugin(_project_root)
    settings.set("import.model.create_mmd_shaders", True)
    settings.set("import.model.mmd_shader_backend", _shader_backend)

    root = mmd_importer.import_mmd_file(case["model"])
    if root is None:
        raise RuntimeError("import_mmd_file returned None: " + case["model"])
    _apply_unique_shader_path()
    debug_actions = {{"mmdLight": _apply_mmd_light(case)}}
    morph_weights = _apply_case_morph_weights(root, case)
    if morph_weights is not None:
        debug_actions["morphWeights"] = morph_weights
    light_controller = debug_actions["mmdLight"].get("controller")
    if _enable_mmd_self_shadow:
        debug_actions["mmdSelfShadow"] = {{
            "controller": light_controller,
            "prePanel": _configure_mmd_self_shadow_inputs(light_controller, "pre-panel"),
        }}
    # The Python RenderOverride caster discovery was removed with the legacy
    # override path.  Keep the report explicit until a native self-shadow
    # Oracle and production caster pass exist; do not invent parity evidence.
    debug_actions["selfShadowCasterSelection"] = {{
        "status": "unavailable",
        "reason": "native self-shadow Oracle and caster pass are not available",
        "roots": [],
        "components": [],
        "flaggedMaterials": [],
        "skippedMaterials": [],
    }}
    if _hide_orig_shapes:
        debug_actions["hideOrigShapes"] = _mark_orig_shapes_intermediate()
    if _debug_lambert_control:
        debug_actions["lambertControl"] = _assign_debug_lambert()
    if _debug_outline_sentinel:
        debug_actions["outlineSentinel"] = _apply_outline_sentinel()

    camera_plan = _camera_plans.get(case["name"])
    if not isinstance(camera_plan, dict):
        raise RuntimeError("Camera plan is missing for " + str(case.get("name")))
    if camera_plan.get("source") == "maya-vmd-camera-import":
        camera = _import_vmd_camera(camera_plan.get("vmd"))
        effective_camera = dict(camera_plan)
        effective_camera["camera"] = camera
    elif camera_plan.get("source") == "manifest":
        parameters = camera_plan.get("parameters")
        if not isinstance(parameters, dict):
            raise RuntimeError("Manifest camera parameters are missing for " + str(case.get("name")))
        camera = _make_camera(parameters)
        effective_camera = dict(camera_plan)
    else:
        raise RuntimeError("Unsupported camera plan source for " + str(case.get("name")))
    _setup_color_management()
    capture_panel = _setup_panel(camera, _display_textures)
    if _enable_mmd_self_shadow:
        debug_actions["mmdSelfShadow"]["postPanel"] = _configure_mmd_self_shadow_inputs(
            light_controller, "post-panel"
        )
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
    shader_issues = []
    if not shader_diag:
        shader_issues.append({{"issue": "no " + _shader_node_type + " nodes found after import"}})
    for shader in shader_diag:
        if _shader_backend == "dx11" and not shader.get("listTechniques"):
            shader_issues.append({{"shader": shader.get("name"), "issue": "empty dx11Shader technique list"}})
        diagnostics = shader.get("attrs", {{}}).get("diagnostics")
        if diagnostics:
            shader_issues.append({{"shader": shader.get("name"), "issue": _shader_node_type + " diagnostics not empty", "diagnostics": diagnostics}})
    if center_sample.get("vp2_unassigned_green_suspected"):
        shader_issues.append({{"issue": "center pixel resembles VP2 unassigned-material green", "center_sample": center_sample}})
    ok = int(stats.get("max", 0)) > 10 and not shader_issues
    diag = {{
        "case": case,
        "actual_png": str(actual),
        "actual_png_stats": stats,
        "actual_png_center_sample": center_sample,
        "oracle_png": oracle,
        "diff": diff,
        "shader_backend": _shader_backend,
        "shader_node_type": _shader_node_type,
        "display_textures": _display_textures,
        "mmd_self_shadow_enabled": _enable_mmd_self_shadow,
        "shader_issues": shader_issues,
        "debug_actions": debug_actions,
        "effective_camera": effective_camera,
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
        "oracle_png": oracle,
        "diagnostics": str(diag_path),
        "stats": stats,
        "center_sample": center_sample,
        "shader_backend": _shader_backend,
        "shader_issues": shader_issues,
        "effective_camera": effective_camera,
        "display_textures": _display_textures,
        "mmd_self_shadow_enabled": _enable_mmd_self_shadow,
        "diff": diff,
    }}

def _mmd_plugin_name_for_path(plugin_path):
    expected = plugin_path.resolve()
    for name in cmds.pluginInfo(query=True, listPlugins=True) or []:
        try:
            if not cmds.pluginInfo(name, query=True, loaded=True):
                continue
            loaded_path = Path(cmds.pluginInfo(name, query=True, path=True)).resolve()
        except Exception:
            continue
        if os.path.normcase(str(loaded_path)) == os.path.normcase(str(expected)):
            return str(name)
    return None

def _ensure_mmd_tools_plugin():
    candidates = [
        _project_root / "plug-ins" / "mmd_tools_plugin.py",
        _project_root / "mmd_tools" / "plugin_main.py",
    ]
    for plugin_path in candidates:
        plugin_name = _mmd_plugin_name_for_path(plugin_path)
        if plugin_name:
            missing = sorted({{"mmdMorphController"}} - set(cmds.allNodeTypes() or []))
            if missing:
                raise RuntimeError(
                    "canonical mmd_tools plugin is loaded but node registration is incomplete: "
                    + ", ".join(missing)
                )
            return {{
                "path": str(plugin_path),
                "name": plugin_name,
                "loaded": True,
                "reused": True,
                "morph_node_registered": True,
            }}

    if "mmdMorphController" in (cmds.allNodeTypes() or []):
        raise RuntimeError(
            "mmdMorphController is already registered by a non-canonical MMD plugin path"
        )
    target = next((path for path in candidates if path.is_file()), None)
    if target is None:
        raise RuntimeError("mmd_tools plugin entrypoint not found: " + str(candidates[0]))
    cmds.loadPlugin(str(target), quiet=True)
    plugin_name = _mmd_plugin_name_for_path(target)
    if not plugin_name:
        raise RuntimeError("mmd_tools plugin did not remain loaded: " + str(target))
    missing = sorted({{"mmdMorphController"}} - set(cmds.allNodeTypes() or []))
    if missing:
        raise RuntimeError(
            "mmd_tools plugin registration incomplete: " + ", ".join(missing)
        )
    return {{
        "path": str(target),
        "name": plugin_name,
        "loaded": True,
        "reused": False,
        "morph_node_registered": True,
    }}

def _main():
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    _log("Visual regression cases: " + str(len(_cases)))
    report = {{
        "schemaVersion": 2,
        "kind": "maya-visual-regression-report",
        "shader_backend": _shader_backend,
        "shader_node_type": _shader_node_type,
        "shader_fx": str(_shader_fx),
        "output_dir": str(_output_dir),
        "deviceInformation": None,
        "vp2_device_valid": False,
        "mmd_tools_plugin": {{
            "path": None,
            "loaded": False,
            "error": None,
        }},
        "shader_plugin": {{"name": _shader_plugin_name, "before": None, "after": None}},
        "results": [],
        "errors": [],
    }}
    try:
        dev = cmds.ogs(deviceInformation=True)
        report["deviceInformation"] = dev
        device_text = str(dev).lower()
        report["vp2_device_valid"] = ("directx" in device_text or "dx11" in device_text) if _shader_backend == "dx11" else ("opengl" in device_text or "gl core" in device_text or "glcore" in device_text)
    except Exception as exc:
        report["deviceInformation"] = "ERR: " + str(exc)

    # Load the production MMD plug-in before importing any PMX.  Morph-bearing
    # fixtures require mmdMorphController; a shader-only preload is not enough
    # and otherwise makes the all-cases report fail before rendering starts.
    try:
        report["mmd_tools_plugin"] = _ensure_mmd_tools_plugin()
    except Exception as exc:
        report["mmd_tools_plugin"]["error"] = str(exc)
        _log("MMD plugin preload failed: " + str(exc))
        raise

    # Load the selected hardware-shader plug-in once before the baseline
    # snapshot so the before/after report can prove it remained available for
    # the full capture.  This is intentionally not an autoload change.
    try:
        cmds.loadPlugin(_shader_plugin_name, quiet=True)
    except Exception as exc:
        report["shader_plugin"]["preload_error"] = str(exc)
    report["shader_plugin"]["before"] = _shader_plugin_diag()
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

    report["shader_plugin"]["after"] = _shader_plugin_diag()

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

    _, cases = _load_cases(
        manifest_path,
        args.case,
        args.tag,
        args.limit,
    )
    if not cases:
        raise RuntimeError("No manifest cases selected.")
    _camera_plan(cases)
    shader_fx = _prepare_shader(project_root, output_dir, args.shader_fx)
    log_path = output_dir / "maya_visual_regression.log"
    report_path = output_dir / "visual-regression-report.json"

    LOGGER.info("Manifest: %s", manifest_path)
    LOGGER.info("Cases: %d", len(cases))
    LOGGER.info("Shader backend: %s", args.shader_backend)
    LOGGER.info("Unique shader: %s", shader_fx)

    _preflight_command_port(args.port, args.attach_existing)

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
                env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": _vp2_override(args.shader_backend, args.vp2_device)},
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
            debug_outline_sentinel=args.debug_outline_sentinel,
            hide_orig_shapes=args.hide_orig_shapes,
            shader_backend=args.shader_backend,
            display_textures=args.display_textures == "on",
            enable_mmd_self_shadow=args.enable_mmd_self_shadow,
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
    if not report.get("vp2_device_valid"):
        raise RuntimeError(f"Viewport device does not match {args.shader_backend}. See report diagnostics.")
    blank = [r for r in report.get("results", []) if not r.get("ok")]
    if blank:
        raise RuntimeError(f"Blank-like captures detected: {[r.get('name') for r in blank]}")

    LOGGER.info("Visual regression report: %s", report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
