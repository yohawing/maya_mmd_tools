"""GUI DX11 capture for MMD physics collider locator drawing.

Host side launches Maya GUI with a commandPort and DX11 VP2 override.  Maya side
loads the Python plugin, imports a physics PMX fixture, frames the rigid-body
locator shapes, playblasts one PNG, and writes diagnostics that prove the
locator draw override produced visible cyan-ish wire pixels.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import struct
import sys
import time
import zlib
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

DEFAULT_MAYA_VERSION = "2024"
COMMAND_PORT = 7726
COMPLETION_MARKER = "//-- PHYSICS COLLIDER CAPTURE FINISHED --//"
CAPTURE_TIMEOUT = 600
LOG_POLL_INTERVAL = 1

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _force_utf8_stdio() -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", errors="replace", closefd=False)
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = open(sys.stderr.fileno(), mode="w", encoding="utf-8", errors="replace", closefd=False)


def _actual_playblast_path(target: Path) -> Path | None:
    if target.is_file():
        return target
    candidates = list(target.parent.glob(target.stem + "*.png"))
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _png_scanline_stats(path: Path) -> dict[str, int]:
    """Return simple RGB stats for an 8-bit non-interlaced PNG using stdlib only."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a PNG: {path}")

    offset = 8
    width = height = color_type = bit_depth = None
    idat = bytearray()
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_data = data[offset + 8:offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(">IIBBBBB", chunk_data)
            if bit_depth != 8 or interlace != 0 or color_type not in (2, 6):
                raise ValueError(f"Unsupported PNG format: bit_depth={bit_depth}, color_type={color_type}, interlace={interlace}")
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None or color_type is None:
        raise ValueError(f"PNG header missing: {path}")

    channels = 4 if color_type == 6 else 3
    stride = width * channels
    raw = zlib.decompress(bytes(idat))
    rows: list[bytearray] = []
    cursor = 0
    previous = bytearray(stride)
    for _row in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scan = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        for i in range(stride):
            left = scan[i - channels] if i >= channels else 0
            up = previous[i]
            up_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                scan[i] = (scan[i] + left) & 0xFF
            elif filter_type == 2:
                scan[i] = (scan[i] + up) & 0xFF
            elif filter_type == 3:
                scan[i] = (scan[i] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                p = left + up - up_left
                pa = abs(p - left)
                pb = abs(p - up)
                pc = abs(p - up_left)
                predictor = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                scan[i] = (scan[i] + predictor) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"Unsupported PNG filter: {filter_type}")
        rows.append(scan)
        previous = scan

    nonblank = 0
    cyanish = 0
    max_channel = 0
    for row in rows:
        for i in range(0, stride, channels):
            r, g, b = row[i], row[i + 1], row[i + 2]
            max_channel = max(max_channel, r, g, b)
            if max(r, g, b) > 12:
                nonblank += 1
            if r < 120 and g > 110 and b > 130:
                cyanish += 1
    return {
        "width": int(width),
        "height": int(height),
        "nonblank_pixels": nonblank,
        "cyanish_pixels": cyanish,
        "max_channel": max_channel,
    }


def run_capture(log_path: str, model_path: str, out_png: str, diag_json: str, width: int = 1280, height: int = 720) -> None:
    """Maya-side capture entrypoint imported through commandPort."""
    import math
    import traceback

    import maya.cmds as cmds

    def _log(message: str) -> None:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(str(message) + "\n")
        try:
            print(message)
        except Exception:
            pass

    diag: dict[str, object] = {
        "capture_type": "gui_physics_collider",
        "model": model_path,
        "out_png": out_png,
        "capture_failed": False,
    }
    try:
        _log("=== physics collider capture begin ===")
        cmds.file(new=True, force=True)
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
        plugin_path = root / "mmd_tools" / "plugin_main.py"
        try:
            cmds.loadPlugin(str(plugin_path), quiet=True)
            _log(f"plugin loaded: {plugin_path}")
        except Exception as exc:
            _log(f"WARN plugin load: {exc}")

        from mmd_tools.io.mmd_importer import import_mmd_file

        scene_root = import_mmd_file(
            str(model_path),
            options={
                "import_physics": True,
                "create_physics_joints": True,
                "create_mmd_shaders": False,
            },
        )
        _log(f"imported root: {scene_root}")

        locators = cmds.ls(type="mmdRigidBodyLocator", long=True) or []
        rigid_bodies = cmds.ls(type="bulletRigidBodyShape", long=True) or []
        diag["locator_count"] = len(locators)
        diag["bullet_rigid_body_shape_count"] = len(rigid_bodies)
        if not locators:
            diag["capture_failed"] = True
            _log("ERROR: no mmdRigidBodyLocator shapes found")

        locator_parents: list[str] = []
        for locator in locators:
            locator_parents.extend(cmds.listRelatives(locator, parent=True, fullPath=True) or [])
        bounds_nodes = locator_parents or locators
        bbox = cmds.exactWorldBoundingBox(bounds_nodes) if bounds_nodes else [-5, -5, -5, 5, 5, 5]
        center = [(bbox[0] + bbox[3]) * 0.5, (bbox[1] + bbox[4]) * 0.5, (bbox[2] + bbox[5]) * 0.5]
        radius = max(
            math.sqrt((bbox[3] - bbox[0]) ** 2 + (bbox[4] - bbox[1]) ** 2 + (bbox[5] - bbox[2]) ** 2) * 0.5,
            1.0,
        )
        distance = radius * 2.8
        cmds.setAttr("persp.translate", center[0], center[1] + radius * 0.35, center[2] + distance, type="double3")
        cmds.setAttr("persp.rotate", -10.0, 0.0, 0.0, type="double3")
        cmds.setAttr("perspShape.nearClipPlane", 0.01)
        cmds.setAttr("perspShape.farClipPlane", distance + radius * 8.0)

        panels = cmds.getPanel(type="modelPanel") or []
        panel = "modelPanel4" if "modelPanel4" in panels else (panels[0] if panels else None)
        if panel:
            cmds.modelEditor(panel, edit=True, camera="persp")
            cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer")
            cmds.modelEditor(panel, edit=True, displayAppearance="smoothShaded", displayTextures=False)
            cmds.modelEditor(
                panel,
                edit=True,
                polymeshes=True,
                locators=True,
                dynamics=True,
                joints=False,
                nurbsCurves=False,
                handles=False,
                ikHandles=False,
                deformers=False,
                cameras=False,
                lights=False,
                grid=False,
                headsUpDisplay=False,
            )
            try:
                cmds.setFocus(panel)
            except Exception:
                pass
        diag["panel"] = panel

        try:
            renderer = cmds.ogs(query=True, deviceInformation=True)
        except Exception as exc:
            renderer = f"deviceInformation failed: {exc}"
        diag["device_information"] = str(renderer)
        diag["dx11_device_valid"] = any(token in str(renderer) for token in ("DirectX", "Direct3D11", "DX11", "Dx11"))
        if not diag["dx11_device_valid"]:
            diag["capture_failed"] = True
            _log(f"ERROR: VP2 device did not report DX11: {renderer}")

        cmds.select(locator_parents or locators, replace=True)
        cmds.refresh(force=True)
        time.sleep(2.0)

        out = Path(out_png)
        out.parent.mkdir(parents=True, exist_ok=True)
        for old in out.parent.glob(out.stem + "*.png"):
            try:
                old.unlink()
            except Exception:
                pass
        cmds.playblast(
            filename=str(out.with_suffix("")),
            frame=1,
            format="image",
            compression="png",
            offScreen=True,
            viewer=False,
            width=width,
            height=height,
            forceOverwrite=True,
            showOrnaments=False,
            percent=100,
            editorPanelName=panel,
        )
        actual = _actual_playblast_path(out)
        diag["actual_png"] = str(actual) if actual else ""
        diag["png_exists"] = bool(actual and actual.is_file())
        diag["png_size"] = actual.stat().st_size if actual and actual.is_file() else 0
        _log(f"playblast png: {actual} size={diag['png_size']}")
        if not actual or not actual.is_file() or actual.stat().st_size <= 0:
            diag["capture_failed"] = True
        _log(COMPLETION_MARKER)
    except Exception:
        diag["capture_failed"] = True
        diag["exception"] = traceback.format_exc()
        _log("EXCEPTION:\n" + str(diag["exception"]))
        _log(COMPLETION_MARKER)
    finally:
        Path(diag_json).parent.mkdir(parents=True, exist_ok=True)
        Path(diag_json).write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")


def _tail_until_marker(log_path: Path, timeout: int) -> None:
    if not log_path.exists():
        log_path.touch()
    start = time.time()
    with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, 2)
        while time.time() - start < timeout:
            line = handle.readline()
            if line:
                print(line, end="")
                if COMPLETION_MARKER in line:
                    return
            else:
                time.sleep(LOG_POLL_INTERVAL)
    raise TimeoutError(f"physics collider capture did not finish within {timeout}s")


def _validate_diag(diag_path: Path) -> dict[str, object]:
    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if diag.get("capture_failed"):
        errors.append("Maya-side capture_failed flag is true")
    if diag.get("dx11_device_valid") is not True:
        errors.append("DX11 VP2 device was not proven")
    if int(diag.get("locator_count", 0)) <= 0:
        errors.append("No mmdRigidBodyLocator shapes were found")
    png = Path(str(diag.get("actual_png") or ""))
    if not png.is_file():
        errors.append(f"PNG missing: {png}")
    else:
        stats = _png_scanline_stats(png)
        diag["png_stats"] = stats
        if stats["max_channel"] < 16 or stats["nonblank_pixels"] < 100:
            errors.append(f"PNG is blank-like: {stats}")
        if stats["cyanish_pixels"] < 10:
            errors.append(f"Collider cyan-ish wire pixels not detected: {stats}")
    diag_path.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
    if errors:
        raise RuntimeError("Physics collider capture diagnostics failed:\n- " + "\n- ".join(errors))
    return diag


def main() -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="GUI DX11 physics collider locator capture")
    parser.add_argument("--maya", default=DEFAULT_MAYA_VERSION)
    parser.add_argument("--model", default=str(Path("tests/data/physics/test_hair_physics.pmx")))
    parser.add_argument("--out", default="build/captures/gui-physics-collider/physics_collider.png")
    parser.add_argument("--port", type=int, default=COMMAND_PORT)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--attach-existing", action="store_true", help="Use an already-open commandPort instead of launching Maya.")
    parser.add_argument("--leave-open", action="store_true", help="Do not send cmds.quit() after capture.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = (project_root / out_path).resolve()
    model_path = Path(args.model)
    if not model_path.is_absolute():
        model_path = (project_root / model_path).resolve()
    output_dir = out_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "gui_physics_collider_capture.log"
    diag_path = output_dir / "gui_physics_collider_capture.diag.json"
    for path in (log_path, diag_path):
        if path.exists():
            path.unlink()

    maya_exe = maya_commandport.maya_exe(args.maya)
    logger.info("Maya executable: %s", maya_exe)
    stdout_path = output_dir / "maya_stdout.log"
    stderr_path = output_dir / "maya_stderr.log"
    proc = None
    try:
        if not args.attach_existing:
            proc = maya_commandport.launch_maya(
                version=args.maya,
                project_root=project_root,
                output_dir=output_dir,
                port=args.port,
                launch_mode="direct",
                env_overrides={
                    "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                    "MMD_TOOLS_SKIP_SHADER_OVERRIDE": "1",
                },
            )
        maya_commandport.wait_for_port(args.port, timeout=120, process=proc)
        logger.info("commandPort :%d open", args.port)

        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path(r'{project_root.as_posix()}')\n"
            "if str(project_root) not in sys.path:\n"
            "    sys.path.insert(0, str(project_root))\n"
            "from tests.viewport.gui_physics_collider_capture import run_capture\n"
            f"run_capture(r'{log_path.as_posix()}', r'{model_path.as_posix()}', "
            f"r'{out_path.as_posix()}', r'{diag_path.as_posix()}', {args.width}, {args.height})\n"
        )
        maya_commandport.send_python(args.port, command, label="<physics-collider-command>")
        logger.info("capture command sent (%d bytes)", len(command))
        _tail_until_marker(log_path, CAPTURE_TIMEOUT)
        diag = _validate_diag(diag_path)
        logger.info("physics collider capture passed: %s", diag_path)
        print(json.dumps(diag, ensure_ascii=False, indent=2))
        return 0
    finally:
        if not args.leave_open:
            maya_commandport.quit_maya(args.port)
        if proc is not None:
            try:
                proc.wait(timeout=30)
            except Exception:
                proc.kill()
        maya_commandport.close_process_logs(proc)
        for label, path in (("MAYA STDOUT", stdout_path), ("MAYA STDERR", stderr_path)):
            try:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                text = ""
            if text:
                print(f"\n===== {label} (tail) =====")
                print("\n".join(text.splitlines()[-40:]))
                print(f"===== end {label} =====")


if __name__ == "__main__":
    raise SystemExit(main())
