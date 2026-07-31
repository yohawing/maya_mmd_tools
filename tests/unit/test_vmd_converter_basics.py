"""VMD converter basic scene setup tests.

VmdConverter の初期状態、timeline 設定、基本的な camera/light 生成を
大きな converter regression ファイルから分けて検証する。
"""

import maya.cmds as cmds
from unittest.mock import patch

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.core.constants import ATTR_MMD_CAMERA, ATTR_MMD_LIGHT
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from tests.common.maya_test_base import MayaTestBase
from tests.common.vmd_mock import create_test_vmd_data


def _bone_frame(bone_name, frame_number, position, rotation=(0.0, 0.0, 0.0, 1.0)):
    frame = VmdBoneFrame()
    frame.bone_name = bone_name
    frame.frame_number = frame_number
    frame.position = position
    frame.rotation = rotation
    return frame


class TestVmdConverterBasics(MayaTestBase):
    """VmdConverter の基本設定と scene helper を検証する。"""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_init(self):
        """初期化のテスト"""
        self.assertEqual(self.converter.bone_name_mapping, {})
        self.assertEqual(self.converter.morph_name_mapping, {})
        self.assertEqual(self.converter.fps, 30.0)
        self.assertIsNotNone(self.converter.logger)
        self.assertEqual(len(self.converter._failed_bones), 0)
        self.assertTrue(self.converter.use_animation_layers)
        self.assertIsNone(self.converter.anim_layer)

    def test_fps_60_timeline_maps_vmd_frame_30_to_maya_time_60(self):
        """60fps import では timeline max も VMD frame 30 -> Maya time 60 にする。"""
        vmd_data = type("VmdDataStub", (), {})()
        vmd_data.bone_frames = [_bone_frame("センター", 30, (0.0, 0.0, 0.0))]
        self.converter.fps = 60.0

        self.converter._setup_timeline(vmd_data)

        self.assertEqual(cmds.playbackOptions(q=True, max=True), 60.0)

    def test_convert_with_test_vmd_data(self):
        """テスト用 VMD データで convert が timeline を設定できる。"""
        vmd_data = create_test_vmd_data()
        self.converter.set_bone_name_mapping({"センター": "center", "上半身": "upper_body", "頭": "head"})

        # This host-neutral test only exercises timeline setup.  Registered
        # sparse model conversion requires paired raw PMX/VMD bytes, which the
        # synthetic VmdData fixture intentionally does not provide.
        with patch.object(
            self.converter,
            "_compiled_registered_sparse_frames",
            return_value=(tuple(vmd_data.bone_frames), {}),
        ):
            self.converter.convert(vmd_data, target_model="model_root")

        self.assertEqual(cmds.playbackOptions(q=True, max=True), 30)

    def test_get_or_create_camera(self):
        """カメラの作成・取得テスト"""
        camera_name = self.converter._get_or_create_camera()
        self.assertIsNotNone(camera_name)
        self.assertTrue(cmds.objExists(camera_name))
        self.assertTrue(cmds.attributeQuery(ATTR_MMD_CAMERA, node=camera_name, exists=True))
        target = cmds.listConnections(f"{camera_name}.mmd_camera_target_node", source=True, destination=False)
        root = cmds.listConnections(f"{camera_name}.mmd_camera_root_node", source=True, destination=False)
        self.assertTrue(target)
        self.assertTrue(root)
        self.assertEqual(cmds.listRelatives(camera_name, parent=True)[0], root[0])
        self.assertEqual(cmds.listRelatives(target[0], parent=True)[0], root[0])
        self.assertEqual(cmds.getAttr(f"{camera_name}.rotateOrder"), 2)

        camera_name2 = self.converter._get_or_create_camera()
        self.assertEqual(camera_name, camera_name2)

    def test_get_or_create_light(self):
        """照明の作成・取得テスト"""
        light_name = self.converter._get_or_create_light()
        self.assertIsNotNone(light_name)
        self.assertTrue(cmds.objExists(light_name))
        self.assertTrue(cmds.attributeQuery(ATTR_MMD_LIGHT, node=light_name, exists=True))

        light_name2 = self.converter._get_or_create_light()
        self.assertEqual(light_name, light_name2)
