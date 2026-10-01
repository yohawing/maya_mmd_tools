"""Check material proxy editing, appearance and timings across GUI scene reloads."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override.common import capture_view, require_requested_plugin  # noqa: E402
from tools.render_override.render_override_visual_gate import read_png_rgb  # noqa: E402

MARKER = "MATERIAL PROXY RELOAD FINISHED"


def probe(config_path):
    """Exercise freshly imported material proxies and keep raw load diagnostics."""
    from maya import cmds, utils
    from maya.api import OpenMaya as om
    from mmd_tools.core import maya_proxy_attribute
    from mmd_tools.core.mmd_parser import parse_pmx_file
    from mmd_tools.io.mmd_importer import import_mmd_file

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["output"])
    report = {"status": "fail", "conditions": config, "cases": [], "loads": []}
    images = {}
    try:
        plugin = require_requested_plugin(cmds, config["plugin"], print)
        report["pluginSha256"] = hashlib.sha256(plugin.read_bytes()).hexdigest()
        report["modelSha256"] = hashlib.sha256(Path(config["model"]).read_bytes()).hexdigest()
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        cmds.file(new=True, force=True)
        root = import_mmd_file(config["model"], options={"setup_rig": True, "import_physics": False})
        assert root
        pmx = parse_pmx_file(config["model"], use_native_pmx_parse=False)
        indices = [i for i, morph in enumerate(pmx.morphs) if int(morph.morph_type) == 8 and morph.offsets][:2]
        assert len(indices) == 2, "fixture must contain two material morphs"
        controller, = cmds.ls(type="mmdMorphController")
        plugs = [f"{shader}.{attr}" for shader in cmds.ls(type="standardSurface")
                 for attr in ("diffuse_color", "specular_color", "ambient_color", "mmd_edge_color")
                 if cmds.attributeQuery(attr, node=shader, exists=True)
                 and maya_proxy_attribute.is_proxy(f"{shader}.{attr}")]
        assert plugs, "no material proxies were created"
        report["proxyCount"] = len(plugs)
        panel = cmds.getPanel(type="modelPanel")[-1]
        cmds.modelEditor(panel, edit=True, camera="persp", displayAppearance="smoothShaded",
                         displayTextures=True, grid=False)
        cmds.select(root)
        cmds.viewFit()
        cmds.select(clear=True)
        cmds.setAttr("hardwareRenderingGlobals.multiSampleEnable", False)
        for stage, fmt in (("fresh", None), ("ascii", "mayaAscii"),
                           ("binary", "mayaBinary"), ("ascii-repeat", "mayaAscii")):
            if fmt:
                path = out / (stage + (".ma" if fmt == "mayaAscii" else ".mb"))
                cmds.file(rename=str(path))
                cmds.file(save=True, type=fmt, force=True)
                errors = []
                callback = om.MCommandMessage.addCommandOutputCallback(
                    lambda message, kind, *_: errors.append(message) if kind == om.MCommandMessage.kError else None
                )
                try:
                    cmds.file(str(path), open=True, force=True)
                finally:
                    om.MMessage.removeCallback(callback)
                report["loads"].append({"stage": stage, "mayaErrors": errors})
                # Maya's own addAttr -uap -nc error remains; don't hide other errors.
                assert all("プロキシ" in error or "proxy" in error.lower() for error in errors), errors
            assert all(maya_proxy_attribute.is_proxy(plug) for plug in plugs), stage
            for plug in plugs:
                before = cmds.getAttr(plug)[0]
                cmds.setAttr(plug, 0.23, 0.34, 0.45, type="double3")
                assert cmds.getAttr(plug)[0] == (0.23, 0.34, 0.45), plug
                cmds.undo()
                assert cmds.getAttr(plug)[0] == before, plug
                cmds.redo()
                assert cmds.getAttr(plug)[0] == (0.23, 0.34, 0.45), plug
                cmds.setAttr(plug, *before, type="double3")
            for mode in ("", "mmdOrdered"):
                cmds.modelEditor(panel, edit=True, rendererOverrideName=mode)
                for index in indices:
                    weight_plug = f"{controller}.outputWeight[{index}]"
                    samples = []
                    for iteration in range(24):
                        start = time.perf_counter()
                        cmds.setAttr(weight_plug, (iteration % 2) * 0.8)
                        written = time.perf_counter()
                        cmds.refresh(force=True)
                        if iteration >= 4:
                            samples.append([(written - start) * 1000, (time.perf_counter() - written) * 1000])
                    row = {"stage": stage, "mode": mode, "morphIndex": index,
                           "samplesMs": samples, "writeMedianMs": statistics.median(s[0] for s in samples),
                           "refreshMedianMs": statistics.median(s[1] for s in samples), "images": []}
                    report["cases"].append(row)
                    for weight in (0, 0.5, 1):
                        cmds.setAttr(weight_plug, weight)
                        for _ in range(3):
                            cmds.refresh(force=True)
                            utils.processIdleEvents()
                        image_path = capture_view(cmds, out / f"{stage}-{mode or 'standard'}-{index}-{weight}.png", panel, 640, 480)
                        pixels = read_png_rgb(image_path)
                        key = (mode, index, weight)
                        if stage == "fresh":
                            images[key] = pixels
                        reference = images[key]
                        assert reference[:2] == pixels[:2]
                        different = sum(a != b for a, b in zip(reference[2], pixels[2]))
                        errors = [max(abs(a - b) for a, b in zip(left, right))
                                  for left, right in zip(reference[2], pixels[2])]
                        mean = sum(errors) / len(errors)
                        fraction = sum(error > 8 for error in errors) / len(errors)
                        row["images"].append({"path": str(image_path), "differentPixels": different,
                                              "mean": mean, "fractionOver8": fraction})
                        # Match the existing normal-seam scene-reload visual gate.
                        assert mean < 0.1 and fraction < 0.001, row["images"][-1]
                    if mode:
                        witness = json.loads(cmds.mmdOrderedRenderWitness())
                        assert witness.get("state") == "active" and witness.get("drawCount", 0) > 0, witness
                    cmds.setAttr(weight_plug, 0)
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", choices=("2024", "2026", "2027"), default="2024")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7796)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if not out.is_relative_to(ROOT / "build"):
        parser.error("output must be inside build")
    out.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    config = out / "config.json"
    config.write_text(json.dumps({"model": str(args.model.resolve()), "plugin": str(plugin),
                                 "output": str(out), "maya": args.maya}), encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=900,
        log_path=out / "probe.log", report_path=out / "report.json", marker=MARKER,
        command=("from maya import cmds\n"
                 "from tools.smoke.maya_material_proxy_reload import probe\n"
                 f"cmds.evalDeferred(lambda: probe({str(config)!r}), lowestPriority=True)\n"),
        send_label="material-proxy-reload", stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_SKIP_USERSETUP_PY": "1", "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                       "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps({"status": report["status"], "error": report.get("error")}, ensure_ascii=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
