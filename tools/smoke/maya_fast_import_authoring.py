"""Compare editable Fast Load scenes through GUI import, animation and reopen."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_probe(config_path: str) -> None:
    """Run inside an isolated Maya GUI and write a completion marker on failure too."""
    from maya import cmds
    from maya.api import OpenMaya as om

    from mmd_tools.io.mmd_importer import import_mmd_file
    from tools.smoke.maya_fast_import_parity import _snapshot

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["out"])
    report = {"status": "fail", "config": config, "routes": {}, "checks": []}
    try:
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        cmds.loadPlugin(config["plugin"], quiet=True)
        for route in ("python", "cpp"):
            cmds.file(new=True, force=True)
            from mmd_tools.actions.import_model_action import ImportModelAction, ImportModelRequest
            from mmd_tools.services.settings_service import SettingsService

            service = SettingsService()
            for key, value in {
                "ui.general.development_mode": True,
                "import.general.scale_factor": config.get("scale", 1.0),
                "import.native.use_cpp_fast_load": route == "cpp",
                "import.native.cpp_fast_load_mesh_only": True,  # Legacy profile must still author a complete model.
                "import.native.use_cpp_vp2_ownership": route == "cpp" and config.get("vp2", True),
                "import.physics.import_physics": config.get("physics", False),
                "import.morph.import_morphs": True,
                "import.model.create_mmd_shaders": False,
                "import.model.create_mmd_control_rig": False,
                "import.model.separate_meshes_by_material": config.get("split", False),
            }.items():
                service.set(key, value)
            options = service.build_pmx_import_options()
            options["profile"] = {}
            native_calls = []
            original_fast_load = cmds.mmdFastLoad

            def observe_fast_load(*args, **kwargs):
                return _call_native(original_fast_load, native_calls, *args, **kwargs)

            cmds.mmdFastLoad = observe_fast_load
            try:
                imported = ImportModelAction().execute(ImportModelRequest(config["model"], options))
                if imported.error:
                    raise imported.error
                if imported.outcome != "success":
                    raise RuntimeError(f"{route} UI import outcome: {imported.outcome}: {imported.warnings}")
                root = imported.root_node
            finally:
                cmds.mmdFastLoad = original_fast_load
            if not root:
                raise RuntimeError(f"{route} import returned no model")
            _require_native_route(route, native_calls)
            result = {"import": _snapshot(cmds, om, root), "nativeCalls": native_calls}
            report["routes"][route] = result
            result["options"] = options
            result["outcome"] = imported.outcome
            if route == "cpp" and config.get("vp2", True):
                result["viewport"] = _viewport(cmds, root, out)
            import_mmd_file(config["motion"], options={"target_model": root})
            samples = {}
            for frame in (0, 1, 15, 30, 60):
                cmds.currentTime(frame, update=True)
                samples[str(frame)] = _positions(cmds, om, root)
            result["samples"] = samples
            if _motion_delta(samples) <= 1e-5:
                raise RuntimeError(f"{route} motion did not deform the fixture")
            path = out / f"{route}.ma"
            cmds.file(rename=str(path))
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(new=True, force=True)
            cmds.file(str(path), open=True, force=True)
            result["reopen"] = _positions(cmds, om, root)
            result["reopenImport"] = _snapshot(cmds, om, root)
            result["editUndoRedo"] = _edit_roundtrip(cmds, om, root)
        left, right = (report["routes"][name] for name in ("python", "cpp"))
        for phase in ("samples", "reopen"):
            error = _max_error(left[phase], right[phase])
            report["checks"].append({"name": phase, "maxError": error, "pass": error <= 1e-5})
        for route, result in report["routes"].items():
            error = _max_error(result["samples"]["60"], result["reopen"])
            report["checks"].append({"name": f"{route}-save-reopen", "maxError": error, "pass": error <= 1e-5})
        report["status"] = "pass" if all(item["pass"] for item in report["checks"]) else "fail"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "probe.log").write_text("CPP_AUTHORING_COMPLETE\n", encoding="utf-8")


def _edit_roundtrip(cmds, om, root):
    """Exercise a component edit after reopen and verify Undo/Redo restores points."""
    before = _positions(cmds, om, root)
    mesh = next(iter(before))
    cmds.move(0.25, 0, 0, mesh + ".vtx[0]", relative=True, objectSpace=True)
    edited = _positions(cmds, om, root)
    if _max_error(before, edited) <= 1e-5:
        raise RuntimeError("Reopened mesh component edit did not change a vertex")
    cmds.undo()
    if _max_error(before, _positions(cmds, om, root)) > 1e-5:
        raise RuntimeError("Component edit Undo did not restore mesh points")
    cmds.redo()
    if _max_error(edited, _positions(cmds, om, root)) > 1e-5:
        raise RuntimeError("Component edit Redo did not restore mesh points")
    cmds.undo()
    return True


def _viewport(cmds, root, out):
    """Require every proxy to draw before accepting full-import VP2 ownership."""
    from tools.smoke.maya_render_override_gui_smoke import _wait_ready
    from tools.render_override.common import capture_view

    shapes = cmds.listRelatives(root, allDescendents=True, type="mmdRenderShape", fullPath=True) or []
    if not shapes:
        raise RuntimeError("Full VP2 import has no render proxies")
    panels = cmds.getPanel(type="modelPanel") or []
    panel = "modelPanel4" if "modelPanel4" in panels else panels[0]
    cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer", displayAppearance="smoothShaded",
                     displayTextures=True, wireframeOnShaded=False, grid=False)
    cmds.lookThru(panel, "persp")
    cmds.select(shapes, replace=True)
    cmds.viewFit("persp", all=False, animate=False, fitFactor=0.8)
    cmds.select(clear=True)
    witnesses = {}
    for shape in shapes:
        witness = _wait_ready(cmds, shape, print)
        sources = cmds.listConnections(shape + ".inputMesh", source=True, destination=False, shapes=True) or []
        if not witness.startswith("ready") or len(sources) != 1 or cmds.getAttr(sources[0] + ".visibility"):
            raise RuntimeError(f"VP2 ownership not ready: {shape}: {witness}, sources={sources}")
        witnesses[shape] = witness
    capture = capture_view(cmds, out / "cpp-viewport.png", panel, 800, 600)
    return {"witnesses": witnesses, "capture": str(capture)}


def _call_native(command, calls, *args, **kwargs):
    """Record native completion so a later Python fallback cannot count as success."""
    entry = {"arguments": kwargs, "success": False}
    calls.append(entry)
    result = command(*args, **kwargs)
    expected = 1 if kwargs.get("sp") else (3 if kwargs.get("vp2Ownership") else 2)
    entry["success"] = isinstance(result, (list, tuple)) and len(result) == expected
    return result


def _require_native_route(route, calls):
    if (route == "cpp") != bool(calls) or any(not call["success"] for call in calls):
        raise RuntimeError(f"{route} did not complete its requested geometry route")


def _motion_delta(samples):
    """Recognize motion that returns to its initial pose at the last sample."""
    return max(_max_error(samples["0"], points) for points in samples.values())


def _positions(cmds, om, root):
    """Capture all editable mesh points without relying on generated shape names."""
    values = {}
    for shape in cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []:
        if cmds.getAttr(f"{shape}.intermediateObject"):
            continue
        selection = om.MSelectionList()
        selection.add(shape)
        points = om.MFnMesh(selection.getDagPath(0)).getPoints(om.MSpace.kWorld)
        key = shape.rsplit("|", 1)[0]
        values[key] = [[p.x, p.y, p.z] for p in points]
    return values


def _max_error(left, right):
    """Compare nested point samples; different topology is always a failure."""
    if isinstance(left, dict):
        if not left:
            raise ValueError("No mesh samples")
        if left.keys() != right.keys():
            raise ValueError("Sample mesh/frame keys differ")
        return max((_max_error(left[key], right[key]) for key in left), default=0.0)
    if isinstance(left, list):
        if len(left) != len(right):
            raise ValueError("Sample vertex counts differ")
        return max((_max_error(a, b) for a, b in zip(left, right)), default=0.0)
    if not math.isfinite(left) or not math.isfinite(right):
        raise ValueError("Non-finite sampled position")
    return abs(left - right)


def main() -> int:
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--model", type=Path, default=ROOT / "tests/data/mmt_test_model.pmx")
    parser.add_argument("--motion", type=Path, default=ROOT / "tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--plugin", type=Path)
    parser.add_argument("--port", type=int, default=7791)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--physics", action="store_true")
    parser.add_argument("--split", action="store_true")
    parser.add_argument("--no-vp2", action="store_true")
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args()
    out = args.out_dir or (
        ROOT / "build/reports/fast-import-authoring" / f"maya{args.maya}"
        / f"{args.model.stem}-{args.scale}-{'mesh' if args.no_vp2 else 'vp2'}-{'split' if args.split else 'unified'}-{'physics' if args.physics else 'static'}"
    )
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    suffix = ".mll" if sys.platform == "win32" else (".bundle" if sys.platform == "darwin" else ".so")
    plugin = (args.plugin or ROOT / "plug-ins" / args.maya / f"Debug/mmd_tools_cpp{suffix}").resolve()
    config = out / "input.json"
    config.write_text(json.dumps({
        "out": str(out), "plugin": str(plugin), "model": str(args.model.resolve()),
        "motion": str(args.motion.resolve()),
        "physics": args.physics, "split": args.split, "scale": args.scale, "vp2": not args.no_vp2,
    }, ensure_ascii=False), encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=300,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=f"from tools.smoke.maya_fast_import_authoring import run_probe\nrun_probe({str(config)!r})",
        marker="CPP_AUTHORING_COMPLETE", send_label="cpp-authoring",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11" if sys.platform == "win32" else "VirtualDeviceGLCore",
                       "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps({"status": report["status"], "checks": report["checks"], "error": report.get("error")}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
