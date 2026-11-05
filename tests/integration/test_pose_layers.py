"""Animator Copy/Paste uses authored inputs and preserves animation-layer keys."""

import json
from pathlib import Path
from unittest import mock

from maya import cmds

from mmd_tools.actions.pose_actions import (
    CopyPoseAction, CopyPoseRequest, PastePoseAction, PastePoseRequest,
)
from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
from tests.common.maya_test_base import MayaTestBase


ROOT = Path(__file__).resolve().parents[2]


def select_layer(layer):
    """Select one destination explicitly, including the BaseAnimation layer."""
    for other in cmds.ls(type="animLayer") or []:
        cmds.animLayer(other, edit=True, selected=other == layer, preferred=other == layer)


def create_pose_case(kind, mode):
    """Key frame 1's source and frame 10's neutral pose on safe authoring plugs.

    IK layer membership is solver inputRotate, never the solver-driven joint.
    This fixture intentionally does not claim to fix issue 194's separate
    new-layer-on-joint IK problem.
    """
    cmds.file(new=True, force=True)
    cmds.currentTime(1)
    root = None
    if kind == "direct":
        joint = cmds.joint(name="pose_joint")
        target = joint
        plugs = [joint + "." + attr + axis for attr in ("translate", "rotate") for axis in "XYZ"]
        source = (1, 2, 3, 10, 20, 30)
    else:
        from mmd_tools.core.mmd_control_rig_analyzer import analyze_mmd_control_rig
        from mmd_tools.io.mmd_importer import import_mmd_file

        root = import_mmd_file(str(ROOT / "tests/data/mmt_test_model.pmx"), options={
            "setup_rig": True, "setup_bone_orientation": True, "import_physics": False,
        })
        if kind.startswith("control_"):
            from mmd_tools.core.mmd_control_rig_builder import (
                build_mmd_control_rig, read_mmd_control_rig_metadata, resolve_mmd_control_rig_binding_joint,
            )
            from mmd_tools.core.mmd_control_rig_motion import enter_mmd_control_rig_edit

            build_mmd_control_rig(root)
            enter_mmd_control_rig_edit(root)
            metadata = read_mmd_control_rig_metadata(root)
            role = "upper_body" if kind == "control_rotation" else "center"
            target = cmds.ls(metadata["controls"][role], long=True)[0]
            joint = resolve_mmd_control_rig_binding_joint(cmds, metadata["bindings"][role])
            attrs = ("rotate",) if kind == "control_rotation" else ("translate", "rotate")
            plugs = [target + "." + attr + axis for attr in attrs for axis in "XYZ"]
            source = (10, 20, 30) if kind == "control_rotation" else (1, 2, 3, 10, 20, 30)
        else:
            spec = analyze_mmd_control_rig(root, cmds_module=cmds)
            binding = next(b for b in spec.bones if b.joint.endswith("|right_leg"))
            joint = target = binding.joint
            output = next(row.source_plug for row in binding.incoming if row.source_node_type == "mmdCcdIk")
            solver = output.split(".", 1)[0]
            index = int(output.split("[")[1].split("]")[0])
            chain = json.loads(cmds.getAttr(solver + ".chainJson"))
            slot = chain["links"][index]["bone_slot"]
            plugs = [f"{solver}.inputRotate[{slot}].inputRotateElement{axis}" for axis in "XYZ"]
            source = (10, 20, 30)
            control = next(b.joint for b in spec.bones if b.joint.endswith("|right_leg_ik"))
            cmds.setAttr(control + ".translateZ", cmds.getAttr(control + ".translateZ") + 0.5)
    for plug, value in zip(plugs, source):
        cmds.setKeyframe(plug, time=1, value=value)
        cmds.setKeyframe(plug, time=10, value=0.0)
    layer = None
    if mode != "base":
        layer = cmds.animLayer("PasteLayer", override=mode == "override", weight=1.0)
        for plug in plugs:
            cmds.animLayer(layer, edit=True, attribute=plug)
        select_layer(layer)
        for plug, value in zip(plugs, source):
            cmds.setKeyframe(plug, time=1, value=value, animLayer=layer)
            cmds.setKeyframe(plug, time=10, value=0.0, animLayer=layer)
    cmds.currentTime(1)
    return {"kind": kind, "mode": mode, "root": root, "joint": joint, "target": target,
            "plugs": plugs, "layer": layer}


def pose_snapshot(case):
    """Read both the editable inputs and their evaluated joint pose."""
    return {
        "inputs": [cmds.getAttr(plug) for plug in case["plugs"]],
        "world_matrix": cmds.xform(case["joint"], query=True, worldSpace=True, matrix=True),
    }


def curve_snapshot():
    """Preserve original curve identity, key times and values on failure."""
    return {curve: {
        "times": cmds.keyframe(curve, query=True, timeChange=True) or [],
        "values": cmds.keyframe(curve, query=True, valueChange=True) or [],
        "destinations": cmds.listConnections(curve + ".output", source=False, plugs=True) or [],
    } for curve in cmds.ls(type="animCurve") or []}


def pose_delta(left, right):
    return max(abs(a-b) for key in ("inputs", "world_matrix") for a, b in zip(left[key], right[key]))


class TestPoseLayers(MayaTestBase):
    def _copy(self, case):
        result = CopyPoseAction(MayaCmdsAdapter(cmds)).execute(
            CopyPoseRequest(joints=[case["target"]], model_root=case["root"]),
        )
        self.assertTrue(result.succeeded, result.error)
        return result.pose

    def _paste(self, case, pose):
        return PastePoseAction(MayaCmdsAdapter(cmds)).execute(
            PastePoseRequest(pose=pose, model_root=case["root"]),
        )

    def test_direct_base_additive_override_preserve_pose_and_layer_controls(self):
        for mode in ("base", "additive", "override"):
            with self.subTest(mode=mode):
                case = create_pose_case("direct", mode)
                source, pose = pose_snapshot(case), self._copy(case)
                cmds.currentTime(10)
                before = pose_snapshot(case)
                result = self._paste(case, pose)
                self.assertTrue(result.succeeded, result.error)
                self.assertEqual(result.applied_count, 1)
                self.assertLess(pose_delta(source, pose_snapshot(case)), 1e-5)
                cmds.undo()
                self.assertLess(pose_delta(before, pose_snapshot(case)), 1e-5)
                cmds.redo()
                cmds.currentTime(11)
                cmds.currentTime(10)
                self.assertLess(pose_delta(source, pose_snapshot(case)), 1e-5)
                if case["layer"]:
                    cmds.setAttr(case["layer"] + ".mute", True)
                    self.assertLess(pose_delta(before, pose_snapshot(case)), 1e-5)
                    cmds.setAttr(case["layer"] + ".mute", False)
                    cmds.setAttr(case["layer"] + ".weight", 0.0)
                    self.assertLess(pose_delta(before, pose_snapshot(case)), 1e-5)
                    cmds.setAttr(case["layer"] + ".weight", 1.0)
                    self.assertLess(pose_delta(source, pose_snapshot(case)), 1e-5)

    def test_cross_layer_copy_uses_evaluated_inputs_and_rolls_back_covered_base(self):
        for source_mode, destination_mode in (
            ("base", "additive"), ("base", "override"),
            ("additive", "base"), ("override", "base"),
        ):
            with self.subTest(copy=source_mode, paste=destination_mode):
                case = create_pose_case("direct", source_mode)
                source, pose = pose_snapshot(case), self._copy(case)
                cmds.currentTime(10)
                if destination_mode == "base":
                    destination = cmds.animLayer(query=True, root=True)
                else:
                    destination = cmds.animLayer("DestinationLayer", override=destination_mode == "override")
                    for plug in case["plugs"]:
                        cmds.animLayer(destination, edit=True, attribute=plug)
                select_layer(destination)
                before, curves = pose_snapshot(case), curve_snapshot()
                result = self._paste(case, pose)
                if source_mode == "override":
                    self.assertFalse(result.succeeded)
                    self.assertEqual(result.applied_count, 0)
                    self.assertEqual(curve_snapshot(), curves)
                    self.assertLess(pose_delta(before, pose_snapshot(case)), 1e-5)
                else:
                    self.assertTrue(result.succeeded, result.error)
                    self.assertLess(pose_delta(source, pose_snapshot(case)), 1e-5)

    def test_locked_channel_preserves_original_keys(self):
        case = create_pose_case("direct", "base")
        pose = self._copy(case)
        cmds.currentTime(10)
        cmds.setAttr(case["plugs"][-1], lock=True)
        curves, before = curve_snapshot(), pose_snapshot(case)
        result = self._paste(case, pose)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.applied_count, 0)
        self.assertEqual(curve_snapshot(), curves)
        self.assertLess(pose_delta(before, pose_snapshot(case)), 1e-5)

    def test_control_owned_respects_rotation_only_and_full_channel_policies(self):
        self.load_plugin(str(ROOT / "mmd_tools/plugin_main.py"))
        for kind in ("control_rotation", "control_center"):
            with self.subTest(kind=kind):
                case = create_pose_case(kind, "base")
                source, pose = pose_snapshot(case), self._copy(case)
                if kind == "control_rotation":
                    self.assertTrue(all(cmds.getAttr(case["target"] + ".translate" + axis, lock=True) for axis in "XYZ"))
                cmds.currentTime(10)
                before = pose_snapshot(case)
                result = self._paste(case, pose)
                self.assertTrue(result.succeeded, result.error)
                self.assertLess(pose_delta(source, pose_snapshot(case)), 1e-5)
                cmds.undo()
                self.assertLess(pose_delta(before, pose_snapshot(case)), 1e-5)
                cmds.redo()
                self.assertLess(pose_delta(source, pose_snapshot(case)), 1e-5)

    def test_partial_key_failure_rolls_back_original_keys(self):
        case = create_pose_case("direct", "additive")
        pose = self._copy(case)
        cmds.currentTime(10)
        curves, before = curve_snapshot(), pose_snapshot(case)
        original = cmds.setKeyframe
        calls = []

        def fail_second(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise RuntimeError("injected second pose key failure")
            return original(*args, **kwargs)

        with mock.patch.object(cmds, "setKeyframe", side_effect=fail_second):
            result = self._paste(case, pose)
        self.assertEqual(len(calls), 2)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.applied_count, 0)
        self.assertEqual(curve_snapshot(), curves)
        self.assertLess(pose_delta(before, pose_snapshot(case)), 1e-5)
