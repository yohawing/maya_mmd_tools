"""VMD bone interpolation regression tests."""

from unittest.mock import patch

import maya.api.OpenMaya as om
import maya.cmds as cmds

from mmd_tools.converters import vmd_bezier_tangent
from mmd_tools.converters.vmd_bone_interpolation import evaluate_vmd_bezier
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.core.constants import ATTR_MMD_BONE_NAME
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider


def _bone_interp_bytes_by_channel(**overrides):
    """Build VMD bone interpolation bytes from per-channel control points."""
    default_points = (20, 20, 107, 107)
    channels = ("translate_x", "translate_y", "translate_z", "rotation")
    points_by_channel = {channel: overrides.get(channel, default_points) for channel in channels}
    data = bytearray(64)
    for index, channel in enumerate(channels):
        x1, y1, x2, y2 = points_by_channel[channel]
        data[index] = x1
        data[4 + index] = y1
        data[8 + index] = x2
        data[12 + index] = y2
    return bytes(data)


class TestVmdBoneInterpolation(MayaTestBase):
    """Bone interpolation and tangent regression tests."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        super().tearDown()
        self.fixture_provider.cleanup_temp_files()

    def test_rotation_bezier_evaluator_matches_runtime_probe(self):
        """KOTORA frame 295->304 rotation timing matches mmd-anim."""
        points = (0.0, 20.0 / 127.0, 75.0 / 127.0, 1.0)

        self.assertAlmostEqual(
            evaluate_vmd_bezier(points, 5.0 / 9.0),
            0.77281165,
            delta=1.0e-7,
        )

    def test_non_linear_rotation_bezier_keeps_sparse_rig_keys(self):
        """Rig mode favors authored-key editability over dense runtime parity."""
        joint = cmds.joint(name="rotation_bezier_joint")
        self.converter.bone_name_mapping = {"腕": joint}
        self.converter._bone_bind_poses = {"腕": (0.0, 0.0, 0.0)}

        frame0 = VmdBoneFrame()
        frame0.bone_name = "腕"
        frame0.frame_number = 0
        frame0.rotation = (0.0, 0.0, 0.0, 1.0)
        frame1 = VmdBoneFrame()
        frame1.bone_name = "腕"
        frame1.frame_number = 4
        frame1.rotation = (0.0, 0.70710678, 0.0, 0.70710678)
        frame1.interpolation = _bone_interp_bytes_by_channel(rotation=(0, 20, 75, 127))

        self.converter._set_bone_keyframes(joint, [frame0, frame1], "腕")

        keyed_times = cmds.keyframe(f"{joint}.rotateY", query=True, timeChange=True)
        self.assertEqual(keyed_times, [0.0, 4.0])

    def test_bone_bezier_tangents_use_api_on_animation_layer(self):
        """大量 model VMD の tangent 適用で cmds.keyTangent hot path に落ちない。"""
        joint = cmds.joint(name="api_tangent_center")
        self.converter.bone_name_mapping = {"センター": joint}
        self.converter._bone_bind_poses = {"センター": (0.0, 0.0, 0.0)}
        self.converter.anim_layer = cmds.animLayer("api_tangent_layer", override=False, weight=1.0)
        frame0 = VmdBoneFrame()
        frame0.bone_name = "センター"
        frame0.frame_number = 0
        frame0.position = (0.0, 0.0, 0.0)
        frame0.rotation = (0.0, 0.0, 0.0, 1.0)
        frame1 = VmdBoneFrame()
        frame1.bone_name = "センター"
        frame1.frame_number = 10
        frame1.position = (10.0, 0.0, 0.0)
        frame1.rotation = (0.0, 0.0, 0.0, 1.0)
        frame1.interpolation = _bone_interp_bytes_by_channel(translate_x=(20, 100, 100, 20))

        with patch.object(vmd_bezier_tangent.cmds, "keyTangent", side_effect=AssertionError("cmds tangent path")):
            self.converter._set_bone_keyframes(joint, [frame0, frame1], "センター")

        out_type = cmds.keyTangent(
            f"{joint}.translateX",
            query=True,
            time=(0, 0),
            outTangentType=True,
        )
        self.assertEqual(out_type, ["fixed"])

    def test_bone_interpolation_fixture_matches_mmd_anim_midframe_oracle(self):
        """Rig import 後の中間フレーム translate が mmd-anim oracle と大きくズレない。"""
        from mmd_tools.core.native.mmd_anim_runtime import (
            MmdRuntimeClip,
            MmdRuntimeInstance,
            MmdRuntimeModel,
            is_mmd_runtime_available,
        )

        if not is_mmd_runtime_available():
            self.skipTest("mmd-anim native runtime is unavailable")

        pmx_path = self.fixture_provider.get_pmx_file("mmt_test_model")
        vmd_path = self.fixture_provider.get_vmd_file("bone_interp_ease_center")
        oracle_x = self._mmd_anim_oracle_world_x(
            pmx_path,
            vmd_path,
            bone_index=2,
            frame=5.0,
            runtime_model_cls=MmdRuntimeModel,
            runtime_clip_cls=MmdRuntimeClip,
            runtime_instance_cls=MmdRuntimeInstance,
        )

        root = import_mmd_file(
            pmx_path,
            options={"setup_rig": True, "setup_bone_orientation": True},
        )
        self.assertIsNotNone(root, "PMX import failed")
        self.assertTrue(
            import_mmd_file(vmd_path, options={"target_model": root, "pmx_path": pmx_path}),
            "VMD import failed",
        )

        center_joint = self._find_joint_by_mmd_bone_name("センター")
        self.assertIsNotNone(center_joint, "センター joint が見つかりません")
        cmds.currentTime(5, edit=True)
        cmds.refresh(force=True)
        maya_x = float(cmds.xform(center_joint, query=True, worldSpace=True, translation=True)[0])

        self.assertGreater(abs(oracle_x - 5.0), 0.25, "fixture must use non-linear interpolation")
        self.assertAlmostEqual(maya_x, oracle_x, delta=0.15)

    @staticmethod
    def _find_joint_by_mmd_bone_name(bone_name: str):
        for joint in cmds.ls(type="joint") or []:
            if not cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
                continue
            if cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}") == bone_name:
                return joint
        return None

    def _mmd_anim_oracle_world_x(
        self,
        pmx_path,
        vmd_path,
        *,
        bone_index: int,
        frame: float,
        runtime_model_cls,
        runtime_clip_cls,
        runtime_instance_cls,
    ) -> float:
        """Return mmd-anim runtime world X converted to Maya coordinates."""
        with open(pmx_path, "rb") as file:
            pmx_bytes = file.read()
        with open(vmd_path, "rb") as file:
            vmd_bytes = file.read()

        model = runtime_model_cls.from_pmx_bytes(pmx_bytes)
        if model is None:
            self.skipTest("mmd-anim runtime could not create model")
        clip = None
        instance = None
        try:
            clip = runtime_clip_cls.from_vmd_bytes_for_model(model, vmd_bytes)
            if clip is None:
                self.skipTest("mmd-anim runtime could not create VMD clip")
            instance = runtime_instance_cls.for_model(model)
            if instance is None:
                self.skipTest("mmd-anim runtime could not create instance")
            if not instance.evaluate_clip_frame(clip, float(frame)):
                self.fail(f"mmd-anim runtime evaluate_clip_frame({frame}) failed")
            world_matrices = instance.get_world_matrices() or []
            if bone_index >= len(world_matrices):
                self.fail(f"mmd-anim runtime did not return bone index {bone_index}")
            maya_matrix = om.MMatrix(
                self.converter._convert_mmd_world_matrix_to_maya(list(world_matrices[bone_index]))
            )
            pos = om.MTransformationMatrix(maya_matrix).translation(om.MSpace.kWorld)
            return float(pos.x)
        finally:
            if instance is not None:
                instance.free()
            if clip is not None:
                clip.free()
            model.free()
