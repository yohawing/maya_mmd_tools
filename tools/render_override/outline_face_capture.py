"""Capture a fresh PMX face for opaque-outline depth regression comparisons."""

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

MARKER = "MMD OUTLINE FACE CAPTURE FINISHED"


def compare_capture(report, reference, roi):
    """Require identical framing/assets and unchanged pixels in the selected face region."""
    if reference.get("status") != "pass" or report.get("status") != "pass":
        raise ValueError("Both captures must complete before image comparison")
    if Path(report["image"]).resolve() == Path(reference["image"]).resolve():
        raise ValueError("Reference and candidate must use separate image paths")
    for key in ("mayaVersion", "cameraMatrix", "face"):
        if report[key] != reference[key]:
            raise ValueError(f"Capture conditions differ: {key}")
    for key in ("plugin", "model", "sharedShader"):
        if report["hashes"][key] != reference["hashes"][key]:
            raise ValueError(f"Capture inputs differ: {key}")
    width, height, pixels = read_png_rgb(report["image"])
    ref_width, ref_height, ref_pixels = read_png_rgb(reference["image"])
    if (width, height) != (ref_width, ref_height):
        raise ValueError("Capture dimensions differ")
    left, top, right, bottom = roi
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("ROI must be a nonempty rectangle inside the image")
    changed = sum(pixels[y * width + x] != ref_pixels[y * width + x]
                  for y in range(top, bottom) for x in range(left, right))
    report["comparison"] = {"reference": reference["image"], "roi": roi,
                            "changedPixels": changed,
                            "wholeImageChangedPixels": sum(a != b for a, b in zip(pixels, ref_pixels))}
    report["imageAcceptance"] = "fail" if changed else "pass"
    if changed:
        raise ValueError(f"Face outline ROI changed: {changed} pixels")


def run_probe(config_path):
    """Capture fixed face framing; image acceptance is a separate comparison."""
    from maya import cmds
    from mmd_tools.io.mmd_importer import import_mmd_file

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["output"])
    report = {"status": "fail", "imageAcceptance": "not_run"}
    try:
        cmds.file(new=True, force=True)
        plugin = require_requested_plugin(cmds, config["plugin"], print)
        report["mayaVersion"] = cmds.about(version=True)
        report["hashes"] = {
            key: hashlib.sha256(Path(value).read_bytes()).hexdigest()
            for key, value in {"plugin": str(plugin), "model": config["model"],
                               "shader": config["shader"],
                               "sharedShader": str(Path(config["shader"]).with_name("MMDShader.fx"))}.items()
        }
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        root = import_mmd_file(config["model"], options={
            "use_cpp_fast_load": True, "use_cpp_vp2_ownership": True,
            "import_morphs": False, "import_physics": False,
            "separate_meshes_by_material": True,
        })
        assert root, "PMX import failed"
        faces = [n for n in cmds.ls(type="standardSurface")
                 if cmds.attributeQuery("mmd_material_name", node=n, exists=True)
                 and cmds.getAttr(n + ".mmd_material_name") in config["face_names"]]
        assert len(faces) == 1, f"Expected one face material: {faces}"
        face = faces[0]
        cmds.setAttr(face + ".mmd_edge_size", config["width"])
        report["face"] = {"name": face, "flags": cmds.getAttr(face + ".mmd_draw_flags"),
                          "width": config["width"]}
        shading_group = cmds.listConnections(face, type="shadingEngine")[0]
        cmds.select(cmds.sets(shading_group, query=True))
        cmds.setAttr("persp.rotate", 0, 0, 0, type="double3")
        cmds.viewFit("persp", allObjects=False)
        cmds.select(clear=True)
        panel = "modelPanel4"
        cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer",
                         rendererOverrideName="mmdOrdered", displayAppearance="smoothShaded",
                         displayTextures=True, joints=False, locators=False, grid=False)
        cmds.setFocus(panel)
        cmds.refresh(force=True)
        report["cameraMatrix"] = cmds.xform("persp", query=True, worldSpace=True, matrix=True)
        report["image"] = str(capture_view(cmds, out / "capture.png", panel, 640, 640))
        witness = json.loads(cmds.mmdOrderedRenderWitness())
        assert witness["state"] == "active" and not witness["error"] and witness["drawCount"] > 0, witness
        report["witness"] = witness
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "probe.log").write_text(MARKER, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True,
                        help="UTF-8 JSON with model and face_names; optional width (default 0.5)")
    parser.add_argument("--maya", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--shader", type=Path, default=ROOT / "mmd_tools/shaders/MMDNativeShader.fx")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--reference-report", type=Path)
    parser.add_argument("--roi", type=int, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    args = parser.parse_args()
    if bool(args.reference_report) != bool(args.roi):
        parser.error("--reference-report and --roi must be supplied together")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    out = args.out_dir.resolve()
    if args.reference_report and args.reference_report.resolve() == out / "report.json":
        parser.error("Reference report must be outside the candidate output report")
    out.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    config.update(output=str(out), plugin=str(plugin), shader=str(args.shader.resolve()))
    config.setdefault("width", 0.5)
    path = out / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=300,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=("from maya import cmds\nfrom tools.render_override.outline_face_capture import run_probe\n"
                 f"cmds.evalDeferred(lambda: run_probe({str(path)!r}), lowestPriority=True)"),
        marker=MARKER, send_label="outline-face-capture",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                       "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "MMD_TOOLS_NATIVE_SHADER_PATH": config["shader"],
                       "MMD_TOOLS_NATIVE_TOON_DIR": str(ROOT / "mmd_tools/shaders/toon_textures"),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    if args.reference_report:
        try:
            reference = json.loads(args.reference_report.read_text(encoding="utf-8"))
            compare_capture(report, reference, args.roi)
        except (ValueError, KeyError, OSError):
            report["status"] = "fail"
            report["imageAcceptance"] = "fail"
            report["comparisonError"] = traceback.format_exc()
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
