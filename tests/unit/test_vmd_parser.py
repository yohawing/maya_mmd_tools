import os
import tempfile

from mmd_tools.core import mmd_parser
from mmd_tools.core.exceptions import MMDParseException
from tests.common.test_base import TestBase
from tests.common.vmd_mock import VmdMock


class TestVmdParser(TestBase):
    """VMDパーサーのユニットテスト。

    bone/IK の基本解析は実フィクスチャ ``tests/data/mmt_test_model_test_motion.vmd`` を用いる。
    camera/light/shadow など実ファイルに含まれないフレームは VmdMock 生成データで補う。
    """

    def setUp(self):
        super().setUp()
        # bone/IK 系は実フィクスチャを解析して検証する
        self.sample_vmd_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "data",
            "mmt_test_model_test_motion.vmd",
        )
        self.parsed_data = mmd_parser.parse_mmd_file(self.sample_vmd_path)

    def tearDown(self):
        super().tearDown()

    def test_parse_vmd_header_success(self):
        """VMDヘッダが正しく解析されることをテストする。"""
        # データが正しく解析されているか確認
        self.assertIsNotNone(self.parsed_data)
        # ヘッダのマジックナンバーが正しいことを確認
        self.assertTrue(self.parsed_data.header.magic.startswith(b"Vocaloid Motion Data"))
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

        self.assertGreater(len(self.parsed_data.bone_frames), 0)
        bone_frame = self.parsed_data.bone_frames[0]
        # ボーンフレームの属性が正しく設定されていることを確認
        self.assertIsInstance(bone_frame.bone_name, str)
        # フレーム番号が正しく設定されていることを確認
        self.assertIsInstance(bone_frame.frame_number, int)
        # 位置が正しく設定されていることを確認
        self.assertEqual(len(bone_frame.position), 3)
        # 回転が正しく設定されていることを確認
        self.assertEqual(len(bone_frame.rotation), 4)
        # ボーンフレームの補間データが正しく設定されていることを確認
        self.assertEqual(len(bone_frame.interpolation), 64)  # 補間データは64バイト

    def test_parse_vmd_morph_frames(self):
        """VMDモーフフレームが正しく解析されることをテストする。"""
        mock_vmd_data = VmdMock.create_custom_vmd(
            model_name="TestModel",
            bone_frame_count=0,
            morph_frame_count=3,
        )
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".vmd", delete=False) as temp_file:
            temp_file.write(mock_vmd_data)
            temp_file_path = temp_file.name

        try:
            parsed_data = mmd_parser.parse_mmd_file(temp_file_path)
        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

        self.assertIsNotNone(parsed_data.morph_frames)
        self.assertIsInstance(parsed_data.morph_frames, list)
        self.assertGreater(len(parsed_data.morph_frames), 0)

        morph_frame = parsed_data.morph_frames[0]
        # モーフフレームの属性が正しく設定されていることを確認
        self.assertIsInstance(morph_frame.morph_name, str)
        # フレーム番号が正しく設定されていることを確認
        self.assertEqual(morph_frame.frame_number, 0)
        # モーフ値が0~1の範囲であることを確認
        self.assertGreaterEqual(morph_frame.value, 0.0)
        self.assertLessEqual(morph_frame.value, 1.0)

    def test_parse_vmd_camera_frames_with_mock(self):
        """VMDカメラフレームが正しく解析されることをテストする（モック使用）。"""
        # カメラフレームを含むモックVMDデータを作成
        mock_vmd_data = VmdMock.create_custom_vmd(
            model_name="Camera", bone_frame_count=0, morph_frame_count=0, camera_frame_count=5
        )

        # 一時ファイルに書き込み
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".vmd", delete=False) as temp_file:
            temp_file.write(mock_vmd_data)
            temp_file_path = temp_file.name

        try:
            # パース実行
            parsed_data = mmd_parser.parse_mmd_file(temp_file_path)

            # カメラフレームの確認
            self.assertIsNotNone(parsed_data.camera_frames)
            self.assertEqual(len(parsed_data.camera_frames), 5)

            camera_frame = parsed_data.camera_frames[0]
            # カメラフレームの属性が正しく設定されていることを確認
            self.assertEqual(camera_frame.frame_number, 0)
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
        finally:
            # 一時ファイルを削除
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def test_parse_vmd_light_frames_with_mock(self):
        """VMDライトフレームが正しく解析されることをテストする（モック使用）。"""
        # ライトフレームを含むモックVMDデータを作成
        mock_vmd_data = VmdMock.create_custom_vmd(
            model_name="TestModel", bone_frame_count=0, morph_frame_count=0, light_frame_count=3
        )

        # 一時ファイルに書き込み
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".vmd", delete=False) as temp_file:
            temp_file.write(mock_vmd_data)
            temp_file_path = temp_file.name

        try:
            # パース実行
            parsed_data = mmd_parser.parse_mmd_file(temp_file_path)

            # ライトフレームの確認
            self.assertIsNotNone(parsed_data.light_frames)
            self.assertEqual(len(parsed_data.light_frames), 3)

            light_frame = parsed_data.light_frames[0]
            # ライトフレームの属性が正しく設定されていることを確認
            self.assertEqual(light_frame.frame_number, 0)
            # ライト位置（方向）が正しく設定されていることを確認
            self.assertEqual(len(light_frame.position), 3)
            # ライト色が正しく設定されていることを確認
            self.assertEqual(len(light_frame.color), 3)
            # 色の各成分が0-1の範囲内であることを確認
            for component in light_frame.color:
                self.assertGreaterEqual(component, 0.0)
                self.assertLessEqual(component, 1.0)
        finally:
            # 一時ファイルを削除
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def test_parse_vmd_shadow_frames_with_mock(self):
        """VMDシャドウフレームが正しく解析されることをテストする（モック使用）。"""
        # シャドウフレームを含むモックVMDデータを作成
        mock_vmd_data = VmdMock.create_custom_vmd(
            model_name="TestModel", bone_frame_count=0, morph_frame_count=0, shadow_frame_count=3
        )

        # 一時ファイルに書き込み
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".vmd", delete=False) as temp_file:
            temp_file.write(mock_vmd_data)
            temp_file_path = temp_file.name

        try:
            # パース実行
            parsed_data = mmd_parser.parse_mmd_file(temp_file_path)

            # シャドウフレームの確認
            self.assertIsNotNone(parsed_data.shadow_frames)
            self.assertEqual(len(parsed_data.shadow_frames), 3)

            shadow_frame = parsed_data.shadow_frames[0]
            # シャドウフレームの属性が正しく設定されていることを確認
            self.assertEqual(shadow_frame.frame_number, 0)
            # シャドウモードが正しく設定されていることを確認
            self.assertIn(shadow_frame.mode, (0, 1, 2))
            # シャドウ距離が正しく設定されていることを確認
            self.assertGreaterEqual(shadow_frame.distance, 0.0)
        finally:
            # 一時ファイルを削除
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def test_parse_vmd_without_trailing_shadow_and_ik_sections(self):
        """self-shadow/IK セクションが省略された VMD も読み込めることを確認する。"""
        mock_vmd_data = VmdMock.create_custom_vmd(
            model_name="TestModel",
            bone_frame_count=0,
            morph_frame_count=0,
            light_frame_count=1,
        )
        # VMD variants in the wild may end immediately after the light section.
        mock_vmd_data = mock_vmd_data[:-8]

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".vmd", delete=False) as temp_file:
            temp_file.write(mock_vmd_data)
            temp_file_path = temp_file.name

        try:
            parsed_data = mmd_parser.parse_mmd_file(temp_file_path)
        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

        self.assertEqual(len(parsed_data.light_frames), 1)
        self.assertEqual(parsed_data.shadow_frames, [])
        self.assertEqual(parsed_data.ik_show_hide_frames, [])

    def test_parse_vmd_ik_show_hide_frames(self):
        """VMD IK表示/非表示フレームが正しく解析されることをテストする。"""
        # Noneでないことを確認
        self.assertIsNotNone(self.parsed_data.ik_show_hide_frames)
        # listであることを確認
        self.assertIsInstance(self.parsed_data.ik_show_hide_frames, list)

        # 実際のVMDファイルにIKフレームが含まれている場合のテスト
        if self.parsed_data.ik_show_hide_frames:
            ik_frame = self.parsed_data.ik_show_hide_frames[0]
            # IKフレームの属性が正しく設定されていることを確認
            self.assertIsInstance(ik_frame.frame_number, int)
            # IK表示/非表示の状態が正しく設定されていることを確認
            self.assertIn(ik_frame.visible, (0, 1))
            # IKの数が正しく設定されていることを確認
            self.assertGreater(ik_frame.ik_count, 0)
            # IK状態のリストが正しく設定されていることを確認
            self.assertIsInstance(ik_frame.ik_states, list)

    def test_parse_vmd_with_invalid_data(self):
        """不正なVMDデータのパースがエラーを発生させることをテストする。"""
        # 不正なVMDデータを作成
        invalid_vmd_data = VmdMock.create_invalid_vmd()

        # 一時ファイルに書き込み
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".vmd", delete=False) as temp_file:
            temp_file.write(invalid_vmd_data)
            temp_file_path = temp_file.name

        try:
            # パース実行（不正データのため MMDParseException が発生することを期待）
            with self.assertRaises(MMDParseException):
                mmd_parser.parse_mmd_file(temp_file_path)
        finally:
            # 一時ファイルを削除
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
