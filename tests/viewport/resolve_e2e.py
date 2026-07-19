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
import sys
import time
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

DEFAULT_MAYA_VERSION = "2026"
COMMAND_PORT = 7723
COMPLETION_MARKER = "RESOLVE_E2E_DONE"
RESOLVE_TIMEOUT = 600
LOG_POLL_INTERVAL = 1
CAPTURE_WIDTH = 1280
CAPTURE_HEIGHT = 720

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

    def _actual_playblast_path(target: Path):
        candidates = list(target.parent.glob(target.stem + "*.png"))
        if target.exists():
            candidates.append(target)
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _capture(label):
        out_dir = Path(log_path).resolve().parent if log_path else Path("build/resolve_e2e").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{label}.png"
        for old in target.parent.glob(target.stem + "*.png"):
            try:
                old.unlink()
            except Exception:
                pass
        try:
            cmds.refresh(force=True)
            result = cmds.playblast(
                filename=str(target.with_suffix("")),
                frame=0,
                format="image",
                compression="png",
                offScreen=True,
                viewer=False,
                width=CAPTURE_WIDTH,
                height=CAPTURE_HEIGHT,
                forceOverwrite=True,
                showOrnaments=False,
                percent=100,
            )
            actual = _actual_playblast_path(target)
            payload = {
                "event": "capture",
                "phase": label,
                "requested_path": str(target),
                "actual_path": str(actual) if actual else "",
                "playblast_result": result,
                "size": actual.stat().st_size if actual else 0,
                "width": CAPTURE_WIDTH,
                "height": CAPTURE_HEIGHT,
            }
        except Exception as exc:
            payload = {"event": "capture", "phase": label, "error": str(exc)}
        _emit(payload, log_path)
        return payload

    def _snapshot(label):
        from mmd_tools.core import maya_material_utils
        from mmd_tools.core.constants import ATTR_MMD_ORIGINAL_TEXTURE_PATH, ATTR_MMD_TEXTURE_CACHE_PATH
        from mmd_tools.core.texture_path_cache import decode_original_texture_path

        def _dx11_texture_state(file_node):
            slot_attrs = {
                "MainTexture": "HasMainTexture",
                "SphereTexture": "HasSphereTexture",
                "ToonTexture": "HasToonTexture",
            }
            connected_plugs = cmds.listConnections(
                f"{file_node}.outColor",
                type="dx11Shader",
                source=False,
                destination=True,
                plugs=True,
            ) or []
            shader = ""
            slots = {}
            for texture_attr, has_attr in slot_attrs.items():
                plug = next((item for item in connected_plugs if item.endswith(f".{texture_attr}")), "")
                if plug and "." in plug:
                    shader = plug.rsplit(".", 1)[0]
                has_value = None
                if shader:
                    try:
                        if cmds.attributeQuery(has_attr, node=shader, exists=True):
                            has_value = cmds.getAttr(f"{shader}.{has_attr}")
                    except Exception:
                        pass
                slots[texture_attr] = {
                    "connected": bool(plug),
                    "plug": plug,
                    "has_attr": has_attr,
                    "has_value": has_value,
                }
            if not shader:
                suffixes = {
                    "_sphere_texture": "SphereTexture",
                    "_toon_texture": "ToonTexture",
                    "_texture": "MainTexture",
                }
                for suffix in sorted(suffixes, key=len, reverse=True):
                    if not file_node.endswith(suffix):
                        continue
                    candidate = file_node[: -len(suffix)]
                    try:
                        if cmds.objExists(candidate) and cmds.nodeType(candidate) == "dx11Shader":
                            shader = candidate
                            break
                    except Exception:
                        pass
            if shader:
                try:
                    for texture_attr, has_attr in slot_attrs.items():
                        if slots[texture_attr]["has_value"] is None and cmds.attributeQuery(
                            has_attr,
                            node=shader,
                            exists=True,
                        ):
                            slots[texture_attr]["has_value"] = cmds.getAttr(f"{shader}.{has_attr}")
                except Exception:
                    pass
            return shader, slots

        rows = []
        for file_node in _file_nodes_with_mmd_original():
            original = decode_original_texture_path(_attr(file_node, ATTR_MMD_ORIGINAL_TEXTURE_PATH))
            current = _attr(file_node, "fileTextureName")
            cache_path = _attr(file_node, ATTR_MMD_TEXTURE_CACHE_PATH)
            classification = maya_material_utils.classify_mmd_texture_file_node(file_node)
            shader, slots = _dx11_texture_state(file_node)
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
                    "main_texture_connected": slots["MainTexture"]["connected"],
                    "has_main_texture": slots["MainTexture"]["has_value"],
                    "texture_slots": slots,
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

        from mmd_tools.core import maya_material_utils, settings
        from mmd_tools.io.mmd_importer import import_mmd_file

        settings.set("import.model.create_mmd_shaders", True)
        settings.set("import.model.mmd_shader_backend", "dx11")
        settings.set("import.model.auto_resolve_textures", False)

        root_node = import_mmd_file(str(model_path), options={"import_physics": False})
        _emit(
            {
                "event": "imported",
                "phase": "post_hoc_candidate",
                "auto_resolve": False,
                "root_node": root_node,
            },
            log_path,
        )
        if not root_node:
            _emit({"event": "summary", "line": "RESOLVE_E2E: resolved=0 failed=1"}, log_path)
            _emit({"event": COMPLETION_MARKER, "exit_code": 1}, log_path)
            return 1

        for row in _snapshot("before"):
            _emit(row, log_path)

        results = maya_material_utils.resolve_scene_mmd_textures()
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
                        "rebind_status": getattr(result, "rebind_status", ""),
                        "rebind_reason": getattr(result, "rebind_reason", ""),
                        "rebind_shader": getattr(result, "rebind_shader", ""),
                        "rebind_texture_attr": getattr(result, "rebind_texture_attr", ""),
                        "rebind_has_attr": getattr(result, "rebind_has_attr", ""),
                    }
                    for result in results
                ],
            },
            log_path,
        )

        for row in _snapshot("after"):
            _emit(row, log_path)
        post_hoc_capture = _capture("post_hoc_resolved")

        cmds.file(new=True, force=True)
        settings.set("import.model.create_mmd_shaders", True)
        settings.set("import.model.mmd_shader_backend", "dx11")
        settings.set("import.model.auto_resolve_textures", True)
        baseline_root = import_mmd_file(str(model_path), options={"import_physics": False})
        _emit(
            {
                "event": "imported",
                "phase": "baseline",
                "auto_resolve": True,
                "root_node": baseline_root,
            },
            log_path,
        )
        for row in _snapshot("baseline"):
            _emit(row, log_path)
        baseline_capture = _capture("baseline_auto_resolve")
        _emit(
            {
                "event": "comparison_inputs",
                "post_hoc_capture": post_hoc_capture.get("actual_path", ""),
                "baseline_capture": baseline_capture.get("actual_path", ""),
            },
            log_path,
        )

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
    maya_exe = maya_commandport.maya_exe(maya_version)
    logger.info("Maya executable: %s", maya_exe)

    proc = maya_commandport.launch_maya(
        version=maya_version,
        project_root=project_root,
        output_dir=out_dir,
        port=port,
        launch_mode="explorer" if sys.platform == "win32" else "direct",
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11"},
    )
    maya_out_path = out_dir / "maya_stdout.log"
    maya_err_path = out_dir / "maya_stderr.log"
    try:
        maya_commandport.wait_for_port(port, timeout=120, process=proc)

        command = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path(r'{project_root.as_posix()}')\n"
            "if str(project_root) not in sys.path:\n"
            "    sys.path.insert(0, str(project_root))\n"
            "from tests.viewport.resolve_e2e import run\n"
            f"run({json.dumps(model_path, ensure_ascii=True)}, {json.dumps(log_path.as_posix())})\n"
        )
        maya_commandport.send_python(port, command, label="<resolve-e2e-command>")

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
        maya_commandport.quit_maya(port)
        try:
            if proc is not None:
                proc.wait(timeout=30)
        except Exception:
            if proc is not None:
                proc.kill()
        maya_commandport.close_process_logs(proc)
        for label, path in (("MAYA STDOUT", maya_out_path), ("MAYA STDERR", maya_err_path)):
            text = path.read_text(encoding="utf-8", errors="replace").strip() if path.exists() else ""
            if text:
                print(f"\n===== {label} (tail) =====\n" + "\n".join(text.splitlines()[-40:]) + f"\n===== end {label} =====")


if __name__ == "__main__":
    raise SystemExit(main())
