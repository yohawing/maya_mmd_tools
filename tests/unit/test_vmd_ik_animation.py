"""VMD IK enable animation and mmdCcdIk node behavior tests."""

import json
import os
from pathlib import Path

import maya.cmds as cmds

from mmd_tools.converters.vmd_context import VmdIkEnabledAnimationContext
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_ik_enabled_animation import apply_ik_enabled_animation, collect_ik_nodes_by_bone_name
from mmd_tools.converters.vmd_timeline import get_animation_frame_range
from mmd_tools.core.vmd_data.ik_show_hide_frame import VmdIKShowHideFrame
from tests.common.maya_test_base import MayaTestBase
from tests.common.vmd_mock import create_test_vmd_data


class TestVmdIkAnimation(MayaTestBase):
    """IK enable keying and mmdCcdIk behavior tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._previous_skip_shader_override = os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE")
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
        plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin_path), query=True, loaded=True):
            cls.plugins_loaded.extend(cmds.loadPlugin(str(plugin_path), quiet=True) or [])

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            previous = cls._previous_skip_shader_override
            if previous is None:
                os.environ.pop("MMD_TOOLS_SKIP_SHADER_OVERRIDE", None)
            else:
                os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = previous

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def _ik_enabled_context(self) -> VmdIkEnabledAnimationContext:
        return VmdIkEnabledAnimationContext(
            logger=self.converter.logger,
            collect_ik_nodes_by_bone_name=collect_ik_nodes_by_bone_name,
            get_animation_frame_range=get_animation_frame_range,
            vmd_frame_to_maya_time=self.converter.vmd_frame_to_maya_time,
        )

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

        apply_ik_enabled_animation(self._ik_enabled_context(), vmd_data)

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

        apply_ik_enabled_animation(self._ik_enabled_context(), vmd_data, target_namespace="ModelA")

        self.assertIn(0.0, cmds.keyframe(f"{node_a}.enabled", query=True, timeChange=True) or [])
        self.assertEqual(cmds.keyframe(f"{node_b}.enabled", query=True, timeChange=True), None)

        cmds.delete(node_a, node_b)

    def test_apply_ik_enabled_animation_scopes_namespace_less_nodes_to_target_root(self):
        """Explicit root ownership prevents same-named namespace-less IK cross-keying."""
        root_a = cmds.group(empty=True, name="ik_model_a_root")
        root_b = cmds.group(empty=True, name="ik_model_b_root")
        cmds.select(clear=True)
        joint_a = cmds.joint(name="ik_model_a_joint")
        cmds.parent(joint_a, root_a)
        cmds.select(clear=True)
        joint_b = cmds.joint(name="ik_model_b_joint")
        cmds.parent(joint_b, root_b)
        node_a = cmds.createNode("mmdCcdIk", name="ik_model_a_solver")
        node_b = cmds.createNode("mmdCcdIk", name="ik_model_b_solver")
        for node, joint in ((node_a, joint_a), (node_b, joint_b)):
            cmds.addAttr(node, longName="mmd_ik_bone_name", dataType="string")
            cmds.setAttr(f"{node}.mmd_ik_bone_name", "左足ＩＫ", type="string")
            cmds.setAttr(f"{node}.enabled", False)
            cmds.connectAttr(f"{joint}.rotate", f"{node}.inputRotate[0]")

        vmd_data = create_test_vmd_data()
        frame = VmdIKShowHideFrame()
        frame.frame_number = 20
        frame.ik_states = [("左足ＩＫ", 0)]
        vmd_data.ik_show_hide_frames = [frame]

        apply_ik_enabled_animation(
            self._ik_enabled_context(),
            vmd_data,
            target_model=root_b,
        )

        self.assertIsNone(cmds.keyframe(f"{node_a}.enabled", query=True))
        self.assertIn(0.0, cmds.keyframe(f"{node_b}.enabled", query=True, timeChange=True) or [])
        self.assertIn(20.0, cmds.keyframe(f"{node_b}.enabled", query=True, timeChange=True) or [])

    def test_apply_ik_enabled_animation_passes_target_model_as_callback_keyword(self):
        """Direct public-helper contexts do not bind target_model as namespace_for_node."""
        calls = []

        def collect(target_namespace=None, *, target_model=None):
            calls.append((target_namespace, target_model))
            return {}

        context = VmdIkEnabledAnimationContext(
            logger=self.converter.logger,
            collect_ik_nodes_by_bone_name=collect,
            get_animation_frame_range=get_animation_frame_range,
            vmd_frame_to_maya_time=self.converter.vmd_frame_to_maya_time,
        )

        apply_ik_enabled_animation(context, create_test_vmd_data(), target_model="model_root")

        self.assertEqual(calls, [(None, "model_root")])

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

    def test_mmd_ccd_ik_goal_world_matrix_root_translation_stays_passthrough(self):
        """モデル root 移動は world goal と local input に二重適用しない"""
        node = cmds.createNode("mmdCcdIk", name="goal_world_root_translation_ik_solver")
        root = cmds.group(empty=True, name="goal_world_root_translation_model_root")
        controller = cmds.group(empty=True, name="goal_world_root_translation_controller")
        cmds.parent(controller, root)
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
        cmds.setAttr(f"{controller}.translate", 1.0, 0.0, 0.0)
        cmds.connectAttr(f"{controller}.worldMatrix[0]", f"{node}.goalWorldMatrix")
        cmds.setAttr(f"{node}.inputTranslate[1].inputTranslateElementX", 1.0)

        before = cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")
        cmds.xform(root, relative=True, worldSpace=True, translation=(5.0, -2.0, 3.0))
        after = cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")

        self.assertAlmostEqual(before, 0.0, places=5)
        self.assertAlmostEqual(after, 0.0, places=5)

        cmds.delete(node, root)

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

    def _ancestor_chain_json(self) -> str:
        """腰相当の祖先(slot0) + link(1) + target(2) + 別ブランチ controller(3)"""
        return json.dumps({
            "bones": [
                {"rest_position": [0.0, 0.0, 0.0], "parent_slot": -1, "joint_orient_deg": [0.0, 0.0, 0.0]},
                {"rest_position": [1.0, 0.0, 0.0], "parent_slot": 0, "joint_orient_deg": [0.0, 0.0, 0.0]},
                {"rest_position": [1.0, 0.0, 0.0], "parent_slot": 1, "joint_orient_deg": [0.0, 0.0, 0.0]},
                {"rest_position": [2.0, 0.0, 0.0], "parent_slot": -1, "joint_orient_deg": [0.0, 0.0, 0.0]},
            ],
            "links": [{"bone_slot": 1}],
            "targetBoneSlot": 2,
            "controllerBoneSlot": 3,
            "iterationCount": 40,
            "limitAngle": 2.0,
        })

    def test_mmd_ccd_ik_ancestor_rotation_triggers_solve_when_goal_at_rest(self):
        """腰相当の祖先を回転すると、controller が REST のままでも IK を解き直す"""
        node = cmds.createNode("mmdCcdIk", name="ancestor_rotation_ik_solver")
        ancestor = cmds.createNode("transform", name="ancestor_rotation_src")
        cmds.setAttr(f"{node}.chainJson", self._ancestor_chain_json(), type="string")
        cmds.setAttr(f"{node}.enabled", True)
        cmds.connectAttr(f"{ancestor}.rotate", f"{node}.inputRotate[0]")
        cmds.setAttr(f"{ancestor}.rotateZ", 30.0)

        self.assertGreater(
            abs(cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")),
            20.0,
        )

        cmds.delete(node, ancestor)

    def test_mmd_ccd_ik_ancestor_translation_triggers_solve_when_goal_at_rest(self):
        """controller ブランチ外の祖先を移動しても IK を解き直す"""
        node = cmds.createNode("mmdCcdIk", name="ancestor_translation_ik_solver")
        ancestor = cmds.createNode("transform", name="ancestor_translation_src")
        cmds.setAttr(f"{node}.chainJson", self._ancestor_chain_json(), type="string")
        cmds.setAttr(f"{node}.enabled", True)
        cmds.connectAttr(f"{ancestor}.translate", f"{node}.inputTranslate[0]")
        cmds.setAttr(f"{ancestor}.translate", 0.0, 0.5, 0.0)

        self.assertGreater(
            abs(cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")),
            15.0,
        )

        cmds.delete(node, ancestor)

    def test_mmd_ccd_ik_ancestor_rotation_solves_with_goal_world_matrix_at_rest(self):
        """本番配線（goalWorldMatrix接続）でも祖先回転で IK を解き直す"""
        node = cmds.createNode("mmdCcdIk", name="ancestor_rotation_gwm_ik_solver")
        ancestor = cmds.createNode("transform", name="ancestor_rotation_gwm_src")
        goal = cmds.spaceLocator(name="ancestor_rotation_gwm_goal")[0]
        cmds.setAttr(f"{node}.chainJson", self._ancestor_chain_json(), type="string")
        cmds.setAttr(f"{node}.enabled", True)
        cmds.setAttr(f"{goal}.translate", 2.0, 0.0, 0.0)
        cmds.connectAttr(f"{goal}.worldMatrix[0]", f"{node}.goalWorldMatrix")
        cmds.connectAttr(f"{ancestor}.rotate", f"{node}.inputRotate[0]")
        cmds.setAttr(f"{ancestor}.rotateZ", 30.0)

        self.assertGreater(
            abs(cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ")),
            20.0,
        )

        cmds.delete(node, ancestor, goal)

    def test_mmd_ccd_ik_shared_root_rotation_stays_passthrough(self):
        """全ての親相当の共通ルート回転は target と goal が一緒に動くので解かない"""
        node = cmds.createNode("mmdCcdIk", name="shared_root_ik_solver")
        root = cmds.createNode("transform", name="shared_root_src")
        link = cmds.createNode("transform", name="shared_root_link_src")
        chain = {
            "bones": [
                {"rest_position": [0.0, 0.0, 0.0], "parent_slot": -1, "joint_orient_deg": [0.0, 0.0, 0.0]},
                {"rest_position": [1.0, 0.0, 0.0], "parent_slot": 0, "joint_orient_deg": [0.0, 0.0, 0.0]},
                {"rest_position": [1.0, 0.0, 0.0], "parent_slot": 1, "joint_orient_deg": [0.0, 0.0, 0.0]},
                {"rest_position": [2.0, 0.0, 0.0], "parent_slot": 0, "joint_orient_deg": [0.0, 0.0, 0.0]},
            ],
            "links": [{"bone_slot": 1}],
            "targetBoneSlot": 2,
            "controllerBoneSlot": 3,
            "iterationCount": 40,
            "limitAngle": 2.0,
        }
        cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
        cmds.setAttr(f"{node}.enabled", True)
        cmds.connectAttr(f"{root}.rotate", f"{node}.inputRotate[0]")
        cmds.setAttr(f"{root}.rotateZ", 30.0)
        # link のボーン軸(+X)まわり twist は target を動かさないので、
        # pass-through でそのまま出力に保持されるべき値。
        cmds.connectAttr(f"{link}.rotate", f"{node}.inputRotate[1]")
        cmds.setAttr(f"{link}.rotateX", 25.0)

        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementX"),
            25.0,
            places=4,
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{node}.outputRotate[0].outputRotateElementZ"),
            0.0,
            places=4,
        )

        cmds.delete(node, root, link)

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
