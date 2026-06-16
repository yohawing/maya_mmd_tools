"""
PMDエクスポート機能のユニットテスト
モックデータを使用したラウンドトリップテストを実行
"""

import os

from mmd_tools.core.pmd_data import PmdData
from mmd_tools.core.pmd_data.vertex import PmdVertex
from mmd_tools.core.pmd_data.face import PmdFace
from mmd_tools.core.pmd_data.material import PmdMaterial
from mmd_tools.core.pmd_data.bone import PmdBone, PmdBoneType
from tests.common.test_base import TestBase
from tests.common.pmd_mock import PmdMock


class TestPmdExport(TestBase):
    """PMDエクスポート機能のテストクラス"""

    def test_pmd_round_trip_with_minimal_mock(self):
        """モックデータを使用したPMDファイルのラウンドトリップテスト"""

        # モックデータを作成
        mock_data = PmdMock.create_minimal_pmd()

        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.pmd")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)

        # モックデータからPMDを読み込む
        parser1 = PmdData()
        parser1.parse_file(tmp_input_path)

        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export.pmd")
        parser1.write_file(tmp_path)

        # 3. 書き込んだファイルを再度読み込む
        parser2 = PmdData()
        parser2.parse_file(tmp_path)

        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(
            parser1.header.model_name,
            parser2.header.model_name,
            "モデル名が一致しません",
        )
        self.assertEqual(parser1.header.comment, parser2.header.comment, "コメントが一致しません")

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

    def test_pmd_round_trip_with_full_mock(self):
        """フル機能モックデータを使用したPMDファイルのラウンドトリップテスト"""

        # モックデータを作成
        mock_data = PmdMock.create_full_pmd()

        # 1. モックデータを一時ファイルに書き込む
        tmp_input_path = os.path.join(self.temp_dir, "test_input.pmd")
        with open(tmp_input_path, "wb") as f:
            f.write(mock_data)

        # モックデータからPMDを読み込む
        parser1 = PmdData()
        parser1.parse_file(tmp_input_path)

        # 2. 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_export_full.pmd")
        parser1.write_file(tmp_path)

        # 3. 書き込んだファイルを再度読み込む
        parser2 = PmdData()
        parser2.parse_file(tmp_path)

        # 4. データの一致を確認
        # ヘッダー情報の比較
        self.assertEqual(
            parser1.header.model_name,
            parser2.header.model_name,
            "モデル名が一致しません",
        )
        self.assertEqual(parser1.header.comment, parser2.header.comment, "コメントが一致しません")

        # 詳細なデータ数の比較
        self.assertEqual(len(parser1.vertices), len(parser2.vertices), "頂点数が一致しません")
        self.assertEqual(len(parser1.faces), len(parser2.faces), "面数が一致しません")
        self.assertEqual(len(parser1.materials), len(parser2.materials), "マテリアル数が一致しません")
        self.assertEqual(len(parser1.bones), len(parser2.bones), "ボーン数が一致しません")
        self.assertEqual(len(parser1.ik_data), len(parser2.ik_data), "IK数が一致しません")
        self.assertEqual(len(parser1.morphs), len(parser2.morphs), "モーフ数が一致しません")

    def test_create_simple_pmd(self):
        """簡単なPMDファイルを作成してエクスポートするテスト"""

        # 新しいPMDパーサーインスタンスを作成
        parser = PmdData()

        # ヘッダー情報を設定
        parser.header.magic = b"Pmd"
        parser.header.version = 1.0
        parser.header.model_name = "TestModel"
        parser.header.comment = "This is a test model created by export test"

        # 簡単な三角形の頂点を追加
        # 頂点1
        v1 = PmdVertex()
        v1.position = (0.0, 0.0, 0.0)
        v1.normal = (0.0, 1.0, 0.0)
        v1.uv = (0.0, 0.0)
        v1.bone_indices = (0, 0)
        v1.bone_weight = 100
        v1.edge_flag = 1
        parser.vertices.append(v1)

        # 頂点2
        v2 = PmdVertex()
        v2.position = (1.0, 0.0, 0.0)
        v2.normal = (0.0, 1.0, 0.0)
        v2.uv = (1.0, 0.0)
        v2.bone_indices = (0, 0)
        v2.bone_weight = 100
        v2.edge_flag = 1
        parser.vertices.append(v2)

        # 頂点3
        v3 = PmdVertex()
        v3.position = (0.0, 0.0, 1.0)
        v3.normal = (0.0, 1.0, 0.0)
        v3.uv = (0.0, 1.0)
        v3.bone_indices = (0, 0)
        v3.bone_weight = 100
        v3.edge_flag = 1
        parser.vertices.append(v3)

        # 面（三角形）を追加
        face = PmdFace()
        face.indices = (0, 1, 2)
        parser.faces.append(face)

        # マテリアルを追加
        material = PmdMaterial(0)
        material.diffuse = (0.8, 0.8, 0.8, 1.0)
        material.specular_power = 5.0
        material.specular = (0.5, 0.5, 0.5)
        material.ambient = (0.3, 0.3, 0.3)
        material.toon_texture_index = 0
        material.edge_flag = 1
        material.face_count = 1  # 1面（3頂点）
        material.texture_file_name = ""
        parser.materials.append(material)

        # 最低限のボーンを追加（ルートボーン）
        bone = PmdBone()
        bone.name = "root"
        bone.parent_bone_index = -1
        bone.tail_pos_bone_index = 0xFFFF
        bone.bone_type = PmdBoneType.ROTATE_AND_MOVE
        bone.ik_parent_bone_index = 0
        bone.position = (0.0, 0.0, 0.0)
        parser.bones.append(bone)

        # 一時ファイルに書き込む
        tmp_path = os.path.join(self.temp_dir, "test_created.pmd")
        parser.write_file(tmp_path)

        # 書き込んだファイルを読み込んで確認
        parser2 = PmdData()
        parser2.parse_file(tmp_path)

        # データの検証
        self.assertEqual(parser2.header.model_name, "TestModel")
        self.assertEqual(len(parser2.vertices), 3)
        self.assertEqual(len(parser2.faces), 1)
        self.assertEqual(len(parser2.materials), 1)
        self.assertEqual(len(parser2.bones), 1)
