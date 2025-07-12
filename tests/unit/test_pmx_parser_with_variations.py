"""
PMXパーサーの様々なケースをテストするモジュール
"""

import os
import unittest
from mmd_tools.core import mmd_parser
from mmd_tools.core.exceptions import MMDParseException
from tests.common.test_base import TestBase
from tests.common.pmx_mock import PmxMock


class TestPmxParserVariations(TestBase):
    """PMXパーサーの様々なケースをテストするクラス"""
    
    def test_parse_minimal_pmx(self):
        """最小限のPMXファイルが正しく解析されることをテストする"""
        # 最小限のPMXファイルを作成
        pmx_file = os.path.join(self.temp_dir, "minimal.pmx")
        with open(pmx_file, "wb") as f:
            f.write(PmxMock.create_minimal_pmx())
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmx_file)
        
        # 基本的な確認
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.magic, b'PMX ')
        self.assertEqual(parsed_data.header.version, 2.0)
        self.assertEqual(len(parsed_data.vertices), 8)  # 立方体の頂点
        self.assertEqual(len(parsed_data.faces), 12)  # 立方体の面
        self.assertEqual(len(parsed_data.materials), 1)
        self.assertEqual(len(parsed_data.bones), 3)
    
    def test_parse_full_pmx(self):
        """全機能を含むPMXファイルが正しく解析されることをテストする"""
        # フルバージョンのPMXファイルを作成
        pmx_file = os.path.join(self.temp_dir, "full.pmx")
        with open(pmx_file, "wb") as f:
            f.write(PmxMock.create_full_pmx())
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmx_file)
        
        # 詳細な確認
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.magic, b'PMX ')
        self.assertEqual(parsed_data.header.version, 2.1)
        
        # 材質の確認
        self.assertEqual(len(parsed_data.materials), 2)
        self.assertEqual(parsed_data.materials[0].diffuse[0], 1.0)  # 赤
        self.assertEqual(parsed_data.materials[1].diffuse[2], 1.0)  # 青
        
        # ボーンの確認  
        self.assertGreater(len(parsed_data.bones), 15)  # MMD標準骨格
        
        # IKの確認
        # PMXではIKはボーンに統合されている
        ik_bones = [bone for bone in parsed_data.bones if bone.ik_info is not None]
        self.assertGreater(len(ik_bones), 0)
        
        # モーフの確認
        self.assertEqual(len(parsed_data.morphs), 3)  # まばたき, 笑い, ウィンク
        
        # 剛体とジョイントの確認
        self.assertEqual(len(parsed_data.rigid_bodies), 2)
        self.assertEqual(len(parsed_data.joints), 1)
    
    def test_parse_invalid_pmx(self):
        """不正なPMXファイルで例外が発生することをテストする"""
        # 不正なPMXファイルを作成
        pmx_file = os.path.join(self.temp_dir, "invalid.pmx")
        with open(pmx_file, "wb") as f:
            f.write(PmxMock.create_invalid_pmx())
        
        # 解析時に例外が発生することを確認
        with self.assertRaises(MMDParseException):
            mmd_parser.parse_mmd_file(pmx_file)
    
    def test_parse_pmx_v20(self):
        """PMX 2.0ファイルが正しく解析されることをテストする"""
        # PMX 2.0ファイルを作成
        pmx_file = os.path.join(self.temp_dir, "pmx_v20.pmx")
        with open(pmx_file, "wb") as f:
            f.write(PmxMock.create_pmx_v20())
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmx_file)
        
        # バージョン確認
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.version, 2.0)
    
    def test_parse_pmx_with_additional_uv(self):
        """追加UVを含むPMXファイルが正しく解析されることをテストする"""
        # 追加UV付きPMXファイルを作成
        pmx_file = os.path.join(self.temp_dir, "additional_uv.pmx")
        with open(pmx_file, "wb") as f:
            f.write(PmxMock.create_pmx_with_additional_uv(2))  # 追加UV2つ
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmx_file)
        
        # 追加UVの確認
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.additional_uv, 2)
        if len(parsed_data.vertices) > 0:
            vertex = parsed_data.vertices[0]
            # 追加UVが存在することを確認
            self.assertTrue(hasattr(vertex, 'additional_uvs'))
            if hasattr(vertex, 'additional_uvs'):
                self.assertEqual(len(vertex.additional_uvs), 2)
    
    def test_parse_pmx_with_different_weight_types(self):
        """異なるウェイト変形方式のPMXファイルが正しく解析されることをテストする"""
        # BDEF1, BDEF2, BDEF4, SDEFを含むPMXファイルを作成
        pmx_file = os.path.join(self.temp_dir, "weight_types.pmx")
        with open(pmx_file, "wb") as f:
            f.write(PmxMock.create_pmx_with_weight_types())
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmx_file)
        
        # ウェイト変形方式の確認
        self.assertIsNotNone(parsed_data)
        if len(parsed_data.vertices) >= 4:
            # 各ウェイト変形方式を確認
            weight_types = [v.weight_transform_type for v in parsed_data.vertices[:4]]
            self.assertIn(0, weight_types)  # BDEF1
            self.assertIn(1, weight_types)  # BDEF2
            self.assertIn(2, weight_types)  # BDEF4
            self.assertIn(3, weight_types)  # SDEF
    
    def test_parse_pmx_with_utf8_encoding(self):
        """UTF-8エンコーディングのPMXファイルが正しく解析されることをテストする"""
        # UTF-8エンコーディングのPMXファイルを作成
        pmx_file = os.path.join(self.temp_dir, "utf8.pmx")
        with open(pmx_file, "wb") as f:
            f.write(PmxMock.create_minimal_pmx(text_encoding=1))  # UTF-8
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmx_file)
        
        # エンコーディングの確認
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.text_encoding, 'utf-8')
    
    def test_parse_custom_pmx(self):
        """カスタムパラメータのPMXファイルが正しく解析されることをテストする"""
        # カスタムPMXファイルを作成
        pmx_file = os.path.join(self.temp_dir, "custom.pmx")
        with open(pmx_file, "wb") as f:
            f.write(PmxMock.create_custom_pmx(
                vertex_count=100,
                face_count=50,
                material_count=5,
                bone_count=20,
                morph_count=10
            ))
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmx_file)
        
        # カスタムパラメータの確認
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.magic, b'PMX ')
        self.assertEqual(len(parsed_data.vertices), 100)
        self.assertEqual(len(parsed_data.faces), 50)
        self.assertEqual(len(parsed_data.materials), 5)
        self.assertEqual(len(parsed_data.bones), 20)
        self.assertEqual(len(parsed_data.morphs), 10)


if __name__ == "__main__":
    unittest.main()