"""Run secondary bone control checks in a fresh background Maya GUI."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MARKER = "//-- SECONDARY_CONTROLS_E2E_DONE --//"


def _supplemental_shapes(model, controls, cmds, check):
    """Check generated CV dimensions using an explicitly modified fixture collider."""
    import math
    from maya.api import OpenMaya as om
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.core.mmd_control_rig_builder import build_mmd_control_rig

    cmds.file(new=True, force=True)
    root = import_mmd_file(model, options={"import_physics": True, "create_mmd_shaders": False, "setup_rig": True})
    build_mmd_control_rig(root)
    initial = controls.build(root)
    collider = initial["controls"][0]["collider"]
    controls.remove(root)
    original = cmds.getAttr(collider + ".shapeType")
    for kind in (0, 1):
        cmds.setAttr(collider + ".shapeType", kind)
        state = controls.build(root)
        row = next(item for item in state["controls"] if item["collider"] == collider)
        cvs = cmds.ls(row["control"] + ".cv[*]", flatten=True)
        points = [cmds.pointPosition(cv, world=True) for cv in cvs]
        size = [cmds.getAttr(collider + ".shapeSize" + axis) for axis in "XYZ"]
        if kind == 0:
            center = [sum(point[axis] for point in points[:-1]) / (len(points)-1) for axis in range(3)]
            radii = [math.sqrt(sum((point[axis]-center[axis])**2 for axis in range(3))) for point in points]
            check("supplemental_sphere_circle", len(points) == 33 and max(abs(radius-size[0]) for radius in radii) < 1e-4, {"radius": size[0], "cv_count": len(points)})
        else:
            transform = cmds.listRelatives(collider, parent=True, fullPath=True)[0]
            inverse = om.MMatrix(cmds.xform(transform, query=True, worldSpace=True, matrix=True)).inverse()
            local = [om.MPoint(*point) * inverse for point in points]
            error = max(abs(abs(point[axis])-size[axis]) for point in local for axis in range(3))
            check("supplemental_box_dimensions", len(points) == 16 and error < 1e-4, {"max_component_delta": error, "cv_count": len(points)})
        controls.remove(root)
    cmds.setAttr(collider + ".shapeType", original)


def _checks(config_path):
    """Import a real PMX and exercise controller ownership and live physics."""
    import maya.cmds as cmds
    from mmd_tools.core import secondary_bone_controls as controls
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.core.mmd_parser import parse_pmx_file

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    output = Path(config["out"])
    cmds.scriptEditorInfo(historyFilename=str(output / "maya-history.log"), writeHistory=True)
    handler = logging.FileHandler(str(output / "plugin.log"), encoding="utf-8")
    logging.getLogger("mmd_tools").addHandler(handler)
    report = {"status": "fail", "model": config["model"], "checks": {}, "errors": []}

    def check(name, condition, detail=None, fatal=True):
        report["checks"][name] = {"pass": bool(condition), "detail": detail}
        if not condition and fatal:
            raise AssertionError(name + ": " + repr(detail))

    def matrix(node):
        return cmds.xform(node, query=True, worldSpace=True, matrix=True)

    try:
        report["maya"] = cmds.about(version=True)
        pmx = parse_pmx_file(config["model"])
        report["dynamic_shape_types"] = sorted({int(body.shape_type) for body in pmx.rigid_bodies if int(body.physics_mode) in (1, 2)})
        cmds.file(new=True, force=True)
        cmds.loadPlugin(str(ROOT / "mmd_tools/plugin_main.py"), quiet=True)
        root = import_mmd_file(config["model"], options={
            "import_physics": True, "create_mmd_shaders": False,
            "setup_rig": True, "create_mmd_control_rig": False,
        })
        check("import", bool(root), root)
        yield
        before_nodes = set(cmds.ls(long=True))
        try:
            controls.build(root)
        except ValueError as exc:
            check("requires_base_rig", "Control Rig" in str(exc), str(exc))
        else:
            check("requires_base_rig", False)
        check("rejected_build_preserves_scene", set(cmds.ls(long=True)) == before_nodes)
        from mmd_tools.core.mmd_control_rig_builder import build_mmd_control_rig
        base = build_mmd_control_rig(root)
        check("base_rig_created", bool(base.controls))
        from mmd_tools.core.physics_preroll import run_physics_preroll
        solvers = cmds.ls(type="mmdPhysicsSolver") or []
        initial_world = cmds.ls(type="mmdPhysicsWorldShape", long=True)[0]
        try:
            run_physics_preroll(initial_world, solvers, maya_cmds=cmds)
            report["before_build_preroll"] = "pass"
        except Exception:
            report["before_build_preroll"] = traceback.format_exc()
        state = controls.build(root)
        cmds.undo()
        check("undo_build", not controls.inspect(root)["exists"], {"redo_name": cmds.undoInfo(query=True, redoName=True)})
        cmds.redo()
        state = controls.inspect(root)
        check("redo_build", state["exists"] and bool(state["controls"]))
        check("build_no_cycles", not cmds.cycleCheck(all=True, list=True))
        try:
            run_physics_preroll(state["world"], solvers, maya_cmds=cmds)
            report["after_build_preroll"] = "pass"
        except Exception:
            report["after_build_preroll"] = traceback.format_exc()
        report["control_count"] = len(state["controls"])
        check("build", state["exists"] and bool(state["controls"]))
        from mmd_tools.core.mmd_control_rig_builder import inspect_mmd_control_rig, remove_mmd_control_rig, MmdControlRigBuildError
        check("base_rig_inspection", inspect_mmd_control_rig(root).control_group == base.control_group)
        check("controls_hierarchy", all(
            item[field].startswith(base.control_group + "|Secondary|")
            for item in state["controls"] for field in ("control", "proxy")))
        try:
            remove_mmd_control_rig(root)
        except MmdControlRigBuildError as exc:
            check("base_remove_guard", "secondary bone controls" in str(exc), str(exc))
        else:
            check("base_remove_guard", False)
        foreign = cmds.createNode("transform", name="unownedSecondaryChild", parent=state["groups"][0]["node"])
        try:
            inspect_mmd_control_rig(root)
        except MmdControlRigBuildError as exc:
            check("foreign_child_rejected", "topology changed" in str(exc))
        else:
            check("foreign_child_rejected", False)
        cmds.delete(foreign)
        secondary_group = state["groups"][0]["node"]
        secondary_parent = cmds.listRelatives(secondary_group, parent=True, fullPath=True)[0]
        moved = cmds.parent(secondary_group, root, relative=True)[0]
        try:
            inspect_mmd_control_rig(root)
        except MmdControlRigBuildError as exc:
            check("moved_secondary_rejected", "topology changed" in str(exc))
        else:
            check("moved_secondary_rejected", False)
        cmds.parent(moved, secondary_parent, relative=True)
        metadata_plug = root + ".mmdSecondaryControls"
        saved_metadata = cmds.getAttr(metadata_plug)
        cmds.setAttr(metadata_plug, "{", type="string")
        try:
            inspect_mmd_control_rig(root)
        except MmdControlRigBuildError:
            check("invalid_secondary_metadata_rejected", True)
        else:
            check("invalid_secondary_metadata_rejected", False)
        cmds.setAttr(metadata_plug, saved_metadata, type="string")
        group_id = state["groups"][0]["id"]
        controls.rename_group(root, group_id, "Hair test")
        controls.set_group_visibility(root, group_id, False)
        group = next(item for item in controls.inspect(root)["groups"] if item["id"] == group_id)
        check("group_hide_rename", not group["visible"] and group["label"] == "Hair test")
        controls.set_group_visibility(root, group_id, True)
        world = state["world"]
        entry = next(item for item in state["controls"] if item["channels"])
        control, joint = entry["control"], entry["joint"]
        channel = next((value for value in entry["channels"] if value.startswith("r")), entry["channels"][0])
        cmds.setAttr(world + ".enable", False)
        yield
        check("off_channels_keyable", all(cmds.getAttr(control + "." + attr, keyable=True) for attr in entry["channels"]))
        cmds.currentTime(1)
        before = matrix(joint)
        cmds.setKeyframe(control, attribute=channel, time=1, value=0)
        cmds.setKeyframe(control, attribute=channel, time=5, value=20)
        cmds.currentTime(5)
        check("off_fk_moves_joint", max(abs(a-b) for a,b in zip(before, matrix(joint))) > 1e-4)
        check("off_control_follows", max(abs(a-b) for item in state["controls"]
              for a,b in zip(matrix(item["control"]), matrix(item["joint"]))) < 1e-4)
        parent = cmds.listRelatives(joint, parent=True, fullPath=True)[0]
        original_scale = cmds.getAttr(parent + ".scale")[0]
        try:
            cmds.setAttr(parent + ".scale", 1.2, 0.8, 1.4)
            scale_error = max(abs(a-b) for a,b in zip(matrix(control), matrix(joint)))
            check("parent_nonuniform_scale", scale_error < 1e-4, scale_error)
        finally:
            cmds.setAttr(parent + ".scale", *original_scale)
        curves = cmds.listConnections(control + "." + channel, source=True, destination=False, type="animCurve")
        check("authored_curve", bool(curves))
        keys = cmds.keyframe(curves, query=True, valueChange=True)
        cmds.setAttr(world + ".enable", True)
        yield
        auto_locked = cmds.getAttr(control + "." + channel, lock=True)
        if not auto_locked:
            report["callback_diagnostics"] = {"jobs": controls._JOBS, "matching_jobs": [item for item in cmds.scriptJob(listJobs=True) if "refresh_editability" in item], "uuid_lookup": {key: cmds.ls(key, long=True) for key in controls._JOBS}}
            controls.refresh_editability(root)
            report["callback_diagnostics"]["explicit_refresh_locked"] = cmds.getAttr(control + "." + channel, lock=True)
        check("on_locked", auto_locked, fatal=False)
        check("on_keys_retained", cmds.keyframe(curves, query=True, valueChange=True) == keys)
        controls.set_group_visibility(root, group_id, False)
        grouped = [item for item in state["controls"] if item["group_id"] == group_id]
        check("group_hides_live_curves", all(not cmds.getAttr(item["proxy"] + ".visibility") for item in grouped))
        controls.set_group_visibility(root, group_id, True)
        cmds.currentTime(1)
        run_physics_preroll(world, solvers, maya_cmds=cmds)
        sampled = {}
        for frame in range(1, 11):
            cmds.currentTime(frame)
            cmds.refresh(force=True)
            sampled[frame] = {item["joint"]: matrix(item["joint"]) for item in state["controls"]}
        motion_delta = max(abs(a-b) for bone, source in sampled[1].items() for a,b in zip(source, sampled[10][bone]))
        check("physics_moves", motion_delta > 1e-5, motion_delta)
        statuses = {solver: cmds.getAttr(solver + ".outStatus") for solver in solvers}
        check("physics_solver_ready", all(status in ("stepped", "reset", "pose-updated", "cached") for status in statuses.values()), statuses, fatal=False)
        check("on_proxy_follows", max(abs(a-b) for a,b in zip(matrix(entry["proxy"]), matrix(joint))) < 1e-4)
        check("no_cycles", not cmds.cycleCheck(all=True, list=True))
        controls.bake(root, 1, 10)
        report["bake_undo_name"] = cmds.undoInfo(query=True, undoName=True)
        cmds.undo()
        check("undo_bake_world", controls.inspect(root)["physics_enabled"])
        check("undo_bake_keys", cmds.keyframe(curves, query=True, valueChange=True) == keys)
        cmds.redo()
        state = controls.inspect(root)
        check("bake_off", not state["physics_enabled"])
        baked_curves = cmds.listConnections(control, source=True, destination=False, type="animCurve") or []
        baked_counts = {curve: cmds.keyframe(curve, query=True, keyframeCount=True) for curve in baked_curves}
        report["baked_channel_state"] = {attr: {"locked": cmds.getAttr(control + "." + attr, lock=True), "keyable": cmds.getAttr(control + "." + attr, keyable=True)} for attr in entry["channels"]}
        check("baked_channels_keyable", all(item["keyable"] and not item["locked"] for item in report["baked_channel_state"].values()))
        check("bake_keys", bool(baked_counts) and all(count >= 10 for count in baked_counts.values()), baked_counts, fatal=False)
        bake_error = 0.0
        for frame in range(1, 11):
            cmds.currentTime(frame)
            for bone, source in sampled[frame].items():
                bake_error = max(bake_error, max(abs(a-b) for a,b in zip(source, matrix(bone))))
        check("bake_world_matrices", bake_error < 1e-4, {"max_component_delta": bake_error, "tolerance": 1e-4})
        expected = {frame: matrix(joint) for frame in [10]}
        scene = output / "secondary_controls.ma"
        cmds.file(rename=str(scene))
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(str(scene), open=True, force=True)
        yield
        state = controls.inspect(root)
        check("reload_controls", state["exists"] and len(state["controls"]) == report["control_count"])
        check("reload_base_rig", inspect_mmd_control_rig(root) is not None)
        group = next(item for item in state["groups"] if item["id"] == group_id)
        check("reload_group", group["visible"] and group["label"] == "Hair test")
        check("reload_pose", max(abs(a-b) for a,b in zip(expected[10], matrix(joint))) < 1e-4)
        cmds.setAttr(world + ".enable", True)
        yield
        check("reload_auto_lock", cmds.getAttr(control + "." + channel, lock=True))
        cmds.setAttr(world + ".enable", False)
        yield
        check("reload_auto_unlock", not cmds.getAttr(control + "." + channel, lock=True))
        from mmd_tools.ui.control_rig_manager import ControlRigManagerWindow
        manager = ControlRigManagerWindow.show_manager()
        yield
        check("manager_visible", manager.isVisible())
        check("manager_capture", manager.grab().save(str(output / "control-rig-manager.png")))
        manager.close()
        panels = cmds.getPanel(type="modelPanel") or []
        visible_panels = cmds.getPanel(visiblePanels=True) or []
        panel = next((item for item in panels if item in visible_panels), panels[0])
        cmds.modelEditor(panel, edit=True, allObjects=False, polymeshes=True, nurbsCurves=True, grid=False, displayAppearance="smoothShaded", xray=True)
        cmds.modelPanel(panel, edit=True, camera="persp")
        cmds.select([item["control"] for item in state["controls"]], replace=True)
        cmds.viewFit("persp", all=False, fitFactor=1.15)
        cmds.refresh(force=True)
        viewport = output / "secondary-controls-viewport.png"
        cmds.playblast(format="image", completeFilename=str(viewport), compression="png", frame=10, widthHeight=(960, 720), viewer=False, offScreen=False, showOrnaments=False, percent=100, forceOverwrite=True, editorPanelName=panel)
        check("viewport_capture", viewport.is_file() and viewport.stat().st_size > 0)
        controls.remove(root)
        check("remove", not controls.inspect(root)["exists"])
        check("remove_preserves_pose", max(abs(a-b) for a,b in zip(expected[10], matrix(joint))) < 1e-4)
        cmds.undo()
        check("undo_remove", controls.inspect(root)["exists"])
        cmds.redo()
        check("redo_remove", not controls.inspect(root)["exists"])
        check("base_rig_after_remove", inspect_mmd_control_rig(root) is not None)
        check("removed_no_cycles", not cmds.cycleCheck(all=True, list=True))
        _supplemental_shapes(config["model"], controls, cmds, check)
        report["status"] = "pass" if all(item["pass"] for item in report["checks"].values()) else "fail"
    except Exception:
        report["errors"].append(traceback.format_exc())
    finally:
        cmds.scriptEditorInfo(writeHistory=False)
        logging.getLogger("mmd_tools").removeHandler(handler)
        handler.close()
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (output / "run.log").write_text(json.dumps(report, ensure_ascii=False) + "\n" + MARKER + "\n", encoding="utf-8")


def run_check(config_path):
    """Yield between mode changes so real Maya idle callbacks can execute."""
    import maya.cmds as cmds
    from mmd_tools.ui.qt_compat import QTimer

    steps = _checks(config_path)

    def advance():
        try:
            next(steps)
        except StopIteration:
            return
        QTimer.singleShot(100, advance)

    cmds.evalDeferred(advance, lowestPriority=True)


def main():
    """Launch an isolated Maya and require its completion report."""
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--port", type=int, default=7742)
    parser.add_argument("--model", default=str(ROOT / "tests/data/yw_test_model.pmx"))
    parser.add_argument("--out", default=str(ROOT / "build/reports/secondary-controls"))
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = output / "config.json"
    config.write_text(json.dumps({"model": str(Path(args.model).resolve()), "out": str(output)}, ensure_ascii=False), encoding="utf-8")
    command = "import sys\nsys.path.insert(0, {!r})\nfrom tools.maya_gui.secondary_controls_e2e import run_check\nrun_check({!r})".format(str(ROOT), str(config))
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=output, port=args.port,
        timeout=args.timeout, log_path=output / "run.log", report_path=output / "report.json",
        command=command, marker=MARKER, send_label="secondary-controls-e2e",
        stale_paths=[output / "run.log", output / "report.json"],
        env_overrides={"MAYA_SKIP_USERSETUP_PY": "1"},
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
