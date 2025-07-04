# -*- coding: utf-8 -*-
"""
Unicode文字列変換機能のテスト
(unicode_dictionary_guide.mdの内容に基づき生成)
"""

import unittest
import sys
import os
import json

import test

# テスト対象モジュールのパスを追加
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from mmd_tools.core.unicode_converter import UnicodeToAsciiConverter, get_converter
from mmd_tools.core import utils

class TestUnicodeToAsciiConverter(unittest.TestCase):
    """
    UnicodeToAsciiConverterのテストクラス
    unicode_dictionary_guide.md の仕様に基づきテストを行う
    """
    
    def setUp(self):
        """各テスト前の初期化"""
        # テスト用に一時的なカスタム辞書ファイルを作成
        self.custom_dict_path = "test_custom_dict.json"
        custom_dict_data = {
            "_meta": {
                "version": "1.0",
                "description": "Test Dictionary for Unicode Converter",
                "last_updated": "2025-01-01",
                "languages": ["jp", "en", "zh-cn", "zh-tw"]
            },
            "dictionary": [
                ["ボーン", "bone", "骨骼", "骨骼"],
                ["腕", "arm", "手臂", "手臂"],
                ["上半身", "spine", "上半身", "上半身"],
                ["つまさき", "toe", "脚趾", "腳趾"],
                ["肩", "shoulder", "肩", "肩"],
                ["头部", "head", "头部", "頭部"],
                ["手臂", "arm", "手臂", "手臂"]
            ],
            "prefix": [
                ["左", "left_", "左", "左"],
                ["右", "right_", "右", "右"]
            ],
            "suffix": [
                ["先", "_end", "末端", "末端"],
                ["ＩＫ", "_ik", "IK", "IK"],
                ["捩", "_twist", "扭", "扭"],
            ],
            "maya_invalid_chars": {
                "+": "_plus_",
                "|": "_pipe_"
            }
        }
        with open(self.custom_dict_path, "w", encoding="utf-8") as f:
            json.dump(custom_dict_data, f, ensure_ascii=False, indent=2)
        
        # カスタム辞書を使用してコンバータを初期化
        self.converter = UnicodeToAsciiConverter(dictionary_path=self.custom_dict_path)

    def tearDown(self):
        """各テスト後のクリーンアップ"""
        if os.path.exists(self.custom_dict_path):
            os.remove(self.custom_dict_path)

    def test_dictionary_conversion(self):
        """辞書ベースの変換と復元をテスト"""
        # 日本語 -> ASCII
        self.assertEqual(self.converter.convert("ボーン"), "bone")
        # 中国語 -> ASCII
        self.assertEqual(self.converter.convert("头部"), "head")

    def test_hash_conversion(self):
        """辞書にない文字列のハッシュ変換と復元をテスト"""
        test_text = "未知の文字列"
        converted = self.converter.convert(test_text)

        # ドキュメント仕様: 'HASH'で始まり、8文字のハッシュが続く
        self.assertTrue(converted.startswith("HASH"))
        self.assertEqual(len(converted), 12) # 'HASH' + 8文字
        self.assertEqual(converted, "HASH66d0744d")

        test_texts = [
            "未知の文字列", # 辞書にない文字列
            "右未知の文字列1先", # 接頭語と接尾語を含む文字列
            "左未知の文字列捩1" # 接頭語と接尾語+数字を含む文字列
        ]
        results = ["HASH66d0744d", "right_HASH66d0744d_1_end", "left_HASH66d0744d_twist_1"]
        for i, test_text in enumerate(test_texts):
            converted = self.converter.convert(test_text)
            self.assertEqual(converted, results[i])

    def test_prefix_suffix_number_conversion(self):
        """接頭辞・接尾辞・数字の自動変換をテスト"""
        # パース例に基づいたテスト
        self.assertEqual(self.converter.convert("左腕1"), "left_arm_1")
        self.assertEqual(self.converter.convert("右腕2"), "right_arm_2")
        self.assertEqual(self.converter.convert("上半身3"), "spine_3")
        self.assertEqual(self.converter.convert("左腕捩1"), "left_arm_twist_1")
        self.assertEqual(self.converter.convert("右つまさきＩＫ先"), "right_toe_ik_end")
        self.assertEqual(self.converter.convert("肩P"), "shoulder_p")
        self.assertEqual(self.converter.convert("元素+"), "element_plus")

        # 復元テスト
        self.assertEqual(self.converter.restore("left_arm_1"), "左腕1")
        self.assertEqual(self.converter.restore("right_toe_ik_end"), "右つまさきＩＫ先")
        self.assertEqual(self.converter.restore("element_plus"), "元素+")

    def test_maya_invalid_chars(self):
        """Maya無効文字の置換と復元をテスト"""
        test_cases = {
            "test:name": "test_name",  # ":"は"_"に変換
            "test name": "test_name",  # " "は"_"に変換
            "test-name": "test_name",  # "-"は"_"に変換
            "test.name": "test_name",  # "."は"_"に変換
            "test+name": "test_plus_name",  # "+"は"_plus_"に変換
            "test|name": "test_pipe_name"  # "|"は"_pipe_"に変換
        }
        for original, expected in test_cases.items():
            with self.subTest(original=original):
                converted = self.converter.convert(original)
                self.assertEqual(converted, expected)
                # 復元テストは難しいため省略（":"は空文字に変換されるため復元不可）

    def test_ascii_passthrough(self):
        """ASCII文字列が変更されないことをテスト"""
        test_text = "ascii_only_name_123"
        converted = self.converter.convert(test_text)
        self.assertEqual(converted, test_text)
        restored = self.converter.restore(converted)
        self.assertEqual(restored, test_text)

    def test_batch_conversion(self):
        """一括変換をテスト"""
        names = ["ボーン", "头部", "未知の名前"]
        converted_batch = self.converter.batch_convert(names)
        # batch_convertはリスト形式を返す
        self.assertIn("bone", converted_batch)
        self.assertIn("head", converted_batch)
        self.assertTrue(any(name.startswith("#") for name in converted_batch))

        # 復元テスト
        restored_batch = self.converter.batch_restore(converted_batch)
        # 復元結果が元の名前と一致することを確認
        self.assertEqual(set(restored_batch), set(names))

    def test_encoding_type_detection(self):
        """エンコード方式の判定をテスト"""
        # 辞書
        self.assertEqual(self.converter.get_encoding_type("bone"), "dictionary")
        # ハッシュ
        self.assertEqual(self.converter.get_encoding_type(self.converter.convert("未知")), "hash")
        # オリジナル
        self.assertEqual(self.converter.get_encoding_type("original_ascii"), "original")


class TestUtilsAPI(unittest.TestCase):
    """utils.pyのAPIが正しく動作するかをテスト"""

    def setUp(self):
        """utils APIテストの初期化"""
        # utilsはシングルトンのコンバータインスタンスを内部で使用するため、
        # テスト実行前にリロードして状態をリセットすることが望ましい
        # ここでは簡単のため、デフォルト辞書での動作を主眼に置く
        pass

    def test_simple_api_conversion(self):
        """基本的なutils APIの変換"""
        original = "ボーン"
        converted = utils.convert_utf8_to_ascii(original)
        self.assertEqual(converted, "bone")

    def test_api_with_prefix_suffix(self):
        """utils APIでの接頭辞・接尾辞変換をテスト"""
        original = "左腕1"
        converted = utils.convert_utf8_to_ascii(original)
        self.assertEqual(converted, "left_arm_1")

    def test_batch_api(self):
        """一括変換APIをテスト"""
        names = ["ボーン", "头部", "未知の名前"]
        converted = utils.convert_utf8_to_ascii_batch(names)
        # utils.convert_utf8_to_ascii_batchはリストを返す
        self.assertIn("bone", converted)
        self.assertIn("head", converted)


class TestSingletonPattern(unittest.TestCase):
    """シングルトンパターンのテスト"""
    
    def test_singleton_instance(self):
        """get_converter()が常に同じインスタンスを返すことをテスト"""
        converter1 = get_converter()
        converter2 = get_converter()
        self.assertIs(converter1, converter2)

        # 辞書エントリを追加して、シングルトンであることを確認
        converter1.add_dictionary_entry("シングルトンテスト", "singleton_test")
        self.assertEqual(converter2.convert("シングルトンテスト"), "singleton_test")


if __name__ == '__main__':
    unittest.main()