"""VMD camera scene conversion regression tests.

VmdConverter の camera import が作る Maya camera rig、keyframe、
interpolation、fixture import の scene-level behavior を検証する。
"""

import math
from unittest.mock import patch

import maya.cmds as cmds
import maya.api.OpenMaya as om

import mmd_tools.converters.vmd_camera_animation as vmd_camera_animation_module
from mmd_tools.converters.vmd_camera_animation import (
    maya_camera_eye_from_vmd_state,
    maya_camera_up_from_vmd_state,
)
from mmd_tools.core.coordinate_transform import mmd_point_to_maya
from tests.common.maya_test_base import MayaTestBase
from mmd_tools.converters.vmd_converter import VmdConverter
from tests.common.test_fixture_provider import TestFixtureProvider


class TestVmdCameraScene(MayaTestBase):
    """Scene-level VMD camera conversion tests."""

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
