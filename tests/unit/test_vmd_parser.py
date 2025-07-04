import os

from mmd_tools.core import mmd_parser
from tests.common.test_base import TestBase


class TestVmdParser(TestBase):

    def setUp(self):
        super().setUp()
        self.sample_vmd_path = os.path.join(os.path.dirname(__file__), "..", "data", "Lat式用.vmd")
        self.parsed_data = mmd_parser.parse_mmd_file(self.sample_vmd_path)

    def tearDown(self):
        super().tearDown()
        # if os.path.exists(self.dummy_vmd_path):
        #     os.remove(self.dummy_vmd_path)

    def _create_dummy_vmd_file(self, magic=b'Vocaloid Motion Data', version=2.0, model_name='TestModel', bone_frames=None, morph_frames=None, camera_frames=None, light_frames=None, shadow_frames=None, ik_show_hide_frames=None):
        """
        テスト用にダミーのVMDファイルを生成するヘルパー関数。
        実際のVMDファイル構造に合わせてバイナリデータを書き込む。
        """
        # TODO: 実際のVMDファイルの構造に合わせて、必要なバイナリデータを生成する。

    def test_parse_vmd_header_success(self):
        """VMDヘッダが正しく解析されることをテストする。"""

        # データが正しく解析されているか確認
        self.assertIsNotNone(self.parsed_data)
        # ヘッダのマジックナンバーが正しいことを確認
        self.assertTrue(self.parsed_data.header.magic.startswith(b'Vocaloid Motion Data'))
        # モデル名の型が文字列であることを確認
        self.assertIsInstance(self.parsed_data.header.model_name, str)

    def test_parse_vmd_file_not_found(self):
        """存在しないVMDファイルを解析しようとしたときにFileNotFoundErrorが発生することをテストする。"""
        with self.assertRaises(FileNotFoundError):
            mmd_parser.parse_mmd_file("non_existent_file.vmd")

    def test_parse_vmd_bone_frames(self):
        """VMDボーンフレームが正しく解析されることをテストする。"""

        # 解析結果がNoneでないことを確認
        self.assertIsNotNone(self.parsed_data.bone_frames)

        bone_frame = self.parsed_data.bone_frames[0]
        # ボーンフレームの属性が正しく設定されていることを確認
        self.assertEqual(bone_frame.bone_name, 'センター')
        # フレーム番号が正しく設定されていることを確認
        self.assertEqual(bone_frame.frame_number, 0)
        # 位置が正しく設定されていることを確認
        self.assertEqual(len(bone_frame.position), 3)
        # 回転が正しく設定されていることを確認
        self.assertEqual(len(bone_frame.rotation), 4)
        # ボーンフレームの補間データが正しく設定されていることを確認
        self.assertEqual(len(bone_frame.interpolation), 64)  # 補間データは64バイト

    def test_parse_vmd_morph_frames(self):
        """VMDモーフフレームが正しく解析されることをテストする。"""
        # None出ないことを確認
        self.assertIsNotNone(self.parsed_data.morph_frames)
        # listであることを確認
        self.assertIsInstance(self.parsed_data.morph_frames, list)

        morph_frame = self.parsed_data.morph_frames[0]
        # モーフフレームの属性が正しく設定されていることを確認
        self.assertEqual(morph_frame.morph_name, 'base')
        # フレーム番号が正しく設定されていることを確認
        self.assertEqual(morph_frame.frame_number, 0)
        # モーフ値が0~1の範囲であることを確認
        self.assertGreaterEqual(morph_frame.value, 0.0)
        self.assertLessEqual(morph_frame.value, 1.0)

    def test_parse_vmd_camera_frames(self):
        """VMDカメラフレームが正しく解析されることをテストする。"""
        # Noneでないことを確認
        self.assertIsNotNone(self.parsed_data.camera_frames)
        # listであることを確認
        self.assertIsInstance(self.parsed_data.camera_frames, list)

        if not self.parsed_data.camera_frames:
            self.skipTest("No camera frames found in the VMD file.")

        camera_frame = self.parsed_data.camera_frames[0]
        # カメラフレームの属性が正しく設定されていることを確認
        self.assertIsInstance(camera_frame.frame_number, int)
        # カメラ位置が正しく設定されていることを確認
        self.assertEqual(len(camera_frame.position), 3)
        # カメラ回転が正しく設定されていることを確認
        self.assertEqual(len(camera_frame.rotation), 3)
        # カメラ距離が正しく設定されていることを確認
        self.assertIsInstance(camera_frame.distance, float)
        self.assertGreater(camera_frame.distance, 0.0)
        # 補間データが正しく設定されていることを確認
        self.assertEqual(len(camera_frame.interpolation), 24)
        # 視野角が正しく設定されていることを確認
        self.assertGreater(camera_frame.viewing_angle, 0.0)
        self.assertLessEqual(camera_frame.viewing_angle, 180.0)
        # パースペクティブが0または1であることを確認
        self.assertIn(camera_frame.perspective, (0, 1))


    def test_parse_vmd_light_frames(self):
        """VMDライトフレームが正しく解析されることをテストする。"""

        # Noneでないことを確認
        self.assertIsNotNone(self.parsed_data.light_frames)
        # listであることを確認
        self.assertIsInstance(self.parsed_data.light_frames, list)

        if not self.parsed_data.light_frames:
            self.skipTest("No light frames found in the VMD file.")

        light_frame = self.parsed_data.light_frames[0]
        # ライトフレームの属性が正しく設定されていることを確認
        self.assertIsInstance(light_frame.frame_number, int)
        # ライト位置が正しく設定されていることを確認
        self.assertEqual(len(light_frame.position), 3)
        # ライト色が正しく設定されていることを確認
        self.assertEqual(len(light_frame.color), 3)
        # ライト強度が正しく設定されていることを確認
        self.assertGreater(light_frame.intensity, 0.0)

    def test_parse_vmd_shadow_frames(self):
        """VMDシャドウフレームが正しく解析されることをテストする。"""
        # Noneでないことを確認
        self.assertIsNotNone(self.parsed_data.shadow_frames)
        # listであることを確認
        self.assertIsInstance(self.parsed_data.shadow_frames, list)

        if not self.parsed_data.shadow_frames:
            self.skipTest("No shadow frames found in the VMD file.")

        shadow_frame = self.parsed_data.shadow_frames[0]
        # シャドウフレームの属性が正しく設定されていることを確認
        self.assertIsInstance(shadow_frame.frame_number, int)
        # シャドウモードが正しく設定されていることを確認
        self.assertIn(shadow_frame.mode, (0, 1, 2))
        # シャドウ距離が正しく設定されていることを確認
        self.assertGreaterEqual(shadow_frame.distance, 0.0)

    def test_parse_vmd_ik_show_hide_frames(self):
        """VMD IK表示/非表示フレームが正しく解析されることをテストする。"""
        # Noneでないことを確認
        self.assertIsNotNone(self.parsed_data.ik_show_hide_frames)
        # listであることを確認
        self.assertIsInstance(self.parsed_data.ik_show_hide_frames, list)

        if not self.parsed_data.ik_show_hide_frames:
            self.skipTest("No IK show/hide frames found in the VMD file.")

        ik_frame = self.parsed_data.ik_show_hide_frames[0]
        # IKフレームの属性が正しく設定されていることを確認
        self.assertIsInstance(ik_frame.frame_number, int)
        # IK表示/非表示の状態が正しく設定されていることを確認
        self.assertIn(ik_frame.visible, (0, 1))
        # IKの数が正しく設定されていることを確認
        self.assertGreater(ik_frame.ik_count, 0)
        # IK状態のリストが正しく設定されていることを確認
        self.assertIsInstance(ik_frame.ik_states, list)
