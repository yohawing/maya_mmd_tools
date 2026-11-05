"""Verify standard joint layers over MMD IK and reopen in a separate Maya host."""

from __future__ import annotations

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
MARKER = "IK_LAYERS_E2E_COMPLETE"


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def probe(config_path, phase):
    from maya import cmds
    from maya.api import OpenMaya as om
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin
    from tests.integration.test_ik_animation_layers import (
        create_scene, import_motion, matrices, matrix_delta, samples, samples_delta,
        add_joint_layer, ownership,
    )
    from tests.integration.test_pose_layers import select_layer, curve_snapshot
    from tools.maya_gui.pose_layers_e2e import _known_render_warning
    from tools.render_override.common import require_requested_plugin

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    output = Path(config["out"])
    phase_output = output / phase
    sources = ["mmd_tools/" + path for path in (
        "converters/rig_converter.py", "core/native_layer_routes.py", "core/mmd_control_rig_analyzer.py",
        "core/pose_authoring.py", "actions/pose_actions.py", "converters/vmd_ik_passthrough.py",
        "converters/vmd_legacy_bone_routes.py", "converters/bone_morph_runtime.py", "converters/vmd_append_decomposition.py",
        "converters/vmd_import_state.py", "converters/vmd_converter.py",
    )]
    report = {"status": "fail", "phase": phase, "maya": cmds.about(version=True), "cases": [],
              "messages": [], "scope": "IK and animation-layer behavior; not a rendering gate",
              "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in sources}}

    def capture(message, kind, unused):
        if kind in (om.MCommandMessage.kWarning, om.MCommandMessage.kError):
            report["messages"].append({"kind": int(kind), "text": message})

    callback = om.MCommandMessage.addCommandOutputCallback(capture)
    try:
        require_requested_plugin(cmds, config["plugin"], print)
        load_mmd_tools_plugin(ROOT, cmds_module=cmds)
        cmds.undoInfo(state=True)
        cmds.evaluationManager(mode="parallel")
        for mode in ("additive", "override"):
            row = {"mode": mode, "status": "fail", "checks": {}, "creation_frame": 30,
                   "sample_frames": list(range(61)),
                   "active_layer_contract": "All sampled frames unchanged" if mode == "additive" else "Creation-frame pose unchanged; other frames may be overridden"}
            report["cases"].append(row)

            def check(name, condition, detail=None):
                row["checks"][name] = {"pass": bool(condition), "detail": detail}
                if not condition:
                    raise AssertionError(name + ": " + repr(detail))

            def compare(name, expected, root):
                actual = samples(root, frames=tuple(int(frame) for frame in expected))
                difference = samples_delta(expected, actual)
                if difference >= 1e-5:
                    _write(phase_output / (mode + "-" + name + ".json"), actual)
                check(name, difference < 1e-5, {"max_matrix_delta": difference})

            try:
                scene = output / (mode + ".ma")
                identity = output / (mode + ".json")
                if phase == "save":
                    root = create_scene()
                    original = samples(root)
                    _write(output / (mode + "-original.json"), original)
                    owned = ownership(root)
                    check("nonempty_ik_collectors", bool(owned["passthrough"]) and bool(owned["links"]))
                    layer = add_joint_layer(root, override=mode == "override")
                    layered = samples(root)
                    check("creation_frame_pose", matrix_delta(original["30"], layered["30"]) < 1e-5)
                    if mode == "additive":
                        check("all_frames_unchanged", samples_delta(original, layered) < 1e-5)
                    check("native_ownership", ownership(root) == owned,
                          {"ik_passthrough_count": len(ownership(root)["passthrough"]), "ik_link_count": len(ownership(root)["links"])})
                    cmds.undo()
                    check("undo_layer", not cmds.objExists(layer))
                    compare("undo_pose", original, root)
                    cmds.redo()
                    compare("redo_pose", layered, root)
                    cmds.setAttr(layer + ".mute", True)
                    compare("mute_restores_vmd", original, root)
                    cmds.setAttr(layer + ".mute", False)
                    cmds.setAttr(layer + ".weight", 0.0)
                    compare("weight_zero_restores_vmd", original, root)
                    cmds.setAttr(layer + ".weight", 1.0)

                    # Standard keys on an IK controller must still drive the leg.
                    cmds.currentTime(30)
                    control = next(joint for joint in original["30"] if joint.endswith("|right_leg_ik"))
                    leg = next(joint for joint in original["30"] if joint.endswith("|right_leg"))
                    # Override owns the solved leg output while active; edit
                    # that output directly, and verify original IK when muted.
                    target = control + ".translateZ" if mode == "additive" else leg + ".rotateX"
                    row["key_edit_plug"] = target
                    previous = cmds.getAttr(target)
                    before_key_edit = curve_snapshot()
                    cmds.undoInfo(openChunk=True, chunkName="Edit layered IK target")
                    try:
                        cmds.setKeyframe(target, time=30, value=previous + 0.5, animLayer=layer)
                        cmds.currentTime(30, edit=True, update=True)
                    finally:
                        cmds.undoInfo(closeChunk=True)
                    edited = matrices(root)
                    check("key_edit_target", abs(cmds.getAttr(target) - previous - 0.5) < 1e-5)
                    check("leg_ik_follows_target" if mode == "additive" else "override_leg_rotation_edit",
                          matrix_delta({leg: layered["30"][leg]}, {leg: edited[leg]}) > 1e-4)
                    cmds.undo()
                    check("key_undo", matrix_delta(layered["30"], matrices(root)) < 1e-5)
                    check("key_undo_curves_connections", curve_snapshot() == before_key_edit)
                    cmds.redo()
                    check("key_redo", matrix_delta(edited, matrices(root)) < 1e-5)
                    cmds.undo()
                    check("key_final_undo_curves_connections", curve_snapshot() == before_key_edit)

                    # Keep one real manual key through saving and VMD replacement.
                    manual_joint = next(joint for joint in original["30"] if joint.endswith("|upper_body"))
                    manual_plug = manual_joint + ".rotateZ"
                    cmds.setKeyframe(manual_plug, time=30, value=cmds.getAttr(manual_plug) + 5.0, animLayer=layer)
                    cmds.currentTime(30, edit=True, update=True)
                    layered = samples(root)
                    manual_curves = cmds.animLayer(layer, query=True, animCurves=True) or []
                    all_curves = curve_snapshot()
                    manual_keys = {curve: all_curves[curve] for curve in manual_curves}
                    check("manual_layer_has_keys", bool(manual_keys))
                    row["manual_key_plug"] = manual_plug

                    if mode == "additive":
                        from mmd_tools.actions.pose_actions import CopyPoseAction, CopyPoseRequest, PastePoseAction, PastePoseRequest
                        from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter

                        adapter = MayaCmdsAdapter(cmds)
                        cmds.currentTime(1)
                        copied = CopyPoseAction(adapter).execute(CopyPoseRequest([leg], model_root=root))
                        check("layered_ik_copy", copied.succeeded, str(copied.error))
                        cmds.currentTime(30)
                        before_paste = matrices(root)
                        paste_layer = cmds.animLayer("PoseInputLayer")
                        select_layer(paste_layer)
                        pasted = PastePoseAction(adapter).execute(PastePoseRequest(copied.pose, model_root=root))
                        check("layered_ik_paste", pasted.succeeded, str(pasted.error))
                        authored = copied.pose[leg].authoring
                        check("layered_ik_paste_inputs", max(abs(cmds.getAttr(plug) - value)
                              for plug, value in zip(authored.route.plugs, authored.values)) < 1e-5)
                        cmds.undo()
                        check("layered_ik_paste_undo", matrix_delta(before_paste, matrices(root)) < 1e-5)
                        cmds.delete(paste_layer)
                    select_layer(layer)
                    cmds.currentTime(30)
                    cmds.file(rename=str(scene))
                    cmds.file(save=True, type="mayaAscii", force=True)
                    compare("save_preserves_pose", layered, root)
                    _write(identity, {"root": root, "layer": layer, "original": original, "layered": layered,
                                      "ownership": owned, "manual_keys": manual_keys})

                    # Replacement import is explicit: clear=False adds another
                    # VMD layer and is not equivalent to the original motion.
                    cmds.setAttr(layer + ".mute", True)
                    select_layer(cmds.animLayer(query=True, root=True))
                    base_layer = cmds.animLayer(query=True, root=True)
                    layer_order = cmds.animLayer(base_layer, query=True, children=True) or []
                    row["layer_order_before_reimport"] = layer_order
                    compare("before_reimport_original_vmd", original, root)
                    row["reimport_options"] = {"clear_existing_motion": True, "manual_layer_mute": True, "manual_layer_weight": 1.0}
                    import_motion(root, clear_existing_motion=True)
                    check("reimport_preserves_layer", cmds.objExists(layer))
                    current_curves = curve_snapshot()
                    check("reimport_preserves_manual_keys", {curve: current_curves.get(curve) for curve in manual_keys} == manual_keys)
                    compare("reimport_vmd", original, root)
                    check("reimport_native_ownership", ownership(root) == owned)
                    cmds.setAttr(layer + ".mute", False)
                    row["layer_order_after_reimport"] = cmds.animLayer(base_layer, query=True, children=True) or []
                    compare("reimport_layer_pose", layered, root)
                    check("reimport_layer_order", row["layer_order_after_reimport"] == layer_order)
                else:
                    if not (scene.is_file() and identity.is_file()):
                        row.update(status="not_run", reason="Save phase produced no scene and expectation")
                        continue
                    saved = json.loads(identity.read_text(encoding="utf-8"))
                    root, layer = saved["root"], saved["layer"]
                    cmds.file(str(scene), open=True, force=True)
                    compare("fresh_process_reload", saved["layered"], root)
                    check("reload_native_ownership", ownership(root) == saved["ownership"])
                    current_curves = curve_snapshot()
                    check("reload_manual_keys", {curve: current_curves.get(curve) for curve in saved["manual_keys"]} == saved["manual_keys"])
                    cmds.setAttr(layer + ".mute", True)
                    compare("reload_mute", saved["original"], root)
                    cmds.setAttr(layer + ".mute", False)
                    cmds.setAttr(layer + ".weight", 0.0)
                    compare("reload_weight_zero", saved["original"], root)
                row["status"] = "pass"
            except Exception:
                row["error"] = traceback.format_exc()
            _write(phase_output / "report.json", report)
        report["known_render_warnings"] = [item for item in report["messages"] if item["kind"] == int(om.MCommandMessage.kWarning)
                                           and _known_render_warning(item["text"])]
        report["unexpected_messages"] = [item for item in report["messages"] if item not in report["known_render_warnings"]]
        report["status"] = "pass" if not report["unexpected_messages"] and all(row["status"] == "pass" for row in report["cases"]) else "fail"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        om.MMessage.removeCallback(callback)
        _write(phase_output / "report.json", report)
        (phase_output / "run.log").write_text(MARKER + "\n", encoding="utf-8")


def main():
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--config", choices=("Debug", "Release"), default="Release")
    parser.add_argument("--port", type=int, default=7741)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / f"plug-ins/{args.maya}/{args.config}/mmd_tools_cpp.mll"
    config = output / "config.json"
    _write(config, {"out": str(output), "plugin": str(plugin)})
    report = {"status": "fail", "phases": {}, "plugin_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest()}
    for mode in ("additive", "override"):
        for suffix in ("ma", "json"):
            (output / (mode + "." + suffix)).unlink(missing_ok=True)
    for phase in ("save", "reload"):
        phase_output = output / phase
        phase_output.mkdir(parents=True, exist_ok=True)
        command = ("from maya import cmds\nfrom tools.maya_gui.ik_layers_e2e import probe\n"
                   f"cmds.evalDeferred(lambda: probe({str(config)!r}, {phase!r}), lowestPriority=True)")
        try:
            report["phases"][phase] = run_maya_e2e(
                project_root=ROOT, version=args.maya, out_dir=phase_output, port=args.port, timeout=args.timeout,
                log_path=phase_output / "run.log", report_path=phase_output / "report.json", command=command,
                marker=MARKER, send_label="ik-layers-" + phase, stale_paths=(phase_output / "run.log", phase_output / "report.json"),
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
