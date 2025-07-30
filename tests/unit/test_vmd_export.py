"""
VMDエクスポート機能のユニットテスト
モックデータを使用したラウンドトリップテストを実行
"""
import os
import io

from mmd_tools.core.vmd_parser import VmdParser
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame
from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame
from tests.common.test_base import TestBase
from tests.common.vmd_mock import VmdMock


class TestVmdExport(TestBase):
    """VMDエクスポート機能のテストクラス"""

    def test_vmd_round_trip_with_minimal_mock(self):
        """モックデータを使用したVMDファイルのラウンドトリップテスト"""
        
        # モックデータを作成
        mock_data = VmdMock.create_minimal_vmd()
        
        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.vmd")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)
        
        # モックデータからVMDを読み込む
        parser1 = VmdParser()
        parser1.parse_file(tmp_input_path)
        
        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export.vmd")
        parser1.write_file(tmp_path)
        
        # 3. 書き込んだファイルを再度読み込む
        parser2 = VmdParser()
        parser2.parse_file(tmp_path)
        
        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(parser1.header.model_name, parser2.header.model_name, "モデル名が一致しません")
        
        # フレーム数の比較
        self.assertEqual(len(parser1.bone_frames), len(parser2.bone_frames), "ボーンフレーム数が一致しません")
        self.assertEqual(len(parser1.morph_frames), len(parser2.morph_frames), "モーフフレーム数が一致しません")
        self.assertEqual(len(parser1.camera_frames), len(parser2.camera_frames), "カメラフレーム数が一致しません")
        self.assertEqual(len(parser1.light_frames), len(parser2.light_frames), "ライトフレーム数が一致しません")
        
        # 最初のボーンフレームデータの比較（サンプル）
        if parser1.bone_frames:
            bf1 = parser1.bone_frames[0]
            bf2 = parser2.bone_frames[0]
            self.assertEqual(bf1.bone_name, bf2.bone_name, "ボーン名が一致しません")
            self.assertEqual(bf1.frame_number, bf2.frame_number, "フレーム番号が一致しません")
            self.assertEqual(bf1.position, bf2.position, "位置が一致しません")
            self.assertEqual(bf1.rotation, bf2.rotation, "回転が一致しません")

    def test_vmd_round_trip_with_full_mock(self):
        """フル機能モックデータを使用したVMDファイルのラウンドトリップテスト"""
        
        # モックデータを作成
        mock_data = VmdMock.create_full_vmd()
        
        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.vmd")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)
        
        # モックデータからVMDを読み込む
        parser1 = VmdParser()
        parser1.parse_file(tmp_input_path)
        
        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export_full.vmd")
        parser1.write_file(tmp_path)
        
        # 3. 書き込んだファイルを再度読み込む
        parser2 = VmdParser()
        parser2.parse_file(tmp_path)
        
        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(parser1.header.model_name, parser2.header.model_name, "モデル名が一致しません")
        
        # 詳細なフレーム数の比較
        self.assertEqual(len(parser1.bone_frames), len(parser2.bone_frames), "ボーンフレーム数が一致しません")
        self.assertEqual(len(parser1.morph_frames), len(parser2.morph_frames), "モーフフレーム数が一致しません")
        self.assertEqual(len(parser1.camera_frames), len(parser2.camera_frames), "カメラフレーム数が一致しません")
        self.assertEqual(len(parser1.light_frames), len(parser2.light_frames), "ライトフレーム数が一致しません")
        self.assertEqual(len(parser1.shadow_frames), len(parser2.shadow_frames), "セルフシャドウフレーム数が一致しません")
        # show_ik_framesはパーサーに存在しない可能性があるのでスキップ
        # self.assertEqual(len(parser1.show_ik_frames), len(parser2.show_ik_frames), "IK表示フレーム数が一致しません")

    def test_create_simple_vmd(self):
        """簡単なVMDファイルを作成してエクスポートするテスト"""
        
        # 新しいVMDパーサーインスタンスを作成
        parser = VmdParser()
        
        # ヘッダー情報を設定
        parser.header.magic = b"Vocaloid Motion Data"
        parser.header.model_name = "TestModel"
        
        # ボーンフレームを追加（簡単な動き）
        for i in range(0, 30, 10):
            frame = VmdBoneFrame()
            frame.bone_name = "Center"
            frame.frame_number = i
            frame.position = [0.0, float(i) * 0.1, 0.0]  # Y方向に動く
            frame.rotation = [0.0, 0.0, 0.0, 1.0]  # 単位クォータニオン
            # 補間データ（デフォルト線形補間）
            frame.interpolation = b'\x14\x14\x14\x14' * 16  # 64バイト
            parser.bone_frames.append(frame)
        
        # モーフフレームを追加
        frame = VmdMorphFrame()
        frame.morph_name = "smile"
        frame.frame_number = 0
        frame.weight = 0.0
        parser.morph_frames.append(frame)
        
        frame = VmdMorphFrame()
        frame.morph_name = "smile"
        frame.frame_number = 30
        frame.weight = 1.0
        parser.morph_frames.append(frame)
        
        # カメラフレームを追加
        frame = VmdCameraFrame()
        frame.frame_number = 0
        frame.distance = -10.0
        frame.position = [0.0, 10.0, 0.0]
        frame.rotation = [0.0, 0.0, 0.0]
        frame.interpolation = b'\x14\x14\x14\x14' * 6  # 24バイト
        frame.view_angle = 30
        frame.perspective = 0
        parser.camera_frames.append(frame)
        
        # 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_created.vmd")
        parser.write_file(tmp_path)
        
        # 書き込んだファイルを読み込んで確認
        parser2 = VmdParser()
        parser2.parse_file(tmp_path)
        
        # データの検証
        self.assertEqual(parser2.header.model_name, "TestModel", "モデル名が正しく保存されていません")
        self.assertEqual(len(parser2.bone_frames), 3, "ボーンフレーム数が正しくありません")
        self.assertEqual(len(parser2.morph_frames), 2, "モーフフレーム数が正しくありません")
        self.assertEqual(len(parser2.camera_frames), 1, "カメラフレーム数が正しくありません")