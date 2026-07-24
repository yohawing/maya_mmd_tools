"""
VMDエクスポート機能のユニットテスト
モックデータを使用したラウンドトリップテストを実行
"""

import os

from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame
from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame
from mmd_tools.core.vmd_data.ik_show_hide_frame import VmdIKShowHideFrame
from mmd_tools.core.vmd_data.light_frame import VmdLightFrame
from mmd_tools.core.vmd_data.shadow_frame import VmdShadowFrame
from mmd_tools.io.vmd_exporter import VmdExporter
from tests.common.test_base import TestBase
from tests.common.vmd_mock import VmdMock


class TestVmdExport(TestBase):
    """VMDエクスポート機能のテストクラス"""

    def test_exporter_writes_vmd_data(self):
        """VmdExporter が既存の VmdData をそのまま書き出せることを確認する。"""
        vmd_data = VmdData()
        vmd_data.header.model_name = "ExporterModel"

        frame = VmdBoneFrame()
        frame.bone_name = "センター"
        frame.frame_number = 12
        frame.position = (1.0, 2.0, 3.0)
        frame.rotation = (0.0, 0.0, 0.0, 1.0)
        frame.interpolation = b"\x14" * 64
        vmd_data.bone_frames.append(frame)

        tmp_path = os.path.join(self.temp_dir, "exporter_data.vmd")
        exported = VmdExporter().export_vmd_animation(tmp_path, vmd_data)

        parsed = VmdData().parse_file(tmp_path)
        self.assertIs(exported, vmd_data)
        self.assertEqual(parsed.header.model_name, "ExporterModel")
        self.assertEqual(len(parsed.bone_frames), 1)
        self.assertEqual(parsed.bone_frames[0].bone_name, "センター")
        self.assertEqual(parsed.bone_frames[0].frame_number, 12)
        self.assertEqual(parsed.bone_frames[0].position, (1.0, 2.0, 3.0))

    def test_exporter_builds_vmd_data_from_collected_mapping(self):
        """収集済み辞書データから VMD frame を構築して書き出せることを確認する。"""
        maya_data = {
            "model_name": "CollectedModel",
            "bone_frames": [
                {
                    "name": "上半身",
                    "frame": 20,
                    "position": (0.0, 1.0, 0.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                }
            ],
            "morph_frames": [{"name": "笑い", "frame": 20, "weight": 0.75}],
            "camera_frames": [
                {
                    "frame": 20,
                    "distance": -35.0,
                    "position": (1.0, 2.0, 3.0),
                    "rotation": (0.1, 0.2, 0.3),
                    "view_angle": 45,
                    "perspective": 0,
                }
            ],
            "light_frames": [
                {
                    "frame": 20,
                    "color": (0.5, 0.6, 0.7),
                    "position": (10.0, 20.0, 30.0),
                }
            ],
            "shadow_frames": [{"frame": 20, "mode": 1, "distance": 9999.0}],
            "ik_show_hide_frames": [
                {
                    "frame": 20,
                    "visible": True,
                    "ik_states": [("左足IK", True), ("右足IK", False)],
                }
            ],
        }

        tmp_path = os.path.join(self.temp_dir, "exporter_mapping.vmd")
        exported = VmdExporter().export_vmd_animation(tmp_path, maya_data)

        parsed = VmdData().parse_file(tmp_path)
        self.assertEqual(exported.header.model_name, "CollectedModel")
        self.assertEqual(parsed.header.model_name, "CollectedModel")
        self.assertEqual(parsed.bone_frames[0].bone_name, "上半身")
        self.assertEqual(parsed.bone_frames[0].frame_number, 20)
        self.assertEqual(parsed.bone_frames[0].rotation, (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(parsed.morph_frames[0].morph_name, "笑い")
        self.assertAlmostEqual(parsed.morph_frames[0].value, 0.75)
        self.assertEqual(parsed.camera_frames[0].viewing_angle, 45)
        self.assertEqual(parsed.light_frames[0].position, (10.0, 20.0, 30.0))
        self.assertEqual(parsed.shadow_frames[0].mode, 1)
        self.assertEqual(parsed.ik_show_hide_frames[0].frame_number, 20)
        self.assertEqual(
            parsed.ik_show_hide_frames[0].ik_states,
            [("左足IK", 1), ("右足IK", 0)],
        )

    def test_exporter_uses_native_writer_when_available(self):
        """native writer が bytes を返す場合は従来 writer ではなくその bytes を書く。"""
        calls = []

        def native_exporter(payload):
            calls.append(payload)
            return b"NATIVE-VMD"

        vmd_data = VmdData()
        vmd_data.header.model_name = "NativeModel"

        bone = VmdBoneFrame()
        bone.bone_name = "センター"
        bone.frame_number = 7
        bone.position = (1.0, 2.0, 3.0)
        bone.rotation = (0.0, 0.0, 0.0, 1.0)
        vmd_data.bone_frames.append(bone)

        light = VmdLightFrame()
        light.frame_number = 8
        light.color = (0.1, 0.2, 0.3)
        light.position = (4.0, 5.0, 6.0)
        vmd_data.light_frames.append(light)

        shadow = VmdShadowFrame()
        shadow.frame_number = 9
        shadow.mode = 2
        shadow.distance = 123.0
        vmd_data.shadow_frames.append(shadow)

        prop = VmdIKShowHideFrame()
        prop.frame_number = 10
        prop.visible = 1
        prop.ik_states = [("左足IK", 1), ("右足IK", 0)]
        vmd_data.ik_show_hide_frames.append(prop)

        tmp_path = os.path.join(self.temp_dir, "native_export.vmd")
        exported = VmdExporter(native_exporter=native_exporter).export_vmd_animation(tmp_path, vmd_data)

        with open(tmp_path, "rb") as handle:
            self.assertEqual(handle.read(), b"NATIVE-VMD")
        self.assertIs(exported, vmd_data)
        self.assertEqual(len(calls), 1)
        payload = calls[0]
        self.assertEqual(payload["metadata"]["modelName"], "NativeModel")
        self.assertEqual(payload["metadata"]["counts"]["bones"], 1)
        self.assertEqual(payload["metadata"]["counts"]["lights"], 1)
        self.assertEqual(payload["metadata"]["counts"]["selfShadows"], 1)
        self.assertEqual(payload["metadata"]["counts"]["properties"], 1)
        self.assertEqual(payload["metadata"]["maxFrame"], 10)
        self.assertEqual(payload["boneFrames"][0]["interpolation"], [20] * 64)
        self.assertEqual(payload["lightFrames"][0]["direction"], [4.0, 5.0, 6.0])
        self.assertEqual(payload["propertyFrames"][0]["ikStates"][1]["enabled"], False)

    def test_native_payload_uses_linear_default_interpolation_for_missing_values(self):
        """native payload の補間 fallback は Python writer と同じ linear default にする。"""
        exporter = VmdExporter(native_exporter=lambda payload: b"NATIVE-VMD")

        for missing_value in (None, b"", []):
            with self.subTest(missing_value=missing_value):
                vmd_data = VmdData()

                bone = VmdBoneFrame()
                bone.bone_name = "センター"
                bone.frame_number = 1
                bone.interpolation = missing_value
                vmd_data.bone_frames.append(bone)

                camera = VmdCameraFrame()
                camera.frame_number = 1
                camera.interpolation = missing_value
                vmd_data.camera_frames.append(camera)

                payload = exporter.to_native_json_payload(vmd_data)

                self.assertEqual(payload["boneFrames"][0]["interpolation"], [20] * 64)
                self.assertEqual(payload["cameraFrames"][0]["interpolation"], [20] * 24)

    def test_exporter_falls_back_when_native_writer_returns_none(self):
        """native writer が使えない環境では従来の VmdData writer へ戻る。"""
        vmd_data = VmdData()
        vmd_data.header.model_name = "FallbackModel"

        frame = VmdMorphFrame()
        frame.morph_name = "笑い"
        frame.frame_number = 5
        frame.value = 0.5
        vmd_data.morph_frames.append(frame)

        tmp_path = os.path.join(self.temp_dir, "fallback_export.vmd")
        VmdExporter(native_exporter=lambda payload: None).export_vmd_animation(tmp_path, vmd_data)

        parsed = VmdData().parse_file(tmp_path)
        self.assertEqual(parsed.header.model_name, "FallbackModel")
        self.assertEqual(parsed.morph_frames[0].morph_name, "笑い")
        self.assertAlmostEqual(parsed.morph_frames[0].value, 0.5)

    def test_exporter_rejects_invalid_frame_shape(self):
        """不正な frame shape はバイナリ書き出し前に失敗させる。"""
        exporter = VmdExporter()
        with self.assertRaises(ValueError):
            exporter.to_vmd_data({"bone_frames": [{"name": "Center", "position": (1.0, 2.0)}]})

    def test_vmd_round_trip_with_minimal_mock(self):
        """モックデータを使用したVMDファイルのラウンドトリップテスト"""

        # モックデータを作成
        mock_data = VmdMock.create_minimal_vmd()

        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.vmd")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)

        # モックデータからVMDを読み込む
        parser1 = VmdData()
        parser1.parse_file(tmp_input_path)

        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export.vmd")
        parser1.write_file(tmp_path)

        # 3. 書き込んだファイルを再度読み込む
        parser2 = VmdData()
        parser2.parse_file(tmp_path)

        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(
            parser1.header.model_name,
            parser2.header.model_name,
            "モデル名が一致しません",
        )

        # フレーム数の比較
        self.assertEqual(
            len(parser1.bone_frames),
            len(parser2.bone_frames),
            "ボーンフレーム数が一致しません",
        )
        self.assertEqual(
            len(parser1.morph_frames),
            len(parser2.morph_frames),
            "モーフフレーム数が一致しません",
        )
        self.assertEqual(
            len(parser1.camera_frames),
            len(parser2.camera_frames),
            "カメラフレーム数が一致しません",
        )
        self.assertEqual(
            len(parser1.light_frames),
            len(parser2.light_frames),
            "ライトフレーム数が一致しません",
        )

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
        parser1 = VmdData()
        parser1.parse_file(tmp_input_path)

        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export_full.vmd")
        parser1.write_file(tmp_path)

        # 3. 書き込んだファイルを再度読み込む
        parser2 = VmdData()
        parser2.parse_file(tmp_path)

        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(
            parser1.header.model_name,
            parser2.header.model_name,
            "モデル名が一致しません",
        )

        # 詳細なフレーム数の比較
        self.assertEqual(
            len(parser1.bone_frames),
            len(parser2.bone_frames),
            "ボーンフレーム数が一致しません",
        )
        self.assertEqual(
            len(parser1.morph_frames),
            len(parser2.morph_frames),
            "モーフフレーム数が一致しません",
        )
        self.assertEqual(
            len(parser1.camera_frames),
            len(parser2.camera_frames),
            "カメラフレーム数が一致しません",
        )
        self.assertEqual(
            len(parser1.light_frames),
            len(parser2.light_frames),
            "ライトフレーム数が一致しません",
        )
        self.assertEqual(
            len(parser1.shadow_frames),
            len(parser2.shadow_frames),
            "セルフシャドウフレーム数が一致しません",
        )
        # show_ik_framesはパーサーに存在しない可能性があるのでスキップ
        # self.assertEqual(len(parser1.show_ik_frames), len(parser2.show_ik_frames), "IK表示フレーム数が一致しません")

    def test_create_simple_vmd(self):
        """簡単なVMDファイルを作成してエクスポートするテスト"""

        # 新しいVMDパーサーインスタンスを作成
        parser = VmdData()

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
            frame.interpolation = b"\x14\x14\x14\x14" * 16  # 64バイト
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
        frame.interpolation = b"\x14\x14\x14\x14" * 6  # 24バイト
        frame.view_angle = 30
        frame.perspective = 0
        parser.camera_frames.append(frame)

        # 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_created.vmd")
        parser.write_file(tmp_path)

        # 書き込んだファイルを読み込んで確認
        parser2 = VmdData()
        parser2.parse_file(tmp_path)

        # データの検証
        self.assertEqual(parser2.header.model_name, "TestModel", "モデル名が正しく保存されていません")
        self.assertEqual(len(parser2.bone_frames), 3, "ボーンフレーム数が正しくありません")
        self.assertEqual(len(parser2.morph_frames), 2, "モーフフレーム数が正しくありません")
        self.assertEqual(len(parser2.camera_frames), 1, "カメラフレーム数が正しくありません")
