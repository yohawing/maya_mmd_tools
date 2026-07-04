"""VmdConverterのユニットテスト

VmdConverterクラスの基本的な機能をテスト。
Maya環境内で実行されるが、シーン操作を伴わないテストを行う。
"""

import ctypes
import json
import math
import os
import tempfile
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import maya.cmds as cmds
import maya.api.OpenMaya as om

import mmd_tools.converters.vmd_converter as vmd_converter_module
import mmd_tools.converters.vmd_camera_animation as vmd_camera_animation_module
from mmd_tools.converters.vmd_camera_animation import (
    maya_camera_eye_from_vmd_state,
    maya_camera_up_from_vmd_state,
)
from mmd_tools.core.coordinate_transform import mmd_point_to_maya
from mmd_tools.core.vmd_data.ik_show_hide_frame import VmdIKShowHideFrame
from tests.common.maya_test_base import MayaTestBase
from tests.common.vmd_mock import create_test_vmd_data
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.core.constants import ATTR_MMD_BONE_NAME
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.test_fixture_provider import TestFixtureProvider


class TestVmdConverter(MayaTestBase):
    """VmdConverterクラスのユニットテスト"""

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()
        # VmdConverterのインスタンスを作成
        self.converter = VmdConverter()

        # テストフィクスチャプロバイダーを作成
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        """テスト後のクリーンアップ"""
        super().tearDown()
        # 一時ファイルのクリーンアップ
        self.fixture_provider.cleanup_temp_files()

        # テスト用カメラとライトを削除
        import maya.cmds as cmds
        from mmd_tools.core.constants import DEFAULT_CAMERA_NAME, DEFAULT_LIGHT_NAME

        if cmds.objExists(DEFAULT_CAMERA_NAME):
            cmds.delete(DEFAULT_CAMERA_NAME)
        if cmds.objExists(f"{DEFAULT_CAMERA_NAME}_target"):
            cmds.delete(f"{DEFAULT_CAMERA_NAME}_target")
        if cmds.objExists(f"{DEFAULT_CAMERA_NAME}_up"):
            cmds.delete(f"{DEFAULT_CAMERA_NAME}_up")
        if cmds.objExists(DEFAULT_LIGHT_NAME):
            cmds.delete(DEFAULT_LIGHT_NAME)

        # アニメーションレイヤーをクリーンアップ
        anim_layers = cmds.ls(type="animLayer")
        for layer in anim_layers:
            if layer != "BaseAnimation":  # BaseAnimationレイヤーは削除しない
                try:
                    cmds.delete(layer)
                except Exception:
                    pass

    def _world_translation(self, node: str):
        return cmds.xform(node, query=True, worldSpace=True, translation=True)

    def _world_rotation_degrees(self, node: str):
        return cmds.xform(node, query=True, worldSpace=True, rotation=True)

    def _world_forward_up(self, node: str):
        matrix = om.MMatrix(cmds.getAttr(f"{node}.worldMatrix[0]"))
        forward = om.MVector(0.0, 0.0, -1.0) * matrix
        up = om.MVector(0.0, 1.0, 0.0) * matrix
        forward.normalize()
        up.normalize()
        return forward, up

    def _camera_target_node(self, camera: str) -> str:
        targets = cmds.listConnections(f"{camera}.mmd_camera_target_node", source=True, destination=False) or []
        self.assertTrue(targets)
        return targets[0]

    def _camera_root_node(self, camera: str) -> str:
        roots = cmds.listConnections(f"{camera}.mmd_camera_root_node", source=True, destination=False) or []
        self.assertTrue(roots)
        return roots[0]

    def _camera_vertical_fov(self, camera_shape: str) -> float:
        focal_length = cmds.getAttr(f"{camera_shape}.focalLength")
        aperture_mm = cmds.getAttr(f"{camera_shape}.verticalFilmAperture") * 25.4
        return math.degrees(2.0 * math.atan(aperture_mm / (2.0 * focal_length)))

    def _assert_mmd_camera_raw_attrs_absent(self, camera: str) -> None:
        for attr in (
            "mmd_camera_distance",
            "mmd_camera_viewing_angle",
            "mmd_camera_perspective",
            "mmd_camera_target_x",
            "mmd_camera_target_y",
            "mmd_camera_target_z",
            "mmd_camera_rotation_x",
            "mmd_camera_rotation_y",
            "mmd_camera_rotation_z",
        ):
            self.assertFalse(cmds.attributeQuery(attr, node=camera, exists=True), attr)

    def test_init(self):
        """初期化のテスト"""
        self.assertEqual(self.converter.bone_name_mapping, {})
        self.assertEqual(self.converter.morph_name_mapping, {})
        self.assertEqual(self.converter.fps, 30.0)
        self.assertIsNotNone(self.converter.logger)
        self.assertEqual(len(self.converter._failed_bones), 0)
        self.assertTrue(self.converter.use_animation_layers)
        self.assertIsNone(self.converter.anim_layer)

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

        self.converter._clear_existing_motion(layer, target_namespace=None)

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
        self.converter._record_bind_poses()

        cmds.setKeyframe(joint, attribute="translateX", time=1, value=rest_tx + 5.0)
        cmds.setKeyframe(joint, attribute="rotateX", time=1, value=45.0)
        cmds.currentTime(1, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), rest_tx + 5.0, places=4)

        self.converter.morph_name_mapping = {}
        layer = cmds.animLayer("VMD_Motion_restore_test", override=False, weight=1.0)
        self.converter._clear_existing_motion(layer, target_namespace=None)

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

        self.converter._clear_existing_motion(layer, target_namespace=None)

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
        vmd_data.bone_frames = [self._make_bone_frame("センター", 8, (2.0, 0.0, 0.0))]
        vmd_data.morph_frames = []
        vmd_data.camera_frames = []
        vmd_data.light_frames = []

        self.converter.use_animation_layers = False
        self.assertTrue(self.converter.convert(vmd_data, clear_existing_motion=True))

        self.assertNotIn(1.0, cmds.keyframe(joint, attribute="translateX", query=True, timeChange=True) or [])
        self.assertIn(8.0, cmds.keyframe(joint, attribute="translateX", query=True, timeChange=True) or [])

    def test_fps_60_timeline_maps_vmd_frame_30_to_maya_time_60(self):
        """60fps import では timeline max も VMD frame 30 -> Maya time 60 にする。"""
        vmd_data = type("VmdDataStub", (), {})()
        vmd_data.bone_frames = [self._make_bone_frame("センター", 30, (0.0, 0.0, 0.0))]
        self.converter.fps = 60.0

        self.converter._setup_timeline(vmd_data)

        self.assertEqual(cmds.playbackOptions(q=True, max=True), 60.0)

    def test_convert_with_test_vmd_data(self):
        """テスト用VMDデータでの変換テスト"""
        # テスト用VMDデータを作成
        vmd_data = create_test_vmd_data()

        # ボーン名マッピングを設定
        bone_mapping = {"センター": "center", "上半身": "upper_body", "頭": "head"}
        self.converter.set_bone_name_mapping(bone_mapping)

        # 変換実行（実際のMayaシーンにボーンがないためFalseを返すが、
        # エラーが発生しないことを確認）
        self.converter.convert(vmd_data)

        # フレーム数が正しく設定されていることを確認
        # (VMDデータの通常フレームは30)
        import maya.cmds as cmds

        self.assertEqual(cmds.playbackOptions(q=True, max=True), 30)

    def test_get_or_create_camera(self):
        """カメラの作成・取得テスト"""
        import maya.cmds as cmds
        from mmd_tools.core.constants import ATTR_MMD_CAMERA

        # 新規作成
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

        # 既存カメラの取得
        camera_name2 = self.converter._get_or_create_camera()
        self.assertEqual(camera_name, camera_name2)

    def test_get_or_create_light(self):
        """照明の作成・取得テスト"""
        import maya.cmds as cmds
        from mmd_tools.core.constants import ATTR_MMD_LIGHT

        # 新規作成
        light_name = self.converter._get_or_create_light()
        self.assertIsNotNone(light_name)
        self.assertTrue(cmds.objExists(light_name))
        self.assertTrue(cmds.attributeQuery(ATTR_MMD_LIGHT, node=light_name, exists=True))

        # 既存照明の取得
        light_name2 = self.converter._get_or_create_light()
        self.assertEqual(light_name, light_name2)

    def test_convert_camera_animation(self):
        """カメラアニメーション変換テスト"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        # テスト用カメラフレームを作成
        camera_frames = []
        for i in range(3):
            frame = VmdCameraFrame()
            frame.frame_number = i * 10
            frame.position = (i * 1.0, i * 2.0, i * 3.0)
            frame.rotation = (0.1 * i, 0.2 * i, 0.3 * i)
            frame.distance = 10.0 + i
            frame.viewing_angle = 30 + i * 5
            camera_frames.append(frame)

        # 変換実行
        result = self.converter._convert_camera_animation(camera_frames)
        self.assertTrue(result)

        # カメラが作成されたことを確認
        import maya.cmds as cmds
        from mmd_tools.core.constants import ATTR_MMD_CAMERA

        # カメラ名を正確に確認（変換関数が返すカメラ名をチェック）
        cameras = cmds.ls(type="camera")
        camera_found = False
        for cam in cameras:
            transform = cmds.listRelatives(cam, parent=True)
            if transform and cmds.attributeQuery(ATTR_MMD_CAMERA, node=transform[0], exists=True):
                camera_found = True
                # Sparse Rig は Target/Roll を直接編集できるノードにキーを持つ。
                target = self._camera_target_node(transform[0])
                keyframes = cmds.keyframe(f"{target}.translateX", query=True)
                self.assertIsNotNone(keyframes)
                self.assertEqual(len(keyframes), 3)
                self.assertEqual((cmds.listRelatives(transform[0], parent=True) or [None])[0], target)
                for attr in ("rotateX", "rotateY"):
                    self.assertIsNone(cmds.keyframe(f"{transform[0]}.{attr}", query=True))
                self.assertIsNone(cmds.keyframe(f"{transform[0]}.translateX", query=True))
                self.assertIsNotNone(cmds.keyframe(f"{transform[0]}.translateZ", query=True))
                self.assertIsNotNone(cmds.keyframe(f"{transform[0]}.rotateZ", query=True))
                self.assertIsNotNone(cmds.keyframe(f"{target}.rotateX", query=True))
                self.assertIsNotNone(cmds.keyframe(f"{target}.rotateY", query=True))
                self._assert_mmd_camera_raw_attrs_absent(transform[0])
                self.assertEqual(len(cmds.keyframe(f"{target}.translateX", query=True)), 3)
                root = self._camera_root_node(transform[0])
                self.assertIsNone(cmds.keyframe(f"{root}.translateX", query=True))
                focal_keys = cmds.keyframe(f"{cam}.focalLength", query=True)
                self.assertIsNotNone(focal_keys)
                self.assertFalse(cmds.listConnections(f"{transform[0]}.message", source=False, destination=True, type="expression") or [])
                break

        self.assertTrue(camera_found, "MMDカメラが作成されていません")

    def test_convert_camera_animation_via_convert(self):
        """convert() のレガシーパスが camera_frames を変換することを確認"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame = VmdCameraFrame()
        frame.frame_number = 15
        frame.position = (1.0, 2.0, 3.0)
        frame.rotation = (0.1, 0.2, 0.3)
        frame.distance = -12.0
        frame.viewing_angle = 40

        vmd_data = type(
            "FakeVmdData",
            (),
            {
                "bone_frames": [],
                "morph_frames": [],
                "camera_frames": [frame],
                "light_frames": [],
            },
        )()

        result = self.converter.convert(vmd_data)
        self.assertTrue(result)

        camera_name = self.converter._get_or_create_camera()
        cmds.currentTime(15, edit=True)
        expected_eye = maya_camera_eye_from_vmd_state(frame.position, frame.rotation, frame.distance, 1.0)
        world_translate = self._world_translation(camera_name)
        self.assertAlmostEqual(world_translate[0], expected_eye[0], places=6)
        self.assertAlmostEqual(world_translate[2], expected_eye[2], places=6)
        target_node = self._camera_target_node(camera_name)
        target_translate = self._world_translation(target_node)
        self.assertAlmostEqual(target_translate[0], 1.0, places=6)
        self.assertAlmostEqual(target_translate[2], -3.0, places=6)
        self.assertAlmostEqual((om.MVector(*target_translate) - om.MVector(*world_translate)).length(), 12.0, places=6)
        self._assert_mmd_camera_raw_attrs_absent(camera_name)

    def test_camera_transform_aims_at_mmd_target(self):
        """実 camera transform の視線が MMD camera target を向くことを確認する。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame = VmdCameraFrame()
        frame.frame_number = 0
        frame.position = (4.0, 12.0, -6.0)
        frame.rotation = (0.35, -0.25, 0.45)
        frame.distance = -30.0
        frame.viewing_angle = 45

        self.assertTrue(self.converter._convert_camera_animation([frame]))

        camera_name = self.converter._get_or_create_camera()
        cmds.currentTime(0, edit=True)
        world_translate = self._world_translation(camera_name)
        eye = om.MVector(*world_translate)
        target = om.MVector(*self._world_translation(self._camera_target_node(camera_name)))
        camera_forward, _ = self._world_forward_up(camera_name)
        target_direction = target - eye
        target_direction.normalize()

        self.assertAlmostEqual(camera_forward.x, target_direction.x, places=6)
        self.assertAlmostEqual(camera_forward.y, target_direction.y, places=6)
        self.assertAlmostEqual(camera_forward.z, target_direction.z, places=6)

    def test_mmd_camera_rig_roll_keys_camera_z_under_orbit_target(self):
        """MMD Camera Rig は target orbit + camera.rotateZ で roll を編集可能にする。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frames = []
        for frame_number, roll in ((0, 0.0), (10, math.pi / 2.0)):
            frame = VmdCameraFrame()
            frame.frame_number = frame_number
            frame.position = (0.0, 0.0, 0.0)
            frame.rotation = (0.2, 0.4, roll)
            frame.distance = -30.0
            frame.viewing_angle = 45
            frames.append(frame)

        self.assertTrue(self.converter._convert_camera_animation(frames))

        camera_name = self.converter._get_or_create_camera()
        target_node = self._camera_target_node(camera_name)
        self.assertFalse(cmds.objExists(f"{camera_name}_up"))
        self.assertEqual((cmds.listRelatives(camera_name, parent=True) or [None])[0], target_node)
        self.assertFalse(cmds.listConnections(f"{camera_name}.rotateX", source=True, type="aimConstraint") or [])

        cmds.currentTime(0, edit=True)
        eye0 = om.MVector(*self._world_translation(camera_name))
        forward0, up0 = self._world_forward_up(camera_name)

        cmds.currentTime(10, edit=True)
        eye1 = om.MVector(*self._world_translation(camera_name))
        forward1, up1 = self._world_forward_up(camera_name)

        self.assertAlmostEqual((eye1 - eye0).length(), 0.0, places=6)
        self.assertAlmostEqual(forward0 * forward1, 1.0, places=6)
        self.assertLess(abs(up0 * up1), 1e-6)
        self.assertEqual(cmds.keyframe(f"{camera_name}.rotateZ", query=True, timeChange=True), [0.0, 10.0])
        self._assert_mmd_camera_raw_attrs_absent(camera_name)

    def test_mmd_camera_rig_orbits_around_constant_target_with_yaw_pitch(self):
        """target 一定の yaw/pitch は raw target を中心に camera transform を回り込ませる。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frames = []
        for frame_number, rotation in ((0, (0.0, 0.0, 0.0)), (10, (0.25, 0.5, 0.0))):
            frame = VmdCameraFrame()
            frame.frame_number = frame_number
            frame.position = (1.0, 2.0, -3.0)
            frame.rotation = rotation
            frame.distance = -20.0
            frame.viewing_angle = 45
            frames.append(frame)

        self.assertTrue(self.converter._convert_camera_animation(frames))

        camera_name = self.converter._get_or_create_camera()
        target = om.MVector(*mmd_point_to_maya(frames[0].position, 1.0))
        positions = []
        for frame_number in (0, 10):
            cmds.currentTime(frame_number, edit=True)
            eye = om.MVector(*self._world_translation(camera_name))
            forward, _ = self._world_forward_up(camera_name)
            target_direction = target - eye
            target_direction.normalize()
            self.assertAlmostEqual(forward * target_direction, 1.0, places=6)
            self.assertAlmostEqual((eye - target).length(), 20.0, places=6)
            positions.append(eye)

        self.assertGreater((positions[1] - positions[0]).length(), 1.0)

    def test_camera_viewing_angle_drives_vertical_fov(self):
        """VMD viewing_angle は Maya camera shape の vertical FOV として反映する。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame = VmdCameraFrame()
        frame.frame_number = 0
        frame.position = (0.0, 10.0, 0.0)
        frame.rotation = (0.0, 0.0, 0.0)
        frame.distance = -45.0
        frame.viewing_angle = 60

        self.assertTrue(self.converter._convert_camera_animation([frame]))

        camera_name = self.converter._get_or_create_camera()
        camera_shape = cmds.listRelatives(camera_name, shapes=True, type="camera")[0]
        focal_length = cmds.getAttr(f"{camera_shape}.focalLength")
        aperture_mm = cmds.getAttr(f"{camera_shape}.verticalFilmAperture") * 25.4
        vertical_fov = math.degrees(2.0 * math.atan(aperture_mm / (2.0 * focal_length)))

        self.assertAlmostEqual(vertical_fov, 60.0, places=5)
        self.assertEqual(cmds.camera(camera_shape, query=True, filmFit=True), "vertical")

    def test_camera_perspective_off_drives_orthographic_width(self):
        """VMD perspective off では distance と viewing_angle から orthographicWidth を設定する。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame = VmdCameraFrame()
        frame.frame_number = 0
        frame.position = (0.0, 0.0, 0.0)
        frame.rotation = (0.0, 0.0, 0.0)
        frame.distance = -20.0
        frame.viewing_angle = 60
        frame.perspective = 1

        self.assertTrue(self.converter._convert_camera_animation([frame]))

        camera_name = self.converter._get_or_create_camera()
        camera_shape = cmds.listRelatives(camera_name, shapes=True, type="camera")[0]
        aspect = cmds.camera(camera_shape, query=True, aspectRatio=True)
        expected_width = 2.0 * abs(frame.distance) * math.tan(math.radians(frame.viewing_angle) / 2.0) * aspect

        self.assertTrue(cmds.getAttr(f"{camera_shape}.orthographic"))
        self.assertAlmostEqual(cmds.getAttr(f"{camera_shape}.orthographicWidth"), expected_width, places=5)

    @staticmethod
    def _camera_interp_bytes_by_channel(**overrides):
        """Build VMD camera interpolation bytes from per-channel control points."""
        default_points = (20, 20, 107, 107)
        channels = (
            "translate_x",
            "translate_y",
            "translate_z",
            "rotation",
            "distance",
            "viewing_angle",
        )
        points_by_channel = {channel: overrides.get(channel, default_points) for channel in channels}
        return bytes(value for channel in channels for value in points_by_channel[channel])

    def test_convert_camera_animation_applies_vmd_bezier_tangents(self):
        """camera target/viewing angle などに VMD camera 補間 tangent を適用する。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        def camera_interp(target_points):
            return self._camera_interp_bytes_by_channel(translate_x=target_points, viewing_angle=target_points)

        frame0 = VmdCameraFrame()
        frame0.frame_number = 0
        frame0.position = (0.0, 0.0, 0.0)
        frame0.rotation = (0.0, 0.0, 0.0)
        frame0.distance = 10.0
        frame0.viewing_angle = 30

        frame1 = VmdCameraFrame()
        frame1.frame_number = 10
        frame1.position = (10.0, 0.0, 0.0)
        frame1.rotation = (0.0, 0.0, 0.0)
        frame1.distance = 20.0
        frame1.viewing_angle = 60
        frame1.interpolation = camera_interp((20, 100, 100, 20))

        self.assertTrue(self.converter._convert_camera_animation([frame0, frame1]))

        camera_name = self.converter._get_or_create_camera()
        target_node = self._camera_target_node(camera_name)
        camera_shape = cmds.listRelatives(camera_name, shapes=True, type="camera")[0]
        out_angle = cmds.keyTangent(
            f"{target_node}.translateX",
            query=True,
            time=(0, 0),
            outAngle=True,
        )
        out_type = cmds.keyTangent(
            f"{target_node}.translateX",
            query=True,
            time=(0, 0),
            outTangentType=True,
        )
        fov_out_type = cmds.keyTangent(
            f"{camera_shape}.focalLength",
            query=True,
            time=(0, 0),
            outTangentType=True,
        )

        self.assertIsNotNone(out_angle)
        self.assertGreater(out_angle[0], 70.0)
        self.assertEqual(out_type, ["fixed"])
        self.assertEqual(fov_out_type, ["fixed"])

    def test_camera_rotation_bezier_tangents_use_degree_units(self):
        """rotate animCurveTA の internal radians に引きずられず VMD Bezier を再現する。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame0 = VmdCameraFrame()
        frame0.frame_number = 0
        frame0.position = (0.0, 0.0, 0.0)
        frame0.rotation = (math.radians(-2.291833), math.radians(-4.010705), 0.0)
        frame0.distance = -27.0
        frame0.viewing_angle = 45

        frame1 = VmdCameraFrame()
        frame1.frame_number = 50
        frame1.position = (0.0, 0.0, 0.0)
        frame1.rotation = (math.radians(-2.291833), math.radians(178.189923), 0.0)
        frame1.distance = -27.0
        frame1.viewing_angle = 45
        frame1.interpolation = self._camera_interp_bytes_by_channel(rotation=(74, 61, 24, 105))

        self.assertTrue(self.converter._convert_camera_animation([frame0, frame1]))

        camera_name = self.converter._get_or_create_camera()
        target_node = self._camera_target_node(camera_name)
        out_angle = cmds.keyTangent(
            f"{target_node}.rotateY",
            query=True,
            time=(0, 0),
            outAngle=True,
        )

        self.assertGreater(out_angle[0], 60.0)
        self.assertAlmostEqual(cmds.getAttr(f"{target_node}.rotateY", time=1), -0.95746, places=4)

    def test_camera_interpolation_does_not_leak_y_handle_to_x_channel(self):
        """Y-only sparse camera move keeps constant X/distance stable even with an animLayer."""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame0 = VmdCameraFrame()
        frame0.frame_number = 0
        frame0.position = (-0.15, 0.0, 0.0)
        frame0.rotation = (0.0, 0.0, 0.0)
        frame0.distance = -31.0
        frame0.viewing_angle = 45

        frame1 = VmdCameraFrame()
        frame1.frame_number = 30
        frame1.position = (-0.15, 20.0, 0.0)
        frame1.rotation = (0.0, 0.0, 0.0)
        frame1.distance = -31.0
        frame1.viewing_angle = 45
        frame1.interpolation = self._camera_interp_bytes_by_channel(
            translate_y=(5, 120, 122, 7),
        )

        self.converter.anim_layer = cmds.animLayer("camera_sparse_layer", override=False, weight=1.0)
        self.assertTrue(self.converter._convert_camera_animation([frame0, frame1]))

        camera_name = self.converter._get_or_create_camera()
        target_node = self._camera_target_node(camera_name)
        x_out_type = cmds.keyTangent(
            f"{target_node}.translateX",
            query=True,
            time=(0, 0),
            outTangentType=True,
        )
        y_out_type = cmds.keyTangent(
            f"{target_node}.translateY",
            query=True,
            time=(0, 0),
            outTangentType=True,
        )

        cmds.currentTime(24, edit=True)

        self.assertEqual(x_out_type, ["linear"])
        self.assertEqual(y_out_type, ["fixed"])
        eye = om.MVector(*self._world_translation(camera_name))
        target = om.MVector(*self._world_translation(target_node))
        self.assertAlmostEqual(target.x, -0.15, places=6)
        self.assertAlmostEqual((target - eye).length(), 31.0, places=5)
        self.assertGreater(target.y, 0.0)

    def test_sparse_camera_uses_editable_orbit_rig_without_dense_keys(self):
        """Sparse camera は dense key なしで target orbit / distance / roll の編集用 rig を評価する。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        def camera_interp(points):
            return self._camera_interp_bytes_by_channel(
                translate_x=points,
                translate_y=points,
                translate_z=points,
                rotation=points,
                distance=points,
                viewing_angle=points,
            )

        frame0 = VmdCameraFrame()
        frame0.frame_number = 0
        frame0.position = (0.0, 10.0, 0.0)
        frame0.rotation = (0.0, 0.0, 0.0)
        frame0.distance = -5.0
        frame0.viewing_angle = 30

        frame1 = VmdCameraFrame()
        frame1.frame_number = 10
        frame1.position = (20.0, 30.0, -10.0)
        frame1.rotation = (0.5, -0.4, 0.3)
        frame1.distance = -60.0
        frame1.viewing_angle = 70
        frame1.perspective = 1
        frame1.interpolation = camera_interp((5, 120, 122, 7))

        stale_camera = self.converter._get_or_create_camera()
        stale_shape = cmds.listRelatives(stale_camera, shapes=True, type="camera")[0]
        cmds.setKeyframe(stale_camera, attribute="translateX", time=5, value=999.0)
        cmds.setKeyframe(stale_camera, attribute="rotateY", time=5, value=45.0)
        cmds.setKeyframe(stale_shape, attribute="focalLength", time=5, value=200.0)
        stale_layer = cmds.animLayer("stale_camera_output_layer", override=False, weight=1.0)
        cmds.animLayer(stale_layer, edit=True, attribute=f"{stale_camera}.translateZ")
        cmds.setKeyframe(stale_camera, attribute="translateZ", time=5, value=333.0, animLayer=stale_layer)
        external_expression = cmds.expression(name="externalCameraExpression", string="", alwaysEvaluate=False)
        cmds.addAttr(external_expression, longName="external_camera_owner", attributeType="message")
        cmds.connectAttr(f"{stale_camera}.message", f"{external_expression}.external_camera_owner")

        self.converter.use_animation_layers = False
        self.assertTrue(self.converter._convert_camera_animation([frame0, frame1]))

        camera_name = self.converter._get_or_create_camera()
        cmds.currentTime(5, edit=True)
        target_node = cmds.listConnections(f"{camera_name}.mmd_camera_target_node", source=True, destination=False)[0]

        world_translate = self._world_translation(camera_name)
        target_translate = self._world_translation(target_node)
        forward, _ = self._world_forward_up(camera_name)
        target_direction = om.MVector(*target_translate) - om.MVector(*world_translate)
        target_direction.normalize()

        self.assertAlmostEqual(forward * target_direction, 1.0, places=5)
        self.assertIsNone(cmds.keyframe(f"{camera_name}.translateX", query=True))
        self.assertIsNotNone(cmds.keyframe(f"{camera_name}.translateZ", query=True))
        self.assertIsNotNone(cmds.keyframe(f"{camera_name}.rotateZ", query=True))
        self.assertIsNone(cmds.keyframe(f"{camera_name}.rotateY", query=True))
        self.assertIsNotNone(cmds.keyframe(f"{target_node}.rotateX", query=True))
        self.assertIsNotNone(cmds.keyframe(f"{target_node}.rotateY", query=True))
        self.assertIsNotNone(cmds.keyframe(f"{stale_shape}.focalLength", query=True))
        self.assertFalse(cmds.listConnections(f"{camera_name}.rotateY", source=True, destination=False) or [])
        self.assertNotIn(f"{camera_name}.translateZ", cmds.animLayer(stale_layer, query=True, attribute=True) or [])
        self.assertTrue(cmds.objExists(external_expression))
        self.assertEqual(cmds.keyframe(f"{target_node}.translateX", query=True, timeChange=True), [0.0, 10.0])
        self._assert_mmd_camera_raw_attrs_absent(camera_name)
        cmds.currentTime(0, edit=True)
        self.assertFalse(cmds.getAttr(f"{stale_shape}.orthographic"))
        self.assertAlmostEqual(
            cmds.getAttr(f"{stale_shape}.focalLength"),
            vmd_camera_animation_module.viewing_angle_to_focal_length(stale_shape, frame0.viewing_angle),
            places=5,
        )
        cmds.currentTime(10, edit=True)
        self.assertTrue(cmds.getAttr(f"{stale_shape}.orthographic"))
        self.assertAlmostEqual(
            cmds.getAttr(f"{stale_shape}.focalLength"),
            vmd_camera_animation_module.viewing_angle_to_focal_length(stale_shape, frame1.viewing_angle),
            places=5,
        )

        renamed_camera = cmds.rename(camera_name, "renamed_mmd_camera")
        cmds.currentTime(5, edit=True)
        renamed_world_translate = self._world_translation(renamed_camera)
        for actual, expected in zip(renamed_world_translate, world_translate):
            self.assertAlmostEqual(actual, expected, places=5)

    def test_sparse_camera_animation_bypasses_anim_layer_for_editable_rig_keys(self):
        """Sparse camera の camera/target/shape keys は animLayer 差分ではなく絶対値 curve にする。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frames = []
        for frame_number, pos_x, distance in ((0, 1.0, 10.0), (12, 4.0, 16.0)):
            frame = VmdCameraFrame()
            frame.frame_number = frame_number
            frame.position = (pos_x, 2.0, 3.0)
            frame.rotation = (0.1, 0.2, 0.3)
            frame.distance = distance
            frame.viewing_angle = 40
            frames.append(frame)

        self.converter.anim_layer = cmds.animLayer("camera_batch_layer", override=False, weight=1.0)

        with patch.object(
            self.converter,
            "_batch_key_scalar_channels",
            wraps=self.converter._batch_key_scalar_channels,
        ) as batch_key:
            self.assertTrue(self.converter._convert_camera_animation(frames))

        camera_name = self.converter._get_or_create_camera()
        camera_shape = cmds.listRelatives(camera_name, shapes=True, type="camera")[0]
        camera_target = cmds.listConnections(f"{camera_name}.mmd_camera_target_node", source=True, destination=False)[0]
        batch_nodes = [call.args[0] for call in batch_key.call_args_list]
        self.assertIn(camera_name, batch_nodes)
        self.assertIn(camera_target, batch_nodes)
        self.assertIn(camera_shape, batch_nodes)

        layer_attrs = cmds.animLayer(self.converter.anim_layer, query=True, attribute=True) or []
        self.assertNotIn(f"{camera_name}.translateX", layer_attrs)
        self.assertNotIn(f"{camera_name}.rotateX", layer_attrs)
        self.assertNotIn(f"{camera_name}.rotateY", layer_attrs)
        self.assertNotIn(f"{camera_name}.rotateZ", layer_attrs)
        self.assertNotIn(f"{camera_target}.translateX", layer_attrs)
        self.assertNotIn(f"{camera_shape}.focalLength", layer_attrs)
        self._assert_mmd_camera_raw_attrs_absent(camera_name)

        cmds.currentTime(12, edit=True)
        expected_eye = maya_camera_eye_from_vmd_state(frames[-1].position, frames[-1].rotation, frames[-1].distance, 1.0)
        world_translate = self._world_translation(camera_name)
        self.assertAlmostEqual(world_translate[0], expected_eye[0], places=6)
        self.assertIsNone(cmds.keyframe(f"{camera_name}.rotateY", query=True, time=(12, 12)))
        self.assertIsNotNone(cmds.keyframe(f"{camera_name}.rotateZ", query=True, time=(12, 12)))
        self.assertIsNotNone(cmds.keyframe(f"{camera_target}.translateX", query=True, time=(12, 12)))
        target_translate = self._world_translation(camera_target)
        self.assertAlmostEqual(target_translate[0], 4.0, places=6)
        self.assertAlmostEqual((om.MVector(*target_translate) - om.MVector(*world_translate)).length(), 16.0, places=6)

    def test_motion_scale_affects_camera_translate_and_distance_only(self):
        """motion_scale は camera の位置と距離だけに適用する。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame = VmdCameraFrame()
        frame.frame_number = 7
        frame.position = (1.0, 2.0, 3.0)
        frame.rotation = (0.1, 0.2, 0.3)
        frame.distance = -12.0
        frame.viewing_angle = 40
        frame.perspective = 0

        self.converter.motion_scale = 2.0
        result = self.converter._convert_camera_animation([frame])

        self.assertTrue(result)
        camera_name = self.converter._get_or_create_camera()
        cmds.currentTime(7, edit=True)
        expected_eye = maya_camera_eye_from_vmd_state(frame.position, frame.rotation, frame.distance, 2.0)
        world_translate = self._world_translation(camera_name)
        self.assertAlmostEqual(world_translate[0], expected_eye[0], places=6)
        self.assertAlmostEqual(world_translate[1], expected_eye[1], places=6)
        self.assertAlmostEqual(world_translate[2], expected_eye[2], places=6)
        target_node = self._camera_target_node(camera_name)
        target_translate = self._world_translation(target_node)
        self.assertAlmostEqual(target_translate[0], 2.0, places=6)
        self.assertAlmostEqual(target_translate[1], 4.0, places=6)
        self.assertAlmostEqual(target_translate[2], -6.0, places=6)
        self.assertAlmostEqual((om.MVector(*target_translate) - om.MVector(*world_translate)).length(), 24.0, places=6)
        camera_forward, camera_up = self._world_forward_up(camera_name)
        maya_target = om.MVector(*target_translate)
        target_direction = maya_target - om.MVector(*world_translate)
        expected_up = om.MVector(*maya_camera_up_from_vmd_state(frame.rotation))
        target_direction.normalize()
        expected_up.normalize()
        self.assertAlmostEqual(camera_forward * target_direction, 1.0, places=6)
        self.assertAlmostEqual(camera_up * expected_up, 1.0, places=6)
        camera_shape = cmds.listRelatives(camera_name, shapes=True, type="camera")[0]
        self.assertAlmostEqual(self._camera_vertical_fov(camera_shape), 40.0, places=5)
        self._assert_mmd_camera_raw_attrs_absent(camera_name)

    def test_camera_distance_offsets_actual_maya_camera_eye(self):
        """VMD camera の distance は注視点ではなく実カメラ位置へ反映する。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame = VmdCameraFrame()
        frame.frame_number = 0
        frame.position = (1.0, 2.0, 3.0)
        frame.rotation = (0.0, 0.0, 0.0)
        frame.distance = -10.0
        frame.viewing_angle = 45

        self.assertTrue(self.converter._convert_camera_animation([frame]))

        camera_name = self.converter._get_or_create_camera()
        cmds.currentTime(0, edit=True)
        world_translate = self._world_translation(camera_name)
        self.assertAlmostEqual(world_translate[0], 1.0, places=6)
        self.assertAlmostEqual(world_translate[1], 2.0, places=6)
        self.assertAlmostEqual(world_translate[2], 7.0, places=6)
        target_translate = self._world_translation(self._camera_target_node(camera_name))
        self.assertAlmostEqual(target_translate[2], -3.0, places=6)

    def test_runtime_camera_sampling_dense_keys_maya_frames(self):
        """VMD bytes がある場合は mmd-anim camera sampler の補間済み値を frame ごとに key する。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame0 = VmdCameraFrame()
        frame0.frame_number = 0
        frame0.position = (0.0, 0.0, 0.0)
        frame0.rotation = (0.0, 0.0, 0.0)
        frame0.distance = -10.0
        frame0.viewing_angle = 30

        frame1 = VmdCameraFrame()
        frame1.frame_number = 2
        frame1.position = (2.0, 0.0, 0.0)
        frame1.rotation = (0.0, 0.0, 0.0)
        frame1.distance = -20.0
        frame1.viewing_angle = 40

        samples = [
            {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "distance": -10.0, "fov": 30.0, "perspective": True},
            {"position": [1.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "distance": -15.0, "fov": 35.0, "perspective": True},
            {"position": [2.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "distance": -20.0, "fov": 40.0, "perspective": False},
        ]

        with patch.object(vmd_camera_animation_module, "sample_vmd_camera_frames", return_value=samples) as sampler:
            self.assertTrue(self.converter._convert_camera_animation([frame0, frame1], vmd_bytes=b"vmd"))

        sampler.assert_called_once_with(b"vmd", 0.0, 1.0, 3)
        camera_name = self.converter._get_or_create_camera()
        self.assertEqual(cmds.keyframe(f"{camera_name}.translateX", query=True, timeChange=True), [0.0, 1.0, 2.0])
        cmds.currentTime(1, edit=True)
        world_translate = self._world_translation(camera_name)
        self.assertAlmostEqual(world_translate[0], 1.0, places=6)
        self.assertAlmostEqual(world_translate[2], 15.0, places=6)
        camera_shape = cmds.listRelatives(camera_name, shapes=True, type="camera")[0]
        self.assertAlmostEqual(self._camera_vertical_fov(camera_shape), 35.0, places=5)

    def test_fps_60_camera_keys_vmd_frame_30_at_maya_time_60(self):
        """60fps import では VMD frame 30 の camera key を Maya time 60 に置く。"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        frame = VmdCameraFrame()
        frame.frame_number = 30
        frame.position = (1.0, 2.0, 3.0)
        frame.rotation = (0.1, 0.2, 0.3)
        frame.distance = 12.0
        frame.viewing_angle = 40
        frame.perspective = 0

        self.converter.fps = 60.0
        self.converter._convert_camera_animation([frame])

        camera_name = self.converter._get_or_create_camera()
        target_node = self._camera_target_node(camera_name)
        self.assertEqual(cmds.keyframe(f"{target_node}.translateX", query=True, timeChange=True), [60.0])
        self.assertEqual(cmds.keyframe(f"{target_node}.rotateX", query=True, timeChange=True), [60.0])
        self.assertEqual(cmds.keyframe(f"{camera_name}.rotateZ", query=True, timeChange=True), [60.0])
        self.assertEqual(cmds.keyframe(f"{camera_name}.translateZ", query=True, timeChange=True), [60.0])

    @staticmethod
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

    def test_bone_bezier_tangents_use_api_on_animation_layer(self):
        """大量 model VMD の tangent 適用で cmds.keyTangent hot path に落ちない。"""
        from mmd_tools.converters import vmd_bezier_tangent
        from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame

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
        frame1.interpolation = self._bone_interp_bytes_by_channel(translate_x=(20, 100, 100, 20))

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

    def test_runtime_bake_infrastructure(self):
        """Phase 1 runtime bake のインフラテスト (native なし環境でも安全)"""
        vmd_data = create_test_vmd_data()
        self.converter.set_bone_name_mapping({"センター": "center"})

        # 新パラメータを受け付ける
        res = self.converter.convert(vmd_data, vmd_bytes=b"dummy", pmx_bytes=None, pmx_path=None)
        self.assertIsInstance(res, bool)

        # should_use はデータ不足で False
        self.assertFalse(
            self.converter._should_use_mmd_runtime_bake(b"vmd", None, "/nonexistent.pmx")
        )

        # runtime bake インフラの確認のみ (キーフレーム検証は別テストに依存しないよう削除)
        # ここでは convert が例外なく完了し、should_use が正しく動くことを確認
        pass  # 追加の検証は test_convert_light_animation 等で行う

    def test_runtime_bake_uses_batch_evaluation_when_available(self):
        """batch ABI がある runtime では per-frame 評価へ落ちずに cache を構築する。"""
        class Frame:
            frame_number = 2

        class VmdDataLike:
            bone_frames = [Frame()]
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        class FakeClip:
            @classmethod
            def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
                return cls()

            def free(self):
                pass

        class BatchResult:
            frame_count = 3
            bone_count = 0
            morph_count = 0
            world_matrices = (ctypes.c_float * 0)()
            morph_weights = (ctypes.c_float * 0)()

        class FakeInstance:
            last = None

            def __init__(self):
                self.batch_calls = []
                self.per_frame_calls = []

            @classmethod
            def for_model(cls, _model):
                cls.last = cls()
                return cls.last

            def evaluate_clip_frame_batch(self, _clip, start_frame, frame_step, frame_count, *, worker_count=0):
                self.batch_calls.append((start_frame, frame_step, frame_count, worker_count))
                return BatchResult

            def evaluate_clip_frame(self, _clip, frame):
                self.per_frame_calls.append(frame)
                return False

            def free(self):
                pass

        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        self.assertEqual(FakeInstance.last.batch_calls, [(0.0, 1.0, 3, 0)])
        self.assertEqual(FakeInstance.last.per_frame_calls, [])

    def test_runtime_bake_fps_60_batch_samples_target_maya_frames(self):
        """60fps runtime bake は Maya output frame ごとに 0.5 VMD frame step で評価する。"""
        class Frame:
            frame_number = 100

        class VmdDataLike:
            bone_frames = [Frame()]
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        class FakeClip:
            @classmethod
            def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
                return cls()

            def free(self):
                pass

        class BatchResult:
            bone_count = 0
            morph_count = 0
            world_matrices = (ctypes.c_float * 0)()
            morph_weights = (ctypes.c_float * 0)()

            def __init__(self, frame_count):
                self.frame_count = frame_count

        class FakeInstance:
            last = None

            def __init__(self):
                self.batch_calls = []

            @classmethod
            def for_model(cls, _model):
                cls.last = cls()
                return cls.last

            def evaluate_clip_frame_batch(self, _clip, start_frame, frame_step, frame_count, *, worker_count=0):
                self.batch_calls.append((start_frame, frame_step, frame_count, worker_count))
                return BatchResult(frame_count)

            def free(self):
                pass

        apply_calls = []

        def capture_apply(_joint_values, _joint_static, _bake_times, baked_frames, morph_cache, _pmx_morph_names):
            apply_calls.append((list(baked_frames), list(morph_cache)))

        undo_calls = []

        def fake_undo_info(**kwargs):
            undo_calls.append(kwargs)
            if kwargs == {"q": True, "state": True}:
                return True
            return None

        self.converter.fps = 60.0
        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            self.converter,
            "_apply_runtime_channel_arrays_to_scene",
            side_effect=capture_apply,
        ), patch.object(
            vmd_converter_module.cmds,
            "undoInfo",
            side_effect=fake_undo_info,
        ):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        self.assertEqual(FakeInstance.last.batch_calls, [(0.0, 0.5, 201, 0)])
        self.assertEqual(apply_calls[0][0][0], 0.0)
        self.assertEqual(apply_calls[0][0][-1], 200.0)
        self.assertEqual(len(apply_calls[0][0]), 201)
        self.assertEqual(
            undo_calls,
            [
                {"q": True, "state": True},
                {"stateWithoutFlush": False},
                {"stateWithoutFlush": True},
            ],
        )

    def test_runtime_bake_uses_clip_frame_range_when_python_vmd_is_empty(self):
        """Python VMD parser が空でも runtime clip の frame range で bake 範囲を決める。"""
        class VmdDataLike:
            bone_frames = []
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        class FakeClip:
            @classmethod
            def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
                return cls()

            def frame_range(self):
                return (2, 4)

            def free(self):
                pass

        class BatchResult:
            bone_count = 0
            morph_count = 0
            world_matrices = (ctypes.c_float * 0)()
            morph_weights = (ctypes.c_float * 0)()

            def __init__(self, frame_count):
                self.frame_count = frame_count

        class FakeInstance:
            last = None

            def __init__(self):
                self.batch_calls = []

            @classmethod
            def for_model(cls, _model):
                cls.last = cls()
                return cls.last

            def evaluate_clip_frame_batch(self, _clip, start_frame, frame_step, frame_count, *, worker_count=0):
                self.batch_calls.append((start_frame, frame_step, frame_count, worker_count))
                return BatchResult(frame_count)

            def free(self):
                pass

        apply_calls = []

        def capture_apply(_joint_values, _joint_static, _bake_times, baked_frames, _morph_cache, _pmx_morph_names):
            apply_calls.append(list(baked_frames))

        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            self.converter,
            "_apply_runtime_channel_arrays_to_scene",
            side_effect=capture_apply,
        ):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        self.assertEqual(FakeInstance.last.batch_calls, [(2.0, 1.0, 3, 0)])
        self.assertEqual(apply_calls[0], [2.0, 3.0, 4.0])

    def test_runtime_bake_fps_60_fallback_evaluates_fractional_vmd_frames(self):
        """per-frame ABI でも Maya output frame から逆算した fractional VMD frame を評価する。"""
        class Frame:
            frame_number = 2

        class VmdDataLike:
            bone_frames = [Frame()]
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        class FakeClip:
            @classmethod
            def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
                return cls()

            def free(self):
                pass

        class FakeInstance:
            last = None

            def __init__(self):
                self.per_frame_calls = []

            @classmethod
            def for_model(cls, _model):
                cls.last = cls()
                return cls.last

            def evaluate_clip_frame_batch(self, *_args, **_kwargs):
                return None

            def evaluate_clip_frame(self, _clip, frame):
                self.per_frame_calls.append(frame)
                return True

            def get_world_matrices(self):
                return []

            def get_morph_weights(self):
                return []

            def free(self):
                pass

        apply_calls = []

        def capture_apply(_joint_values, _joint_static, _bake_times, baked_frames, _morph_cache, _pmx_morph_names):
            apply_calls.append(list(baked_frames))

        self.converter.fps = 60.0
        self.converter.bone_index_to_joint = {}
        self.converter.bone_name_to_index = {}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            self.converter,
            "_apply_runtime_channel_arrays_to_scene",
            side_effect=capture_apply,
        ):
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        self.assertEqual(FakeInstance.last.per_frame_calls, [0.0, 0.5, 1.0, 1.5, 2.0])
        self.assertEqual(apply_calls[0], [0.0, 1.0, 2.0, 3.0, 4.0])

    def test_runtime_bake_with_joint_map_uses_channel_cache_pipeline(self):
        """通常 PMX runtime bake は joint map があっても direct world bake に入らない。"""
        class Frame:
            frame_number = 2

        class VmdDataLike:
            bone_frames = [Frame()]
            morph_frames = []
            camera_frames = []
            light_frames = []

        class FakeModel:
            @classmethod
            def from_pmx_bytes(cls, _pmx_bytes):
                return cls()

            def free(self):
                pass

        class FakeClip:
            @classmethod
            def from_vmd_bytes_for_model(cls, _model, _vmd_bytes):
                return cls()

            def free(self):
                pass

        class FakeInstance:
            @classmethod
            def for_model(cls, _model):
                return cls()

            def evaluate_clip_frame(self, *_args, **_kwargs):
                raise AssertionError("direct world bake should not evaluate frames here")

            def get_world_matrices(self):
                raise AssertionError("direct world bake should not read world matrices")

            def free(self):
                pass

        runtime_cache = SimpleNamespace(
            baked_frames=[0.0, 1.0, 2.0, 3.0, 4.0],
            bake_times=object(),
            joint_channel_values={"runtime_cache_joint": {}},
            joint_channel_static={"runtime_cache_joint": {}},
            morph_cache=[(0.0, [])],
            batch_mode=True,
            eval_elapsed=0.01,
            eval_copy_elapsed=0.0,
            batch_unpack_elapsed=0.0,
            local_elapsed=0.0,
            append_elapsed=0.0,
        )

        self.converter.motion_scale = 2.0
        self.converter.bone_index_to_joint = {0: "runtime_cache_joint"}
        self.converter.bone_name_to_index = {"センター": 0}
        with patch.object(vmd_converter_module, "MmdRuntimeModel", FakeModel), patch.object(
            vmd_converter_module,
            "MmdRuntimeClip",
            FakeClip,
        ), patch.object(vmd_converter_module, "MmdRuntimeInstance", FakeInstance), patch.object(
            self.converter,
            "_disable_mmd_rig_constraints_for_runtime_bake",
        ), patch.object(
            self.converter,
            "_restore_joints_to_bind_pose_for_runtime_bake",
        ), patch.object(
            self.converter,
            "_build_runtime_bind_world_maps",
        ) as build_bind_maps, patch.object(
            self.converter,
            "_bake_bone_poses_from_world_matrices",
            side_effect=AssertionError("direct world bake helper should not be called"),
        ) as direct_bake, patch.object(
            vmd_converter_module,
            "collect_runtime_bake_cache",
            return_value=runtime_cache,
        ) as collect_cache, patch.object(
            vmd_converter_module,
            "apply_runtime_channel_arrays_to_scene_with_undo_disabled",
        ) as apply_cache:
            result = self.converter._convert_using_mmd_runtime(
                VmdDataLike(),
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                pmx_path="",
            )

        self.assertTrue(result)
        build_bind_maps.assert_called_once_with()
        direct_bake.assert_not_called()
        collect_cache.assert_called_once()
        self.assertEqual(collect_cache.call_args.args[0], self.converter)
        self.assertEqual(collect_cache.call_args.args[3], [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
        apply_cache.assert_called_once_with(
            self.converter,
            runtime_cache.joint_channel_values,
            runtime_cache.joint_channel_static,
            runtime_cache.bake_times,
            runtime_cache.baked_frames,
            runtime_cache.morph_cache,
            [],
        )

    def test_should_use_mmd_runtime_bake_accepts_bake_pmx_rejects_pmd(self):
        """Bake mode は PMX 入力で runtime bake を使い、PMD 入力では無効"""
        with tempfile.TemporaryDirectory() as temp_dir:
            pmx_path = os.path.join(temp_dir, "model.pmx")
            pmd_path = os.path.join(temp_dir, "model.pmd")
            open(pmx_path, "wb").close()
            open(pmd_path, "wb").close()

            with patch.object(vmd_converter_module, "HAS_MMD_RUNTIME", True), patch.object(
                vmd_converter_module,
                "is_mmd_runtime_available",
                return_value=True,
            ):
                self.assertTrue(
                    self.converter._should_use_mmd_runtime_bake(
                        vmd_bytes=b"vmd",
                        pmx_bytes=None,
                        pmx_path=pmx_path,
                        bake_mode=True,
                    )
                )
                self.assertFalse(
                    self.converter._should_use_mmd_runtime_bake(
                        vmd_bytes=b"vmd",
                        pmx_bytes=None,
                        pmx_path=pmd_path,
                        bake_mode=True,
                    )
                )
                self.assertTrue(
                    self.converter._should_use_mmd_runtime_bake(
                        vmd_bytes=b"vmd",
                        pmx_bytes=b"pmx",
                        pmx_path=pmd_path,
                        bake_mode=True,
                    )
                )

    def test_live_rig_target_uses_sparse_vmd_path(self):
        """Rig mode でも VMD import は runtime dense bake に逃げない"""
        joint = cmds.joint(name="runtime_live_rig_target_joint")
        ik_node = cmds.createNode("mmdCcdIk", name="runtime_live_rig_ik")
        cmds.connectAttr(f"{ik_node}.outputRotate[0]", f"{joint}.rotate", force=True)
        self.converter.bone_name_mapping = {"左足ＩＫ": joint}

        with tempfile.TemporaryDirectory() as temp_dir:
            pmx_path = os.path.join(temp_dir, "model.pmx")
            open(pmx_path, "wb").close()

            with patch.object(vmd_converter_module, "HAS_MMD_RUNTIME", True), patch.object(
                vmd_converter_module,
                "is_mmd_runtime_available",
                return_value=True,
            ):
                self.assertFalse(
                    self.converter._should_use_mmd_runtime_bake(
                        vmd_bytes=b"vmd",
                        pmx_bytes=None,
                        pmx_path=pmx_path,
                        live_rig_target=True,
                    )
                )

        cmds.delete(ik_node, joint)

    def test_mmd_ik_passthrough_keys_chain_bone_slot(self):
        """runtime live apply 中は mmdCcdIk output link の入力 slot へ final rotation を焼く"""
        joint = cmds.joint(name="runtime_live_toe_link")
        ik_node = cmds.createNode("mmdCcdIk", name="runtime_live_toe_ik")
        chain_json = {
            "bones": [{"rest_position": [0, 0, 0], "parent_slot": -1} for _ in range(4)],
            "controllerBoneSlot": -1,
            "targetBoneSlot": 0,
            "links": [{"bone_slot": 3}],
            "iterationCount": 1,
            "limitAngle": 0.1,
        }
        cmds.setAttr(f"{ik_node}.chainJson", json.dumps(chain_json), type="string")
        cmds.connectAttr(f"{ik_node}.outputRotate[0]", f"{joint}.rotate", force=True)

        info = self.converter._collect_mmd_ik_passthrough_info()[joint]
        self.assertEqual(info["link_index"], 0)
        self.assertEqual(info["input_slot"], 3)

        times = om.MTimeArray()
        frames = [0, 5]
        for frame in frames:
            times.append(om.MTime(float(frame), om.MTime.uiUnit()))
        channels = {
            "rotateX": om.MDoubleArray([math.radians(10.0), math.radians(20.0)]),
            "rotateY": om.MDoubleArray([0.0, 0.0]),
            "rotateZ": om.MDoubleArray([0.0, 0.0]),
        }
        keyed = self.converter._key_mmd_ik_passthrough_rotation(info, channels, {}, times, frames)

        self.assertEqual(keyed, 4)
        self.assertFalse(cmds.getAttr(f"{ik_node}.enabled"))
        self.assertEqual(
            cmds.keyframe(
                f"{ik_node}.inputRotate[3].inputRotateElementX",
                query=True,
                time=(5, 5),
                valueChange=True,
            ),
            [20.0],
        )
        self.assertIsNone(cmds.keyframe(f"{ik_node}.inputRotate[0].inputRotateElementX", query=True))

        cmds.delete(ik_node, joint)

    def test_ik_link_input_rotate_stores_correct_radian_values(self):
        """Rig+JO の IK link pre-rotation が solver.inputRotate に正しい角度単位で保存される"""
        pmx_path = self.fixture_provider.get_pmx_file("mmt_test_model")
        vmd_path = self.fixture_provider.get_vmd_file("mmt_test_model_test_motion")

        root = import_mmd_file(
            pmx_path,
            options={"setup_rig": True, "setup_bone_orientation": True},
        )
        self.assertIsNotNone(root, "PMX import failed")
        visual_controller_joints = [
            joint for joint in (cmds.ls(type="joint") or [])
            if cmds.attributeQuery("mmd_ik_controller_visual", node=joint, exists=True)
            and cmds.getAttr(f"{joint}.mmd_ik_controller_visual")
        ]
        self.assertGreater(len(visual_controller_joints), 0, "IK controller visual が作成されていません")
        self.assertTrue(
            any(cmds.listRelatives(joint, shapes=True, type="nurbsCurve") for joint in visual_controller_joints),
            "IK controller visual の NURBS curve shape が見つかりません",
        )
        self.assertTrue(
            import_mmd_file(vmd_path, options={"target_model": root, "pmx_path": pmx_path}),
            "VMD import failed",
        )

        solver_node = None
        for node in cmds.ls(type="mmdCcdIk") or []:
            if not cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
                continue
            ik_name = cmds.getAttr(f"{node}.mmd_ik_bone_name") or ""
            if "左足" in ik_name and "つま先" not in ik_name:
                solver_node = node
                break

        self.assertIsNotNone(solver_node, "左足 IK の mmdCcdIk solver が見つかりません")

        chain = json.loads(cmds.getAttr(f"{solver_node}.chainJson") or "{}")
        slots = [int(link["bone_slot"]) for link in chain.get("links", [])]
        self.assertGreater(len(slots), 0, "左足 IK の chainJson に links がありません")

        selection = om.MSelectionList()
        selection.add(solver_node)
        fn_dep = om.MFnDependencyNode(selection.getDependNode(0))
        input_rotate = fn_dep.findPlug("inputRotate", False)

        cmds.currentTime(10, edit=True)

        non_zero_radians = []
        for slot in slots:
            elem = input_rotate.elementByLogicalIndex(slot)
            for axis_index, axis in enumerate("XYZ"):
                attr = f"{solver_node}.inputRotate[{slot}].inputRotateElement{axis}"
                ui_degrees = cmds.getAttr(attr)
                plug_radians = elem.child(axis_index).asDouble()
                self.assertAlmostEqual(
                    plug_radians,
                    math.radians(ui_degrees),
                    delta=1e-6,
                    msg=f"{attr} の getAttr 度数値と MPlug ラジアン値が一致しません",
                )
                non_zero_radians.append(abs(plug_radians))

        self.assertGreater(
            max(non_zero_radians),
            0.01,
            "IK link inputRotate がほぼゼロで、二重ラジアン変換の再発が疑われます",
        )

    def test_resolve_runtime_bake_sources_uses_vmd_source_file_and_scene_pmx(self):
        """convert 直呼びでも VmdData.source_file と model root の mmd_source_file から runtime 入力を復元する"""
        with tempfile.TemporaryDirectory() as temp_dir:
            vmd_path = os.path.join(temp_dir, "motion.vmd")
            pmx_path = os.path.join(temp_dir, "model.pmx")
            with open(vmd_path, "wb") as file:
                file.write(b"vmd-bytes")
            with open(pmx_path, "wb") as file:
                file.write(b"pmx-bytes")

            root = cmds.group(empty=True, name="runtime_source_model_root")
            cmds.addAttr(root, longName="mmd_source_file", dataType="string")
            cmds.setAttr(f"{root}.mmd_source_file", pmx_path, type="string")
            vmd_data = create_test_vmd_data()
            vmd_data.source_file = vmd_path

            vmd_bytes, pmx_bytes, resolved_pmx_path = self.converter._resolve_runtime_bake_sources(
                vmd_data,
                vmd_bytes=None,
                pmx_bytes=None,
                pmx_path=None,
            )

            self.assertEqual(vmd_bytes, b"vmd-bytes")
            self.assertIsNone(pmx_bytes)
            self.assertEqual(resolved_pmx_path, pmx_path)

            cmds.delete(root)

    def test_disable_mmd_rig_constraints_for_runtime_bake_only_marked_constraints(self):
        """runtime bakeではMMD付与constraintとlive IK solverを無効化する"""
        source = cmds.spaceLocator(name="grant_source")[0]
        target = cmds.spaceLocator(name="grant_target")[0]
        other_source = cmds.spaceLocator(name="other_source")[0]
        other_target = cmds.spaceLocator(name="other_target")[0]
        ik_node = cmds.createNode("mmdCcdIk", name="runtime_disabled_ik_solver")
        ik_link = cmds.joint(name="runtime_disabled_ik_link")
        other_ik_node = cmds.createNode("mmdCcdIk", name="runtime_other_ik_solver")
        other_ik_link = cmds.joint(name="runtime_other_ik_link")

        marked = cmds.orientConstraint(source, target)[0]
        unmarked = cmds.orientConstraint(other_source, other_target)[0]
        cmds.addAttr(marked, longName="mmd_grant_constraint", attributeType="bool")
        cmds.setAttr(f"{marked}.mmd_grant_constraint", True)
        cmds.setAttr(f"{ik_node}.enabled", True)
        cmds.setAttr(f"{other_ik_node}.enabled", True)
        cmds.connectAttr(f"{ik_node}.outputRotate[0]", f"{ik_link}.rotate", force=True)
        cmds.connectAttr(f"{other_ik_node}.outputRotate[0]", f"{other_ik_link}.rotate", force=True)
        self.converter.bone_name_mapping = {
            "grant_target": target,
            "ik_link": ik_link,
        }

        self.converter._disable_mmd_rig_constraints_for_runtime_bake()

        self.assertEqual(cmds.getAttr(f"{marked}.nodeState"), 2)
        self.assertEqual(cmds.getAttr(f"{unmarked}.nodeState"), 0)
        self.assertFalse(cmds.getAttr(f"{ik_node}.enabled"))
        self.assertFalse(cmds.listConnections(f"{ik_link}.rotate", s=True, d=False, p=True) or [])
        self.assertTrue(cmds.getAttr(f"{other_ik_node}.enabled"))
        self.assertTrue(cmds.listConnections(f"{other_ik_link}.rotate", s=True, d=False, p=True) or [])

        cmds.delete(source, target, other_source, other_target, ik_node, ik_link, other_ik_node, other_ik_link)

    def test_restore_joints_to_bind_pose_for_runtime_bake_clears_live_values(self):
        """runtime bake 前にlive rig由来の残り値を消してbind姿勢へ戻す"""
        joint = cmds.joint(name="runtime_restore_bind_joint")
        cmds.setAttr(f"{joint}.translate", 1.0, 2.0, 3.0)
        cmds.setAttr(f"{joint}.rotate", 10.0, 20.0, 30.0)
        driver = cmds.createNode("animCurveTA", name="runtime_restore_rotate_driver")
        cmds.connectAttr(f"{driver}.output", f"{joint}.rotateX", force=True)

        self.converter.bone_name_mapping = {"センター": joint}
        self.converter._bone_bind_poses = {"センター": (4.0, 5.0, 6.0)}

        self.converter._restore_joints_to_bind_pose_for_runtime_bake()

        self.assertFalse(cmds.listConnections(f"{joint}.rotateX", s=True, d=False, p=True) or [])
        self.assertEqual(tuple(round(v, 6) for v in cmds.getAttr(f"{joint}.translate")[0]), (4.0, 5.0, 6.0))
        self.assertEqual(tuple(round(v, 6) for v in cmds.getAttr(f"{joint}.rotate")[0]), (0.0, 0.0, 0.0))

        cmds.delete(joint, driver)

    def _create_test_joints_for_vmd(self):
        """VMDテスト用のジョイントを作成"""
        from mmd_tools.core.constants import ATTR_MMD_BONE_NAME

        # センタージョイント
        center = cmds.joint(name="center", position=[0, 10, 0])
        cmds.addAttr(center, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{center}.{ATTR_MMD_BONE_NAME}", "センター", type="string")

        # 上半身ジョイント
        upper_body = cmds.joint(name="upper_body", position=[0, 15, 0])
        cmds.addAttr(upper_body, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{upper_body}.{ATTR_MMD_BONE_NAME}", "上半身", type="string")

        # 頭ジョイント
        head = cmds.joint(name="head", position=[0, 20, 0])
        cmds.addAttr(head, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{head}.{ATTR_MMD_BONE_NAME}", "頭", type="string")

        cmds.select(clear=True)

        return {"center": center, "upper_body": upper_body, "head": head}

    def _make_bone_frame(self, bone_name, frame_number, position, rotation=(0.0, 0.0, 0.0, 1.0)):
        """テスト用 VMD ボーンフレームを作成する。"""
        from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame

        frame = VmdBoneFrame()
        frame.bone_name = bone_name
        frame.frame_number = frame_number
        frame.position = position
        frame.rotation = rotation
        return frame

    def test_legacy_bone_keyframes_use_bind_pose_without_accumulation(self):
        """レガシー VMD パスは現在フレーム値ではなく bind pose + VMD offset を key する。"""
        joint = cmds.joint(name="legacy_bind_pose_joint")
        cmds.setAttr(f"{joint}.translate", 100.0, 100.0, 100.0, type="double3")
        self.converter.use_animation_layers = False
        self.converter._bone_bind_poses["センター"] = (3.0, 4.0, 5.0)

        frames = [
            self._make_bone_frame("センター", 0, (1.0, 2.0, 3.0)),
            self._make_bone_frame("センター", 10, (2.0, 3.0, 4.0)),
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

        frames = [self._make_bone_frame("センター", 12, (1.0, 2.0, 3.0))]
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

        frames = [self._make_bone_frame("センター", 30, (1.0, 2.0, 3.0))]
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

    def test_convert_exposes_live_rig_target_during_sparse_import_only(self):
        """convert 中の live rig 判定は legacy bone keying 中だけ状態として見える。"""
        frame = self._make_bone_frame("センター", 0, (0.0, 0.0, 0.0))
        vmd_data = type("FakeVmdData", (), {})()
        vmd_data.bone_frames = [frame]
        vmd_data.morph_frames = []
        vmd_data.camera_frames = []
        vmd_data.light_frames = []
        vmd_data.ik_show_hide_frames = []

        def assert_live_flag(_frames):
            self.assertTrue(self.converter._current_import_live_rig_target)
            return True

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_has_live_mmd_rig_for_runtime_target", return_value=True))
            stack.enter_context(patch.object(self.converter, "_build_bone_hierarchy_and_order_maps"))
            stack.enter_context(patch.object(self.converter, "_build_runtime_bind_world_maps"))
            stack.enter_context(patch.object(self.converter, "_should_use_mmd_runtime_bake", return_value=False))
            stack.enter_context(patch.object(self.converter, "_apply_ik_enabled_animation"))
            stack.enter_context(patch.object(self.converter, "_convert_bone_animation", side_effect=assert_live_flag))

            self.assertTrue(self.converter.convert(vmd_data))

        self.assertFalse(self.converter._current_import_live_rig_target)

    def test_convert_with_fixture_vmd_camera(self):
        """フィクスチャを使用したカメラアニメーション変換テスト"""
        try:
            vmd_path = self.fixture_provider.get_vmd_file("test_camera_light")
        except FileNotFoundError:
            self.skipTest("カメラ/照明テスト用VMDが見つかりません")

        from mmd_tools.core.vmd_data import VmdData

        parser = VmdData()
        parser.parse_file(vmd_path)

        result = self.converter._convert_camera_animation(parser.camera_frames)
        self.assertTrue(result, "カメラアニメーション変換に失敗しました")

        # MMDカメラが作成されたことを確認
        from mmd_tools.core.constants import ATTR_MMD_CAMERA

        cameras = cmds.ls(type="camera")
        mmd_camera = None
        for cam in cameras:
            transform = cmds.listRelatives(cam, parent=True)
            if transform and cmds.attributeQuery(ATTR_MMD_CAMERA, node=transform[0], exists=True):
                mmd_camera = transform[0]
                break

        self.assertIsNotNone(mmd_camera, "MMDカメラが作成されていません")

        target_node = self._camera_target_node(mmd_camera)
        target_keys = cmds.keyframe(f"{target_node}.translateX", query=True)
        self.assertIsNotNone(target_keys, "MMD camera target にキーフレームが設定されていません")
        self.assertGreater(len(target_keys), 0, "MMD camera target にキーフレームが設定されていません")
        self.assertFalse(cmds.attributeQuery("mmd_camera_target_x", node=mmd_camera, exists=True))
        self.assertIsNone(cmds.keyframe(f"{mmd_camera}.translateX", query=True))
        self.assertIsNotNone(cmds.keyframe(f"{mmd_camera}.translateZ", query=True))
        self.assertIsNotNone(cmds.keyframe(f"{target_node}.rotateX", query=True))
        self.assertFalse(cmds.listConnections(f"{mmd_camera}.rotateX", source=True, type="aimConstraint") or [])
        self._assert_mmd_camera_raw_attrs_absent(mmd_camera)

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
