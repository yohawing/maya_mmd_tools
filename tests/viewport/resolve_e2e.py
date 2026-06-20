"""Maya GUI DX11 E2E harness for MMD texture cache resolution.

The Maya-side entrypoint is ``run(model_path)``, so drivers can call it with a
Unicode Python literal instead of passing non-ASCII paths through argv.
``__main__`` reads ``MMD_E2E_MODEL_PATH`` and launches Maya with a short
commandPort call, following the same delivery shape as ``gui_snapshot.py``.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

DEFAULT_MAYA_VERSION = "2026"
COMMAND_PORT = 7723
COMPLETION_MARKER = "RESOLVE_E2E_DONE"
MAYA_START_TIMEOUT = 120
RESOLVE_TIMEOUT = 600
LOG_POLL_INTERVAL = 1

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _json_default(value):
    try:
        return os.fspath(value)
    except TypeError:
        return str(value)


def _emit(payload, log_path=None):
    text = json.dumps(payload, ensure_ascii=False, default=_json_default, sort_keys=True)
    print(text)
    if log_path:
        with open(log_path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(text + "\n")


def run(model_path: str, log_path: Optional[str] = None) -> int:
    """Import a PMX/PMD in Maya GUI DX11 and verify texture cache relinking."""

    import traceback

    import maya.cmds as cmds

    def _attr(node, attr):
        try:
            if cmds.attributeQuery(attr, node=node, exists=True):
                return cmds.getAttr(f"{node}.{attr}") or ""
        except Exception:
            pass
        return ""

    def _file_nodes_with_mmd_original():
        from mmd_tools.core.constants import ATTR_MMD_ORIGINAL_TEXTURE_PATH

        nodes = []
        for file_node in cmds.ls(type="file") or []:
            if cmds.attributeQuery(ATTR_MMD_ORIGINAL_TEXTURE_PATH, node=file_node, exists=True):
                nodes.append(file_node)
        return nodes

    def _snapshot(label):
        from mmd_tools.core import maya_utils
        from mmd_tools.core.constants import ATTR_MMD_ORIGINAL_TEXTURE_PATH, ATTR_MMD_TEXTURE_CACHE_PATH
        from mmd_tools.core.texture_path_cache import decode_original_texture_path

        def _dx11_main_texture_state(file_node):
            connected = cmds.listConnections(
                f"{file_node}.outColor",
                type="dx11Shader",
                source=False,
                destination=True,
                plugs=False,
            ) or []
            shader = connected[0] if connected else ""
            if not shader and file_node.endswith("_texture"):
                candidate = file_node[: -len("_texture")]
                try:
                    if cmds.objExists(candidate) and cmds.nodeType(candidate) == "dx11Shader":
                        shader = candidate
                except Exception:
                    pass

            has_main_texture = None
            if shader:
                try:
                    if cmds.attributeQuery("HasMainTexture", node=shader, exists=True):
                        has_main_texture = cmds.getAttr(f"{shader}.HasMainTexture")
                except Exception:
                    pass
            return bool(connected), shader, has_main_texture

        rows = []
        for file_node in _file_nodes_with_mmd_original():
            original = decode_original_texture_path(_attr(file_node, ATTR_MMD_ORIGINAL_TEXTURE_PATH))
            current = _attr(file_node, "fileTextureName")
            cache_path = _attr(file_node, ATTR_MMD_TEXTURE_CACHE_PATH)
            classification = maya_utils.classify_mmd_texture_file_node(file_node)
            main_texture_connected, shader, has_main_texture = _dx11_main_texture_state(file_node)
            color_space = ""
            try:
                if cmds.attributeQuery("colorSpace", node=file_node, exists=True):
                    color_space = cmds.getAttr(f"{file_node}.colorSpace") or ""
            except Exception:
                pass
            rows.append(
                {
                    "phase": label,
                    "file_node": file_node,
                    "shader": shader,
                    "color_space": color_space,
                    "original_path": original,
                    "fileTextureName": current,
                    "cache_path": cache_path,
                    "cache_exists": bool(cache_path and os.path.exists(cache_path)),
                    "main_texture_connected": main_texture_connected,
                    "has_main_texture": has_main_texture,
                    "status": getattr(classification, "status", None),
                    "reason": getattr(classification, "reason", ""),
                }
            )
        return rows

    try:
        _emit({"event": "begin", "model_path": model_path}, log_path)
        cmds.file(new=True, force=True)
        try:
            cmds.loadPlugin("dx11Shader", quiet=True)
            _emit({"event": "plugin", "name": "dx11Shader", "loaded": True}, log_path)
        except Exception as exc:
            _emit({"event": "plugin", "name": "dx11Shader", "loaded": False, "error": str(exc)}, log_path)

        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        from mmd_tools.core import maya_utils, settings
        from mmd_tools.io.mmd_importer import import_mmd_file

        settings.set("import.model.create_mmd_shaders", True)
        settings.set("import.model.mmd_shader_backend", "dx11")

        root_node = import_mmd_file(str(model_path), options={"import_physics": False})
        _emit({"event": "imported", "root_node": root_node}, log_path)
        if not root_node:
            _emit({"event": "summary", "line": "RESOLVE_E2E: resolved=0 failed=1"}, log_path)
            _emit({"event": COMPLETION_MARKER, "exit_code": 1}, log_path)
            return 1

        for row in _snapshot("before"):
            _emit(row, log_path)

        results = maya_utils.resolve_scene_mmd_textures()
        resolved = sum(1 for result in results if getattr(result, "status", "") == "resolved")
        failed = sum(1 for result in results if getattr(result, "status", "") == "unrecoverable")
        _emit(
            {
                "event": "resolve_results",
                "resolved": resolved,
                "failed": failed,
                "results": [
                    {
                        "file_node": getattr(result, "file_node", ""),
                        "status": getattr(result, "status", ""),
                        "reason": getattr(result, "reason", ""),
                        "source_path": getattr(result, "source_path", ""),
                        "file_texture_path": getattr(result, "file_texture_path", ""),
                        "cache_path": getattr(result, "cache_path", ""),
                    }
                    for result in results
                ],
            },
            log_path,
        )

        for row in _snapshot("after"):
            _emit(row, log_path)

        summary = f"RESOLVE_E2E: resolved={resolved} failed={failed}"
        _emit({"event": "summary", "line": summary}, log_path)
        print(summary)
        if log_path:
            with open(log_path, "a", encoding="utf-8", errors="replace") as handle:
                handle.write(summary + "\n")
        _emit({"event": COMPLETION_MARKER, "exit_code": 0}, log_path)
        return 0
    except Exception:
        _emit({"event": "exception", "traceback": traceback.format_exc()}, log_path)
        _emit({"event": "summary", "line": "RESOLVE_E2E: resolved=0 failed=1"}, log_path)
        _emit({"event": COMPLETION_MARKER, "exit_code": 1}, log_path)
        return 1


def _find_maya(version: str) -> str:
    loc = os.environ.get(f"MAYA_LOCATION_{version}") or os.environ.get("MAYA_LOCATION")
    candidates = []
    if loc:
        candidates.append(Path(loc) / "bin" / "maya.exe")
    for base in (os.environ.get("ProgramFiles", "C:/Program Files"), os.environ.get("ProgramW6432", "C:/Program Files")):
        candidates.append(Path(base) / f"Autodesk/Maya{version}" / "bin" / "maya.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(f"Maya {version} not found (set MAYA_LOCATION).")


def main() -> int:
    """Launch Maya GUI and call ``run()`` with ``MMD_E2E_MODEL_PATH``."""

    import io

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    model_path = os.environ.get("MMD_E2E_MODEL_PATH")
    if not model_path:
        raise SystemExit("Set MMD_E2E_MODEL_PATH to the PMX/PMD path.")

    project_root = Path(__file__).resolve().parents[2]
    out_dir = Path(os.environ.get("MMD_E2E_OUT_DIR", project_root / "build" / "resolve_e2e")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "resolve_e2e.log"
    if log_path.exists():
        log_path.unlink()

    maya_version = os.environ.get("MMD_E2E_MAYA_VERSION", DEFAULT_MAYA_VERSION)
    port = int(os.environ.get("MMD_E2E_COMMAND_PORT", str(COMMAND_PORT)))
    maya_exe = _find_maya(maya_version)
    logger.info("Maya executable: %s", maya_exe)

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{project_root};{env.get('PYTHONPATH', '')}"
    env["MAYA_VP2_DEVICE_OVERRIDE"] = "VirtualDeviceDx11"

    maya_out_path = out_dir / "maya_gui_stdout.log"
    maya_err_path = out_dir / "maya_gui_stderr.log"
    out_fh = open(maya_out_path, "w", encoding="utf-8", errors="replace")
    err_fh = open(maya_err_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [maya_exe, "-command", f'commandPort -name ":{port}" -sourceType "python";'],
        env=env,
        stdout=out_fh,
        stderr=err_fh,
    )
    try:
        start = time.time()
        while time.time() - start < MAYA_START_TIMEOUT:
            if proc.poll() is not None:
                raise RuntimeError(f"Maya exited before commandPort opened ({proc.returncode})")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except (socket.timeout, ConnectionRefusedError):
                time.sleep(1)
        else:
            raise TimeoutError(f"commandPort :{port} never opened")

        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path(r'{project_root.as_posix()}')\n"
            "if str(project_root) not in sys.path:\n"
            "    sys.path.insert(0, str(project_root))\n"
            "from tests.viewport.resolve_e2e import run\n"
            f"run({json.dumps(model_path, ensure_ascii=True)}, {json.dumps(log_path.as_posix())})\n"
        )
        with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
            sock.sendall(command.encode("utf-8"))

        if not log_path.exists():
            log_path.touch()
        done = False
        exit_code = 1
        start = time.time()
        with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, 2)
            while time.time() - start < RESOLVE_TIMEOUT:
                line = handle.readline()
                if line:
                    print(line, end="")
                    if COMPLETION_MARKER in line:
                        done = True
                        try:
                            exit_code = int(json.loads(line).get("exit_code", 1))
                        except Exception:
                            exit_code = 1
                        break
                else:
                    time.sleep(LOG_POLL_INTERVAL)
        if not done:
            raise TimeoutError(f"resolve E2E did not finish within {RESOLVE_TIMEOUT}s")
        return exit_code
    finally:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                sock.sendall(b"import maya.cmds as cmds; cmds.quit(force=True)")
        except Exception:
            pass
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
        for handle in (out_fh, err_fh):
            try:
                handle.close()
            except Exception:
                pass
        for label, path in (("MAYA STDOUT", maya_out_path), ("MAYA STDERR", maya_err_path)):
            text = path.read_text(encoding="utf-8", errors="replace").strip() if path.exists() else ""
            if text:
                print(f"\n===== {label} (tail) =====\n" + "\n".join(text.splitlines()[-40:]) + f"\n===== end {label} =====")


if __name__ == "__main__":
    raise SystemExit(main())
