"""VMD bone keying and quaternion conversion tests."""

import math
from unittest.mock import MagicMock

import maya.api.OpenMaya as om
import maya.cmds as cmds

from mmd_tools.converters.vmd_bone_animation import convert_bone_animation
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_context import VmdBoneAnimationContext
from mmd_tools.converters.vmd_registered_sparse import RegisteredSparseBoneFrame
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME
from tests.common.maya_test_base import MayaTestBase


def _bone_frame(bone_name, frame_number, position, rotation=(0.0, 0.0, 0.0, 1.0)):
    frame = VmdBoneFrame()
    frame.bone_name = bone_name
    frame.frame_number = frame_number
    frame.position = position
    frame.rotation = rotation
    return frame


class TestVmdBoneAnimation(MayaTestBase):
    """Bone keying and quaternion conversion tests."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_registered_semantic_rotation_drives_layer_quaternion_time(self):
        """Compiled semantic rotation uses Bezier-warped slerp on an animLayer."""
        joint = cmds.joint(name="registered_semantic_layer_joint")
        cmds.select(clear=True)
        layer = cmds.animLayer("registered_semantic_layer", override=False, weight=1.0)
        self.converter.use_animation_layers = True
        self.converter.anim_layer = layer
        self.converter.bone_name_mapping = {"回転": joint}
        self.converter.bone_index_to_joint = {0: joint}
        self.converter._bone_bind_poses["回転"] = (0.0, 0.0, 0.0)
        linear = (0.0, 0.0, 1.0, 1.0)
        nonlinear = (0.4, 0.0, 0.55, 1.0)
        semantic_start = {
            "translate_x": linear,
            "translate_y": linear,
            "translate_z": linear,
            "rotation": linear,
        }
        semantic_end = {**semantic_start, "rotation": nonlinear}
        half_sqrt = math.sqrt(0.5)
        frames = [
            RegisteredSparseBoneFrame(
                "回転",
                0,
                0,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0, 1.0),
                semantic_start,
            ),
            RegisteredSparseBoneFrame(
                "回転",
                0,
                10,
                (0.0, 0.0, 0.0),
                (-half_sqrt, 0.0, 0.0, half_sqrt),
                semantic_end,
            ),
        ]

        self.assertTrue(self.converter._convert_bone_animation(frames))

        time_curves = cmds.ls(type="animCurveTT") or []
        self.assertEqual(len(time_curves), 1)
        cmds.currentTime(5, edit=True)
        time_value = float(cmds.getAttr(f"{time_curves[0]}.output"))
        self.assertNotAlmostEqual(time_value, 5.0, places=3)

        low, high = 0.0, 1.0
        for _ in range(50):
            u = (low + high) * 0.5
            inv = 1.0 - u
            x = 3 * inv * inv * u * nonlinear[0] + 3 * inv * u * u * nonlinear[2] + u**3
            if x < 0.5:
                low = u
            else:
                high = u
        u = (low + high) * 0.5
        inv = 1.0 - u
        expected_time = 10.0 * (
            3 * inv * inv * u * nonlinear[1]
            + 3 * inv * u * u * nonlinear[3]
            + u**3
        )
        self.assertAlmostEqual(time_value, expected_time, places=5)
        for axis in "XYZ":
            curves = cmds.keyframe(
                f"{joint}.rotate{axis}", query=True, name=True
            ) or []
            self.assertEqual(len(curves), 1)
            self.assertEqual(
                cmds.rotationInterpolation(curves[0], query=True),
                "quaternionSlerp",
            )

    def test_bone_animation_context_exposes_legacy_keying_state(self):
        """Legacy bone helper state is passed through an explicit context object."""
        self.converter.bone_name_mapping["センター"] = "center_joint"
        self.converter._bone_bind_poses["センター"] = (1.0, 2.0, 3.0)
        self.converter._failed_bones.add("missing")

        context = self.converter._bone_animation_context()

        self.assertIsInstance(context, VmdBoneAnimationContext)
        self.assertIs(context.bone_name_mapping, self.converter.bone_name_mapping)
        self.assertIs(context.bone_bind_poses, self.converter._bone_bind_poses)
        self.assertIs(context.failed_bones, self.converter._failed_bones)
        self.assertEqual(context.motion_scale, self.converter.motion_scale)
        self.assertEqual(context.vmd_frame_to_maya_time(30), self.converter.vmd_frame_to_maya_time(30))

    def test_missing_bone_detail_is_debug_while_converted_aggregate_is_info(self):
        """Per-missing-bone detail is DEBUG; Converted aggregate stays INFO."""
        logger_mock = MagicMock()
        self.converter.logger = logger_mock
        self.converter.bone_name_mapping = {}
        self.converter._failed_bones = set()
        self.converter.use_animation_layers = False

        frames = [_bone_frame("存在しないボーン", 0, (1.0, 2.0, 3.0))]
        result = convert_bone_animation(self.converter._bone_animation_context(), frames)

        self.assertFalse(result)
        self.assertIn("存在しないボーン", self.converter._failed_bones)

        debug_msgs = [call[0][0] for call in logger_mock.debug.call_args_list if call[0]]
        info_msgs = [call[0][0] for call in logger_mock.info.call_args_list if call[0]]
        missing_detail = "Bone '存在しないボーン' not found"
        self.assertIn(missing_detail, debug_msgs)
        self.assertNotIn(missing_detail, info_msgs)
        self.assertTrue(
            any(
                isinstance(msg, str) and msg.startswith("Converted ") and "bone animations" in msg
                for msg in info_msgs
            ),
            "expected INFO aggregate Converted log, got %r" % (info_msgs,),
        )
        self.assertFalse(
            any(
                isinstance(msg, str) and msg.startswith("Converted ") and "bone animations" in msg
                for msg in debug_msgs
            ),
            "Converted aggregate must remain INFO, not DEBUG",
        )

    def test_legacy_bone_keyframes_use_bind_pose_without_accumulation(self):
        """レガシー VMD パスは現在フレーム値ではなく bind pose + VMD offset を key する。"""
        joint = cmds.joint(name="legacy_bind_pose_joint")
        cmds.setAttr(f"{joint}.translate", 100.0, 100.0, 100.0, type="double3")
        self.converter.use_animation_layers = False
        self.converter._bone_bind_poses["センター"] = (3.0, 4.0, 5.0)

        frames = [
            _bone_frame("センター", 0, (1.0, 2.0, 3.0)),
            _bone_frame("センター", 10, (2.0, 3.0, 4.0)),
        ]
        self.converter._set_bone_keyframes(joint, frames, "センター")

        cmds.currentTime(0, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 4.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateY"), 6.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateZ"), 2.0, places=6)

        cmds.currentTime(10, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 5.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateY"), 7.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateZ"), 1.0, places=6)

        cmds.delete(joint)

    def test_control_owned_translate_keys_motion_delta_without_bind_duplication(self):
        """Control Rig translate baseline と VMD keyer で bind 値を二重加算しない。"""
        joint = cmds.joint(name="control_owned_translate_joint")
        control = cmds.createNode("transform", name="control_owned_translate_CTRL")
        self.converter.use_animation_layers = False
        self.converter._bone_bind_poses["センター"] = (3.0, 4.0, 5.0)
        route = {
            "attr_targets": {
                attr: (control, attr)
                for attr in ("translateX", "translateY", "translateZ")
            },
            "control_owned": True,
            "control_owned_channels": (
                "translateX",
                "translateY",
                "translateZ",
            ),
        }

        self.converter._set_bone_keyframes(
            joint,
            [_bone_frame("センター", 8, (1.0, 2.0, 3.0))],
            "センター",
            route,
        )

        cmds.currentTime(8, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{control}.translateX"), 1.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{control}.translateY"), 2.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{control}.translateZ"), -3.0, places=6)
        cmds.delete(joint, control)

    def test_namespace_less_duplicate_bones_key_only_explicit_target_root(self):
        """Same bone name/index on two roots keys only the explicitly selected model."""
        root_a = cmds.group(empty=True, name="bone_model_a_root")
        root_b = cmds.group(empty=True, name="bone_model_b_root")
        cmds.select(clear=True)
        joint_a = cmds.joint(name="bone_model_a_center")
        cmds.parent(joint_a, root_a)
        cmds.select(clear=True)
        joint_b = cmds.joint(name="bone_model_b_center")
        cmds.parent(joint_b, root_b)
        for joint in (joint_a, joint_b):
            cmds.addAttr(joint, longName=ATTR_MMD_BONE_NAME, dataType="string")
            cmds.addAttr(joint, longName=ATTR_MMD_BONE_INDEX, attributeType="long")
            cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_NAME}", "センター", type="string")
            cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}", 0)

        self.converter.use_animation_layers = False
        self.converter._build_name_mappings(target_model=root_b)
        self.converter._bone_bind_poses["センター"] = (0.0, 0.0, 0.0)
        self.assertTrue(
            self.converter._convert_bone_animation(
                [_bone_frame("センター", 9, (1.0, 2.0, 3.0))]
            )
        )

        self.assertIsNone(cmds.keyframe(joint_a, query=True))
        self.assertIn(9.0, cmds.keyframe(joint_b, query=True, timeChange=True))

    def test_motion_scale_affects_bone_translate_offset_only(self):
        """motion_scale は bind pose ではなく VMD translate offset にだけ適用する。"""
        joint = cmds.joint(name="legacy_motion_scale_joint")
        self.converter.use_animation_layers = False
        self.converter.motion_scale = 2.0
        self.converter._bone_bind_poses["センター"] = (3.0, 4.0, 5.0)

        frames = [_bone_frame("センター", 12, (1.0, 2.0, 3.0))]
        self.converter._set_bone_keyframes(joint, frames, "センター")

        cmds.currentTime(12, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 5.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateY"), 8.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateZ"), -1.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), 0.0, places=6)

        cmds.delete(joint)

    def test_bone_motion_uses_motion_scale_only(self):
        self.converter.motion_scale = 1.5

        self.assertEqual(self.converter._bone_animation_context().motion_scale, 1.5)

    def test_fps_60_bone_keys_vmd_frame_30_at_maya_time_60(self):
        """60fps import では VMD frame 30 の bone key を Maya time 60 に置く。"""
        joint = cmds.joint(name="legacy_fps_60_joint")
        self.converter.use_animation_layers = False
        self.converter.fps = 60.0
        self.converter._bone_bind_poses["センター"] = (0.0, 0.0, 0.0)

        frames = [_bone_frame("センター", 30, (1.0, 2.0, 3.0))]
        self.converter._set_bone_keyframes(joint, frames, "センター")

        self.assertEqual(cmds.keyframe(joint, attribute="translateX", query=True, timeChange=True), [60.0])

        cmds.delete(joint)

    def test_motion_scale_affects_runtime_local_translate_delta_only(self):
        """runtime local values are not post-scaled after clip preparation."""
        joint = cmds.joint(name="runtime_motion_scale_joint")
        self.converter.motion_scale = 2.0
        self.converter.bone_index_to_joint = {0: joint}
        self.converter._bone_bind_poses[joint] = (3.0, 4.0, 5.0)
        channel_values = self.converter._create_runtime_joint_channel_arrays()
        static_state = self.converter._create_runtime_joint_channel_static_state()

        self.converter._append_bone_locals_to_channel_arrays(
            {0: (4.0, 6.0, 2.0, 10.0, 20.0, 30.0)},
            channel_values,
            static_state,
        )

        self.assertAlmostEqual(static_state[joint]["translateX"]["first"], 4.0, places=6)
        self.assertAlmostEqual(static_state[joint]["translateY"]["first"], 6.0, places=6)
        self.assertAlmostEqual(static_state[joint]["translateZ"]["first"], 2.0, places=6)
        self.assertAlmostEqual(static_state[joint]["rotateX"]["first"], math.radians(10.0), places=6)

        cmds.delete(joint)

    def test_convert_vmd_quat_to_joint_rotate_keeps_rest_joint_orient(self):
        """Rig live 経路では VMD identity が JO 付き REST を壊さない。"""
        joint = cmds.joint(name="legacy_joint_orient_joint")
        cmds.setAttr(f"{joint}.jointOrient", 0.0, 0.0, 45.0)
        cmds.setAttr(f"{joint}.rotateOrder", 0)

        rx, ry, rz = self.converter._convert_vmd_quat_to_joint_rotate(
            joint,
            0.0,
            0.0,
            0.0,
            1.0,
        )

        self.assertAlmostEqual(rx, 0.0, places=6)
        self.assertAlmostEqual(ry, 0.0, places=6)
        self.assertAlmostEqual(rz, 0.0, places=6)

        cmds.delete(joint)

    def test_convert_vmd_quat_to_joint_rotate_matches_no_jo_skinning_delta(self):
        """非 identity VMD 回転は JO 付き joint.rotate 空間へ共役変換する。"""
        joint = cmds.joint(name="legacy_joint_orient_motion_joint")
        cmds.setAttr(f"{joint}.jointOrient", 0.0, 45.0, 0.0)
        cmds.setAttr(f"{joint}.rotateOrder", 0)

        bind_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        q_maya = om.MEulerRotation(math.radians(90.0), 0.0, 0.0).asQuaternion()
        rx, ry, rz = self.converter._convert_vmd_quat_to_joint_rotate(
            joint,
            -q_maya.x,
            -q_maya.y,
            q_maya.z,
            q_maya.w,
        )
        cmds.setAttr(f"{joint}.rotate", rx, ry, rz, type="double3")

        actual_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        actual_skinning = bind_world.inverse() * actual_world
        expected_tfm = om.MTransformationMatrix()
        expected_tfm.setRotation(q_maya)
        expected_skinning = expected_tfm.asMatrix()

        for i in range(16):
            self.assertAlmostEqual(actual_skinning[i], expected_skinning[i], places=5)

        cmds.delete(joint)
