"""Collect real Maya GUI import errors for local MMD assets.

Host side launches or attaches to Maya commandPort.  Maya side imports each PMX
asset, mirrors mmd_tools logging to a file, enables Script Editor history, and
writes one JSONL record per asset.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
COMMON = ROOT / "tests" / "common"
if str(COMMON) not in sys.path:
    sys.path.insert(0, str(COMMON))

import maya_commandport


DEFAULT_MAYA_VERSION = "2026"
DEFAULT_PORT = 7721
DONE_MARKER = "//-- MMD_ASSET_ERROR_PROBE_DONE --//"


def run_asset_probe(
    repo_root: str,
    assets: list[str],
    out_dir: str,
    import_physics: bool = True,
    shader_backend: str = "dx11",
    reload_mmd_tools: bool = False,
) -> None:
    import contextlib
    import io
    import logging
    import time
    import traceback

    import maya.cmds as cmds

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw_log = out / "maya_asset_error_probe.log"
    jsonl_path = out / "maya_asset_error_probe.jsonl"
    script_history = out / "script_editor_history.log"

    for path in (raw_log, jsonl_path, script_history):
        try:
            path.write_text("", encoding="utf-8", errors="replace")
        except Exception:
            pass

    def log(message: str) -> None:
        text = str(message)
        with raw_log.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(text + "\n")
        try:
            print(text)
        except Exception:
            pass

    def emit(record: dict) -> None:
        with jsonl_path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def ensure_mmd_tools_plugin_loaded(repo: Path) -> None:
        plugin_path = repo / "mmd_tools" / "plugin_main.py"
        if not plugin_path.is_file():
            raise RuntimeError(f"mmd_tools plugin not found: {plugin_path}")

        def plugin_loaded() -> bool:
            loaded_plugins = cmds.pluginInfo(query=True, listPlugins=True) or []
            return "plugin_main" in loaded_plugins or "plugin_main.py" in loaded_plugins

        def locator_available() -> bool:
            return "mmdRigidBodyLocator" in (cmds.allNodeTypes() or [])

        if not locator_available():
            previous = os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE")
            os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
            try:
                if plugin_loaded():
                    try:
                        cmds.unloadPlugin(str(plugin_path), force=True)
                        log(f"mmd_tools plugin unloaded for clean reload: {plugin_path}")
                    except Exception as exc:
                        log(f"WARN unload mmd_tools plugin before reload: {exc}")
                cmds.loadPlugin(str(plugin_path), quiet=True)
                log(f"mmd_tools plugin loaded: {plugin_path}")
            finally:
                if previous is None:
                    os.environ.pop("MMD_TOOLS_SKIP_SHADER_OVERRIDE", None)
                else:
                    os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = previous

        if not locator_available():
            raise RuntimeError("mmdRigidBodyLocator node type is unavailable after loading mmd_tools plugin")

    log("=== Maya asset error probe begin ===")
    log(f"Maya version: {cmds.about(version=True)}")
    log(f"assets: {len(assets)}")

    try:
        cmds.scriptEditorInfo(clearHistory=True)
        cmds.scriptEditorInfo(historyFilename=str(script_history), writeHistory=True)
        log(f"script history: {script_history}")
    except Exception as exc:
        log(f"WARN scriptEditorInfo unavailable: {exc}")

    file_handler = logging.FileHandler(str(raw_log), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("LOG %(levelname)s %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    previous_root_level = root_logger.level
    root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.DEBUG)

    try:
        repo = str(Path(repo_root).resolve())
        if repo not in sys.path:
            sys.path.insert(0, repo)
        if reload_mmd_tools:
            stale_modules = [
                name
                for name in list(sys.modules)
                if name == "mmd_tools" or name.startswith("mmd_tools.")
            ]
            for name in stale_modules:
                sys.modules.pop(name, None)
            if stale_modules:
                log(f"reloaded mmd_tools modules: {len(stale_modules)}")

        ensure_mmd_tools_plugin_loaded(Path(repo))

        from mmd_tools.core import settings, settings_keys
        from mmd_tools.core.logger import install_maya_script_editor_handler
        from mmd_tools.io.mmd_importer import import_mmd_file

        install_maya_script_editor_handler()
        settings.set(settings_keys.IMPORT_MODEL_CREATE_MMD_SHADERS, True)
        settings.set(settings_keys.IMPORT_MODEL_MMD_SHADER_BACKEND, shader_backend)
        settings.set(settings_keys.IMPORT_MODEL_SHOW_TEXTURE_ISSUE_DIALOG, False)
        settings.set(settings_keys.IMPORT_MODEL_AUTO_RESOLVE_TEXTURES, True)
        settings.set(settings_keys.IMPORT_PHYSICS_IMPORT_PHYSICS, import_physics)

        try:
            cmds.loadPlugin("dx11Shader", quiet=True)
        except Exception as exc:
            log(f"WARN loadPlugin dx11Shader: {exc}")

        for index, asset in enumerate(assets, start=1):
            start = time.perf_counter()
            asset_path = str(Path(asset))
            log(f"--- ASSET {index}/{len(assets)} START: {asset_path}")
            record = {
                "asset": asset_path,
                "index": index,
                "ok": False,
                "root": None,
                "duration_sec": None,
                "exception": None,
                "captured_output": "",
                "mesh_count": None,
                "joint_count": None,
                "shader_count": None,
                "physics_profile": None,
                "mesh_profile": None,
                "texture_issues": [],
            }
            try:
                cmds.file(new=True, force=True)
                captured = io.StringIO()
                profile = {}
                with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                    root = import_mmd_file(
                        asset_path,
                        options={
                            "import_physics": import_physics,
                            "create_rigid_bodies": import_physics,
                            "create_physics_joints": import_physics,
                            "profile": profile,
                        },
                    )
                captured_output = captured.getvalue()
                if captured_output.strip():
                    log("CAPTURED OUTPUT >>>")
                    log(captured_output.rstrip())
                    log("<<< CAPTURED OUTPUT")
                record["root"] = root
                record["ok"] = bool(root)
                record["captured_output"] = captured_output
                record["mesh_count"] = len(cmds.ls(type="mesh") or [])
                record["joint_count"] = len(cmds.ls(type="joint") or [])
                record["shader_count"] = len(cmds.ls(type="dx11Shader") or [])
                record["physics_profile"] = profile.get("physics_converter")
                record["mesh_profile"] = profile.get("mesh_converter")
                record["texture_issues"] = _texture_issues_from_profile(profile)
                log(
                    "RESULT ok={ok} root={root} meshes={meshes} joints={joints} shaders={shaders}".format(
                        ok=record["ok"],
                        root=record["root"],
                        meshes=record["mesh_count"],
                        joints=record["joint_count"],
                        shaders=record["shader_count"],
                    )
                )
            except Exception:
                tb = traceback.format_exc()
                record["exception"] = tb
                log("EXCEPTION >>>")
                log(tb.rstrip())
                log("<<< EXCEPTION")
                try:
                    import maya.api.OpenMaya as om

                    om.MGlobal.displayError(f"MMD asset probe failed: {asset_path}")
                except Exception:
                    pass
            finally:
                record["duration_sec"] = round(time.perf_counter() - start, 3)
                emit(record)
                log(f"--- ASSET {index}/{len(assets)} END: {asset_path} ({record['duration_sec']}s)")
                try:
                    cmds.scriptEditorInfo(writeHistory=True)
                except Exception:
                    pass
    except Exception:
        fatal_traceback = traceback.format_exc()
        log("PROBE_FATAL >>>")
        log(fatal_traceback.rstrip())
        log("<<< PROBE_FATAL")
        emit(
            {
                "fatal": True,
                "exception": fatal_traceback,
            }
        )
    finally:
        try:
            cmds.scriptEditorInfo(writeHistory=False)
        except Exception:
            pass
        try:
            root_logger.removeHandler(file_handler)
            root_logger.setLevel(previous_root_level)
            file_handler.close()
        except Exception:
            pass
        log(DONE_MARKER)


def _project_root() -> Path:
    return ROOT


def _read_assets(args: argparse.Namespace) -> list[str]:
    assets = list(args.asset or [])
    if args.asset_list:
        for line in Path(args.asset_list).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                assets.append(line)
    seen: set[str] = set()
    result: list[str] = []
    for asset in assets:
        key = os.path.normcase(os.path.abspath(asset))
        if key not in seen:
            seen.add(key)
            result.append(asset)
    return result


def _texture_issues_from_profile(profile: dict) -> list[dict]:
    issues = profile.get("texture_issues")
    if issues:
        return list(issues)
    return list(profile.get("mesh_converter", {}).get("unresolved_textures") or [])


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default=DEFAULT_MAYA_VERSION)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--out-dir", default="build/asset-error-probe")
    parser.add_argument("--asset", action="append", default=[])
    parser.add_argument("--asset-list")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--startup-timeout", type=float, default=240.0)
    parser.add_argument("--attach-existing", action="store_true")
    parser.add_argument("--keep-maya", action="store_true")
    parser.add_argument("--no-physics", action="store_true")
    parser.add_argument("--reload-mmd-tools", action="store_true")
    parser.add_argument("--shader-backend", default="dx11")
    parser.add_argument(
        "--launch-mode",
        choices=["direct", "powershell"],
        default="powershell" if os.name == "nt" else "direct",
    )
    return parser.parse_args()


def _read_jsonl_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def _validate_probe_result(out_dir: Path) -> None:
    records = _read_jsonl_records(out_dir / "maya_asset_error_probe.jsonl")
    if not records:
        raise RuntimeError("Probe did not write any JSONL records")
    fatal_records = [record for record in records if record.get("fatal")]
    if fatal_records:
        raise RuntimeError("Maya asset probe failed during setup; see PROBE_FATAL in log")


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = _parse_args()
    root = _project_root()
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = (root / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    assets = _read_assets(args)
    if not assets:
        raise SystemExit("No assets specified")
    missing = [asset for asset in assets if not Path(asset).is_file()]
    if missing:
        raise SystemExit("Missing assets:\n" + "\n".join(missing))

    process: Optional[subprocess.Popen] = None
    launched = False
    if not args.attach_existing:
        if maya_commandport.is_port_open(args.port):
            raise SystemExit(
                f"commandPort :{args.port} is already open; "
                "use --attach-existing or choose a free --port"
            )
        process = maya_commandport.launch_maya(
            version=args.maya,
            project_root=root,
            output_dir=out_dir,
            port=args.port,
            launch_mode=args.launch_mode,
            env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11"},
        )
        launched = True
        print(f"launched Maya {args.maya} via {args.launch_mode}")

    try:
        maya_commandport.wait_for_port(args.port, args.startup_timeout, process)
        print(f"commandPort :{args.port} open")
        maya_commandport.remove_stale_logs(
            [
                out_dir / "maya_asset_error_probe.log",
                out_dir / "maya_asset_error_probe.jsonl",
                out_dir / "script_editor_history.log",
            ]
        )
        assets_json = json.dumps(assets, ensure_ascii=True)
        code = (
            "import importlib.util, json, sys\n"
            "from pathlib import Path\n"
            f"repo = Path(r'{root.as_posix()}')\n"
            "if str(repo) not in sys.path:\n"
            "    sys.path.insert(0, str(repo))\n"
            "module_path = repo / 'tests' / 'viewport' / 'maya_asset_error_probe.py'\n"
            "spec = importlib.util.spec_from_file_location('maya_asset_error_probe', str(module_path))\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "sys.modules['maya_asset_error_probe'] = mod\n"
            "spec.loader.exec_module(mod)\n"
            f"assets = json.loads({assets_json!r})\n"
            f"mod.run_asset_probe(r'{root.as_posix()}', assets, r'{out_dir.as_posix()}', "
            f"import_physics={not args.no_physics!r}, shader_backend={args.shader_backend!r}, "
            f"reload_mmd_tools={args.reload_mmd_tools!r})\n"
        )
        maya_commandport.send_python(args.port, code, label="<maya-asset-error-probe>")
        print("probe command sent")
        done = maya_commandport.tail_until_marker(
            out_dir / "maya_asset_error_probe.log",
            DONE_MARKER,
            args.timeout,
        )
        if not done:
            raise TimeoutError(f"Probe did not finish within {args.timeout}s")
        _validate_probe_result(out_dir)
    finally:
        if launched and not args.keep_maya:
            maya_commandport.quit_maya(args.port)
        if process is not None:
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.terminate()
            maya_commandport.close_process_logs(process)

    print(f"outputs: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
