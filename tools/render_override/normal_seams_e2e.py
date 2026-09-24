"""Compare unified/split MMD normals through small real-joint rotations."""

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
from tools.render_override.profile_e2e import geometry_summary  # noqa: E402

MARKER = "MMD NORMAL SEAMS FINISHED"


def pixel_error(left, right):
    """Compare all visible materials at identical camera and viewport size."""
    assert left[:2] == right[:2]
    errors = [max(abs(a - b) for a, b in zip(p, q)) for p, q in zip(left[2], right[2])]
    return {"mean": sum(errors) / len(errors), "fractionOver8": sum(e > 8 for e in errors) / len(errors)}


def render_pass_signature(witness):
    """Compare persisted material draw policy independently of submesh IDs."""
    return [(item["materialIndex"], item["pass"], item["outline"])
            for item in witness["pmxOrder"]]


def require_render_state(cmds, root):
    """Reject blank captures and fallback rendering, including after reopen."""
    witness = json.loads(cmds.mmdOrderedRenderWitness())
    assert witness.get("state") == "active" and witness.get("drawCount", 0) > 0, witness
    shapes = cmds.listRelatives(root, ad=True, type="mmdRenderShape", fullPath=True) or []
    states = [json.loads(cmds.mmdRenderWitness(node=s, json=True)) for s in shapes]
    assert shapes and all(not s.get("fallbackReason") and s.get("geometryUpdates", 0) > 0
                          and s.get("staticNormalFallbacks") == 0 for s in states), states
    return {"witness": witness, "shapeStates": states}


def run_probe(config_path):
    """Yield to GUI/DG between import and each pose."""
    try:
        from PySide2.QtCore import QTimer
    except ImportError:
        from PySide6.QtCore import QTimer
    steps = probe_steps(json.loads(Path(config_path).read_text(encoding="utf-8")))

    def advance():
        try:
            next(steps)
        except StopIteration:
            return
        QTimer.singleShot(150, advance)

    advance()


def probe_steps(config):
    from maya import cmds
    from mmd_tools.io.mmd_importer import import_mmd_file

    out = Path(config["output"])
    report = {"status": "fail", "conditions": config, "cases": [],
              "scope": "unified/split shading parity under skinning and after reload; not source serialization parity"}
    images = {}
    camera_matrix = None
    try:
        plugin = require_requested_plugin(cmds, config["plugin"], print)
        report["pluginSha256"] = hashlib.sha256(plugin.read_bytes()).hexdigest()
        report["modelSha256"] = hashlib.sha256(Path(config["model"]).read_bytes()).hexdigest()
        report["device"] = cmds.ogs(deviceInformation=True)
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        for split in (False, True):
            mode = "split" if split else "unified"
            initial_passes = None
            cmds.file(new=True, force=True)
            root = import_mmd_file(config["model"], options={
                "use_cpp_fast_load": True, "use_cpp_vp2_ownership": True,
                "import_morphs": False, "import_physics": False,
                "separate_meshes_by_material": split,
            })
            assert root
            joints = cmds.listRelatives(root, ad=True, type="joint", fullPath=True) or []
            joint = next(j for j in joints if cmds.attributeQuery("mmd_bone_name", node=j, exists=True)
                         and cmds.getAttr(j + ".mmd_bone_name") == config["bone"])
            assert cmds.listConnections(joint, type="skinCluster"), "joint does not drive skin"
            for old_panel in cmds.getPanel(type="modelPanel"):
                cmds.deleteUI(old_panel, panel=True)
            window = cmds.window(widthHeight=(820, 660))
            pane = cmds.paneLayout()
            panel = cmds.modelPanel(parent=pane)
            cmds.showWindow(window)
            camera, _ = cmds.camera()
            cmds.setAttr(camera + ".translateZ", 30)
            cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer", camera=camera,
                             displayAppearance="smoothShaded", displayTextures=True,
                             grid=False, allObjects=False, polymeshes=True,
                             rendererOverrideName="mmdOrdered")
            if camera_matrix is None:
                cmds.select(root)
                cmds.viewFit(camera, fitFactor=0.8, animate=False)
                camera_matrix = cmds.xform(camera, query=True, matrix=True, worldSpace=True)
            else:
                cmds.xform(camera, matrix=camera_matrix, worldSpace=True)
            cmds.select(clear=True)
            cmds.setAttr("hardwareRenderingGlobals.multiSampleEnable", False)
            yield
            initial = cmds.getAttr(joint + ".rotateX")
            for case, angle in enumerate((0.0, 0.25, -0.25, 1.0, 0.0)):
                cmds.setAttr(joint + ".rotateX", initial + angle)
                yield
                cmds.refresh(force=True)
                path = capture_view(cmds, out / f"{mode}-{case}.png", panel, 800, 600)
                pixels = read_png_rgb(path)
                images[mode, case] = pixels
                row = {"mode": mode, "angle": angle, "image": str(path),
                       **require_render_state(cmds, root)}
                if case == 0:
                    initial_passes = render_pass_signature(row["witness"])
                if split:
                    row["unifiedDifference"] = pixel_error(images["unified", case], pixels)
                report["cases"].append(row)
            assert images[mode, 0] == images[mode, 4], "restoring joint changed pixels"
            scene = out / f"{mode}.ma"
            meshes = [m for m in cmds.listRelatives(root, ad=True, type="mesh", fullPath=True) or []
                      if not cmds.getAttr(m + ".intermediateObject")]
            before_geometry = geometry_summary(meshes)
            cmds.file(rename=str(scene))
            cmds.file(save=True, type="mayaAscii")
            cmds.file(str(scene), open=True, force=True)
            yield
            cmds.refresh(force=True)
            reopened = read_png_rgb(capture_view(cmds, out / f"{mode}-reopened.png", panel, 800, 600))
            images[mode, "reopened"] = reopened
            row = {"mode": mode, "reopenDifference": pixel_error(images[mode, 0], reopened),
                   "sourceGeometryBefore": before_geometry,
                   "sourceGeometryAfter": geometry_summary(meshes),
                   **require_render_state(cmds, root)}
            row["renderPassesPreserved"] = (
                render_pass_signature(row["witness"]) == initial_passes
            )
            if split:
                row["unifiedDifference"] = pixel_error(images["unified", "reopened"], reopened)
            report["cases"].append(row)
            assert row["renderPassesPreserved"], "material draw passes changed on reopen"
            assert row["reopenDifference"]["mean"] < 0.1, row["reopenDifference"]
            assert row["reopenDifference"]["fractionOver8"] < 0.001, row["reopenDifference"]
        # The baseline YYB fixture changes upstream on save/reopen in both modes.
        # Record that separately; verify the renderer matches split corner normals
        # for the CURRENT source geometry, including after mapping reconstruction.
        report["sourceSerializationUnchanged"] = all(
            row["sourceGeometryBefore"] == row["sourceGeometryAfter"]
            for row in report["cases"] if "sourceGeometryBefore" in row)
        # Permit subpixel raster/order differences (<0.1% of pixels beyond 8/255).
        # The old averaged-normal baseline exceeds this by over six times.
        for row in report["cases"]:
            if "unifiedDifference" in row:
                error = row["unifiedDifference"]
                assert error["mean"] < 0.1 and error["fractionOver8"] < 0.001, error
        assert images["unified", 0] != images["unified", 3], "rotation had no visible effect"
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", choices=("2024", "2026"), default="2024")
    parser.add_argument("--config", choices=("Debug", "Release"), default="Release")
    parser.add_argument("--plugin", type=Path, help="Explicit candidate or baseline binary")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--bone", default="上半身2")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7758)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if not out.is_relative_to(ROOT / "build"):
        parser.error("output must be inside build")
    out.mkdir(parents=True, exist_ok=True)
    plugin = (args.plugin or ROOT / f"plug-ins/{args.maya}/{args.config}/mmd_tools_cpp.mll").resolve()
    config = out / "config.json"
    config.write_text(json.dumps({"model": str(args.model.resolve()), "plugin": str(plugin),
                                 "output": str(out), "bone": args.bone}), encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=480,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=("from tools.render_override.normal_seams_e2e import run_probe\n"
                 f"run_probe({str(config)!r})"),
        marker=MARKER, send_label="mmd-normal-seams",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                       "MAYA_SKIP_USERSETUP_PY": "1", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    print(json.dumps({"status": report.get("status"), "error": report.get("error")}, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
