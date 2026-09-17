"""Capture generated PMX fixtures through the product MMD Render path."""

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
from tests.viewport.visual_regression_capture import _load_cases  # noqa: E402
from tests.viewport.visual_regression_compare import _image_metrics  # noqa: E402
from tools.render_override.common import capture_view, make_parity_camera, require_requested_plugin  # noqa: E402
from tools.render_override.render_override_visual_gate import read_png_rgb  # noqa: E402

MARKER = "MMD RELEASE CAPTURE FINISHED"


def load_cases(manifest, names):
    """Require every selected fixture and oracle before starting a Maya process."""
    _, cases = _load_cases(manifest, names, [], 0)
    actual = [case["name"] for case in cases]
    if not names or len(actual) != len(set(actual)) or set(actual) != set(names):
        raise ValueError("Manifest must contain every requested case exactly once")
    for case in cases:
        for key in ("model", "oracle_png"):
            if not case.get(key) or not Path(case[key]).is_file():
                raise FileNotFoundError(f"{case['name']}: missing {key}: {case.get(key)}")
        if case.get("camera_motion"):
            raise ValueError("Release fixture capture requires a fixed manifest camera")
    return cases


def visible_pixels(actual, hidden):
    """Reject blank/background captures even when two Maya versions agree."""
    aw, ah, pixels = read_png_rgb(actual)
    hw, hh, background = read_png_rgb(hidden)
    if (aw, ah) != (hw, hh):
        raise ValueError("Hidden control dimensions differ from the capture")
    count = sum(max(abs(a - b) for a, b in zip(rgb, bg)) > 8
                for rgb, bg in zip(pixels, background))
    if count < max(100, aw * ah // 1000):
        raise ValueError(f"Model is invisible against hidden control: {count} pixels")
    return count


def run_probe(config_path):
    """Return to Maya's event loop between scene changes and captures."""
    from maya import cmds
    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtCore import QTimer
    steps = _probe_steps(config_path)

    def advance():
        try:
            next(steps)
        except StopIteration:
            return
        QTimer.singleShot(100, advance)

    cmds.evalDeferred(advance, lowestPriority=True)


def _probe_steps(config_path):
    from maya import cmds
    from maya.api import OpenMayaRender as omr
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.converters.light_converter import set_mmd_light_direction
    from mmd_tools.converters.material_morph_runtime import _collect_shaders_by_material_index

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["output"])
    report = {"status": "fail", "results": [], "errors": [], "renderer": "mmdOrdered",
              "mayaVersion": cmds.about(version=True), "oracleMode": "report-only"}
    try:
        plugin = require_requested_plugin(cmds, config["plugin"], print)
        report["pluginSha256"] = hashlib.sha256(plugin.read_bytes()).hexdigest()
        assert omr.MRenderer.drawAPI() == omr.MRenderer.kDirectX11, "DirectX 11 required"
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        window = cmds.window(widthHeight=(1050, 1050))
        panel = cmds.modelPanel(parent=cmds.paneLayout())
        cmds.showWindow(window)
        for case in config["cases"]:
            cmds.file(new=True, force=True)
            root = import_mmd_file(case["model"], options={
                "use_cpp_fast_load": True, "use_cpp_vp2_ownership": True,
                "import_morphs": False, "import_physics": False,
                "separate_meshes_by_material": False,
            })
            assert root, case["name"]
            shaders = _collect_shaders_by_material_index(root)
            assert shaders and all(cmds.nodeType(s) == "standardSurface" for s in shaders.values())
            light = case["light"]
            direction = light.get("direction", [0.5, -1.0, 0.5])
            controller = set_mmd_light_direction(
                [-direction[0], direction[1], -direction[2]], light.get("color", [1, 1, 1]))
            cmds.setAttr(controller + ".mmd_self_shadow_mode", 0)
            camera = make_parity_camera(cmds, case["camera"])
            cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer",
                             rendererOverrideName="mmdOrdered", camera=camera,
                             displayAppearance="smoothShaded", displayTextures=True,
                             grid=False, hud=False, manipulators=False, cameras=False,
                             lights=False, locators=False, joints=False, ikHandles=False,
                             deformers=False, dynamics=False, nurbsCurves=False,
                             selectionHiliteDisplay=False, wireframeOnShaded=False)
            cmds.colorManagementPrefs(edit=True, cmEnabled=False)
            for name in ("background", "backgroundTop", "backgroundBottom"):
                cmds.displayRGBColor(name, 1, 1, 1)
            cmds.currentTime(case["frame"])
            cmds.select(clear=True)
            yield
            case_dir = out / case["name"]
            width, height = case["image"]["width"], case["image"]["height"]

            def capture(label):
                cmds.refresh(force=True)
                return capture_view(cmds, case_dir / f"{label}.png", panel,
                                    width, height, case["frame"])

            actual = capture("actual")
            witness = json.loads(cmds.mmdOrderedRenderWitness(shadowDepth=True))
            assert witness["state"] == "active" and not witness["error"] and witness["drawCount"] > 0, witness
            cmds.hide(root)
            yield
            hidden = capture("hidden")
            foreground = visible_pixels(actual, hidden)
            cmds.showHidden(root)
            yield
            restored = capture("restored")
            assert read_png_rgb(actual) == read_png_rgb(restored), "Hide/restore changed capture"
            # A/B/A on each fixture's named feature catches a shared regression
            # that would otherwise agree on both supported Maya versions.
            name = case["name"]
            controls = []
            for shader in shaders.values():
                if "sphere" in name:
                    controls.append((shader + ".mmd_sphere_mode", 0))
                elif "toon" in name:
                    controls.append((shader + ".mmd_resolved_toon_texture_path", ""))
                elif "texture-uv" in name:
                    controls.append((shader + ".mmd_resolved_texture_path", ""))
                elif "alpha" in name:
                    controls.append((shader + ".mmd_diffuse_alpha", 1.0))
                elif "outline" in name:
                    plug = shader + ".mmd_draw_flags"
                    controls.append((plug, cmds.getAttr(plug) & ~16))
            if "diffuse" in name:
                controls.append((controller + ".mmd_light_colorR", 0.0))
            assert controls, f"No feature control for {name}"
            original = [(plug, cmds.getAttr(plug)) for plug, _value in controls]

            def set_controls(values):
                for plug, value in values:
                    if isinstance(value, str):
                        cmds.setAttr(plug, value, type="string")
                    else:
                        cmds.setAttr(plug, value)

            set_controls(controls)
            yield
            disabled = capture("feature_disabled")
            feature_pixels = visible_pixels(actual, disabled)
            set_controls(original)
            yield
            assert read_png_rgb(actual) == read_png_rgb(capture("feature_restored")), "Feature restore changed capture"
            metrics = _image_metrics(Path(case["oracle_png"]), actual)
            assert "size_mismatch" not in metrics, metrics
            report["results"].append({"name": case["name"], "ok": True,
                "actual_png": str(actual), "oracle_png": case["oracle_png"],
                "hidden_png": str(hidden), "visiblePixels": foreground,
                "featureChangedPixels": feature_pixels,
                "modelSha256": hashlib.sha256(Path(case["model"]).read_bytes()).hexdigest(),
                "witness": witness, "oracleMetrics": metrics})
        report["status"] = "pass"
    except Exception:
        report["errors"].append({"error": traceback.format_exc()})
    (out / "visual-regression-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "probe.log").write_text(MARKER, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--case", action="append", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "visual-regression-report.json"
    report_path.unlink(missing_ok=True)
    cases = load_cases(args.manifest.resolve(), args.case)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    config = out / "config.json"
    config.write_text(json.dumps({"output": str(out), "plugin": str(plugin), "cases": cases}), encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=300,
        log_path=out / "probe.log", report_path=report_path,
        command=("from tools.render_override.release_capture import run_probe\n"
                 f"run_probe({str(config)!r})"),
        marker=MARKER, send_label="release-render-capture",
        stale_paths=(out / "probe.log", report_path),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
                       "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")})
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
