"""Verify Ordered effects on an explicit representative PMX in a real Maya GUI."""

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
from tools.render_override.render_override_visual_gate import read_png_rgb  # noqa: E402

MARKER = "MMD RENDER EFFECTS FINISHED"


def run_probe(config_path):
    from maya import cmds
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.converters.material_morph_runtime import _collect_shaders_by_material_index
    from tools.render_override.shadow_checks import check_self_shadow

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["output"])
    report = {"status": "fail", "model": config["model"], "splitMaterials": config["split"]}
    try:
        cmds.file(new=True, force=True)
        plugin = require_requested_plugin(cmds, Path(config["plugin"]), print)
        report["pluginSha256"] = hashlib.sha256(plugin.read_bytes()).hexdigest()
        report["mayaVersion"] = cmds.about(version=True)
        report["modelSha256"] = hashlib.sha256(Path(config["model"]).read_bytes()).hexdigest()
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        root = import_mmd_file(config["model"], options={
            "use_cpp_fast_load": True, "use_cpp_vp2_ownership": True,
            "import_morphs": False, "import_physics": False,
            "separate_meshes_by_material": config["split"],
        })
        assert root
        shape = cmds.listRelatives(root, ad=True, type="mmdRenderShape", fullPath=True)[0]
        window = cmds.window(widthHeight=(1050, 1050))
        pane = cmds.paneLayout()
        panel = cmds.modelPanel(parent=pane)
        cmds.showWindow(window)
        cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer", rendererOverrideName="mmdOrdered",
                         displayAppearance="smoothShaded", displayTextures=True, grid=False)
        cmds.select(clear=True)
        report["shadow"] = check_self_shadow(cmds, root, shape, panel, out)
        if config.get("vp2ShadowComparison"):
            from tools.render_override.vp2_shadow_checks import check_vp2_shadow

            report["vp2ShadowComparison"] = check_vp2_shadow(
                cmds, root, panel, out, report["shadow"])
        shaders = _collect_shaders_by_material_index(root)
        flags = {shader: cmds.getAttr(shader + ".mmd_draw_flags") for shader in shaders.values()}

        def capture(label):
            cmds.refresh(force=True)
            pixels = read_png_rgb(capture_view(cmds, out / (label + ".png"), panel, 1024, 1024))[2]
            witness = json.loads(cmds.mmdOrderedRenderWitness(shadowDepth=True))
            assert not witness["error"] and witness["drawCount"] > 0, witness
            return pixels, witness

        # Render enabled PMX edges, then disable only the edge bit and restore.
        edges, edge_witness = capture("edges_on")
        assert any(item["outline"] for item in edge_witness["pmxOrder"]), "fixture has no enabled outlines"
        for shader, value in flags.items():
            cmds.setAttr(shader + ".mmd_draw_flags", value & ~16)
        plain, plain_witness = capture("edges_off")
        assert not any(item["outline"] for item in plain_witness["pmxOrder"])
        changed = sum(a != b for a, b in zip(edges, plain))
        assert changed > 100, "outline toggle has no visible effect"
        for shader, value in flags.items():
            cmds.setAttr(shader + ".mmd_draw_flags", value)
        assert capture("edges_restored")[0] == edges
        report["outlinePixels"] = changed
        order = [item["materialIndex"] for item in edge_witness["pmxOrder"]
                 if item["pass"] == "Transparent" and not item["outline"]]
        assert len(order) > 1 and order == sorted(order), order
        report["transparentOrder"] = order
        from tools.render_override.lifecycle_checks import check_renderer_lifecycle

        report["lifecycle"] = check_renderer_lifecycle(cmds, panel, plugin, out)
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "probe.log").write_text(MARKER, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7745)
    parser.add_argument("--split-materials", action="store_true")
    parser.add_argument("--vp2-shadow-comparison", action="store_true")
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    config = out / "config.json"
    config.write_text(json.dumps({"model": str(args.model.resolve()), "output": str(out),
                                 "plugin": str(plugin), "split": args.split_materials,
                                 "vp2ShadowComparison": args.vp2_shadow_comparison}), encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=300,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=("from maya import cmds\nfrom tools.render_override.effects_e2e import run_probe\n"
                 f"cmds.evalDeferred(lambda: run_probe({str(config)!r}), lowestPriority=True)"),
        marker=MARKER, send_label="mmd-render-effects",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
