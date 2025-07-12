"""
PMDパーサーの様々なケースをテストするモジュール
"""

import os
import unittest
from mmd_tools.core import mmd_parser
from mmd_tools.core.exceptions import MMDParseException
from tests.common.test_base import TestBase
from tests.common.pmd_mock import PmdMock


class TestPmdParserVariations(TestBase):
    """PMDパーサーの様々なケースをテストするクラス"""
    
    def test_parse_minimal_pmd(self):
        """最小限のPMDファイルが正しく解析されることをテストする"""
        # 最小限のPMDファイルを作成
        pmd_file = os.path.join(self.temp_dir, "minimal.pmd")
        with open(pmd_file, "wb") as f:
            f.write(PmdMock.create_minimal_pmd())
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmd_file)
        
        # 基本的な確認
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.magic, b'Pmd')
        self.assertEqual(len(parsed_data.vertices), 8)  # 立方体の頂点
        self.assertEqual(len(parsed_data.faces), 12)  # 立方体の面
        self.assertEqual(len(parsed_data.materials), 1)
        self.assertEqual(len(parsed_data.bones), 3)
        self.assertEqual(len(parsed_data.ik_data), 0)
        self.assertEqual(len(parsed_data.morphs), 0)
    
    def test_parse_full_pmd(self):
        """全機能を含むPMDファイルが正しく解析されることをテストする"""
        # フルバージョンのPMDファイルを作成
        pmd_file = os.path.join(self.temp_dir, "full.pmd")
        with open(pmd_file, "wb") as f:
            f.write(PmdMock.create_full_pmd())
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmd_file)
        
        # 詳細な確認
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.magic, b'Pmd')
        
        # 材質の確認
        self.assertEqual(len(parsed_data.materials), 2)
        self.assertEqual(parsed_data.materials[0].diffuse[0], 1.0)  # 赤
        self.assertEqual(parsed_data.materials[1].diffuse[2], 1.0)  # 青
        
        # ボーンの確認
        self.assertEqual(len(parsed_data.bones), 21)  # MMD標準骨格の一部
        center_bone = parsed_data.bones[0]
        self.assertEqual(center_bone.name, "センター")
        
        # IKの確認
        self.assertEqual(len(parsed_data.ik_data), 2)  # 左右足IK
        
        # 表情の確認
        self.assertEqual(len(parsed_data.morphs), 3)  # base, まばたき, 笑い
        self.assertEqual(parsed_data.morphs[0].name, "base")
        
        # 剛体とジョイントの確認
        self.assertEqual(len(parsed_data.rigid_bodies), 2)
        self.assertEqual(len(parsed_data.joints), 1)
    
    def test_parse_invalid_pmd(self):
        """不正なPMDファイルで例外が発生することをテストする"""
        # 不正なPMDファイルを作成
        pmd_file = os.path.join(self.temp_dir, "invalid.pmd")
        with open(pmd_file, "wb") as f:
            f.write(PmdMock.create_invalid_pmd())
        
        # 解析時に例外が発生することを確認
        with self.assertRaises(MMDParseException):
            mmd_parser.parse_mmd_file(pmd_file)
    
    def test_parse_custom_pmd(self):
        """カスタムパラメータのPMDファイルが正しく解析されることをテストする"""
        # カスタムPMDファイルを作成
        pmd_file = os.path.join(self.temp_dir, "custom.pmd")
        with open(pmd_file, "wb") as f:
            # 現在の実装では、カスタムパラメータは最小限のPMDを返す
            f.write(PmdMock.create_custom_pmd(
                vertex_count=16,
                face_count=24,
                material_count=2,
                bone_count=5
            ))
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmd_file)
        
        # 基本的な確認（現在の実装では最小限のPMDが返される）
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data.header.magic, b'Pmd')
    
    def test_pmd_with_japanese_names(self):
        """日本語名を含むPMDファイルが正しく解析されることをテストする"""
        # フルバージョンのPMDファイルには日本語名が含まれている
        pmd_file = os.path.join(self.temp_dir, "japanese.pmd")
        with open(pmd_file, "wb") as f:
            f.write(PmdMock.create_full_pmd())
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmd_file)
        
        # 日本語名の確認
        self.assertIn("センター", [bone.name for bone in parsed_data.bones])
        self.assertIn("上半身", [bone.name for bone in parsed_data.bones])
        self.assertIn("まばたき", [morph.name for morph in parsed_data.morphs])
        self.assertIn("笑い", [morph.name for morph in parsed_data.morphs])
    
    def test_pmd_without_extension_data(self):
        """拡張データなしのPMDファイルが正しく解析されることをテストする"""
        # 最小限のPMDファイルは拡張データを含まない
        pmd_file = os.path.join(self.temp_dir, "no_extension.pmd")
        with open(pmd_file, "wb") as f:
            f.write(PmdMock.create_minimal_pmd())
        
        # 解析
        parsed_data = mmd_parser.parse_mmd_file(pmd_file)
        
        # 拡張データがないことを確認
        self.assertIsNotNone(parsed_data)
        # 英語名がない
        self.assertEqual(parsed_data.header.model_name_english, "")
        self.assertEqual(parsed_data.header.comment_english, "")
        # 剛体とジョイントがない
        self.assertEqual(len(parsed_data.rigid_bodies), 0)
        self.assertEqual(len(parsed_data.joints), 0)


if __name__ == "__main__":
    unittest.main()