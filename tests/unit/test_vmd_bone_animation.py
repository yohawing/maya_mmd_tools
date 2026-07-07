"""VMD bone keying and quaternion conversion tests."""

import math

import maya.api.OpenMaya as om
import maya.cmds as cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_context import VmdBoneAnimationContext
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
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
        """runtime bake の local translate も bind pose からの差分だけ倍率化する。"""
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

        self.assertAlmostEqual(static_state[joint]["translateX"]["first"], 5.0, places=6)
        self.assertAlmostEqual(static_state[joint]["translateY"]["first"], 8.0, places=6)
        self.assertAlmostEqual(static_state[joint]["translateZ"]["first"], -1.0, places=6)
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
