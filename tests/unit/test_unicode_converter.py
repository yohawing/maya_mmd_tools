# -*- coding: utf-8 -*-
"""
Unicode文字列変換機能のテスト
"""

import unittest
import sys
import os

# テスト対象モジュールのパスを追加
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from mmd_tools.core.unicode_converter import UnicodeToAsciiConverter, get_converter
from mmd_tools.core import utils


class TestUnicodeToAsciiConverter(unittest.TestCase):
    """UnicodeToAsciiConverterのテストクラス"""
    
    def setUp(self):
        """各テスト前の初期化"""
        self.converter = UnicodeToAsciiConverter()
    
    def test_dictionary_conversion(self):
        """辞書変換のテスト"""
        # 日本語 -> 英語
        self.assertEqual(self.converter.convert("ボーン"), "bone")
        self.assertEqual(self.converter.convert("左腕"), "left_arm")
        self.assertEqual(self.converter.convert("頭"), "head")
        
        # 中国語 -> 英語
        self.assertEqual(self.converter.convert("骨骼"), "bone")
        self.assertEqual(self.converter.convert("左臂"), "left_arm")
        self.assertEqual(self.converter.convert("头部"), "head")
        
        # 英語 -> 日本語（最初に登録されたもの）
        self.assertEqual(self.converter.restore("bone"), "ボーン")
        self.assertEqual(self.converter.restore("left_arm"), "左腕")
        self.assertEqual(self.converter.restore("head"), "頭")
    
    def test_base64_conversion(self):
        """Base64変換のテスト"""
        # 辞書にない文字列
        test_text = "未知の名前"
        converted = self.converter.convert(test_text)
        
        # Base64プレフィックスが付いているか
        self.assertTrue(converted.startswith("utfb64_"))
        
        # 復元できるか
        restored = self.converter.restore(converted)
        self.assertEqual(restored, test_text)
    
    def test_ascii_passthrough(self):
        """ASCII文字列のパススルーテスト"""
        test_text = "bone_custom"
        converted = self.converter.convert(test_text)
        restored = self.converter.restore(converted)
        
        # ASCII文字列はそのまま通る
        self.assertEqual(converted, test_text)
        self.assertEqual(restored, test_text)
    
    def test_maya_invalid_chars(self):
        """Maya無効文字の処理テスト"""
        test_text = "test:name with.spaces"
        converted = self.converter.convert(test_text)
        
        # 無効文字が置換されているか
        self.assertNotIn(":", converted)
        self.assertNotIn(" ", converted)
        self.assertNotIn(".", converted)
        
        # 復元時に元に戻るか
        restored = self.converter.restore(converted)
        self.assertEqual(restored, test_text)
    
    def test_mixed_content(self):
        """日本語と英語の混在テスト"""
        test_text = "左足IK"
        converted = self.converter.convert(test_text)
        restored = self.converter.restore(converted)
        
        # 正しく往復変換できるか
        self.assertEqual(restored, test_text)
    
    def test_batch_conversion(self):
        """一括変換のテスト"""
        test_names = ["ボーン", "骨骼", "unknown_name", "髪"]
        result = self.converter.batch_convert(test_names)
        
        # 全ての名前が変換されているか
        self.assertEqual(len(result), len(test_names))
        
    
    def test_encoding_type_detection(self):
        """エンコード方式の判定テスト"""
        # 辞書変換
        dict_converted = self.converter.convert("ボーン")
        self.assertEqual(self.converter.get_encoding_type(dict_converted), "dictionary")
        
        # Base64変換
        base64_converted = self.converter.convert("未知の名前")
        self.assertEqual(self.converter.get_encoding_type(base64_converted), "base64")
        
        # 元の英語
        original = "bone_custom"
        self.assertEqual(self.converter.get_encoding_type(original), "original")


class TestUtilsAPI(unittest.TestCase):
    """utils.pyのAPIテストクラス"""
    
    def test_simple_api(self):
        """シンプルAPIのテスト"""
        # 基本変換
        converted = utils.convert_unicode_to_maya_safe("ボーン")
        restored = utils.restore_maya_safe_to_unicode(converted)
        
        self.assertEqual(restored, "ボーン")

    # 辞書の順番によるエラーのテスト
    def test_dictionary_order(self):
        """辞書の順番によるエラーのテスト"""
        
        # 辞書の順番が変わると復元できないケース
        # 例えば、"目" -> "eye" の後に "右目" -> "eye" が登録されている場合
        # 復元時にどちらが優先されるかを確認する
        
        test_names = ["右目", "左目"]
        answer_names = ["right_eye", "left_eye"]
        converted_names = [utils.convert_unicode_to_maya_safe(name) for name in test_names]

        # 変換結果が期待通りか確認
        self.assertEqual(converted_names, answer_names)

        # 復元時にどちらが優先されるかを確認
        for original, converted in zip(test_names, converted_names):
            restored = utils.restore_maya_safe_to_unicode(converted)
            self.assertEqual(restored, original)


    def test_conversion_combined_string(self):
        """複合文字列の変換テスト"""

        # 颜2 のような数字付きの名前の変換
        test_names = ["颜2", "骨骼1", "髪4"]
        answer_names = ["face2", "bone1", "hair4"]
        converted_names = [utils.convert_unicode_to_maya_safe(name) for name in test_names]
        
        self.assertEqual(converted_names, answer_names)

        # 元素+ のような特殊文字を含む名前の変換
        test_names = ["元素+", "元素-"]
        answer_names = ["element_plus_", "element_dash_"]
        converted_names = [utils.convert_unicode_to_maya_safe(name) for name in test_names]

        self.assertEqual(converted_names, answer_names)

    def test_batch_api(self):
        """一括変換APIのテスト"""
        test_names = ["ボーン", "骨骼", "髪"]
        
        # 一括変換
        convert_result = utils.batch_convert_unicode_names(test_names)
        self.assertEqual(len(convert_result), 3)
        
        # 一括復元
        converted_names = list(convert_result.values())
        restore_result = utils.batch_restore_unicode_names(converted_names)
        
        # 元の名前に戻るか確認
        for original in test_names:
            converted = convert_result[original]
            restored = restore_result[converted]
            # 注意: 同じ英語名の場合、最初に辞書に登録されたものが復元される
            self.assertIn(restored, test_names)  # いずれかの元の名前に戻る
    
    def test_detection_api(self):
        """判定APIのテスト"""
        # Unicode変換済み
        converted = utils.convert_unicode_to_maya_safe("ボーン")
        self.assertTrue(utils.is_unicode_converted_name(converted))
        
        # 元の英語
        self.assertFalse(utils.is_unicode_converted_name("bone_custom"))
    


class TestSingletonPattern(unittest.TestCase):
    """シングルトンパターンのテスト"""
    
    def test_singleton_instance(self):
        """グローバルインスタンスが同一かテスト"""
        converter1 = get_converter()
        converter2 = get_converter()
        
        self.assertIs(converter1, converter2)


if __name__ == '__main__':
    # テスト実行
    unittest.main()
