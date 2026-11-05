"""Check Animator pose layers and reopen each result in a fresh Maya GUI host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MARKER = "POSE_LAYERS_E2E_COMPLETE"
DEFAULT_CASES = [kind + "_" + mode for kind in ("direct", "ik_input") for mode in ("base", "additive", "override")]


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def _known_render_warning(message):
    """Keep these unrelated renderer limitations visible in the Animator report."""
    return message.strip() in {
        "不明なオブジェクト タイプ: dx11Shader",
        "Unknown object type: dx11Shader",
        "MMD Render requires an active DirectX 11 renderer; override skipped.",
    }


def probe(config_path, phase):
    """Exercise only safe authored-input layer membership, not joint IK rewiring."""
    from maya import cmds
    from maya.api import OpenMaya as om
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin
    from tests.integration.test_pose_layers import create_pose_case, pose_snapshot, pose_delta, curve_snapshot

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    output = Path(config["out"])
    phase_output = output / phase
    report = {"status": "fail", "phase": phase, "maya": cmds.about(version=True),
              "cases": [], "messages": [], "errors": [],
              "scope": "Animator input and pose behavior; not a rendering gate",
              "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                                for path in ("mmd_tools/actions/pose_actions.py",
                                             "mmd_tools/core/pose_authoring.py",
                                             "mmd_tools/core/native_layer_routes.py",
                                             "mmd_tools/core/mmd_control_rig_analyzer.py",
                                             "mmd_tools/converters/rig_converter.py",
                                             "mmd_tools/ui/presenters/animation_presenter.py")}}

    def capture(message, kind, unused):
        if kind in (om.MCommandMessage.kError, om.MCommandMessage.kWarning):
            report["messages"].append({"kind": int(kind), "text": message})

    callback = om.MCommandMessage.addCommandOutputCallback(capture)
    try:
        load_mmd_tools_plugin(ROOT, cmds_module=cmds)
        cmds.undoInfo(state=True)
        cmds.evaluationManager(mode="parallel")
        for name in config["cases"]:
            kind, mode = name.rsplit("_", 1)
            row = {"name": name, "status": "fail", "checks": {},
                   "copy": {"frame": 1, "layer": mode, "keys": "explicit source pose"},
                   "paste": {"frame": 10, "layer": mode, "keys": "existing neutral pose"},
                   "layer_membership": ("joint TR" if kind == "direct" else
                                        "Control Rig policy channels" if kind.startswith("control_") else
                                        "IK solver pre-input rotation")}
            report["cases"].append(row)
            window = None

            def check(label, condition, detail=None):
                row["checks"][label] = {"pass": bool(condition), "detail": detail}
                if not condition:
                    raise AssertionError(label + ": " + repr(detail))

            def matches(label, expected, case):
                actual = pose_snapshot(case)
                delta = pose_delta(expected, actual)
                check(label, delta < 1e-5, {"max_delta": delta, "actual": actual})

            try:
                scene = output / (name + ".ma")
                identity = output / (name + ".json")
                if phase == "reload" and not (scene.is_file() and identity.is_file()):
                    row.update(status="not_run", reason="Save phase produced no scene and identity")
                    continue
                if phase == "save":
                    from mmd_tools.ui.animator_toolset_window import AnimatorToolsetWindow

                    case = create_pose_case(kind, mode)
                    source = pose_snapshot(case)
                    window = AnimatorToolsetWindow()
                    if case["root"]:
                        window.app_state.current_model_root = case["root"]
                    cmds.select(case["target"], replace=True)
                    if kind == "control_rotation":
                        check("locked_translation_policy", all(
                            cmds.getAttr(case["target"] + ".translate" + axis, lock=True) for axis in "XYZ"))
                    window.animation_tab.tool_buttons["copy"].click()
                    check("presenter_copy", bool(window.animation_presenter._pose_clipboard),
                          window.animation_tab.status_label.text())
                    cmds.currentTime(10)
                    before = pose_snapshot(case)
                    window.animation_tab.tool_buttons["paste"].click()
                    row["presenter_paste_status"] = window.animation_tab.status_label.text()
                    if case["layer"]:
                        def canonical(plug):
                            selected = om.MSelectionList()
                            selected.add(plug)
                            return selected.getPlug(0).name()

                        members = cmds.animLayer(case["layer"], query=True, attribute=True) or []
                        expected = {canonical(plug)
                                    for pose in window.animation_presenter._pose_clipboard.values()
                                    for plug in pose.authoring.route.plugs}
                        row["paste_layer_membership"] = members
                        check("paste_inputs_belong_to_selected_layer", expected <= {canonical(plug) for plug in members},
                              {"expected": sorted(expected), "actual": members})
                        check("destination_layer_still_selected", cmds.animLayer(case["layer"], query=True, selected=True)
                              and cmds.animLayer(case["layer"], query=True, preferred=True))
                    matches("paste_inputs_and_evaluated_pose", source, case)
                    cmds.undo()
                    matches("undo", before, case)
                    cmds.redo()
                    matches("redo", source, case)
                    cmds.currentTime(11)
                    cmds.currentTime(10)
                    matches("seek", source, case)
                    if case["layer"]:
                        cmds.setAttr(case["layer"] + ".mute", True)
                        matches("mute", before, case)
                        cmds.setAttr(case["layer"] + ".mute", False)
                        cmds.setAttr(case["layer"] + ".weight", 0.0)
                        matches("weight_zero", before, case)
                        cmds.setAttr(case["layer"] + ".weight", 1.0)
                        matches("weight_restored", source, case)
                    kwargs = {"time": 10, "value": source["inputs"][0] + 0.25}
                    if case["layer"]:
                        kwargs["animLayer"] = case["layer"]
                    before_key_edit = curve_snapshot()
                    cmds.undoInfo(openChunk=True, chunkName="Pose layer test key edit")
                    try:
                        cmds.setKeyframe(case["plugs"][0], **kwargs)
                        cmds.currentTime(11)
                        cmds.currentTime(10)
                    finally:
                        cmds.undoInfo(closeChunk=True)
                    check("key_edit", abs(cmds.getAttr(case["plugs"][0]) - source["inputs"][0] - 0.25) < 1e-5)
                    cmds.undo()
                    matches("key_edit_undo", source, case)
                    check("key_edit_undo_restores_curves_and_connections", curve_snapshot() == before_key_edit)
                    cmds.currentTime(1)
                    matches("source_frame_unchanged", source, case)
                    cmds.currentTime(10)
                    saved = {"case": case, "source": source, "before": before, "curves": curve_snapshot()}
                    cmds.file(rename=str(scene))
                    cmds.file(save=True, type="mayaAscii", force=True)
                    _write(identity, saved)
                    matches("save_preserves_pose", source, case)
                else:
                    saved = json.loads(identity.read_text(encoding="utf-8"))
                    case, source, before = saved["case"], saved["source"], saved["before"]
                    cmds.file(str(scene), open=True, force=True)
                    cmds.currentTime(10)
                    matches("fresh_process_reload", source, case)
                    check("keys_and_connections", curve_snapshot() == saved["curves"])
                    cmds.currentTime(1)
                    matches("source_frame_unchanged", source, case)
                    cmds.currentTime(10)
                    if case["layer"]:
                        cmds.setAttr(case["layer"] + ".mute", True)
                        matches("reload_mute", before, case)
                        cmds.setAttr(case["layer"] + ".mute", False)
                        cmds.setAttr(case["layer"] + ".weight", 0.0)
                        matches("reload_weight_zero", before, case)
                        cmds.setAttr(case["layer"] + ".weight", 1.0)
                        matches("reload_weight_restored", source, case)
                row["status"] = "pass"
            except Exception:
                row["error"] = traceback.format_exc()
            finally:
                if window is not None:
                    window._cleanup()
                    window.close()
                    window.deleteLater()
            _write(phase_output / "report.json", report)
        report["known_render_warnings"] = [item for item in report["messages"]
                                           if item["kind"] == int(om.MCommandMessage.kWarning) and _known_render_warning(item["text"])]
        report["unexpected_messages"] = [item for item in report["messages"]
                                         if item not in report["known_render_warnings"]]
        report["status"] = "pass" if not report["unexpected_messages"] and all(row["status"] == "pass" for row in report["cases"]) else "fail"
    except Exception:
        report["errors"].append(traceback.format_exc())
    finally:
        om.MMessage.removeCallback(callback)
        _write(phase_output / "report.json", report)
        (phase_output / "run.log").write_text(MARKER + "\n", encoding="utf-8")


def main():
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--port", type=int, default=7741)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--case", action="append", choices=DEFAULT_CASES + ["control_rotation_base", "control_center_base"])
    args = parser.parse_args()
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/Release/mmd_tools_cpp.mll"
    config_path = output / "config.json"
    cases = args.case or DEFAULT_CASES
    _write(config_path, {"out": str(output), "cases": cases})
    report = {"status": "fail", "phases": {},
              "plugin_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
              "model_sha256": hashlib.sha256((ROOT / "tests/data/mmt_test_model.pmx").read_bytes()).hexdigest()}
    # Do not consume scenes from an earlier attempt after a failed save phase.
    for name in cases:
        for suffix in ("ma", "json"):
            (output / f"{name}.{suffix}").unlink(missing_ok=True)
    for phase in ("save", "reload"):
        phase_output = output / phase
        phase_output.mkdir(parents=True, exist_ok=True)
        command = ("from maya import cmds\nfrom tools.maya_gui.pose_layers_e2e import probe\n"
                   f"cmds.evalDeferred(lambda: probe({str(config_path)!r}, {phase!r}), lowestPriority=True)")
        try:
            report["phases"][phase] = run_maya_e2e(
                project_root=ROOT, version=args.maya, out_dir=phase_output, port=args.port,
                timeout=args.timeout, log_path=phase_output / "run.log", report_path=phase_output / "report.json",
                command=command, marker=MARKER, send_label="pose-layers-" + phase,
                stale_paths=(phase_output / "run.log", phase_output / "report.json"),
                env_overrides={"MAYA_SKIP_USERSETUP_PY": "1", "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                               "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
            )
        except Exception:
            report["phases"][phase] = {"status": "fail", "error": traceback.format_exc()}
        _write(output / "report.json", report)
    report["status"] = "pass" if all(row["status"] == "pass" for row in report["phases"].values()) else "fail"
    _write(output / "report.json", report)
    print(json.dumps({"status": report["status"], "phases": {k: v["status"] for k, v in report["phases"].items()}}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
