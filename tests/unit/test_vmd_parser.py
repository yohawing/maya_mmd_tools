import os
import struct

from tests.common.test_base import TestBase
from mmd_tools.core import mmd_parser

class TestVmdParser(TestBase):

    def setUp(self):
        super().setUp()
        self.dummy_vmd_path = os.path.join(self.temp_dir, "test_motion.vmd")

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.dummy_vmd_path):
            os.remove(self.dummy_vmd_path)

    def _create_dummy_vmd_file(self, magic=b'Vocaloid Motion Data file', version=2.0, model_name='TestModel', bone_frames=None, morph_frames=None, camera_frames=None, light_frames=None, shadow_frames=None, ik_show_hide_frames=None):
        """
        テスト用にダミーのVMDファイルを生成するヘルパー関数。
        実際のVMDファイル構造に合わせてバイナリデータを書き込む。
        """
        if bone_frames is None:
            bone_frames = []
        if morph_frames is None:
            morph_frames = []
        if camera_frames is None:
            camera_frames = []
        if light_frames is None:
            light_frames = []
        if shadow_frames is None:
            shadow_frames = []
        if ik_show_hide_frames is None:
            ik_show_hide_frames = []

        with open(self.dummy_vmd_path, 'wb') as f:
            # Header
            f.write(magic.ljust(30, b'\x00'))
            f.write(struct.pack('<f', version))
            f.write(model_name.encode('shift_jis').ljust(20, b'\x00'))

            # Bone Frames
            f.write(struct.pack('<I', len(bone_frames)))
            for frame in bone_frames:
                # bone_name (15 bytes, Shift-JIS)
                f.write(frame['bone_name'].encode('shift_jis').ljust(15, b'\x00'))
                # frame_number (int)
                f.write(struct.pack('<I', frame['frame_number']))
                # position (3 floats)
                f.write(struct.pack('<fff', *frame['position']))
                # rotation (4 floats, quaternion)
                f.write(struct.pack('<ffff', *frame['rotation']))
                # interpolation (64 bytes, 4x4 matrix of bytes)
                f.write(frame['interpolation'].ljust(64, b'\x00'))

            # Morph Frames
            f.write(struct.pack('<I', len(morph_frames)))
            for frame in morph_frames:
                f.write(frame['morph_name'].encode('shift_jis').ljust(15, b'\x00'))
                f.write(struct.pack('<I', frame['frame_number']))
                f.write(struct.pack('<f', frame['value']))

            # Camera Frames
            f.write(struct.pack('<I', len(camera_frames)))
            for frame in camera_frames:
                f.write(struct.pack('<I', frame['frame_number']))
                f.write(struct.pack('<f', frame['distance']))
                f.write(struct.pack('<fff', *frame['position']))
                f.write(struct.pack('<fff', *frame['rotation']))
                f.write(frame['interpolation'].ljust(24, b'\x00')) # 24 bytes for camera interpolation
                f.write(struct.pack('<f', frame['view_angle']))
                f.write(struct.pack('<B', frame['perspective']))

            # Light Frames
            f.write(struct.pack('<I', len(light_frames)))
            for frame in light_frames:
                f.write(struct.pack('<I', frame['frame_number']))
                f.write(struct.pack('<fff', *frame['color']))
                f.write(struct.pack('<fff', *frame['position']))

            # Shadow Frames (VMD 2.0 only)
            if version >= 2.0:
                f.write(struct.pack('<I', len(shadow_frames)))
                for frame in shadow_frames:
                    f.write(struct.pack('<I', frame['frame_number']))
                    f.write(struct.pack('<B', frame['mode']))
                    f.write(struct.pack('<f', frame['distance']))

            # IK Show/Hide Frames (VMD 2.0 only)
            if version >= 2.0:
                f.write(struct.pack('<I', len(ik_show_hide_frames)))
                for frame in ik_show_hide_frames:
                    f.write(struct.pack('<I', frame['frame_number']))
                    f.write(struct.pack('<B', frame['show']))
                    f.write(struct.pack('<I', len(frame['ik_bones'])))
                    for ik_bone in frame['ik_bones']:
                        f.write(ik_bone['bone_name'].encode('shift_jis').ljust(15, b'\x00'))
                        f.write(struct.pack('<B', ik_bone['show']))

    def test_parse_vmd_header_success(self):
        """VMDヘッダが正しく解析されることをテストする。"""
        self._create_dummy_vmd_file(model_name='TestMotion', version=2.0)
        parsed_data = mmd_parser.parse_mmd_file(self.dummy_vmd_path)

        self.assertIsNotNone(parsed_data)
        self.assertTrue(parsed_data.header.magic.startswith(b'Vocaloid Motion Data file'))
        self.assertAlmostEqual(parsed_data.header.version, 2.0)
        self.assertEqual(parsed_data.header.model_name, 'TestMotion')

    def test_parse_vmd_file_not_found(self):
        """存在しないVMDファイルを解析しようとしたときにFileNotFoundErrorが発生することをテストする。"""
        with self.assertRaises(FileNotFoundError):
            mmd_parser.parse_mmd_file("non_existent_file.vmd")

    def test_parse_vmd_invalid_magic(self):
        """VMDマジックが不正な場合にMMDParseExceptionが発生することをテストする。"""
        self._create_dummy_vmd_file(magic=b'INVALID_VMD_MAGIC')
        with self.assertRaisesRegex(mmd_parser.MMDParseException, "Unsupported MMD file format"):
            mmd_parser.parse_mmd_file(self.dummy_vmd_path)

    def test_parse_vmd_bone_frames(self):
        """VMDボーンフレームが正しく解析されることをテストする。"""
        bone_frames = [
            {'bone_name': 'ボーン1', 'frame_number': 10, 'position': (1.0, 2.0, 3.0), 'rotation': (0.1, 0.2, 0.3, 0.4), 'interpolation': b''},
            {'bone_name': 'ボーン2', 'frame_number': 20, 'position': (4.0, 5.0, 6.0), 'rotation': (0.5, 0.6, 0.7, 0.8), 'interpolation': b''},
        ]
        self._create_dummy_vmd_file(bone_frames=bone_frames)
        parsed_data = mmd_parser.parse_mmd_file(self.dummy_vmd_path)

        self.assertEqual(len(parsed_data.bone_frames), len(bone_frames))
        for i, expected_frame in enumerate(bone_frames):
            actual_frame = parsed_data.bone_frames[i]
            self.assertEqual(actual_frame.bone_name, expected_frame['bone_name'])
            self.assertEqual(actual_frame.frame_number, expected_frame['frame_number'])
            self.assertAlmostEqual(actual_frame.position[0], expected_frame['position'][0])
            self.assertAlmostEqual(actual_frame.position[1], expected_frame['position'][1])
            self.assertAlmostEqual(actual_frame.position[2], expected_frame['position'][2])
            self.assertAlmostEqual(actual_frame.rotation[0], expected_frame['rotation'][0])
            self.assertAlmostEqual(actual_frame.rotation[1], expected_frame['rotation'][1])
            self.assertAlmostEqual(actual_frame.rotation[2], expected_frame['rotation'][2])
            self.assertAlmostEqual(actual_frame.rotation[3], expected_frame['rotation'][3])

    def test_parse_vmd_morph_frames(self):
        """VMDモーフフレームが正しく解析されることをテストする。"""
        morph_frames = [
            {'morph_name': 'モーフ1', 'frame_number': 5, 'value': 0.5},
            {'morph_name': 'モーフ2', 'frame_number': 15, 'value': 1.0},
        ]
        self._create_dummy_vmd_file(morph_frames=morph_frames)
        parsed_data = mmd_parser.parse_mmd_file(self.dummy_vmd_path)

        self.assertEqual(len(parsed_data.morph_frames), len(morph_frames))
        for i, expected_frame in enumerate(morph_frames):
            actual_frame = parsed_data.morph_frames[i]
            self.assertEqual(actual_frame.morph_name, expected_frame['morph_name'])
            self.assertEqual(actual_frame.frame_number, expected_frame['frame_number'])
            self.assertAlmostEqual(actual_frame.value, expected_frame['value'])

    def test_parse_vmd_camera_frames(self):
        """VMDカメラフレームが正しく解析されることをテストする。"""
        camera_frames = [
            {'frame_number': 0, 'distance': 50.0, 'position': (0.0, 10.0, -20.0), 'rotation': (0.0, 0.0, 0.0), 'interpolation': b'', 'view_angle': 30.0, 'perspective': 1},
            {'frame_number': 30, 'distance': 60.0, 'position': (10.0, 15.0, -25.0), 'rotation': (0.1, 0.2, 0.3), 'interpolation': b'', 'view_angle': 45.0, 'perspective': 0},
        ]
        self._create_dummy_vmd_file(camera_frames=camera_frames)
        parsed_data = mmd_parser.parse_mmd_file(self.dummy_vmd_path)

        self.assertEqual(len(parsed_data.camera_frames), len(camera_frames))
        for i, expected_frame in enumerate(camera_frames):
            actual_frame = parsed_data.camera_frames[i]
            self.assertEqual(actual_frame.frame_number, expected_frame['frame_number'])
            self.assertAlmostEqual(actual_frame.distance, expected_frame['distance'])
            self.assertAlmostEqual(actual_frame.position[0], expected_frame['position'][0])
            self.assertAlmostEqual(actual_frame.position[1], expected_frame['position'][1])
            self.assertAlmostEqual(actual_frame.position[2], expected_frame['position'][2])
            self.assertAlmostEqual(actual_frame.rotation[0], expected_frame['rotation'][0])
            self.assertAlmostEqual(actual_frame.rotation[1], expected_frame['rotation'][1])
            self.assertAlmostEqual(actual_frame.rotation[2], expected_frame['rotation'][2])
            self.assertAlmostEqual(actual_frame.view_angle, expected_frame['view_angle'])
            self.assertEqual(actual_frame.perspective, expected_frame['perspective'])

    def test_parse_vmd_light_frames(self):
        """VMDライトフレームが正しく解析されることをテストする。"""
        light_frames = [
            {'frame_number': 0, 'color': (1.0, 1.0, 1.0), 'position': (-0.5, -1.0, 0.5)},
            {'frame_number': 60, 'color': (0.5, 0.5, 0.5), 'position': (0.0, 0.0, 0.0)},
        ]
        self._create_dummy_vmd_file(light_frames=light_frames)
        parsed_data = mmd_parser.parse_mmd_file(self.dummy_vmd_path)

        self.assertEqual(len(parsed_data.light_frames), len(light_frames))
        for i, expected_frame in enumerate(light_frames):
            actual_frame = parsed_data.light_frames[i]
            self.assertEqual(actual_frame.frame_number, expected_frame['frame_number'])
            self.assertAlmostEqual(actual_frame.color[0], expected_frame['color'][0])
            self.assertAlmostEqual(actual_frame.color[1], expected_frame['color'][1])
            self.assertAlmostEqual(actual_frame.color[2], expected_frame['color'][2])
            self.assertAlmostEqual(actual_frame.position[0], expected_frame['position'][0])
            self.assertAlmostEqual(actual_frame.position[1], expected_frame['position'][1])
            self.assertAlmostEqual(actual_frame.position[2], expected_frame['position'][2])

    def test_parse_vmd_shadow_frames(self):
        """VMDシャドウフレームが正しく解析されることをテストする。"""
        shadow_frames = [
            {'frame_number': 0, 'mode': 0, 'distance': 100.0},
            {'frame_number': 45, 'mode': 1, 'distance': 200.0},
        ]
        self._create_dummy_vmd_file(shadow_frames=shadow_frames, version=2.0)
        parsed_data = mmd_parser.parse_mmd_file(self.dummy_vmd_path)

        self.assertEqual(len(parsed_data.shadow_frames), len(shadow_frames))
        for i, expected_frame in enumerate(shadow_frames):
            actual_frame = parsed_data.shadow_frames[i]
            self.assertEqual(actual_frame.frame_number, expected_frame['frame_number'])
            self.assertEqual(actual_frame.mode, expected_frame['mode'])
            self.assertAlmostEqual(actual_frame.distance, expected_frame['distance'])

    def test_parse_vmd_ik_show_hide_frames(self):
        """VMD IK表示/非表示フレームが正しく解析されることをテストする。"""
        ik_show_hide_frames = [
            {'frame_number': 0, 'show': 1, 'ik_bones': [{'bone_name': 'IK足D', 'show': 1}]},
            {'frame_number': 10, 'show': 0, 'ik_bones': [{'bone_name': 'IK足D', 'show': 0}, {'bone_name': 'IK腕D', 'show': 1}]},
        ]
        self._create_dummy_vmd_file(ik_show_hide_frames=ik_show_hide_frames, version=2.0)
        parsed_data = mmd_parser.parse_mmd_file(self.dummy_vmd_path)

        self.assertEqual(len(parsed_data.ik_show_hide_frames), len(ik_show_hide_frames))
        for i, expected_frame in enumerate(ik_show_hide_frames):
            actual_frame = parsed_data.ik_show_hide_frames[i]
            self.assertEqual(actual_frame.frame_number, expected_frame['frame_number'])
            self.assertEqual(actual_frame.show, expected_frame['show'])
            self.assertEqual(len(actual_frame.ik_bones), len(expected_frame['ik_bones']))
            for j, expected_ik_bone in enumerate(expected_frame['ik_bones']):
                actual_ik_bone = actual_frame.ik_bones[j]
                self.assertEqual(actual_ik_bone.bone_name, expected_ik_bone['bone_name'])
                self.assertEqual(actual_ik_bone.show, expected_ik_bone['show'])
