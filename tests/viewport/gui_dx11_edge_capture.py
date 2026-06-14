"""
GUI DX11 edge capture: launch Maya GUI with DX11 VP2 device,
import edge-enabled PMX, capture before/after shader screenshots
from Maya Viewport 2.0, and write diagnostic JSON.

Usage (system Python, must have Maya on PATH):
    python tests/viewport/gui_dx11_edge_capture.py --maya 2024

Flow:
  1. (host)  Extract --before-ref/--before-fx MMDShader.fx -> build/captures/gui-dx11/MMDShader.before.fx
             Copy   working-tree MMDShader.fx -> build/captures/gui-dx11/MMDShader.after.fx
  2. (host)  Launch Maya GUI with MAYA_VP2_DEVICE_OVERRIDE=VirtualDeviceDx11
  3. (host)  Send capture command via commandPort
  4. (Maya)  Import edge-enabled PMX fixture with dx11 shader backend
  5. (Maya)  Create modelPanel, frame model in Viewport 2.0
  6. (Maya)  Swap dx11Shader to before.fx, capture before.png
  7. (Maya)  Swap dx11Shader to after.fx,  capture after.png
  8. (Maya)  Write diagnostic JSON under build/captures/gui-dx11/
  9. (host)  Read log, clean up Maya, report results

The diagnostic JSON includes deviceInformation, shader paths, technique,
output PNG paths, and nonblank PNG stats (min/max/avg pixel values).

Reuses _resolve_actual_png and _check_png_not_blank logic from
static_render_capture.py (copied here for self-contained execution
inside Maya GUI, not mayapy standalone).

Use --prepare-only when only shader/fixture preparation is desired. A normal
run fails unless Maya GUI launches and the diagnostic JSON proves DX11 VP2
capture with nonblank before/after PNGs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import socket
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

# Lazily imported for PMX fixture generation (requires mmd_tools on sys.path)
# from mmd_tools.core.pmx_data import PmxData
# from mmd_tools.core.pmx_data.material import PmxDrawFlag

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MAYA_VERSION = "2024"
COMMAND_PORT = 7721
LOG_FILE_NAME = "gui_dx11_edge_capture.log"
MAYA_START_TIMEOUT = 120  # seconds
CAPTURE_TIMEOUT = 300  # seconds
LOG_POLL_INTERVAL = 1  # second

CAPTURES_DIR = Path("build/captures/gui-dx11")
OUTPUT_DIR_REL = CAPTURES_DIR  # relative to project root
SHADER_BEFORE_NAME = "MMDShader.before.fx"
SHADER_AFTER_NAME = "MMDShader.after.fx"
LOG_COMPLETION_MARKER = "//-- GUI DX11 EDGE CAPTURE FINISHED --//"

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ===================================================================
# Host-side helpers (run outside Maya, in system Python)
# ===================================================================


def _get_project_root() -> Path:
    """Return the project root (two levels up from this file)."""
    return Path(__file__).resolve().parents[2]


def _sha256_file(path: Path) -> str:
    """Return SHA-256 digest for *path*."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_shader_files(
    project_root: Path,
    output_dir: Path,
    before_ref: str,
    before_fx: str | None = None,
) -> tuple[Path, Path]:
    """Extract committed and working-tree MMDShader.fx into captures dir.

    Returns (before_fx_path, after_fx_path).
    Before  = explicit --before-fx, or the file at --before-ref.
    After   = working-tree version (with corrected CullMode + drawContext).

    Both paths are under the selected output directory.
    """
    out_dir = output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    shader_rel = "mmd_tools/shaders/MMDShader.fx"
    before_path = out_dir / SHADER_BEFORE_NAME
    after_path = out_dir / SHADER_AFTER_NAME

    if before_fx:
        before_source = (project_root / before_fx).resolve()
        if not before_source.is_file():
            raise FileNotFoundError(f"--before-fx does not exist: {before_source}")
        before_content = before_source.read_text(encoding="utf-8")
        before_path.write_text(before_content, encoding="utf-8")
        logger.info("Copied explicit before shader %s -> %s", before_source, before_path)
    else:
        try:
            result = subprocess.run(
                ["git", "show", f"{before_ref}:{shader_rel}"],
                capture_output=True,
                text=True,
                cwd=str(project_root),
                check=True,
                timeout=30,
            )
            before_content = result.stdout
            before_path.write_text(before_content, encoding="utf-8")
            logger.info("Extracted %s:%s -> %s", before_ref, shader_rel, before_path)
        except (subprocess.CalledProcessError, FileNotFoundError, TimeoutError) as exc:
            raise RuntimeError(f"Could not extract before shader from {before_ref}:{shader_rel}: {exc}") from exc

    # -- Copy working-tree version as "after" --
    after_source = project_root / shader_rel
    if after_source.is_file():
        after_content = after_source.read_text(encoding="utf-8")
        after_path.write_text(after_content, encoding="utf-8")
        logger.info("Copied working-tree MMDShader.fx -> %s", after_path)
    else:
        raise FileNotFoundError(
            f"Working-tree MMDShader.fx not found at {after_source}"
        )

    if _sha256_file(before_path) == _sha256_file(after_path):
        raise RuntimeError(
            "Before and after shader files are identical. "
            "Pass --before-ref <old-commit> or --before-fx <file> for a valid comparison."
        )

    return before_path, after_path


def _ensure_edge_pmx_fixture(project_root: Path, model_arg: str) -> Path:
    """Return path to an edge-enabled PMX fixture.

    If *model_arg* already exists, return it directly.  Otherwise parse
    tests/data/for_unit_test/test_1bone_cube.pmx with the repo's PMX
    parser, enable the edge draw flag on every material, and write a new
    fixture to *model_arg*.

    Raises FileNotFoundError if the source cannot be found or if the
    PMX parser/writer is not importable outside Maya.
    """
    model_path = (project_root / model_arg).resolve()
    if model_path.is_file():
        return model_path

    src_rel = "tests/data/for_unit_test/test_1bone_cube.pmx"
    src_path = (project_root / src_rel).resolve()
    if not src_path.is_file():
        raise FileNotFoundError(
            f"Edge-enabled fixture not found at {model_path} "
            f"and source PMX not found at {src_path}. "
            "Cannot auto-generate the fixture."
        )

    # Lazily import the repo's PMX parser/writer
    try:
        sys.path.insert(0, str(project_root))
        from mmd_tools.core.pmx_data import PmxData  # noqa: F811
        from mmd_tools.core.pmx_data.material import PmxDrawFlag  # noqa: F811
    except ImportError as exc:
        raise ImportError(
            f"Cannot import mmd_tools PMX parser from {project_root}. "
            "Cannot auto-generate the edge-enabled fixture without the "
            "repo's PMX parser/writer.  Either provide the fixture manually "
            f"at {model_path} or ensure the mmd_tools package is on sys.path. "
            f"Original error: {exc}"
        )

    logger.info(
        "Generating edge-enabled fixture %s from %s ...",
        model_path, src_path,
    )
    model_path.parent.mkdir(parents=True, exist_ok=True)

    pmx = PmxData()
    pmx.parse_file(str(src_path))

    # Enable EDGE_DRAWING on every material
    edge_flag = PmxDrawFlag.EDGE_DRAWING.value  # 0x10
    for mat in pmx.materials:
        mat.draw_flag |= edge_flag
        logger.debug(
            "  material %r draw_flag now 0x%02x", mat.get_name(), mat.draw_flag
        )

    pmx.write_file(str(model_path))
    logger.info("Wrote edge-enabled fixture to %s", model_path)
    return model_path


def _find_maya_executable(maya_version: str) -> str:
    """Find Maya bin/maya.exe path."""
    loc = os.environ.get(f"MAYA_LOCATION_{maya_version}") or os.environ.get(
        "MAYA_LOCATION"
    )
    if loc:
        exe = Path(loc) / "bin" / "maya.exe"
        if exe.is_file():
            return str(exe)
    for base in [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")),
        Path(os.environ.get("ProgramW6432", "C:/Program Files")),
    ]:
        exe = base / f"Autodesk/Maya{maya_version}/bin/maya.exe"
        if exe.is_file():
            return str(exe)
    raise FileNotFoundError(
        f"Maya {maya_version} not found. Set MAYA_LOCATION environment variable."
    )


def _launch_maya(
    maya_path: str, project_root: Path, output_dir: Path, port: int
) -> subprocess.Popen:
    """Launch Maya GUI with commandPort and DX11 device override.

    MAYA_VP2_DEVICE_OVERRIDE=VirtualDeviceDx11 is set in the subprocess
    environment to force Viewport 2.0 to use the DirectX 11 device,
    which is required for dx11Shader rendering.
    """
    logger.info("Launching Maya GUI (DX11 device override)...")
    cmd = [
        maya_path,
        "-command",
        f'commandPort -name ":{port}" -sourceType "python";',
    ]
    env = os.environ.copy()
    env["MAYA_VP2_DEVICE_OVERRIDE"] = "VirtualDeviceDx11"
    # Ensure project root is on Maya's PYTHONPATH
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{project_root};{pythonpath}"
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_handle = open(output_dir / "maya_gui_stdout.log", "w", encoding="utf-8", errors="replace")
    stderr_handle = open(output_dir / "maya_gui_stderr.log", "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, env=env, stdout=stdout_handle, stderr=stderr_handle)
    proc._stdout_handle = stdout_handle  # type: ignore[attr-defined]
    proc._stderr_handle = stderr_handle  # type: ignore[attr-defined]
    proc._stdout_path = output_dir / "maya_gui_stdout.log"  # type: ignore[attr-defined]
    proc._stderr_path = output_dir / "maya_gui_stderr.log"  # type: ignore[attr-defined]
    logger.info("Maya PID=%d (MAYA_VP2_DEVICE_OVERRIDE=VirtualDeviceDx11)", proc.pid)
    return proc


def _close_process_logs(process: subprocess.Popen) -> None:
    """Close log handles attached by _launch_maya."""
    for attr in ("_stdout_handle", "_stderr_handle"):
        handle = getattr(process, attr, None)
        if handle:
            try:
                handle.close()
            except Exception:
                pass


def _process_log_hint(process: subprocess.Popen | None) -> str:
    """Return a short diagnostic hint for Maya process startup failures."""
    if process is None:
        return ""
    parts: list[str] = []
    for label, attr in [("stdout", "_stdout_path"), ("stderr", "_stderr_path")]:
        path = getattr(process, attr, None)
        if not path:
            continue
        path = Path(path)
        parts.append(f"{label}: {path}")
        if path.is_file() and path.stat().st_size:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                tail = text[-1200:].strip()
                if tail:
                    parts.append(f"{label} tail:\n{tail}")
            except Exception as exc:
                parts.append(f"{label} tail unavailable: {exc}")
    temp_dir = os.environ.get("TEMP") or os.environ.get("TMP") or "%TEMP%"
    parts.append(f"licensing logs: {temp_dir}\\MayaCLM-*.log")
    return "\n".join(parts)


def _wait_for_command_port(port: int, timeout: int, process: subprocess.Popen | None = None) -> None:
    """Wait until Maya commandPort :port accepts connections."""
    logger.info("Waiting for commandPort :%d ...", port)
    start = time.time()
    while time.time() - start < timeout:
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"Maya exited before commandPort opened (exit code {process.returncode}).\n"
                + _process_log_hint(process)
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                logger.info("commandPort :%d is open.", port)
                return
        except (socket.timeout, ConnectionRefusedError):
            time.sleep(1)
    raise TimeoutError(
        f"Timed out after {timeout}s waiting for commandPort :{port}.\n"
        + _process_log_hint(process)
    )


def _send_command(port: int, command: str) -> None:
    """Send a Python command string to Maya via commandPort."""
    logger.info("Sending command to Maya (port=%d, %d bytes)...", port, len(command))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
            sock.sendall(command.encode("utf-8"))
        logger.info("Command sent.")
    except Exception as exc:
        logger.error("Failed to send command: %s", exc)
        raise


def _monitor_log(log_path: Path, timeout: int) -> bool:
    """Tail log file until completion marker appears.

    Returns True if marker found, raises TimeoutError otherwise.
    """
    logger.info("Monitoring log: %s", log_path)
    if not log_path.is_file():
        log_path.touch()

    start = time.time()
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # tail
        while time.time() - start < timeout:
            line = f.readline()
            if line:
                print(line, end="")
                if LOG_COMPLETION_MARKER in line:
                    logger.info("Completion marker found.")
                    return True
            else:
                time.sleep(LOG_POLL_INTERVAL)
    raise TimeoutError(
        f"Timed out after {timeout}s waiting for completion marker"
    )


def _validate_capture_diagnostics(output_dir: Path) -> dict:
    """Load and validate the Maya-side diagnostic JSON."""
    diag_path = output_dir / "gui_dx11_edge_capture.diag.json"
    if not diag_path.is_file():
        raise RuntimeError(f"Diagnostic JSON was not written: {diag_path}")

    with open(diag_path, encoding="utf-8") as f:
        diag = json.load(f)

    errors: list[str] = []
    if diag.get("capture_failed"):
        errors.append("Maya-side capture_failed flag is true")
    if diag.get("dx11_device_valid") is not True:
        errors.append("deviceInformation did not prove a DX11 VP2 device")

    for key in ("before_png", "after_png"):
        png_path = diag.get(key)
        if not png_path or not Path(png_path).is_file():
            errors.append(f"{key} is missing or does not exist: {png_path}")

    for key in ("before_png_stats", "after_png_stats"):
        stats = diag.get(key)
        if not isinstance(stats, dict):
            errors.append(f"{key} is missing")
            continue
        if int(stats.get("max", 0)) < 10:
            errors.append(f"{key}.max is blank-like: {stats.get('max')}")

    if errors:
        raise RuntimeError(
            "GUI DX11 capture diagnostics did not pass:\n- " + "\n- ".join(errors)
        )
    return diag


# ===================================================================
# Maya-side capture logic (sent via commandPort, runs inside Maya GUI)
# ===================================================================


def _build_maya_command(
    project_root: str,
    model_path: str,
    before_fx: str,
    after_fx: str,
    output_dir: str,
    port: int,
    log_path: str,
) -> str:
    """Build a Python command string to execute inside Maya GUI.

    Returns a single string containing the entire capture logic,
    which will be sent via commandPort and executed inside Maya.
    """
    # Use the log file for output streaming.
    # All print() calls go to Maya's script editor but we redirect
    # key messages to the log file for the host to monitor.
    code = f"""
import sys, os, json, struct, zlib, math
from pathlib import Path

_project_root = Path(r"{project_root}")
_log_path = Path(r"{log_path}")
_output_dir = Path(r"{output_dir}")
_model_path = Path(r"{model_path}")
_before_fx = Path(r"{before_fx}")
_after_fx  = Path(r"{after_fx}")

if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

_output_dir.mkdir(parents=True, exist_ok=True)

def _log(msg: str) -> None:
    '''Append a line to the log file.'''
    with open(str(_log_path), "a", encoding="utf-8") as _lf:
        _lf.write(msg + "\\n")
    print(msg)

_capture_failed = False

_log("=== GUI DX11 Edge Capture: Maya-side begin ===")

import maya.cmds as cmds

# -- 1. Fresh scene --
cmds.file(new=True, force=True)
_log("Scene cleared.")

# -- 2. Load dx11Shader plugin --
try:
    cmds.loadPlugin("dx11Shader", quiet=True)
    _log("dx11Shader plugin loaded.")
except Exception as _e:
    _log(f"ERROR: could not load dx11Shader plugin: {{_e}}")
    cmds.quit(force=True)
    raise

# -- 3. Import edge-enabled PMX with dx11 shader backend --
from mmd_tools.core import settings
from mmd_tools.io.mmd_importer import import_mmd_file

settings.set("import.model.create_mmd_shaders", True)
settings.set("import.model.mmd_shader_backend", "dx11")

if not _model_path.is_file():
    _log(f"ERROR: model not found: {{_model_path}}")
    cmds.quit(force=True)
    raise FileNotFoundError(str(_model_path))

_root_node = import_mmd_file(str(_model_path))
if _root_node is None:
    _log("ERROR: import_mmd_file returned None")
    cmds.quit(force=True)
    raise RuntimeError("PMX import failed")
_log(f"Imported PMX, root node: {{_root_node}}")

# -- 4. Find dx11Shader nodes --
_dx11_nodes = cmds.ls(type="dx11Shader") or []
_log(f"dx11Shader nodes found: {{len(_dx11_nodes)}}")
if not _dx11_nodes:
    _log("ERROR: no dx11Shader nodes created by importer")
    cmds.quit(force=True)
    raise RuntimeError("No dx11Shader nodes")

# Record original shader path and technique from the first node
_shader_node = _dx11_nodes[0]
_orig_shader_path = str(cmds.getAttr(f"{{_shader_node}}.shader"))
_technique = str(cmds.getAttr(f"{{_shader_node}}.technique"))
_log(f"Original dx11Shader path: {{_orig_shader_path}}")
_log(f"Technique: {{_technique}}")

# -- 5. Set up camera framing --
def _compute_model_bounds(root_node):
    meshes = cmds.listRelatives(root_node, allDescendents=True, type="mesh")
    if not meshes:
        return [0.0, 0.0, 0.0], 5.0
    try:
        bbox = cmds.exactWorldBoundingBox(meshes)
    except Exception:
        return [0.0, 0.0, 0.0], 5.0
    center = [
        (bbox[0] + bbox[3]) * 0.5,
        (bbox[1] + bbox[4]) * 0.5,
        (bbox[2] + bbox[5]) * 0.5,
    ]
    dx = bbox[3] - bbox[0]
    dy = bbox[4] - bbox[1]
    dz = bbox[5] - bbox[2]
    radius = math.sqrt(dx*dx + dy*dy + dz*dz) * 0.5
    _log(f"Bounds: center=({{center[0]:.3f}},{{center[1]:.3f}},{{center[2]:.3f}}) radius={{radius:.3f}}")
    return center, radius

def _normalize(v):
    L = math.sqrt(sum(x*x for x in v))
    if L < 1e-12:
        return [1.0, 0.0, 0.0]
    return [x/L for x in v]

_model_center, _model_radius = _compute_model_bounds(_root_node)

# Arrange camera: FOV=25 deg, view from front-right-above
_FOV = 25
_aspect = 16.0 / 9.0
_tan_hfov = math.tan(math.radians(_FOV) * 0.5)
_d_h = _model_radius / (0.7 * _tan_hfov) if _tan_hfov > 1e-9 else _model_radius * 3.0
_d_v = _model_radius / (0.7 * _tan_hfov / _aspect) if _tan_hfov > 1e-9 else _model_radius * 3.0
_cam_dist = max(_d_h, _d_v, _model_radius * 2.0, 5.0)
_view_dir = _normalize([0.4, 0.2, 0.9])
_cam_pos = [
    _model_center[0] + _view_dir[0] * _cam_dist,
    _model_center[1] + _view_dir[1] * _cam_dist,
    _model_center[2] + _view_dir[2] * _cam_dist,
]

# Position persp camera
cmds.setAttr("persp.translateX", _cam_pos[0])
cmds.setAttr("persp.translateY", _cam_pos[1])
cmds.setAttr("persp.translateZ", _cam_pos[2])
# Compute Euler to look at target
_dx = _model_center[0] - _cam_pos[0]
_dy = _model_center[1] - _cam_pos[1]
_dz = _model_center[2] - _cam_pos[2]
_L = math.sqrt(_dx*_dx + _dy*_dy + _dz*_dz)
if _L > 1e-12:
    _yaw = math.degrees(math.asin(max(-1.0, min(1.0, -_dx/_L))))
    _pitch = math.degrees(math.atan2(_dy, -_dz))
    cmds.setAttr("persp.rotateX", _pitch)
    cmds.setAttr("persp.rotateY", _yaw)
    cmds.setAttr("persp.rotateZ", 0.0)
cmds.setAttr("perspShape.focalLength", 18.0 / _tan_hfov if _tan_hfov > 1e-9 else 35.0)
cmds.setAttr("perspShape.nearClipPlane", max(0.01, _cam_dist * 0.01))
cmds.setAttr("perspShape.farClipPlane", _cam_dist + _model_radius * 4.0 + 100.0)
_log(f"Camera: pos=({{_cam_pos[0]:.2f}},{{_cam_pos[1]:.2f}},{{_cam_pos[2]:.2f}}) dist={{_cam_dist:.2f}}")

# -- 6. Set up a directional light --
_light_shape = cmds.directionalLight(name="guiDx11Light", intensity=1.0, rgb=(1,1,1))
_light_xform = cmds.listRelatives(_light_shape, parent=True)[0]
_light_dir = [0.5, -1.0, 0.5]
_light_target = _model_center
_light_pos = [
    _model_center[0] - _light_dir[0],
    _model_center[1] - _light_dir[1],
    _model_center[2] - _light_dir[2],
]
_ldx = _light_target[0] - _light_pos[0]
_ldy = _light_target[1] - _light_pos[1]
_ldz = _light_target[2] - _light_pos[2]
_lL = math.sqrt(_ldx*_ldx + _ldy*_ldy + _ldz*_ldz)
if _lL > 1e-12:
    _lyaw = math.degrees(math.asin(max(-1.0, min(1.0, -_ldx/_lL))))
    _lpitch = math.degrees(math.atan2(_ldy, -_ldz))
    cmds.setAttr(f"{{_light_xform}}.rotateX", _lpitch)
    cmds.setAttr(f"{{_light_xform}}.rotateY", _lyaw)
    cmds.setAttr(f"{{_light_xform}}.rotateZ", 0.0)
_log(f"Light created: {{_light_xform}}")

# -- 7. Create modelPanel for Viewport 2.0 --
_capture_width = 1280
_capture_height = 720

try:
    _panel = cmds.modelPanel(label="GuiDx11CapturePanel")
    cmds.modelPanel(_panel, edit=True, camera="persp")
    cmds.modelEditor(_panel, edit=True, rendererName="vp2Renderer")
    cmds.modelEditor(_panel, edit=True, displayTextures=True)
    cmds.modelEditor(_panel, edit=True, shadows=False)
    cmds.modelEditor(_panel, edit=True, grid=False)
    cmds.modelEditor(_panel, edit=True, headsUpDisplay=False)
    _log(f"ModelPanel created: {{_panel}}")
except Exception as _e:
    _log(f"WARNING: modelPanel setup failed: {{_e}}")
    _panel = None

# -- 8. Helper: resolve actual PNG path --
def _resolve_png(requested_path, frame=1):
    p = Path(requested_path).resolve()
    out_dir = p.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = p.stem
    candidates = [
        p, p.with_suffix('.png'),
        out_dir / f"{{stem}}.png",
        out_dir / f"{{stem}}.{{frame:04d}}.png",
        out_dir / f"{{stem}}.{{frame:03d}}.png",
        out_dir / f"{{stem}}.{{frame:02d}}.png",
        out_dir / f"{{stem}}.{{frame}}.png",
    ]
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return c
    pngs = sorted(out_dir.glob(f"{{stem}}*.png"), key=lambda x: x.stat().st_mtime, reverse=True)
    if pngs:
        return pngs[0]
    return p.with_suffix('.png')

# -- 9. Helper: check PNG for non-blank --
def _check_png(path, threshold=10, allow_blank=False):
    '''Parse PNG with stdlib, return stats dict.'''
    path = Path(path)
    with open(str(path), 'rb') as f:
        raw = f.read()
    if raw[:8] != b'\\x89PNG\\r\\n\\x1a\\n':
        raise ValueError(f"Not a valid PNG: {{path}}")
    ihdr_len = struct.unpack_from('>I', raw, 8)[0]
    if raw[12:16] != b'IHDR':
        raise ValueError(f"No IHDR")
    ihdr = raw[16:16+ihdr_len]
    img_w = struct.unpack_from('>I', ihdr, 0)[0]
    img_h = struct.unpack_from('>I', ihdr, 4)[0]
    bit_depth = ihdr[8]
    color_type = ihdr[9]
    pos = 16 + ihdr_len + 4
    idat_parts = []
    while pos + 8 <= len(raw):
        clen = struct.unpack_from('>I', raw, pos)[0]
        ctyp = raw[pos+4:pos+8]
        if ctyp == b'IDAT':
            idat_parts.append(raw[pos+8:pos+8+clen])
        elif ctyp == b'IEND':
            break
        pos += 12 + clen
    if not idat_parts:
        raise ValueError(f"No IDAT in {{path}}")
    decomp = zlib.decompress(b''.join(idat_parts))
    if bit_depth != 8:
        raise ValueError(f"Bit depth not 8: {{bit_depth}}")
    if color_type == 0:
        ch = 1
    elif color_type == 2:
        ch = 3
    elif color_type == 4:
        ch = 2
    elif color_type == 6:
        ch = 4
    else:
        raise ValueError(f"Unsupported color type: {{color_type}}")
    px_bytes = ch
    row_bytes = img_w * px_bytes
    row_stride = 1 + row_bytes
    mn, mx, total, count = 255, 0, 0, 0
    prev_row = bytearray(row_bytes)
    total_rows = max(1, len(decomp) // row_stride)
    row_step = max(1, total_rows // 400)
    for row_idx in range(total_rows):
        off = row_idx * row_stride
        if off + row_stride > len(decomp):
            break
        ft = decomp[off]
        row = bytearray(decomp[off+1:off+1+row_bytes])
        for idx in range(row_bytes):
            left = row[idx - px_bytes] if idx >= px_bytes else 0
            up = prev_row[idx]
            up_left = prev_row[idx - px_bytes] if idx >= px_bytes else 0
            if ft == 1:
                row[idx] = (row[idx] + left) & 0xFF
            elif ft == 2:
                row[idx] = (row[idx] + up) & 0xFF
            elif ft == 3:
                row[idx] = (row[idx] + ((left + up)//2)) & 0xFF
            elif ft == 4:
                pr = left + up - up_left
                pa, pb = abs(pr-left), abs(pr-up)
                pc = abs(pr - up_left)
                pv = left if pa <= pb and pa <= pc else up if pb <= pc else up_left
                row[idx] = (row[idx] + pv) & 0xFF
        if row_idx % row_step == 0:
            for px in range(0, row_bytes, px_bytes * 4):
                if color_type in (0, 4):
                    v = row[px]
                else:
                    v = max(row[px], row[px+1], row[px+2])
                if v < mn: mn = v
                if v > mx: mx = v
                total += v
                count += 1
        prev_row = row
    avg = total / count if count else 0.0
    stats = {{"min": mn, "max": mx, "avg": round(avg, 3), "samples": count,
             "width": img_w, "height": img_h}}
    if mx < threshold and not allow_blank:
        raise RuntimeError(f"PNG blank: max={{mx}} < {{threshold}}")
    return stats

# -- 10. Capture: before (committed shader) --
_log("=== Capturing BEFORE (committed shader) ===")
before_png = _output_dir / "gui_dx11_edge_before.png"

# Swap to before shader
try:
    cmds.setAttr(f"{{_shader_node}}.shader", str(_before_fx), type="string")
    _log(f"Switched shader to: {{_before_fx}}")
except Exception as _e:
    _log(f"WARNING: could not set before shader path: {{_e}}")

try:
    cmds.refresh(force=True)
    time.sleep(2)  # brief settle for shader recompile in Viewport 2.0
except Exception:
    pass

# Captures clean before pngs
for old in _output_dir.glob("gui_dx11_edge_before*.png"):
    try: old.unlink()
    except: pass

try:
    result = cmds.playblast(
        filename=str(before_png.with_suffix('')),
        frame=1, format="image", compression="png",
        offScreen=True, viewer=False,
        width=_capture_width, height=_capture_height,
        forceOverwrite=True, showOrnaments=False, percent=100,
    )
    _log(f"Before playblast result: {{result!r}}")
except Exception as _e:
    _log(f"Before playblast failed: {{_e}}")
    # In GUI Maya, try with activeEditor
    try:
        if _panel:
            result = cmds.playblast(
                filename=str(before_png.with_suffix('')),
                frame=1, format="image", compression="png",
                activeEditor=True, viewer=False,
                width=_capture_width, height=_capture_height,
                forceOverwrite=True, showOrnaments=False, percent=100,
            )
            _log(f"Before playblast (activeEditor) result: {{result!r}}")
    except Exception as _e2:
        _log(f"Before playblast (activeEditor) also failed: {{_e2}}")

actual_before = _resolve_png(before_png, 1)
_log(f"Before actual PNG: {{actual_before}} (exists={{actual_before.exists()}}, size={{actual_before.stat().st_size if actual_before.exists() else 0}})")

before_stats = None
if actual_before.exists() and actual_before.stat().st_size > 0:
    try:
        before_stats = _check_png(actual_before, allow_blank=True)
        _log(f"Before PNG stats: min={{before_stats['min']}} max={{before_stats['max']}} avg={{before_stats['avg']}}")
    except Exception as _e:
        _log(f"Before PNG check error: {{_e}}")
else:
    _log("Before PNG does not exist or is zero size")

# -- 11. Capture: after (working-tree shader) --
_log("=== Capturing AFTER (working-tree shader) ===")
after_png = _output_dir / "gui_dx11_edge_after.png"

try:
    cmds.setAttr(f"{{_shader_node}}.shader", str(_after_fx), type="string")
    _log(f"Switched shader to: {{_after_fx}}")
except Exception as _e:
    _log(f"WARNING: could not set after shader path: {{_e}}")

try:
    cmds.refresh(force=True)
    time.sleep(2)
except Exception:
    pass

for old in _output_dir.glob("gui_dx11_edge_after*.png"):
    try: old.unlink()
    except: pass

try:
    result = cmds.playblast(
        filename=str(after_png.with_suffix('')),
        frame=1, format="image", compression="png",
        offScreen=True, viewer=False,
        width=_capture_width, height=_capture_height,
        forceOverwrite=True, showOrnaments=False, percent=100,
    )
    _log(f"After playblast result: {{result!r}}")
except Exception as _e:
    _log(f"After playblast failed: {{_e}}")
    try:
        if _panel:
            result = cmds.playblast(
                filename=str(after_png.with_suffix('')),
                frame=1, format="image", compression="png",
                activeEditor=True, viewer=False,
                width=_capture_width, height=_capture_height,
                forceOverwrite=True, showOrnaments=False, percent=100,
            )
            _log(f"After playblast (activeEditor) result: {{result!r}}")
    except Exception as _e2:
        _log(f"After playblast (activeEditor) also failed: {{_e2}}")

actual_after = _resolve_png(after_png, 1)
_log(f"After actual PNG: {{actual_after}} (exists={{actual_after.exists()}}, size={{actual_after.stat().st_size if actual_after.exists() else 0}})")

after_stats = None
if actual_after.exists() and actual_after.stat().st_size > 0:
    try:
        after_stats = _check_png(actual_after, allow_blank=True)
        _log(f"After PNG stats: min={{after_stats['min']}} max={{after_stats['max']}} avg={{after_stats['avg']}}")
    except Exception as _e:
        _log(f"After PNG check error: {{_e}}")
else:
    _log("After PNG does not exist or is zero size")

if not before_stats or int(before_stats.get("max", 0)) < 10:
    _capture_failed = True
    _log("ERROR: before PNG is missing or blank-like")
if not after_stats or int(after_stats.get("max", 0)) < 10:
    _capture_failed = True
    _log("ERROR: after PNG is missing or blank-like")

# -- 12. Gather deviceInformation and write diagnostic JSON --
diag = {{
    "capture_type": "gui_dx11_edge",
    "model": str(_model_path),
    "fixture_edge_enabled": True,
    "shader_before_path": str(_before_fx),
    "shader_after_path": str(_after_fx),
    "technique": str(_technique),
    "vp2_device_override": "VirtualDeviceDx11",
    "before_png": str(actual_before) if actual_before.exists() else None,
    "after_png": str(actual_after) if actual_after.exists() else None,
    "before_png_stats": before_stats,
    "after_png_stats": after_stats,
    "capture_resolution": f"{{_capture_width}}x{{_capture_height}}",
    "camera_fov": _FOV,
    "camera_distance": round(_cam_dist, 2),
    "shader_node_count": len(_dx11_nodes),
}}

# Device information
try:
    dev_info = cmds.ogs(deviceInformation=True)
    diag["deviceInformation"] = dev_info
    # ---- Validate that the VP2 device is actually DirectX 11 ----
    _dev_str = str(dev_info)
    _is_dx11 = any(
        kw in _dev_str for kw in ["Direct3D11", "DirectX", "DX11", "Dx11", "dx11"]
    )
    if _is_dx11:
        diag["dx11_device_valid"] = True
        _log(f"VP2 device confirmed as DirectX 11: {{_dev_str[:200]}}")
    else:
        diag["dx11_device_valid"] = False
        _log(f"ERROR: VP2 device is NOT DirectX 11. deviceInformation={{_dev_str[:500]}}")
        _capture_failed = True
        _log("Capture marked as failed: non-DX11 device in use.")
except Exception as _e:
    diag["deviceInformation"] = f"<error: {{_e}}>"
    diag["dx11_device_valid"] = False
    _capture_failed = True
    _log(f"ERROR: could not query device information: {{_e}}")

# VP2 engine
try:
    diag["vp2RenderingEngine"] = str(cmds.optionVar(query="vp2RenderingEngine"))
except Exception as _e:
    diag["vp2RenderingEngine"] = f"<error: {{_e}}>"

# Plugin info
for plug in ["dx11Shader"]:
    try:
        loaded = cmds.pluginInfo(plug, q=True, loaded=True)
        version = cmds.pluginInfo(plug, q=True, version=True)
        p_path = cmds.pluginInfo(plug, q=True, path=True)
        diag[f"plugin_{{plug}}"] = {{"loaded": loaded, "version": version, "path": p_path}}
    except Exception as _e:
        diag[f"plugin_{{plug}}"] = f"<error: {{_e}}>"

# Color management
cm_info = {{}}
for attr_name in ["viewTransformName", "displayName", "renderingSpaceName"]:
    try:
        cm_info[attr_name] = str(cmds.colorManagementPrefs(q=True, **{{attr_name: True}}))
    except Exception as _e:
        cm_info[attr_name] = f"<error: {{_e}}>"
diag["color_management"] = cm_info

diag["capture_failed"] = _capture_failed

# Dump JSON
diag_path = _output_dir / "gui_dx11_edge_capture.diag.json"
with open(str(diag_path), "w", encoding="utf-8") as _df:
    json.dump(diag, _df, indent=2, default=str)
_log(f"Diagnostic JSON written to: {{diag_path}}")

_log("=== GUI DX11 Edge Capture: Maya-side complete ===")
if _capture_failed:
    _log("CAPTURE FAILED (see diagnostics for details)")
_log("{LOG_COMPLETION_MARKER}")
"""
    # Replace escaped curly braces for f-string formatting
    # The {LOG_COMPLETION_MARKER} should use the constant value
    code = code.replace("{LOG_COMPLETION_MARKER}", LOG_COMPLETION_MARKER)
    return code


# ===================================================================
# Main (host)
# ===================================================================


def main() -> int:
    """Orchestrate the GUI DX11 edge capture from the host side.

    A normal run exits nonzero when Maya GUI is unavailable or when the
    diagnostic JSON does not prove DX11 VP2 nonblank before/after captures.
    """
    # Fix stdout encoding for log streaming
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = __import__("io").TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8"
        )

    parser = argparse.ArgumentParser(
        description="GUI DX11 edge capture: launch Maya GUI, capture before/after shader screenshots"
    )
    parser.add_argument(
        "--maya",
        default=DEFAULT_MAYA_VERSION,
        dest="maya_version",
        help=f"Maya version (default: {DEFAULT_MAYA_VERSION})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=COMMAND_PORT,
        help=f"commandPort number (default: {COMMAND_PORT})",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Prepare shader files and fixture, then exit without launching Maya GUI",
    )
    parser.add_argument(
        "--before-ref",
        default="HEAD",
        help="Git ref used for the before shader when --before-fx is not set (default: HEAD)",
    )
    parser.add_argument(
        "--before-fx",
        default=None,
        help="Explicit before shader file path. Overrides --before-ref.",
    )
    parser.add_argument(
        "--model",
        default="build/fixtures/test_1bone_cube_edge.pmx",
        help="Edge-enabled PMX fixture path (relative to project root)",
    )
    parser.add_argument(
        "--out",
        default=str(OUTPUT_DIR_REL),
        help=f"Output directory for captures and diagnostics (default: {OUTPUT_DIR_REL})",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Log file path (default: build/captures/gui-dx11/gui_dx11_edge_capture.log)",
    )
    args = parser.parse_args()

    project_root = _get_project_root()
    logger.info("Project root: %s", project_root)

    # -- Prepare paths --
    output_dir = project_root / args.out
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = (
        Path(args.log).resolve()
        if args.log
        else output_dir / LOG_FILE_NAME
    )
    if log_path.exists():
        log_path.unlink()

    # -- Prepare before/after shader files --
    logger.info("Preparing shader files...")
    try:
        before_fx, after_fx = _prepare_shader_files(
            project_root,
            output_dir,
            args.before_ref,
            args.before_fx,
        )
        logger.info("Before shader: %s", before_fx)
        logger.info("After shader:  %s", after_fx)
    except Exception as exc:
        logger.error("Failed to prepare shader files: %s", exc)
        return 1

    # -- Ensure edge-enabled PMX fixture exists --
    try:
        model_path = _ensure_edge_pmx_fixture(project_root, args.model)
    except (FileNotFoundError, ImportError) as exc:
        logger.error("Cannot obtain edge-enabled PMX fixture: %s", exc)
        return 1

    # -- Early exit for --prepare-only --
    if args.prepare_only:
        logger.info(
            "--prepare-only: shader files and fixture ready. Exiting."
        )
        return 0

    # -- Check if we can actually run Maya --
    maya_exe = None
    can_run = sys.platform == "win32"
    if can_run:
        try:
            maya_exe = _find_maya_executable(args.maya_version)
            logger.info("Maya executable: %s", maya_exe)
        except FileNotFoundError as exc:
            logger.warning("Maya not found: %s", exc)
            can_run = False

    if not can_run:
        logger.error(
            "Maya GUI execution is not available in this environment.\n"
            "  - Maya executable not found or platform is not Windows.\n"
            "  - Use --prepare-only to set up artifacts without launching Maya.\n"
            "Shader files and output directory are ready under %s",
            output_dir,
        )
        note = {
            "status": "prepared",
            "message": "GUI Maya execution not available. "
            "Shader files and output dir are ready for manual invocation.",
            "before_shader": str(before_fx),
            "after_shader": str(after_fx),
            "model": str(model_path),
            "output_dir": str(output_dir),
            "vp2_device_override": "VirtualDeviceDx11",
        }
        note_path = output_dir / "gui_dx11_edge_capture.note.json"
        with open(note_path, "w", encoding="utf-8") as f:
            json.dump(note, f, indent=2)
        logger.info("Note written to: %s", note_path)
        return 1

    # -- Actually run Maya GUI --
    maya_process = None
    try:
        maya_process = _launch_maya(maya_exe, project_root, output_dir, args.port)
        _wait_for_command_port(args.port, MAYA_START_TIMEOUT, maya_process)

        # Build the Maya-side capture command
        maya_cmd = _build_maya_command(
            project_root=str(project_root),
            model_path=str(model_path),
            before_fx=str(before_fx),
            after_fx=str(after_fx),
            output_dir=str(output_dir),
            port=args.port,
            log_path=str(log_path),
        )

        # Send it
        _send_command(args.port, maya_cmd)

        # Monitor for completion
        _monitor_log(log_path, CAPTURE_TIMEOUT)
        _validate_capture_diagnostics(output_dir)

        logger.info("GUI DX11 edge capture completed successfully.")

    except (FileNotFoundError, TimeoutError, Exception) as exc:
        logger.error("Capture failed: %s", exc, exc_info=True)
        return 1
    finally:
        if maya_process:
            logger.info("Terminating Maya...")
            try:
                # Try graceful quit
                _send_command(
                    args.port,
                    'import maya.cmds as cmds; cmds.quit(force=True)',
                )
                maya_process.wait(timeout=30)
            except Exception:
                logger.warning("Force-killing Maya process...")
                maya_process.kill()
            logger.info("Maya terminated.")
            _close_process_logs(maya_process)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
