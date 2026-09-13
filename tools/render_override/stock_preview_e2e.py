"""Check the standardSurface preview on Maya's OpenGL Core viewport."""

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
from tools.render_override.render_override_visual_gate import read_png_rgb, write_png_rgb  # noqa: E402

MARKER = "MMD STOCK PREVIEW FINISHED"


def run_probe(output, plugin):
    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtCore import QTimer
    steps = _probe_steps(output, plugin)

    def advance():
        try:
            next(steps)
        except StopIteration:
            return
        QTimer.singleShot(100, advance)

    advance()


def _probe_steps(output, plugin):
    from maya import cmds, utils
    from maya.api import OpenMayaRender as omr
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.converters.material_morph_runtime import _collect_shaders_by_material_index

    out = Path(output)
    report = {"status": "fail", "mayaVersion": cmds.about(version=True)}
    try:
        cmds.file(new=True, force=True)
        loaded = require_requested_plugin(cmds, Path(plugin), print)
        report["pluginSha256"] = hashlib.sha256(loaded.read_bytes()).hexdigest()
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        assert omr.MRenderer.drawAPI() == omr.MRenderer.kOpenGLCoreProfile
        report["drawAPI"] = "OpenGLCoreProfile"
        fixture = PmxData().parse_file(str(ROOT / "tests/data/test_morph_model.pmx"))
        texture = out / "checker.png"
        write_png_rgb(texture, 8, 8, [(230, 210, 160) if (x // 2 + y // 2) % 2 else (20, 80, 230)
                                     for y in range(8) for x in range(8)])
        fixture.textures = [str(texture)]
        for material in fixture.materials:
            material.texture_index = 0
        model = out / "textured.pmx"
        fixture.write_file(str(model))
        root = import_mmd_file(str(model), options={"use_cpp_fast_load": True,
            "use_cpp_vp2_ownership": False, "import_morphs": True, "import_physics": False})
        assert root
        shaders = _collect_shaders_by_material_index(root)
        assert len(shaders) == 2 and all(cmds.nodeType(s) == "standardSurface" for s in shaders.values())
        assert not cmds.ls(type="dx11Shader") and not cmds.ls(type="GLSLShader")
        window = cmds.window(widthHeight=(660, 520))
        pane = cmds.paneLayout()
        panel = cmds.modelPanel(parent=pane)
        cmds.showWindow(window)
        assert "mmdOrdered" not in (cmds.modelEditor(panel, q=True, rendererOverrideList=True) or [])
        cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer", displayAppearance="smoothShaded",
                         displayTextures=True, camera="persp", grid=False, twoSidedLighting=True, displayLights="all")
        cmds.setAttr("persp.translate", 17, 17, 17, type="double3")
        cmds.setAttr("persp.rotate", -27, 45, 0, type="double3")
        cmds.directionalLight(rotation=(0, 0, 0), intensity=1.0)
        cmds.directionalLight(rotation=(0, 180, 0), intensity=1.0)
        cmds.select(clear=True)

        def capture(label):
            cmds.undoInfo(stateWithoutFlush=False)
            try:
                for _ in range(3):
                    cmds.refresh(force=True)
                    utils.processIdleEvents()
                return read_png_rgb(capture_view(cmds, out / (label + ".png"), panel, 640, 480))[2]
            finally:
                cmds.undoInfo(stateWithoutFlush=True)

        yield
        original = capture("original")
        cmds.modelEditor(panel, edit=True, displayTextures=False)
        plain = capture("texture_off")
        report["texturePixels"] = sum(a != b for a, b in zip(original, plain))
        assert report["texturePixels"] > 100
        cmds.modelEditor(panel, edit=True, displayTextures=True)
        assert capture("texture_restored") == original
        shader = shaders[0]
        cmds.setAttr(shader + ".diffuse_color", .1, .8, .1, type="double3")
        edited = capture("edited")
        report["colorPixels"] = sum(a != b for a, b in zip(original, edited))
        assert report["colorPixels"] > 100
        cmds.undo()
        assert capture("undo") == original
        cmds.redo()
        assert capture("redo") == edited
        cmds.setAttr(shader + ".mmd_diffuse_alpha", .3)
        alpha = capture("alpha")
        report["alphaPixels"] = sum(a != b for a, b in zip(edited, alpha))
        assert report["alphaPixels"] > 100
        scene = out / "stock.ma"
        cmds.file(rename=str(scene))
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(str(scene), open=True, force=True)
        yield
        assert capture("reloaded") == alpha
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "probe.log").write_text(MARKER, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7747)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=180,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=("from maya import cmds\nfrom tools.render_override.stock_preview_e2e import run_probe\n"
                 f"cmds.evalDeferred(lambda: run_probe({str(out)!r}, {str(plugin)!r}), lowestPriority=True)"),
        marker=MARKER, send_label="mmd-stock-preview",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceGLCore", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
