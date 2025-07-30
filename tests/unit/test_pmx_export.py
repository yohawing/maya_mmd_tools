"""
PMXエクスポート機能のユニットテスト
モックデータを使用したラウンドトリップテストを実行
"""
import os
import io

from mmd_tools.core.pmx_parser import PmxParser
from mmd_tools.core.pmx_data.vertex import PmxVertex
from mmd_tools.core.pmx_data.face import PmxFace
from mmd_tools.core.pmx_data.material import PmxMaterial
from mmd_tools.core.pmx_data.bone import PmxBone
from mmd_tools.core.pmx_data.display_frame import PmxDisplayFrame
from tests.common.test_base import TestBase
from tests.common.pmx_mock import PmxMock


class TestPmxExport(TestBase):
    """PMXエクスポート機能のテストクラス"""

    def test_pmx_round_trip_with_minimal_mock(self):
        """モックデータを使用したPMXファイルのラウンドトリップテスト"""
        
        # モックデータを作成
        mock_data = PmxMock.create_minimal_pmx(version=2.0)
        
        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.pmx")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)
        
        # モックデータからPMXを読み込む
        parser1 = PmxParser()
        parser1.parse_file(tmp_input_path)
        
        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export.pmx")
        parser1.write_file(tmp_path)
        
        # 3. 書き込んだファイルを再度読み込む
        parser2 = PmxParser()
        parser2.parse_file(tmp_path)
        
        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(parser1.header.model_name, parser2.header.model_name, "モデル名が一致しません")
        self.assertEqual(parser1.header.comment, parser2.header.comment, "コメントが一致しません")
        self.assertAlmostEqual(parser1.header.version, parser2.header.version, places=3, msg="バージョンが一致しません")
        
        # 頂点数の比較
        self.assertEqual(len(parser1.vertices), len(parser2.vertices), "頂点数が一致しません")
        
        # 最初の頂点データの比較（サンプル）
        if parser1.vertices:
            v1 = parser1.vertices[0]
            v2 = parser2.vertices[0]
            self.assertEqual(v1.position, v2.position, "頂点位置が一致しません")
            self.assertEqual(v1.normal, v2.normal, "頂点法線が一致しません")
            self.assertEqual(v1.uv, v2.uv, "頂点UVが一致しません")
        
        # 面数の比較
        self.assertEqual(len(parser1.faces), len(parser2.faces), "面数が一致しません")
        
        # マテリアル数の比較
        self.assertEqual(len(parser1.materials), len(parser2.materials), "マテリアル数が一致しません")
        
        # ボーン数の比較
        self.assertEqual(len(parser1.bones), len(parser2.bones), "ボーン数が一致しません")

    def test_pmx_round_trip_with_full_mock(self):
        """フル機能モックデータを使用したPMXファイルのラウンドトリップテスト"""
        
        # モックデータを作成
        mock_data = PmxMock.create_full_pmx(version=2.1)
        
        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.pmx")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)
        
        # モックデータからPMXを読み込む
        parser1 = PmxParser()
        parser1.parse_file(tmp_input_path)
        
        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export_full.pmx")
        parser1.write_file(tmp_path)
        
        # 3. 書き込んだファイルを再度読み込む
        parser2 = PmxParser()
        parser2.parse_file(tmp_path)
        
        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(parser1.header.model_name, parser2.header.model_name, "モデル名が一致しません")
        self.assertEqual(parser1.header.comment, parser2.header.comment, "コメントが一致しません")
        
        # 詳細なデータ数の比較
        self.assertEqual(len(parser1.vertices), len(parser2.vertices), "頂点数が一致しません")
        self.assertEqual(len(parser1.faces), len(parser2.faces), "面数が一致しません")
        self.assertEqual(len(parser1.materials), len(parser2.materials), "マテリアル数が一致しません")
        self.assertEqual(len(parser1.bones), len(parser2.bones), "ボーン数が一致しません")
        self.assertEqual(len(parser1.morphs), len(parser2.morphs), "モーフ数が一致しません")
        self.assertEqual(len(parser1.display_frames), len(parser2.display_frames), "表示枠数が一致しません")

    def test_create_simple_pmx(self):
        """簡単なPMXファイルを作成してエクスポートするテスト"""
        
        # 新しいPMXパーサーインスタンスを作成
        parser = PmxParser()
        
        # ヘッダー情報を設定
        parser.header.magic = b"PMX "
        parser.header.version = 2.0
        parser.header.model_name = "TestModel"
        parser.header.model_name_english = "TestModel"
        parser.header.comment = "This is a test model created by export test"
        parser.header.comment_english = "This is a test model created by export test"
        
        # インデックスサイズを設定（小さいモデルなので1バイトで十分）
        parser.header.vertex_index_size = 1
        parser.header.texture_index_size = 1
        parser.header.material_index_size = 1
        parser.header.bone_index_size = 1
        parser.header.morph_index_size = 1
        parser.header.rigid_body_index_size = 1
        
        # 簡単な三角形の頂点を追加
        # 頂点1
        v1 = PmxVertex(bone_index_size=parser.header.bone_index_size, additional_vec4_count=parser.header.additional_uv)
        v1.position = [0.0, 0.0, 0.0]
        v1.normal = [0.0, 1.0, 0.0]
        v1.uv = [0.0, 0.0]
        v1.weight_transform_type = 0  # BDEF1
        v1.bone_indices = [0]
        v1.bone_weights = []
        v1.edge_magnification = 1.0
        parser.vertices.append(v1)
        
        # 頂点2
        v2 = PmxVertex(bone_index_size=parser.header.bone_index_size, additional_vec4_count=parser.header.additional_uv)
        v2.position = [1.0, 0.0, 0.0]
        v2.normal = [0.0, 1.0, 0.0]
        v2.uv = [1.0, 0.0]
        v2.weight_transform_type = 0  # BDEF1
        v2.bone_indices = [0]
        v2.bone_weights = []
        v2.edge_magnification = 1.0
        parser.vertices.append(v2)
        
        # 頂点3
        v3 = PmxVertex(bone_index_size=parser.header.bone_index_size, additional_vec4_count=parser.header.additional_uv)
        v3.position = [0.0, 0.0, 1.0]
        v3.normal = [0.0, 1.0, 0.0]
        v3.uv = [0.0, 1.0]
        v3.weight_transform_type = 0  # BDEF1
        v3.bone_indices = [0]
        v3.bone_weights = []
        v3.edge_magnification = 1.0
        parser.vertices.append(v3)
        
        # 面（三角形）を追加
        face = PmxFace(parser.header.vertex_index_size)
        face.indices = [0, 1, 2]
        parser.faces.append(face)
        
        # マテリアルを追加
        material = PmxMaterial(parser.header.texture_index_size, parser.header.encoding_flag, material_index=0)
        material.name = "Material1"
        material.name_english = "Material1"
        material.diffuse = [0.8, 0.8, 0.8, 1.0]
        material.specular = [0.5, 0.5, 0.5]
        material.specular_coefficient = 5.0
        material.ambient = [0.3, 0.3, 0.3]
        material.draw_flag = 0x01  # 両面描画
        material.edge_color = [0.0, 0.0, 0.0, 1.0]
        material.edge_size = 1.0
        material.texture_index = -1
        material.sphere_mode = 0
        material.sphere_texture_index = -1
        material.shared_toon_flag = 0
        material.toon_texture_index = 0
        material.memo = ""
        material.face_count = 1  # 1面（3頂点）
        parser.materials.append(material)
        
        # 最低限のボーンを追加（ルートボーン）
        bone = PmxBone(parser.header.bone_index_size, parser.header.encoding_flag)
        bone.name = "root"
        bone.name_english = "root"
        bone.position = [0.0, 0.0, 0.0]
        bone.parent_bone_index = -1
        bone.transform_layer = 0
        bone.bone_flag = 0x0001  # 接続先表示
        bone.connect_position_offset = [0.0, 1.0, 0.0]  # 接続先表示の場合は位置を設定
        parser.bones.append(bone)
        
        # 表示枠を追加（必須：Root, 表情）
        # Root表示枠
        root_frame = PmxDisplayFrame(parser.header.bone_index_size, parser.header.morph_index_size, parser.header.encoding_flag)
        root_frame.name = "Root"
        root_frame.name_english = "Root"
        root_frame.special_flag = 1  # 特殊枠
        parser.display_frames.append(root_frame)
        
        # 表情枠
        exp_frame = PmxDisplayFrame(parser.header.bone_index_size, parser.header.morph_index_size, parser.header.encoding_flag)
        exp_frame.name = "表情"
        exp_frame.name_english = "Exp"
        exp_frame.special_flag = 1  # 特殊枠
        parser.display_frames.append(exp_frame)
        
        # 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_created.pmx")
        parser.write_file(tmp_path)
        
        # 書き込んだファイルを読み込んで確認
        parser2 = PmxParser()
        parser2.parse_file(tmp_path)
        
        # データの検証
        self.assertEqual(parser2.header.model_name, "TestModel")
        self.assertEqual(parser2.header.version, 2.0)
        self.assertEqual(len(parser2.vertices), 3)
        self.assertEqual(len(parser2.faces), 1)
        self.assertEqual(len(parser2.materials), 1)
        self.assertEqual(len(parser2.bones), 1)