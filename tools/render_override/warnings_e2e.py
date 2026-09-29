"""Verify actionable, non-repeating MMD Render warnings in a real Maya GUI."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override.common import capture_view, require_requested_plugin  # noqa: E402

MARKER = "MMD RENDER WARNINGS FINISHED"


def run_probe(output, plugin, failure):
    """Let VP2 finish each state transition before reading its diagnostics."""
    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtCore import QTimer
    steps = probe_steps(Path(output), Path(plugin), failure)

    def advance():
        try:
            next(steps)
        except StopIteration:
            return
        QTimer.singleShot(200, advance)

    advance()


def probe_steps(out, plugin, failure):
    from maya import cmds
    from maya.api import OpenMaya as om
    from mmd_tools.io.mmd_importer import import_mmd_file

    report = {"status": "fail", "failureCase": failure, "checks": []}
    warnings = []
    callback = None
    try:
        cmds.file(new=True, force=True)
        loaded = require_requested_plugin(cmds, plugin, print)
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        report.update(mayaVersion=cmds.about(version=True), plugin=str(loaded),
                      pluginSha256=hashlib.sha256(loaded.read_bytes()).hexdigest(),
                      device=cmds.ogs(deviceInformation=True))
        for panel in cmds.getPanel(type="modelPanel"):
            cmds.deleteUI(panel, panel=True)
        window = cmds.window(widthHeight=(660, 520))
        pane = cmds.paneLayout()
        panel = cmds.modelPanel(parent=pane)
        cmds.showWindow(window)
        cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer", camera="persp",
                         displayAppearance="smoothShaded", displayTextures=True,
                         grid=False, allObjects=False, polymeshes=True,
                         rendererOverrideName="mmdOrdered")

        def on_output(message, message_type, *_):
            if message_type == om.MCommandMessage.kWarning and "MMD Render:" in message:
                warnings.append(message)
                if failure and "D3DCompileFromFile(MainVS) failed" in message:
                    # Make retry reach a different failure: MainVS compiles,
                    # but the ordinary shader has no native body techniques.
                    shader = Path(os.environ["MMD_TOOLS_NATIVE_SHADER_PATH"])
                    shared = (ROOT / "mmd_tools/shaders/MMDShader.fx").as_posix()
                    shader.write_text(f'#include "{shared}"\n', encoding="utf-8")
                    report["changedShaderOnFirstFailure"] = True

        callback = om.MCommandMessage.addCommandOutputCallback(on_output)

        def check(name, new_warnings, reason, state):
            cmds.refresh(force=True)
            before = len(warnings)
            for _ in range(6):
                cmds.refresh(force=True)
            witness = json.loads(cmds.mmdOrderedRenderWitness())
            row = {"name": name, "warningCount": len(warnings), "witness": witness}
            report["checks"].append(row)
            assert len(warnings) == new_warnings, (name, warnings)
            assert witness["state"] == state and witness.get("reasonCode") == reason, row
            # The asynchronous transition may already have warned before this
            # check. Repeated explicit redraws must not issue another copy.
            assert len(warnings) == before, row
            return witness

        def import_model(native, activate=True):
            cmds.modelEditor(panel, edit=True, rendererOverrideName="")
            root = import_mmd_file(str(ROOT / "tests/data/yw_test_model.pmx"), options={
                "use_cpp_fast_load": True, "use_cpp_vp2_ownership": native,
                "import_morphs": False, "import_physics": False})
            assert root
            cmds.select(root)
            cmds.viewFit("persp", fitFactor=0.8, animate=False)
            cmds.select(clear=True)
            if activate:
                cmds.modelEditor(panel, edit=True, rendererOverrideName="mmdOrdered")
            return root

        yield
        check("empty", 0, "no_drawables", "idle")
        cube = cmds.polyCube()[0]
        yield
        check("ordinary_mesh", 0, "no_drawables", "idle")
        cmds.delete(cube)
        if failure:
            # Both warnings can coexist; retrying the broken shader must not
            # alternate the two messages indefinitely.
            import_model(False, activate=False)
            import_model(True)
            yield
            witness = check("shader_failure", 2, "render_failed", "fallback")
            assert witness["error"] and witness["missingRenderData"], witness
            assert sum("Could not use MMD rendering" in warning for warning in warnings) == 1, warnings
            assert sum("Reimport the PMX" in warning for warning in warnings) == 1, warnings
            yield
            retried = check("latched_failure_no_repeat", 2, "render_failed", "fallback")
            assert report.get("changedShaderOnFirstFailure"), report
            assert "getEffectsFileShader failed" in retried["error"], retried
        else:
            root = import_model(False)
            yield
            check("missing_data", 1, "missing_render_data", "fallback")
            assert "Reimport the PMX" in warnings[0]
            capture_view(cmds, out / "missing.png", panel, 640, 480)
            cmds.hide(root)
            yield
            check("hidden", 1, "no_drawables", "idle")
            cmds.showHidden(root)
            yield
            check("visible_again", 2, "missing_render_data", "fallback")
            cube = cmds.polyCube()[0]
            cmds.select(cube)
            cmds.isolateSelect(panel, state=True)
            cmds.isolateSelect(panel, addSelected=True)
            yield
            check("isolated_ordinary_mesh", 2, "no_drawables", "idle")
            cmds.isolateSelect(panel, state=False)
            cmds.modelEditor(panel, edit=True, polymeshes=False)
            yield
            check("mesh_display_off", 2, "no_drawables", "idle")
            cmds.modelEditor(panel, edit=True, polymeshes=True, displayAppearance="wireframe")
            yield
            check("wireframe", 2, "no_drawables", "idle")
            cmds.file(new=True, force=True)
            cmds.modelEditor(panel, edit=True, displayAppearance="smoothShaded")
            yield
            check("new_scene", 2, "no_drawables", "idle")
            root = import_model(True)
            yield
            witness = check("native_model", 2, "", "active")
            assert witness["drawCount"] > 0 and any(p["outline"] for p in witness["pmxOrder"])
            capture_view(cmds, out / "native.png", panel, 640, 480)
            cmds.hide(root)
            yield
            check("native_hidden", 2, "no_drawables", "idle")
            cmds.showHidden(root)
            proxy = cmds.ls(type="mmdRenderShape", long=True)[0]
            # Proxy transform visibility is connected to the source; hide the
            # shape itself to keep the ordinary mesh visible for this check.
            cmds.setAttr(proxy + ".visibility", False)
            yield
            check("proxy_hidden", 2, "no_drawables", "idle")
            cmds.setAttr(proxy + ".visibility", True)
            import_model(False)
            yield
            check("mixed_native_and_missing", 3, "missing_render_data", "active")
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if callback is not None:
            om.MMessage.removeCallback(callback)
    report["warnings"] = warnings
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")
    cmds.quit(force=True)
    yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", choices=("2024", "2026"), default="2024")
    parser.add_argument("--port", type=int, default=7784)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--failure", action="store_true", help="Exercise shader failure in a fresh process")
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if not out.is_relative_to(ROOT / "build"):
        parser.error("output must be inside build")
    out.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    env = {"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11", "MAYA_SKIP_USERSETUP_PY": "1",
           "MMD_TOOLS_CPP_PLUGIN": str(plugin),
           "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")}
    if args.failure:
        missing_shader = out / "intentionally-missing.fx"
        # The callback creates this owned fixture during retry; reset it when
        # rerunning the same output directory.
        missing_shader.unlink(missing_ok=True)
        env["MMD_TOOLS_NATIVE_SHADER_PATH"] = str(missing_shader)
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=180,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=("from maya import cmds\nfrom tools.render_override.warnings_e2e import run_probe\n"
                 f"cmds.evalDeferred(lambda: run_probe({str(out)!r}, {str(plugin)!r}, {args.failure!r}), lowestPriority=True)"),
        marker=MARKER, send_label="mmd-render-warnings",
        stale_paths=(out / "probe.log", out / "report.json"), env_overrides=env)
    print(json.dumps({"status": report.get("status"), "error": report.get("error")}, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
