"""VMD existing-motion clearing and layer selection tests."""

import maya.cmds as cmds

from mmd_tools.converters.vmd_context import VmdImportStateContext
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_import_state import clear_existing_motion, record_bind_poses
from mmd_tools.core.constants import ATTR_MMD_BONE_NAME
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from tests.common.maya_test_base import MayaTestBase


def _bone_frame(bone_name, frame_number, position, rotation=(0.0, 0.0, 0.0, 1.0)):
    frame = VmdBoneFrame()
    frame.bone_name = bone_name
    frame.frame_number = frame_number
    frame.position = position
    frame.rotation = rotation
    return frame


class TestVmdMotionClear(MayaTestBase):
    """Existing VMD motion clearing tests."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def _import_state_context(self) -> VmdImportStateContext:
        return VmdImportStateContext(
            logger=self.converter.logger,
            bone_name_mapping=self.converter.bone_name_mapping,
            bone_bind_poses=self.converter._bone_bind_poses,
            morph_name_mapping=self.converter.morph_name_mapping,
            collect_append_info=lambda: {},
            iter_morph_mappings=self.converter._iter_morph_mappings,
            set_refresh_suspended=self.converter._set_vmd_import_refresh_suspended,
        )

    def test_anim_layer_selection_restore_deselects_new_vmd_layer(self):
        """VMD import 中に作られた layer を selected のまま残さない。"""
        previous_layer = cmds.animLayer("pre_vmd_selected_layer", override=False, weight=1.0)
        cmds.animLayer(previous_layer, edit=True, selected=True)
        snapshot = VmdConverter._capture_anim_layer_selection()

        vmd_layer = cmds.animLayer("VMD_Motion_restore_test", override=False, weight=1.0)
        cmds.animLayer(vmd_layer, edit=True, selected=True)

        VmdConverter._restore_anim_layer_selection(snapshot)

        self.assertTrue(cmds.animLayer(previous_layer, query=True, selected=True))
        self.assertFalse(cmds.animLayer(vmd_layer, query=True, selected=True))

    def test_clear_existing_motion_cuts_joint_morph_keys_and_deletes_vmd_layer(self):
        """VMD 再インポート前に対象 motion key と既存 VMD layer を消す。"""
        joint = cmds.joint(name="clear_existing_motion_joint")
        cmds.setKeyframe(joint, attribute="translateX", time=1, value=1.0)
        cmds.setKeyframe(joint, attribute="rotateY", time=5, value=20.0)

        mesh = cmds.polyCube(name="clear_existing_motion_mesh")[0]
        blend_shape = cmds.blendShape(mesh, name="clear_existing_motion_blendShape")[0]
        cmds.aliasAttr("smile", f"{blend_shape}.weight[0]")
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=3, value=0.75)

        layer = cmds.animLayer("VMD_Motion_clear_existing_test", override=False, weight=1.0)

        self.converter.bone_name_mapping = {"センター": joint}
        self.converter.morph_name_mapping = {"smile": (blend_shape, "weight[0]", "smile")}

        clear_existing_motion(self._import_state_context(), layer, target_namespace=None)

        self.assertIsNone(cmds.keyframe(joint, attribute="translateX", query=True, timeChange=True))
        self.assertIsNone(cmds.keyframe(joint, attribute="rotateY", query=True, timeChange=True))
        self.assertIsNone(cmds.keyframe(blend_shape, attribute="weight[0]", query=True, timeChange=True))
        self.assertFalse(cmds.objExists(layer))
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateY"), 0.0, places=4)

    def test_clear_existing_motion_restores_bind_pose_for_accurate_reimport(self):
        """cutKey 後にジョイントが rest position に戻り、後続の bind pose 記録が正確になる。"""
        joint = cmds.joint(name="clear_restore_bind_joint")
        cmds.setAttr(f"{joint}.translateX", 3.0)
        rest_tx = cmds.getAttr(f"{joint}.translateX")

        self.converter.bone_name_mapping = {"test_bone": joint}
        record_bind_poses(self._import_state_context())

        cmds.setKeyframe(joint, attribute="translateX", time=1, value=rest_tx + 5.0)
        cmds.setKeyframe(joint, attribute="rotateX", time=1, value=45.0)
        cmds.currentTime(1, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), rest_tx + 5.0, places=4)

        self.converter.morph_name_mapping = {}
        layer = cmds.animLayer("VMD_Motion_restore_test", override=False, weight=1.0)
        clear_existing_motion(self._import_state_context(), layer, target_namespace=None)

        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), 0.0, places=4)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), rest_tx, places=4)

    def test_clear_existing_motion_does_not_clear_camera_or_light_keys(self):
        """clear_existing_motion はモデル motion だけを対象にし、camera/light key は残す。"""
        camera = self.converter._get_or_create_camera()
        camera_shape = cmds.listRelatives(camera, shapes=True, type="camera")[0]
        light = self.converter._get_or_create_light()
        light_shape = cmds.listRelatives(light, shapes=True, type="directionalLight")[0]

        cmds.setKeyframe(camera, attribute="translateX", time=2, value=4.0)
        cmds.setKeyframe(camera_shape, attribute="focalLength", time=2, value=35.0)
        cmds.setKeyframe(light, attribute="rotateX", time=2, value=10.0)
        cmds.setKeyframe(light_shape, attribute="colorR", time=2, value=0.25)

        self.converter.bone_name_mapping = {}
        self.converter.morph_name_mapping = {}
        layer = cmds.animLayer("VMD_Motion_model_only_clear_test", override=False, weight=1.0)

        clear_existing_motion(self._import_state_context(), layer, target_namespace=None)

        self.assertEqual(cmds.keyframe(camera, attribute="translateX", query=True, timeChange=True), [2.0])
        self.assertEqual(cmds.keyframe(camera_shape, attribute="focalLength", query=True, timeChange=True), [2.0])
        self.assertEqual(cmds.keyframe(light, attribute="rotateX", query=True, timeChange=True), [2.0])
        self.assertEqual(cmds.keyframe(light_shape, attribute="colorR", query=True, timeChange=True), [2.0])
        self.assertFalse(cmds.objExists(layer))

    def test_convert_clear_existing_motion_replaces_previous_bone_keys(self):
        """clear ON の再 import は古いキーを残さず新しい VMD キーだけにする。"""
        joint = cmds.joint(name="clear_existing_convert_center")
        cmds.addAttr(joint, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_NAME}", "センター", type="string")
        cmds.setKeyframe(joint, attribute="translateX", time=1, value=10.0)

        vmd_data = type("VmdDataStub", (), {})()
        vmd_data.bone_frames = [_bone_frame("センター", 8, (2.0, 0.0, 0.0))]
        vmd_data.morph_frames = []
        vmd_data.camera_frames = []
        vmd_data.light_frames = []

        self.converter.use_animation_layers = False
        self.assertTrue(self.converter.convert(vmd_data, clear_existing_motion=True))

        self.assertNotIn(1.0, cmds.keyframe(joint, attribute="translateX", query=True, timeChange=True) or [])
        self.assertIn(8.0, cmds.keyframe(joint, attribute="translateX", query=True, timeChange=True) or [])
