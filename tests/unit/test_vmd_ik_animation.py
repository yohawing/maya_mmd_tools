"""VMD IK enable animation and mmdCcdIk node behavior tests."""

import json

import maya.cmds as cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.core.vmd_data.ik_show_hide_frame import VmdIKShowHideFrame
from tests.common.maya_test_base import MayaTestBase
from tests.common.vmd_mock import create_test_vmd_data


class TestVmdIkAnimation(MayaTestBase):
    """IK enable keying and mmdCcdIk behavior tests."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_apply_ik_enabled_animation_defaults_all_ik_on_before_property_keys(self):
        """IK property frame が一部だけでも未指定 IK と初期区間は default ON で評価する"""
        left = cmds.createNode("mmdCcdIk", name="left_ik_solver")
        right = cmds.createNode("mmdCcdIk", name="right_ik_solver")
        for node, bone_name in ((left, "左足ＩＫ"), (right, "右足ＩＫ")):
            if not cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
                cmds.addAttr(node, longName="mmd_ik_bone_name", dataType="string")
            cmds.setAttr(f"{node}.mmd_ik_bone_name", bone_name, type="string")
            cmds.setAttr(f"{node}.enabled", False)

        vmd_data = create_test_vmd_data()
        frame = VmdIKShowHideFrame()
        frame.frame_number = 20
        frame.ik_states = [("左足ＩＫ", 0)]
        vmd_data.ik_show_hide_frames = [frame]

        self.converter._apply_ik_enabled_animation(vmd_data)

        self.assertEqual(cmds.getAttr(f"{left}.enabled"), False)
        self.assertEqual(cmds.getAttr(f"{right}.enabled"), True)
        self.assertIn(0.0, cmds.keyframe(f"{left}.enabled", query=True, timeChange=True) or [])
        self.assertIn(20.0, cmds.keyframe(f"{left}.enabled", query=True, timeChange=True) or [])
        self.assertEqual(cmds.keyframe(f"{left}.enabled", query=True, time=(0, 0), valueChange=True), [1.0])
        self.assertEqual(cmds.keyframe(f"{left}.enabled", query=True, time=(20, 20), valueChange=True), [0.0])
        self.assertEqual(cmds.keyframe(f"{right}.enabled", query=True, time=(0, 0), valueChange=True), [1.0])

        cmds.delete(left, right)

    def test_apply_ik_enabled_animation_scopes_to_target_namespace(self):
        """複数リグがあるシーンでは target_namespace の IK node だけに key を打つ"""
        cmds.namespace(add="ModelA")
        cmds.namespace(add="ModelB")
        node_a = cmds.createNode("mmdCcdIk", name="ModelA:left_ik_solver")
        node_b = cmds.createNode("mmdCcdIk", name="ModelB:left_ik_solver")
        for node in (node_a, node_b):
            if not cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
                cmds.addAttr(node, longName="mmd_ik_bone_name", dataType="string")
            cmds.setAttr(f"{node}.mmd_ik_bone_name", "左足ＩＫ", type="string")
            cmds.setAttr(f"{node}.enabled", False)

        vmd_data = create_test_vmd_data()
        frame = VmdIKShowHideFrame()
        frame.frame_number = 20
        frame.ik_states = [("左足ＩＫ", 0)]
        vmd_data.ik_show_hide_frames = [frame]

        self.converter._apply_ik_enabled_animation(vmd_data, target_namespace="ModelA")

        self.assertIn(0.0, cmds.keyframe(f"{node_a}.enabled", query=True, timeChange=True) or [])
        self.assertEqual(cmds.keyframe(f"{node_b}.enabled", query=True, timeChange=True), None)

        cmds.delete(node_a, node_b)

    def test_mmd_ccd_ik_disabled_passes_input_rotate_through(self):
        """IK OFF 時は link joint の FK/VMD 回転を失わないよう inputRotate を outputRotate に通す"""
        node = cmds.createNode("mmdCcdIk", name="disabled_passthrough_ik_solver")
        chain = {
            "bones": [
                {
                    "rest_position": [0.0, 0.0, 0.0],
                    "parent_slot": -1,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
            ],
            "links": [{"bone_slot": 0}],
            "targetBoneSlot": 0,
            "controllerBoneSlot": 0,
            "iterationCount": 1,
            "limitAngle": 1.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.setAttr(f"{node}.enabled", False)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementX", 0.25)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementY", -0.5)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementZ", 0.75)

        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementX"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementX"),
            places=6,
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementY"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementY"),
            places=6,
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementZ"),
            places=6,
        )

        cmds.delete(node)

    def test_mmd_ccd_ik_enabled_controller_at_rest_passes_input_rotate_through(self):
        """controllerBoneSlot が REST 位置なら IK ON でも REST を崩さず inputRotate を通す"""
        node = cmds.createNode("mmdCcdIk", name="enabled_rest_passthrough_ik_solver")
        chain = {
            "bones": [
                {
                    "rest_position": [0.0, 0.0, 0.0],
                    "parent_slot": -1,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
                {
                    "rest_position": [1.0, 0.0, 0.0],
                    "parent_slot": 0,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
            ],
            "links": [{"bone_slot": 0}],
            "targetBoneSlot": 1,
            "controllerBoneSlot": 1,
            "iterationCount": 40,
            "limitAngle": 2.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.setAttr(f"{node}.enabled", True)
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementX", 1.0)
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementY", 0.0)
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementZ", 0.0)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementX", 0.25)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementY", -0.5)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementZ", 0.75)

        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementX"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementX"),
            places=6,
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementY"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementY"),
            places=6,
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementZ"),
            places=6,
        )

        cmds.delete(node)

    def test_mmd_ccd_ik_goal_world_matrix_at_rest_passes_input_rotate_through(self):
        """goalWorldMatrix 接続があっても controller が REST 位置なら IK は REST を崩さない"""
        node = cmds.createNode("mmdCcdIk", name="goal_world_rest_passthrough_ik_solver")
        goal = cmds.spaceLocator(name="goal_world_rest_locator")[0]
        chain = {
            "bones": [
                {
                    "rest_position": [0.0, 0.0, 0.0],
                    "parent_slot": -1,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
                {
                    "rest_position": [1.0, 0.0, 0.0],
                    "parent_slot": 0,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
            ],
            "links": [{"bone_slot": 0}],
            "targetBoneSlot": 1,
            "controllerBoneSlot": 1,
            "iterationCount": 40,
            "limitAngle": 2.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.setAttr(f"{node}.enabled", True)
        cmds.setAttr(f"{goal}.translate", 1.0, 0.0, 0.0)
        cmds.connectAttr(f"{goal}.worldMatrix[0]", f"{node}.goalWorldMatrix")
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementX", 1.0)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementX", 0.25)

        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementX"),
            cmds.getAttr(f"{node}.inputRotate[0].inputRotateElementX"),
            places=6,
        )

        cmds.delete(node, goal)

    def test_mmd_ccd_ik_goal_world_matrix_offset_solves_without_translate_offset(self):
        """goalWorldMatrix が REST から動いたら controller translate offset なしでも IK を解く"""
        node = cmds.createNode("mmdCcdIk", name="goal_world_offset_ik_solver")
        goal = cmds.spaceLocator(name="goal_world_offset_locator")[0]
        chain = {
            "bones": [
                {
                    "rest_position": [0.0, 0.0, 0.0],
                    "parent_slot": -1,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
                {
                    "rest_position": [1.0, 0.0, 0.0],
                    "parent_slot": 0,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
            ],
            "links": [{"bone_slot": 0}],
            "targetBoneSlot": 1,
            "controllerBoneSlot": 1,
            "iterationCount": 40,
            "limitAngle": 2.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.setAttr(f"{node}.enabled", True)
        cmds.setAttr(f"{goal}.translate", 0.0, 1.0, 0.0)
        cmds.connectAttr(f"{goal}.worldMatrix[0]", f"{node}.goalWorldMatrix")
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementX", 1.0)
        cmds.setAttr(f"{node}.inputRotate[0].inputRotateElementX", 0.25)

        self.assertGreater(
            abs(cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")),
            45.0,
        )

        cmds.delete(node, goal)

    def test_mmd_ccd_ik_external_goal_connection_overrides_controller_slot_goal(self):
        """controllerBoneSlot があっても外部 goal 接続は公開入力として尊重する"""
        node = cmds.createNode("mmdCcdIk", name="external_goal_ik_solver")
        goal = cmds.spaceLocator(name="external_goal_locator")[0]
        chain = {
            "bones": [
                {
                    "rest_position": [0.0, 0.0, 0.0],
                    "parent_slot": -1,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
                {
                    "rest_position": [1.0, 0.0, 0.0],
                    "parent_slot": 0,
                    "joint_orient_deg": [0.0, 0.0, 0.0],
                },
            ],
            "links": [{
                "bone_slot": 0,
                "has_angle_limit": False,
                "angle_limit_min": [0.0, 0.0, 0.0],
                "angle_limit_max": [0.0, 0.0, 0.0],
            }],
            "targetBoneSlot": 1,
            "controllerBoneSlot": 1,
            "iterationCount": 40,
            "limitAngle": 2.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.connectAttr(f"{goal}.translate", f"{node}.goal")
        cmds.setAttr(f"{goal}.translate", 1.0, 0.0, 0.0)
        rest_z = cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")
        cmds.setAttr(f"{goal}.translate", 0.0, 1.0, 0.0)
        aimed_z = cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")

        self.assertAlmostEqual(rest_z, 0.0, places=5)
        self.assertGreater(abs(aimed_z), 45.0)

        cmds.delete(node, goal)
