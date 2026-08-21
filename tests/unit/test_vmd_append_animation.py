"""VMD append-bone routing and decomposition tests."""

import os
from pathlib import Path
from unittest.mock import patch

import maya.api.OpenMaya as om
import maya.cmds as cmds

from mmd_tools.converters.vmd_append_decomposition import stable_long_dag_path
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_redirected_authoring_proxy import (
    resolve_redirected_authoring_proxy_authority,
)
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from tests.common.maya_test_base import MayaTestBase


def _bone_frame(bone_name, frame_number, position, rotation=(0.0, 0.0, 0.0, 1.0)):
    frame = VmdBoneFrame()
    frame.bone_name = bone_name
    frame.frame_number = frame_number
    frame.position = position
    frame.rotation = rotation
    return frame


class TestVmdAppendAnimation(MayaTestBase):
    """Append-bone animation route and decomposition tests."""

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

    def test_legacy_bone_animation_redirects_append_rotate_to_base_rotate(self):
        """append target ボーンの rotate は append node の baseRotate に key する。"""
        joint = cmds.joint(name="legacy_append_target_joint")
        cmds.select(clear=True)
        append_node = cmds.createNode("transform", name="legacy_append_route_node")
        for attr in ("baseRotateX", "baseRotateY", "baseRotateZ"):
            cmds.addAttr(append_node, longName=attr, attributeType="double", keyable=True)

        self.converter.use_animation_layers = False
        self.converter.set_bone_name_mapping({"付与先": joint})
        self.converter._bone_bind_poses["付与先"] = (0.0, 0.0, 0.0)
        frames = [_bone_frame("付与先", 3, (0.0, 0.0, 0.0))]

        append_info = {
            joint: {
                "node": append_node,
                "attr_map": {
                    "rotateX": "baseRotateX",
                    "rotateY": "baseRotateY",
                    "rotateZ": "baseRotateZ",
                },
            }
        }
        with patch.object(self.converter, "_collect_append_info", return_value=append_info), patch.object(
            self.converter,
            "_collect_ik_link_joints",
            return_value={},
        ), patch(
            "mmd_tools.converters.vmd_bone_animation.cmds.setKeyframe",
            side_effect=AssertionError("append route should use batch keying"),
        ):
            self.assertTrue(self.converter._convert_bone_animation(frames))

        proxy_route, authority, claimed = resolve_redirected_authoring_proxy_authority(joint)
        self.assertTrue(claimed)
        self.assertEqual(authority["rotateX"], (append_node, "baseRotateX"))
        proxy, proxy_attr = proxy_route["rotateX"]
        self.assertIn(3.0, cmds.keyframe(f"{proxy}.{proxy_attr}", query=True, timeChange=True) or [])
        self.assertIsNone(cmds.keyframe(f"{append_node}.baseRotateX", query=True, timeChange=True))
        self.assertIsNone(cmds.keyframe(f"{joint}.rotateX", query=True, timeChange=True))

        cmds.delete(joint, append_node)

    def test_root_scoped_long_path_mapping_routes_real_append_with_duplicate_short_names(self):
        """Canonical append metadata selects model B base attrs, never its driven joint."""
        root_a = cmds.group(empty=True, name="append_route_model_a")
        root_b = cmds.group(empty=True, name="append_route_model_b")
        source_a = cmds.createNode("joint", name="shared_source", parent=root_a)
        target_a = cmds.createNode("joint", name="shared_target", parent=root_a)
        source_b = cmds.createNode("joint", name="shared_source", parent=root_b)
        target_b = cmds.createNode("joint", name="shared_target", parent=root_b)
        append_node = cmds.createNode("mmdAppend", name="model_b_append")
        cmds.setAttr(f"{append_node}.ratio", 0.5)
        cmds.setAttr(f"{append_node}.affectRotation", True)
        cmds.connectAttr(f"{source_b}.rotate", f"{append_node}.sourceRotate")
        cmds.connectAttr(f"{append_node}.outputRotate", f"{target_b}.rotate")

        long_source_b = cmds.ls(source_b, long=True)[0]
        long_target_b = cmds.ls(target_b, long=True)[0]
        append_info = self.converter._collect_append_info()
        self.assertIn(long_target_b, append_info)
        self.assertEqual(append_info[long_target_b]["target_joint"], long_target_b)
        self.assertEqual(append_info[long_target_b]["source_joint"], long_source_b)
        # Duplicate leaf names are intentionally not accepted as aliases.
        self.assertNotIn("shared_target", append_info)

        self.converter.use_animation_layers = False
        self.converter.set_bone_name_mapping({"付与先": long_target_b})
        self.converter._bone_bind_poses["付与先"] = (0.0, 0.0, 0.0)
        frames = [_bone_frame("付与先", 3, (0.0, 0.0, 0.0))]

        self.assertTrue(self.converter._convert_bone_animation(frames))

        proxy_route, authority, claimed = resolve_redirected_authoring_proxy_authority(long_target_b)
        self.assertTrue(claimed)
        self.assertEqual(authority["rotateX"], (append_node, "baseRotateX"))
        proxy, proxy_attr = proxy_route["rotateX"]
        self.assertIn(3.0, cmds.keyframe(f"{proxy}.{proxy_attr}", query=True, timeChange=True) or [])
        self.assertIsNone(cmds.keyframe(f"{append_node}.baseRotateX", query=True, timeChange=True))
        self.assertIsNone(cmds.keyframe(f"{long_target_b}.rotateX", query=True, timeChange=True))
        self.assertIsNone(cmds.keyframe(f"{target_a}.rotateX", query=True, timeChange=True))
        self.assertTrue(cmds.objExists(source_a))

    def test_append_target_is_not_added_to_animation_layer_joint_rotate(self):
        """append target joint は layer 登録で joint.rotate へ直接キーを作らない。"""
        joint = cmds.joint(name="legacy_append_layer_target_joint")
        cmds.select(clear=True)
        append_node = cmds.createNode("transform", name="legacy_append_layer_route_node")
        for attr in ("baseRotateX", "baseRotateY", "baseRotateZ"):
            cmds.addAttr(append_node, longName=attr, attributeType="double", keyable=True)

        self.converter.use_animation_layers = True
        self.converter.anim_layer = cmds.animLayer("legacy_append_layer", override=False, weight=1.0)
        self.converter.set_bone_name_mapping({"付与先": joint})
        self.converter._bone_bind_poses["付与先"] = (0.0, 0.0, 0.0)
        frames = [_bone_frame("付与先", 3, (0.0, 0.0, 0.0))]
        append_info = {
            joint: {
                "node": append_node,
                "attr_map": {
                    "rotateX": "baseRotateX",
                    "rotateY": "baseRotateY",
                    "rotateZ": "baseRotateZ",
                },
            }
        }
        with patch.object(self.converter, "_collect_append_info", return_value=append_info), patch.object(
            self.converter,
            "_collect_ik_link_joints",
            return_value={},
        ):
            self.assertTrue(self.converter._convert_bone_animation(frames))

        self.assertFalse(cmds.listConnections(f"{joint}.rotateX", s=True, d=False, p=True) or [])
        proxy_route, authority, claimed = resolve_redirected_authoring_proxy_authority(joint)
        self.assertTrue(claimed)
        self.assertEqual(authority["rotateX"], (append_node, "baseRotateX"))
        proxy, proxy_attr = proxy_route["rotateX"]
        self.assertIn(3.0, cmds.keyframe(f"{proxy}.{proxy_attr}", query=True, timeChange=True) or [])
        self.assertIsNone(cmds.keyframe(f"{append_node}.baseRotateX", query=True, timeChange=True))

        cmds.delete(joint, append_node)

    def test_legacy_bone_animation_skips_ik_link_rotate_keys(self):
        """IK link ボーンは translate のみ key し、solver 駆動 rotate には key しない。"""
        joint = cmds.joint(name="legacy_ik_link_joint")
        self.converter.use_animation_layers = False
        self.converter.set_bone_name_mapping({"ＩＫリンク": joint})
        self.converter._bone_bind_poses["ＩＫリンク"] = (1.0, 2.0, 3.0)
        frames = [_bone_frame("ＩＫリンク", 7, (1.0, 0.0, 2.0))]

        with patch.object(self.converter, "_collect_append_info", return_value={}), patch.object(
            self.converter,
            "_collect_ik_link_joints",
            return_value={joint: None},
        ), patch(
            "mmd_tools.converters.vmd_bone_animation.cmds.setKeyframe",
            side_effect=AssertionError("IK link translate route should use batch keying"),
        ):
            self.assertTrue(self.converter._convert_bone_animation(frames))

        self.assertIn(7.0, cmds.keyframe(f"{joint}.translateX", query=True, timeChange=True) or [])
        self.assertIsNone(cmds.keyframe(f"{joint}.rotateX", query=True, timeChange=True))

        cmds.delete(joint)

    def test_decompose_append_own_translation_removes_grant_offset(self):
        """runtime final translate から付与移動分を引いた値を mmdAppend.baseTranslate にキーできる"""
        final_tx = om.MDoubleArray([2.0, 2.0])
        final_ty = om.MDoubleArray([2.0, 2.0])
        final_tz = om.MDoubleArray([-2.0, -2.0])
        source_tx = om.MDoubleArray([0.0, 1.0])
        source_ty = om.MDoubleArray([0.0, 0.0])
        source_tz = om.MDoubleArray([0.0, 0.0])

        own, grant = self.converter._decompose_append_own_translation(
            final_tx, final_ty, final_tz,
            source_tx, source_ty, source_tz,
            ratio=1.0,
        )

        self.assertAlmostEqual(own[0][0], 2.0, places=6)
        self.assertAlmostEqual(own[0][1], 1.0, places=6)
        self.assertAlmostEqual(own[1][1], 2.0, places=6)
        self.assertAlmostEqual(own[2][1], -2.0, places=6)
        self.assertAlmostEqual(grant[0][1], 1.0, places=6)

    def test_decompose_append_own_rotation_ignores_joint_orient_at_rest(self):
        """付与回転の逆分解は JO を REST grant として扱わない"""

        def q_from_deg(x, y, z):
            return om.MEulerRotation(
                x * 3.141592653589793 / 180.0,
                y * 3.141592653589793 / 180.0,
                z * 3.141592653589793 / 180.0,
            ).asQuaternion()

        source_jo = q_from_deg(0.0, 30.0, 15.0)
        target_jo = q_from_deg(-20.0, 10.0, 35.0)
        identity = q_from_deg(0.0, 0.0, 0.0)
        final_euler = identity.asEulerRotation()
        source_euler = identity.asEulerRotation()
        (own_rx, own_ry, own_rz), _ = self.converter._decompose_append_own_rotation(
            om.MDoubleArray([final_euler.x]),
            om.MDoubleArray([final_euler.y]),
            om.MDoubleArray([final_euler.z]),
            om.MDoubleArray([source_euler.x]),
            om.MDoubleArray([source_euler.y]),
            om.MDoubleArray([source_euler.z]),
            1.0,
            target_joint_orient=target_jo,
            source_joint_orient=source_jo,
        )

        actual = om.MEulerRotation(own_rx[0], own_ry[0], own_rz[0]).asQuaternion()
        dot = abs(
            actual.x * identity.x
            + actual.y * identity.y
            + actual.z * identity.z
            + actual.w * identity.w
        )
        self.assertAlmostEqual(dot, 1.0, places=6)

    def test_mmd_append_node_ignores_joint_orient_at_rest(self):
        """mmdAppend は JO 非ゼロでも REST で付与回転を発生させない"""
        try:
            node = cmds.createNode("mmdAppend", name="append_joint_orient_space_node")
        except Exception as exc:
            self.skipTest(f"mmdAppend node is unavailable: {exc}")

        def set_angle3(attr, values):
            for axis, value in zip("XYZ", values):
                cmds.setAttr(f"{node}.{attr}{axis}", value)

        def q_from_deg(x, y, z):
            return om.MEulerRotation(
                x * 3.141592653589793 / 180.0,
                y * 3.141592653589793 / 180.0,
                z * 3.141592653589793 / 180.0,
            ).asQuaternion()

        source_jo_deg = (0.0, 30.0, 15.0)
        target_jo_deg = (-20.0, 10.0, 35.0)

        cmds.setAttr(f"{node}.ratio", 1.0)
        cmds.setAttr(f"{node}.affectRotation", True)
        set_angle3("baseRotate", (0.0, 0.0, 0.0))
        set_angle3("sourceRotate", (0.0, 0.0, 0.0))
        set_angle3("sourceJointOrient", source_jo_deg)
        set_angle3("targetJointOrient", target_jo_deg)

        actual_deg = cmds.getAttr(f"{node}.outputRotate")[0]
        actual = q_from_deg(*actual_deg)
        expected = q_from_deg(0.0, 0.0, 0.0)
        dot = abs(
            actual.x * expected.x
            + actual.y * expected.y
            + actual.z * expected.z
            + actual.w * expected.w
        )
        self.assertAlmostEqual(dot, 1.0, places=5)

        cmds.delete(node)

    def test_collect_append_info_finds_source_from_translation_only_grant(self):
        """移動付与のみの mmdAppend でも sourceTranslate 経由で source joint を特定する"""
        source = cmds.joint(name="translation_only_source")
        cmds.select(clear=True)
        target = cmds.joint(name="translation_only_target")
        node = cmds.createNode("mmdAppend", name="translation_only_append")
        delta = cmds.createNode("plusMinusAverage", name="translation_only_source_delta")

        cmds.setAttr(f"{node}.affectRotation", False)
        cmds.setAttr(f"{node}.affectTranslation", True)
        cmds.setAttr(f"{delta}.operation", 2)
        cmds.connectAttr(f"{source}.translate", f"{delta}.input3D[0]")
        cmds.connectAttr(f"{delta}.output3D", f"{node}.sourceTranslate")
        cmds.connectAttr(f"{node}.outputTranslate", f"{target}.translate")

        append_info = self.converter._collect_append_info()

        self.assertEqual(append_info[target]["target_joint"], cmds.ls(target, long=True)[0])
        self.assertEqual(append_info[target]["source_joint"], cmds.ls(source, long=True)[0])
        self.assertTrue(append_info[target]["affect_translation"])
        self.assertFalse(append_info[target]["affect_rotation"])
        self.assertEqual(append_info[target]["attr_map"]["translateX"], "baseTranslateX")

        cmds.delete(source, target, node, delta)

    def test_decompose_local_append_translation_uses_source_delta_not_rest_offset(self):
        """local 付与移動の連鎖では source の rest offset を grant として扱わない"""
        driver = cmds.joint(name="append_driver")
        cmds.setAttr(f"{driver}.translate", 0.0, 0.0, 0.0)
        cmds.select(clear=True)
        source = cmds.joint(name="append_source")
        cmds.setAttr(f"{source}.translate", 5.0, 0.0, 0.0)
        cmds.select(clear=True)
        target = cmds.joint(name="append_target")
        cmds.setAttr(f"{target}.translate", 10.0, 0.0, 0.0)

        source_node = cmds.createNode("network", name="source_append_node")
        target_node = cmds.createNode("network", name="target_append_node")
        for node, value in ((source_node, 5.0), (target_node, 10.0)):
            cmds.addAttr(node, longName="baseTranslate", attributeType="double3")
            cmds.addAttr(node, longName="baseTranslateX", attributeType="double", parent="baseTranslate")
            cmds.addAttr(node, longName="baseTranslateY", attributeType="double", parent="baseTranslate")
            cmds.addAttr(node, longName="baseTranslateZ", attributeType="double", parent="baseTranslate")
            cmds.setAttr(f"{node}.baseTranslate", value, 0.0, 0.0, type="double3")

        joint_channel_values = {
            driver: {
                "translateX": om.MDoubleArray([0.0]),
                "translateY": om.MDoubleArray([0.0]),
                "translateZ": om.MDoubleArray([0.0]),
            },
            source: {
                "translateX": om.MDoubleArray([5.0]),
                "translateY": om.MDoubleArray([0.0]),
                "translateZ": om.MDoubleArray([0.0]),
            },
            target: {
                "translateX": om.MDoubleArray([10.0]),
                "translateY": om.MDoubleArray([0.0]),
                "translateZ": om.MDoubleArray([0.0]),
            },
        }
        append_info = {
            source: {
                "node": source_node,
                "source_joint": driver,
                "ratio": 1.0,
                "affect_translation": True,
                "local_append": False,
            },
            target: {
                "node": target_node,
                "source_joint": source,
                "ratio": 1.0,
                "affect_translation": True,
                "local_append": True,
            },
        }

        with patch(
            "mmd_tools.converters.vmd_append_decomposition.stable_long_dag_path",
            wraps=stable_long_dag_path,
        ) as resolve_path:
            decomposed = self.converter._decompose_append_translations_for_scene(
                joint_channel_values,
                {},
                append_info,
                n_frames=1,
            )

        self.assertAlmostEqual(decomposed[target]["translateX"][0], 10.0, places=6)
        self.assertEqual(resolve_path.call_count, len(joint_channel_values))
        self.assertCountEqual(
            [call.args[0] for call in resolve_path.call_args_list],
            joint_channel_values,
        )

        cmds.delete(driver, source, target, source_node, target_node)
