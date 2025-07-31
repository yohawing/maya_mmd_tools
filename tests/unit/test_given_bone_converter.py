#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
付与ボーン変換のテストケース
"""

import os
import sys
import unittest

# プロジェクトのルートディレクトリをPythonパスに追加
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from mmd_tools.core import mmd_parser
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


class TestGivenBoneConverter(unittest.TestCase):
    """付与ボーン変換のテストクラス"""
    
    def setUp(self):
        """テストの準備"""
        # 包括的な付与ボーン検証用PMXファイルのパス
        self.comprehensive_pmx_path = os.path.join(
            project_root, "tests", "data", "for_unit_test", 
            "test_given_bone_comprehensive.pmx"
        )
        
        # 既存の付与ボーン検証用PMXファイルのパス
        self.original_pmx_path = os.path.join(
            project_root, "tests", "data", "for_unit_test", 
            "test_given_bone.pmx"
        )
    
    def test_parse_comprehensive_given_bone_pmx(self):
        """包括的な付与ボーンPMXファイルが正しく解析できることを確認"""
        if not os.path.exists(self.comprehensive_pmx_path):
            self.skipTest(f"テストファイルが存在しません: {self.comprehensive_pmx_path}")
        
        # PMXファイルを解析
        pmx = mmd_parser.parse_mmd_file(self.comprehensive_pmx_path)
        
        # 基本的な確認
        self.assertIsNotNone(pmx)
        self.assertEqual(pmx.header.model_name, "付与ボーン検証モデル")
        self.assertEqual(len(pmx.bones), 23)
        
        # 付与ボーンのパターンを検証
        given_bone_patterns = []
        for i, bone in enumerate(pmx.bones):
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE) or bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
                pattern = {
                    "index": i,
                    "name": bone.name,
                    "name_en": bone.name_english,
                    "is_local": bool(bone.get_flag(PmxBoneFlag.LOCAL)),
                    "has_rotation": bool(bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE)),
                    "has_translation": bool(bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE)),
                    "given_parent": bone.given_parent_bone_index,
                    "given_rate": bone.given_rate,
                    "layer": bone.transform_layer,
                }
                given_bone_patterns.append(pattern)
        
        # 付与ボーンの数を確認
        self.assertEqual(len(given_bone_patterns), 10)
        
        # 各パターンの検証
        expected_patterns = [
            # グローバル回転付与（付与率1.0）
            {"name": "グローバル回転付与2", "is_local": False, "has_rotation": True, "has_translation": False, "given_rate": 1.0},
            # グローバル移動付与（付与率0.5）
            {"name": "グローバル移動付与3", "is_local": False, "has_rotation": False, "has_translation": True, "given_rate": 0.5},
            # グローバル回転+移動付与（付与率0.8）
            {"name": "グローバル回転移動付与4", "is_local": False, "has_rotation": True, "has_translation": True, "given_rate": 0.8},
            # ローカル回転付与（付与率1.0）
            {"name": "ローカル回転付与5", "is_local": True, "has_rotation": True, "has_translation": False, "given_rate": 1.0},
            # ローカル移動付与（付与率0.7）
            {"name": "ローカル移動付与6", "is_local": True, "has_rotation": False, "has_translation": True, "given_rate": 0.7},
            # ローカル回転+移動付与（付与率1.0）
            {"name": "ローカル回転移動付与7", "is_local": True, "has_rotation": True, "has_translation": True, "given_rate": 1.0},
            # 多重付与チェーンA
            {"name": "付与チェーン8A", "is_local": False, "has_rotation": True, "has_translation": False, "given_rate": 0.5, "layer": 1},
            # 多重付与チェーンB
            {"name": "付与チェーン8B", "is_local": False, "has_rotation": True, "has_translation": False, "given_rate": 0.5, "layer": 2},
            # 負の付与率
            {"name": "負の付与率9", "is_local": False, "has_rotation": True, "has_translation": False, "given_rate": -0.5},
            # 付与率1.5
            {"name": "付与率1.5_10", "is_local": False, "has_rotation": True, "has_translation": False, "given_rate": 1.5},
        ]
        
        for i, expected in enumerate(expected_patterns):
            with self.subTest(pattern=expected["name"]):
                actual = given_bone_patterns[i]
                self.assertEqual(actual["name"], expected["name"])
                self.assertEqual(actual["is_local"], expected["is_local"])
                self.assertEqual(actual["has_rotation"], expected["has_rotation"])
                self.assertEqual(actual["has_translation"], expected["has_translation"])
                self.assertAlmostEqual(actual["given_rate"], expected["given_rate"], places=5)
                if "layer" in expected:
                    self.assertEqual(actual["layer"], expected["layer"])
    
    def test_parse_original_given_bone_pmx(self):
        """既存の付与ボーンPMXファイルが正しく解析できることを確認"""
        if not os.path.exists(self.original_pmx_path):
            self.skipTest(f"テストファイルが存在しません: {self.original_pmx_path}")
        
        # PMXファイルを解析
        pmx = mmd_parser.parse_mmd_file(self.original_pmx_path)
        
        # 基本的な確認
        self.assertIsNotNone(pmx)
        self.assertEqual(pmx.header.model_name, "test_given_bone")
        self.assertEqual(len(pmx.bones), 9)
        
        # 付与ボーンを確認
        given_bones = []
        for bone in pmx.bones:
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE) or bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
                given_bones.append(bone)
        
        # 付与ボーンの数を確認（B、C、D）
        self.assertEqual(len(given_bones), 3)
    
    def test_given_bone_hierarchy(self):
        """付与ボーンの階層構造が正しいことを確認"""
        if not os.path.exists(self.comprehensive_pmx_path):
            self.skipTest(f"テストファイルが存在しません: {self.comprehensive_pmx_path}")
        
        pmx = mmd_parser.parse_mmd_file(self.comprehensive_pmx_path)
        
        # 多重付与のチェーン構造を確認
        chain_a = None
        chain_b = None
        for bone in pmx.bones:
            if bone.name == "付与チェーン8A":
                chain_a = bone
            elif bone.name == "付与チェーン8B":
                chain_b = bone
        
        self.assertIsNotNone(chain_a)
        self.assertIsNotNone(chain_b)
        
        # チェーンBの付与親がチェーンAであることを確認
        chain_a_index = pmx.bones.index(chain_a)
        self.assertEqual(chain_b.given_parent_bone_index, chain_a_index)
        
        # 変形階層が正しいことを確認
        self.assertEqual(chain_a.transform_layer, 1)
        self.assertEqual(chain_b.transform_layer, 2)
        self.assertLess(chain_a.transform_layer, chain_b.transform_layer)
    
    def test_given_bone_flags_consistency(self):
        """付与ボーンのフラグが一貫していることを確認"""
        if not os.path.exists(self.comprehensive_pmx_path):
            self.skipTest(f"テストファイルが存在しません: {self.comprehensive_pmx_path}")
        
        pmx = mmd_parser.parse_mmd_file(self.comprehensive_pmx_path)
        
        for bone in pmx.bones:
            # 付与フラグがある場合、必要な情報が設定されているか確認
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE) or bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
                # 付与親インデックスが有効な範囲内か確認
                self.assertGreaterEqual(bone.given_parent_bone_index, 0)
                self.assertLess(bone.given_parent_bone_index, len(pmx.bones))
                
                # 付与率が設定されているか確認
                self.assertIsNotNone(bone.given_rate)
                
                # ローカル付与の場合、付与親が親ボーンと一致するか確認
                if bone.get_flag(PmxBoneFlag.LOCAL):
                    # ローカル付与の場合、通常は付与親と親ボーンが同じ
                    # ただし、必須ではないのでテストケースによる
                    pass


if __name__ == "__main__":
    unittest.main()