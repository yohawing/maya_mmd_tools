"""CR061-03 late-failure rollback coverage for mixed VMD scene channels."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from maya import cmds

from mmd_tools.converters.vmd_camera_animation import get_or_create_camera
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_light_animation import get_or_create_light
from tests.common.maya_test_base import MayaTestBase


class _EmptyVmdData:
    bone_frames = []
    morph_frames = []
    camera_frames = []
    light_frames = []
    ik_show_hide_frames = []


class TestCr06103SceneTransaction(MayaTestBase):
    """Force a late mixed-import failure and prove scene state is restored."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin_path), query=True, loaded=True):
            cls.plugins_loaded.extend(cmds.loadPlugin(str(plugin_path), quiet=True) or [])

    def test_late_failure_restores_curve_timeline_and_created_camera_light(self):
        root = cmds.group(empty=True, name="cr06103_transaction_model")
        joint = cmds.joint(name="cr06103_transaction_joint")
        if not (cmds.listRelatives(joint, parent=True, fullPath=True) or []):
            cmds.parent(joint, root)
        cmds.setKeyframe(joint, attribute="rotateX", time=1, value=10.0)
        cmds.setKeyframe(joint, attribute="rotateX", time=5, value=30.0)
        mesh = cmds.polyCube(name="cr06103_transaction_mesh")[0]
        cmds.parent(mesh, root)
        blend_shape = cmds.blendShape(mesh, name="cr06103_transaction_blendShape")[0]
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=1, value=0.25)
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=5, value=0.75)

        converter = VmdConverter()
        converter.bone_name_mapping = {"センター": joint}
        converter.morph_name_mapping = {
            "笑顔": [(blend_shape, "weight[0]", "笑顔")],
        }
        snapshot = converter._capture_mmd_control_rig_scene_snapshot(
            root,
            _EmptyVmdData(),
        )

        # Simulate clear_existing_motion plus successful camera/light writes,
        # followed by a late exception in a subsequent mixed channel.
        cmds.setKeyframe(joint, attribute="rotateX", time=1, value=99.0)
        cmds.setKeyframe(joint, attribute="rotateX", time=2, value=55.0)
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=1, value=0.95)
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=2, value=0.5)
        cmds.playbackOptions(min=0, max=240, animationStartTime=0, animationEndTime=240)
        cmds.currentTime(42, edit=True)
        get_or_create_camera()
        get_or_create_light()

        transaction = {
            "root": root,
            "created": False,
            "entered_here": False,
            "prior_animation_snapshot": [],
            "scene_snapshot": snapshot,
        }
        rollback_error = converter._rollback_mmd_control_rig_import(transaction)

        self.assertIsNone(rollback_error, rollback_error)
        self.assertEqual(cmds.keyframe(joint, attribute="rotateX", query=True, timeChange=True), [1.0, 5.0])
        restored_values = cmds.keyframe(joint, attribute="rotateX", query=True, valueChange=True)
        self.assertEqual(len(restored_values), 2)
        self.assertAlmostEqual(restored_values[0], 10.0, places=7)
        self.assertAlmostEqual(restored_values[1], 30.0, places=7)
        morph_values = cmds.keyframe(blend_shape, attribute="weight[0]", query=True, valueChange=True)
        self.assertEqual(len(morph_values), 2)
        self.assertAlmostEqual(morph_values[0], 0.25, places=7)
        self.assertAlmostEqual(morph_values[1], 0.75, places=7)
        self.assertEqual(cmds.playbackOptions(query=True, max=True), snapshot["timeline"]["max"])
        self.assertEqual(cmds.currentTime(query=True), snapshot["timeline"]["current_time"])
        self.assertFalse(cmds.ls("*.mmd_camera", objectsOnly=True))
        self.assertFalse(cmds.ls("*.mmd_light", objectsOnly=True))

    def test_entered_rig_failure_restores_rig_before_original_joint_source(self):
        root = cmds.group(empty=True, name="cr06103_transaction_order_model")
        joint = cmds.joint(name="cr06103_transaction_order_joint")
        if not (cmds.listRelatives(joint, parent=True, fullPath=True) or []):
            cmds.parent(joint, root)
        cmds.setKeyframe(joint, attribute="rotateX", time=2, value=12.0)
        original_curve = (cmds.listConnections(f"{joint}.rotateX", source=True, destination=False) or [None])[0]
        raw_metadata = '{"state":"ATTACHED","owner":"MMD_OWNED"}'
        cmds.addAttr(root, longName="mmd_control_rig_json", dataType="string")
        cmds.setAttr(f"{root}.mmd_control_rig_json", raw_metadata, type="string")

        converter = VmdConverter()
        converter.bone_name_mapping = {"センター": joint}
        snapshot = converter._capture_mmd_control_rig_scene_snapshot(root, _EmptyVmdData())
        cmds.setKeyframe(joint, attribute="rotateX", time=2, value=88.0)

        events = []
        original_scene_restore = converter._restore_mmd_control_rig_scene_snapshot
        transaction = {
            "root": root,
            "created": True,
            "entered_here": True,
            "prior_raw_metadata": raw_metadata,
            "prior_animation_snapshot": [],
            "scene_snapshot": snapshot,
        }
        with patch(
            "mmd_tools.core.mmd_control_rig_motion.restore_mmd_control_rig_attached",
            side_effect=lambda _root: events.append("rig_restore"),
        ), patch(
            "mmd_tools.core.mmd_control_rig_builder.remove_mmd_control_rig",
            side_effect=lambda _root: events.append("rig_remove"),
        ), patch.object(
            converter,
            "_restore_mmd_control_rig_scene_snapshot",
            side_effect=lambda value: (events.append("scene_restore"), original_scene_restore(value))[1],
        ):
            rollback_error = converter._rollback_mmd_control_rig_import(transaction)

        self.assertIsNone(rollback_error, rollback_error)
        self.assertEqual(events, ["rig_restore", "rig_remove", "scene_restore"])
        self.assertTrue(cmds.objExists(original_curve))
        self.assertAlmostEqual(cmds.keyframe(original_curve, query=True, valueChange=True)[0], 12.0, places=7)
        self.assertEqual(cmds.getAttr(f"{root}.mmd_control_rig_json"), raw_metadata)

    def test_existing_camera_and_light_curves_restore(self):
        root = cmds.group(empty=True, name="cr06103_transaction_scene_model")
        camera = get_or_create_camera()
        light = get_or_create_light()
        cmds.setKeyframe(camera, attribute="translateX", time=3, value=4.0)
        cmds.setKeyframe(light, attribute="rotateX", time=3, value=15.0)

        converter = VmdConverter()
        converter.bone_name_mapping = {}
        snapshot = converter._capture_mmd_control_rig_scene_snapshot(root, _EmptyVmdData())
        cmds.setKeyframe(camera, attribute="translateX", time=3, value=99.0)
        cmds.setKeyframe(camera, attribute="translateX", time=4, value=50.0)
        cmds.setKeyframe(light, attribute="rotateX", time=3, value=88.0)

        rollback_error = converter._rollback_mmd_control_rig_import(
            {
                "root": root,
                "entered_here": False,
                "created": False,
                "prior_animation_snapshot": [],
                "scene_snapshot": snapshot,
            }
        )

        self.assertIsNone(rollback_error, rollback_error)
        camera_values = cmds.keyframe(camera, attribute="translateX", query=True, valueChange=True)
        self.assertEqual(len(camera_values), 1)
        self.assertAlmostEqual(camera_values[0], 4.0, places=7)
        light_values = cmds.keyframe(light, attribute="rotateX", query=True, valueChange=True)
        self.assertEqual(len(light_values), 1)
        self.assertAlmostEqual(light_values[0], 15.0, places=7)

    def test_legacy_ik_solver_channels_restore_after_late_failure(self):
        """Rollback restores fallback solver keys, including inputRotate elements."""
        root = cmds.group(empty=True, name="cr06103_transaction_legacy_ik_model")
        joint = cmds.joint(name="cr06103_transaction_legacy_ik_joint")
        if not (cmds.listRelatives(joint, parent=True, fullPath=True) or []):
            cmds.parent(joint, root)
        solver = cmds.createNode("mmdCcdIk", name="cr06103_transaction_legacy_ik_solver")
        if not cmds.attributeQuery("mmd_ik_bone_name", node=solver, exists=True):
            cmds.addAttr(solver, longName="mmd_ik_bone_name", dataType="string")
        cmds.setAttr(f"{solver}.mmd_ik_bone_name", "右髪ＩＫ", type="string")
        chain = {
            "bones": [{"rest_position": [0.0, 0.0, 0.0], "parent_slot": -1}],
            "links": [{"bone_slot": 2}],
            "targetBoneSlot": 0,
            "controllerBoneSlot": 0,
            "iterationCount": 1,
            "limitAngle": 1.0,
        }
        cmds.setAttr(f"{solver}.chainJson", json.dumps(chain), type="string")
        cmds.connectAttr(f"{solver}.outputRotate[0]", f"{joint}.rotate", force=True)
        cmds.setKeyframe(solver, attribute="enabled", time=1, value=0)
        input_plug = f"{solver}.inputRotate[2].inputRotateElementX"
        cmds.setKeyframe(input_plug, time=1, value=0.25)

        converter = VmdConverter()
        converter.bone_name_mapping = {"右髪ＩＫ": joint}
        snapshot = converter._capture_mmd_control_rig_scene_snapshot(root, _EmptyVmdData())
        snapshot_plugs = {
            f"{row['node']}.{row['attribute']}"
            for row in snapshot["channels"]
        }
        self.assertIn(f"{solver}.enabled", snapshot_plugs)
        self.assertIn(input_plug, snapshot_plugs)
        cmds.setKeyframe(solver, attribute="enabled", time=2, value=1)
        cmds.setKeyframe(input_plug, time=2, value=0.75)

        rollback_error = converter._rollback_mmd_control_rig_import(
            {
                "root": root,
                "entered_here": False,
                "created": False,
                "prior_animation_snapshot": [],
                "scene_snapshot": snapshot,
            }
        )

        self.assertIsNone(rollback_error, rollback_error)
        self.assertEqual(cmds.keyframe(f"{solver}.enabled", query=True, timeChange=True), [1.0])
        self.assertEqual(cmds.keyframe(input_plug, query=True, timeChange=True), [1.0])
        self.assertAlmostEqual(
            cmds.keyframe(input_plug, query=True, valueChange=True)[0],
            0.25,
            places=7,
        )

    def test_zero_key_curve_stays_empty_after_late_failure(self):
        root = cmds.group(empty=True, name="cr06103_transaction_empty_curve_model")
        control = cmds.group(empty=True, name="cr06103_transaction_empty_curve_control")
        cmds.parent(control, root)
        curve = cmds.createNode("animCurveTL", name="cr06103_transaction_empty_curve")
        cmds.connectAttr(f"{curve}.output", f"{control}.translateX", force=True)

        converter = VmdConverter()
        converter.bone_name_mapping = {"センター": control}
        snapshot = converter._capture_mmd_control_rig_scene_snapshot(root, _EmptyVmdData())
        cmds.setKeyframe(control, attribute="translateX", time=6, value=42.0)
        self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True), [6.0])

        rollback_error = converter._rollback_mmd_control_rig_import(
            {
                "root": root,
                "entered_here": False,
                "created": False,
                "prior_animation_snapshot": [],
                "scene_snapshot": snapshot,
            }
        )

        self.assertIsNone(rollback_error, rollback_error)
        self.assertFalse(cmds.keyframe(curve, query=True, timeChange=True))
        self.assertEqual(
            cmds.listConnections(f"{control}.translateX", source=True, destination=False),
            [curve],
        )


if __name__ == "__main__":
    unittest.main()
