"""
Unicode文字列変換機能のテスト
(unicode_dictionary_guide.mdの内容に基づき生成)
"""

import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import MagicMock


# テスト対象モジュールのパスを追加
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from mmd_tools.core import maya_name_utils, utils
from mmd_tools.core.mmd_bone_names import (
    convert_mmd_bone_name_to_ascii,
    convert_semistandard_mmd_bone_name_to_ascii,
    has_semistandard_mmd_bone_name,
    normalize_mmd_bone_name,
)
from mmd_tools.core.unicode_converter import (
    UnicodeToAsciiConverter,
    _DictionaryLoader,
    get_converter,
)


def _msgs(mock_log):
    # call[0] is args tuple (Py3.7-safe; _Call.args is 3.8+).
    return [c[0][0] for c in mock_log.call_args_list if c[0]]


CORPUS_EXPECTED_MAPPINGS = {
    'HL消1': 'highlight_off_1',
    'ぐるぐる': 'dizzy',
    'たくらむ': 'scheming',
    'はぁと': 'heart',
    'ほっぺ': 'cheek_puff',
    'むすっ': 'annoyed',
    'やや驚く': 'slightly_surprised',
    'イライラ': 'annoyed',
    'ガーン': 'shock',
    'キラキラ': 'sparkle',
    'グルグル': 'dizzy',
    'コッチミンナ': 'look_at_me',
    'シャツ': 'shirt',
    'ツインテール': 'twin_tail',
    'デフォ': 'default',
    'デフォ2': 'default_2',
    'ニカッ': 'grin',
    'ニッ': 'grin',
    'ハイライト←': 'highlight_left',
    'ハイライト↑': 'highlight_up',
    'ハイライト→': 'highlight_right',
    'ハイライト↓': 'highlight_down',
    'ハイライト下': 'highlight_down',
    'ハイライト拡大': 'highlight_expand',
    'ハイライト除去': 'highlight_off',
    'ハート': 'heart',
    'ブーツ': 'boots',
    'ベルト': 'belt',
    'マジメ': 'serious',
    '上がる': 'up',
    '下がる': 'down',
    '下まぶた': 'lower_eyelid',
    '下瞼上げ': 'lower_eyelid_up',
    '下瞼上げ2': 'lower_eyelid_up_2',
    '下瞼上げ2右': 'lower_eyelid_up_2_right',
    '下瞼上げ2左': 'lower_eyelid_up_2_left',
    '下瞼上げ右': 'lower_eyelid_up_right',
    '下瞼上げ左': 'lower_eyelid_up_left',
    '不機嫌': 'cranky',
    '体2': 'body_2',
    '体饰': 'body_decoration',
    '前发': 'front_hair',
    '前進': 'forward',
    '发': 'hair',
    '发轮廓线': 'hair_outline',
    '发饰': 'hair_accessory',
    '叫び': 'shout',
    '右六芒星': 'hexagram_right',
    '后发': 'back_hair',
    '嘲笑': 'sneer',
    '尾': 'tail',
    '左六芒星': 'hexagram_left',
    '帽': 'hat',
    '帽子': 'hat',
    '帽子非表示': 'hat_hide',
    '平行': 'brow_flat',
    '平行右': 'brow_flat_right',
    '平行左': 'brow_flat_left',
    '影化': 'shadow_shade',
    '後退': 'backward',
    '怒': 'angry',
    '怒る': 'angry',
    '怒る右': 'angry_right',
    '怒る左': 'angry_left',
    '悲しむ': 'sad',
    '惊': 'surprised',
    '披风': 'cape',
    '指甲': 'fingernail',
    '涙2': 'tears_2',
    '涙3': 'tears_3',
    '涙右': 'tears_right',
    '涙左': 'tears_left',
    '照れ': 'blush',
    '照れ2': 'blush_2',
    '照れ斜線': 'blush_line',
    '照れ消': 'blush_off',
    '爪': 'fingernail',
    '牙': 'teeth',
    '白': 'white',
    '眇める': 'squint',
    '笑': 'smile',
    '絶望': 'despair',
    '耳': 'ear',
    '脸': 'face',
    '袜': 'socks',
    '裙': 'skirt',
    '裙2': 'skirt_2',
    '裙带': 'skirt_band',
    '裙饰': 'skirt_decoration',
    '裤': 'pants',
    '襟': 'collar',
    '見開き': 'eyes_wide',
    '見開く': 'eyes_wide',
    '赤面2': 'blush_2',
    '金属': 'metal',
    '青ざめ': 'pale',
    '青ざめる': 'pale',
    '面': 'face',
    '面1': 'face_1',
    '面2': 'face_2',
    '鞋': 'shoes',
    '饰': 'decoration',
    '驚く': 'surprised',
    '驚く右': 'surprised_right',
    '驚く左': 'surprised_left',
    '高さ調整': 'height_adjust',
    '髮+': 'hair_plus',
    '鼻上': 'nose_up',
    '鼻下': 'nose_down',
}


class TestUnicodeToAsciiConverter(unittest.TestCase):
    """
    UnicodeToAsciiConverterのテストクラス
    unicode_dictionary_guide.md の仕様に基づきテストを行う
    """

    def setUp(self):
        """各テスト前の初期化"""
        # Maya のバージョン別 unit lane は並列実行されるため、共有名にしない。
        # Windows では別プロセスの作成・削除と競合すると、辞書が正常に閉じられて
        # いても tearDown の削除が PermissionError になることがある。
        fd, self.custom_dict_path = tempfile.mkstemp(
            prefix="mmd_tools_unicode_test_", suffix=".json"
        )
        os.close(fd)
        custom_dict_data = {
            "_meta": {
                "version": "1.0",
                "description": "Test Dictionary for Unicode Converter",
                "last_updated": "2025-01-01",
                "languages": ["jp", "en", "zh-cn", "zh-tw"],
            },
            "dictionary": [
                ["ボーン", "bone", "骨骼", "骨骼"],
                ["腕", "arm", "手臂", "手臂"],
                ["上半身", "spine", "上半身", "上半身"],
                ["つまさき", "toe", "脚趾", "腳趾"],
                ["肩", "shoulder", "肩", "肩"],
                ["头部", "head", "头部", "頭部"],
                ["手臂", "arm", "手臂", "手臂"],
                ["元素", "element", "元素", "元素"],
                ["顔", "face", "颜", "顏"],
                ["人差指", "finger_index", "食指", "食指"],
                ["つま先", "toe", "脚趾", "腳趾"],
                ["足", "leg", "足", "足"],
                ["髮", "hair", "发", "髮"],
                ["左つま先ＩＫ先", "left_toe_ik_end", "左脚趾IK末端", "左腳趾IK末端"],
            ],
            "prefix": [
                ["左", "left_", "左", "左"],
                ["右", "right_", "右", "右"],
                ["前", "front_", "前", "前"],
                ["後", "back_", "後", "後"],
                ["横", "side_", "侧", "側"],
            ],
            "suffix": [
                ["ＩＫ先", "_ik_end", "IK末端", "IK末端"],
                ["先", "_end", "末端", "末端"],
                ["ＩＫ", "_ik", "IK", "IK"],
                ["捩", "_twist", "扭", "扭"],
                ["親", "_parent", "父", "父"],
            ],
            "exact_match": {
                "左足IK": "left_leg_ik",
                "右足IK": "right_leg_ik",
                "左つま先IK": "left_toe_ik",
                "右つま先IK": "right_toe_ik",
                "左つま先ＩＫ": "left_toe_ik",
                "右つま先ＩＫ": "right_toe_ik",
                "左足ＩＫ": "left_leg_ik",
                "右足ＩＫ": "right_leg_ik",
                "上半身2": "upper_body_2",
                "左腕捩": "left_arm_twist",
                "右腕捩": "right_arm_twist",
                "グルーブ": "groove",
            },
            "maya_invalid_chars": {"+": "_plus_", "|": "_pipe_"},
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
        """辞書ベースの変換をテスト"""
        # 日本語 -> ASCII
        self.assertEqual(self.converter.convert("ボーン"), "bone")
        self.assertEqual(self.converter.convert("顔"), "face")
        # 中国語 -> ASCII
        self.assertEqual(self.converter.convert("头部"), "head")
        self.assertEqual(self.converter.convert("颜"), "face")

    def test_semantic_corpus_vocabulary_avoids_hash_fallback(self):
        """Adopted multi-model Material/Morph vocabulary stays deterministic."""
        converter = UnicodeToAsciiConverter()
        expected = {
            "体": "body",
            "髮": "hair",
            "メガネ": "glasses",
            "ｳｨﾝｸ２右": "wink_2_right",
            "光消": "highlight_off",
            "ハイライト": "highlight",
            "汗": "sweat",
            "赤面": "blush",
            "呼吸": "breathing",
            "深呼吸": "deep_breath",
        }
        for source, target in expected.items():
            with self.subTest(source=source):
                converted = converter.convert(source)
                self.assertEqual(converted, target)
                self.assertTrue(converted.isascii())
                self.assertNotIn("HASH", converted)
                self.assertEqual(converter.get_encoding_type(converted), "dictionary")

    def test_expanded_semantic_corpus_vocabulary_is_safe_and_exact(self):
        """All reviewed Material/Morph mappings remain exact and Maya-safe."""
        self.assertEqual(len(CORPUS_EXPECTED_MAPPINGS), 109)
        converter = UnicodeToAsciiConverter()
        identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

        for source, expected in CORPUS_EXPECTED_MAPPINGS.items():
            with self.subTest(source=source):
                converted = converter.convert(source)
                self.assertEqual(converted, expected)
                self.assertEqual(maya_name_utils.sanitize_text(source), expected)
                self.assertTrue(converted.isascii())
                self.assertRegex(converted, identifier)
                self.assertNotIn("HASH", converted)
                self.assertEqual(converter.get_encoding_type(converted), "dictionary")

        # Synonyms intentionally share a token; numbered/directional variants
        # must retain their distinct suffixes instead of collapsing to a base.
        for source, synonym in (("面", "脸"), ("帽", "帽子"), ("怒", "怒る"), ("惊", "驚く")):
            self.assertEqual(CORPUS_EXPECTED_MAPPINGS[source], CORPUS_EXPECTED_MAPPINGS[synonym])
        for source, expected in (("照れ2", "blush_2"), ("下瞼上げ2右", "lower_eyelid_up_2_right"), ("ハイライト↓", "highlight_down")):
            self.assertEqual(CORPUS_EXPECTED_MAPPINGS[source], expected)

    def test_hash_conversion(self):
        """辞書にない文字列のハッシュ変換をテスト"""
        test_text = "未知の文字列"
        converted = self.converter.convert(test_text)

        # ドキュメント仕様: 'HASH'で始まり、8文字のハッシュが続く
        self.assertTrue(converted.startswith("HASH"))
        self.assertEqual(len(converted), 12)  # 'HASH' + 8文字
        self.assertEqual(converted, "HASH66d0744d")

        test_texts = [
            "未知の文字列",  # 辞書にない文字列
            "右未知の文字列1先",  # 接頭語と接尾語を含む文字列
            "左未知の文字列捩1",  # 接頭語と接尾語+数字を含む文字列
        ]
        results = [
            "HASH66d0744d",
            "right_HASH66d0744d_end_1",
            "left_HASH66d0744d_twist_1",
        ]
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
        self.assertEqual(self.converter.convert("元素+"), "element_plus_")

    def test_maya_invalid_chars(self):
        """Maya無効文字の置換をテスト"""
        test_cases = {
            "test:name": "test_name",  # ":"は"_"に変換
            "test name": "test_name",  # " "は"_"に変換
            "test-name": "test_name",  # "-"は"_"に変換
            "test.name": "test_name",  # "."は"_"に変換
            "test+name": "test_plus_name",  # "+"は"_plus_"に変換
            "test|name": "test_pipe_name",  # "|"は"_pipe_"に変換
        }
        for original, expected in test_cases.items():
            with self.subTest(original=original):
                converted = self.converter.convert(original)
                self.assertEqual(converted, expected)

    def test_ascii_passthrough(self):
        """ASCII文字列が変更されないことをテスト"""
        test_text = "ascii_only_name_123"
        converted = self.converter.convert(test_text)
        self.assertEqual(converted, test_text)

    def test_batch_conversion(self):
        """一括変換をテスト"""
        names = ["ボーン", "头部", "未知の名前"]
        converted_batch = self.converter.batch_convert(names)
        # batch_convertはリスト形式を返す
        self.assertIn("bone", converted_batch)
        self.assertIn("head", converted_batch)
        self.assertIn("HASH5565828f", converted_batch)

    def test_encoding_type_detection(self):
        """エンコード方式の判定をテスト"""
        # 辞書
        self.assertEqual(self.converter.get_encoding_type("bone"), "dictionary")
        # ハッシュ
        self.assertEqual(self.converter.get_encoding_type(self.converter.convert("未知")), "hash")
        # オリジナル
        self.assertEqual(self.converter.get_encoding_type("original_ascii"), "original")

    def test_edge_cases(self):
        """エッジケースのテスト"""
        # 空文字列
        self.assertEqual(self.converter.convert(""), "")
        # None
        self.assertEqual(self.converter.convert(None), None)
        # 数字のみ
        self.assertEqual(self.converter.convert("12345"), "_12345")
        # 特殊文字のみ
        self.assertEqual(self.converter.convert("!@#$%^&*()"), "__________")

        self.assertEqual(self.converter.convert("左人差指先"), "left_finger_index_end")
        self.assertEqual(self.converter.convert("右腕捩先IK"), "right_arm_twist_end_ik")
        self.assertEqual(self.converter.convert("左肩P"), "left_shoulder_p")
        self.assertEqual(self.converter.convert("右つま先"), "right_toe")
        # BUG: "右つま先ＩＫ" は "right_toe_ik" になるべきだが、現在の実装では正しく変換されない。
        # 修正されたら unittest.expectedFailure デコレータを外してアサーションを有効化すること。
        self.assertEqual(self.converter.convert("左つま先ＩＫ先"), "left_toe_ik_end")
        self.assertEqual(self.converter.convert("上半身3"), "spine_3")
        self.assertEqual(self.converter.convert("左顔_0_1"), "left_face_0_1")
        self.assertEqual(self.converter.convert("左顔_18_1"), "left_face_18_1")
        self.assertEqual(self.converter.convert("右顔1_0_1"), "right_face_1_0_1")
        # 注意: 現在の実装では、suffixが順番に処理されるため "left_leg_parent_ik" になる
        # exact_match機能実装後は "left_leg_ik_parent" になるべき
        self.assertEqual(self.converter.convert("左足IK親"), "left_leg_parent_ik")
        self.assertEqual(self.converter.convert("上半身3"), "spine_3")
        self.assertEqual(self.converter.convert("髮親"), "hair_parent")
        self.assertEqual(self.converter.convert("前髮2_1"), "front_hair_2_1")

        # 先頭に数字がある場合のテスト
        self.assertEqual(self.converter.convert("001左腕"), "left_arm_001")
        self.assertEqual(self.converter.convert("001 Footsteps"), "Footsteps_001")

    def test_exact_match_conversion(self):
        """完全一致変換のテスト（exact_matchセクション使用）"""

        # exact_matchにある項目はprefix/suffix処理より優先される
        self.assertEqual(self.converter.convert("左足IK"), "left_leg_ik")
        self.assertEqual(self.converter.convert("右足IK"), "right_leg_ik")
        self.assertEqual(self.converter.convert("左つま先IK"), "left_toe_ik")
        self.assertEqual(self.converter.convert("右つま先IK"), "right_toe_ik")

        # 全角IKも対応
        self.assertEqual(self.converter.convert("左足ＩＫ"), "left_leg_ik")
        self.assertEqual(self.converter.convert("右足ＩＫ"), "right_leg_ik")
        self.assertEqual(self.converter.convert("左つま先ＩＫ"), "left_toe_ik")
        self.assertEqual(self.converter.convert("右つま先ＩＫ"), "right_toe_ik")

        # 準標準ボーン
        self.assertEqual(self.converter.convert("上半身2"), "upper_body_2")
        self.assertEqual(self.converter.convert("左腕捩"), "left_arm_twist")
        self.assertEqual(self.converter.convert("右腕捩"), "right_arm_twist")
        self.assertEqual(self.converter.convert("グルーブ"), "groove")


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


class TestMmdBoneNameConversion(unittest.TestCase):
    """PMX/MMDボーン名専用の正規化変換をテストする。"""

    def test_semistandard_bone_names_use_hardcoded_rules(self):
        test_cases = {
            "左足IK親": "left_leg_ik_parent",
            "左足ＩＫ親": "left_leg_ik_parent",
            "右足IK親": "right_leg_ik_parent",
            "右足ＩＫ先": "right_leg_ik_end",
            "左足IK先": "left_leg_ik_end",
            "左腕捩": "left_arm_twist",
            "左腕捩先": "left_arm_twist_end",
            "右手捩": "right_wrist_twist",
            "右手捩先": "right_wrist_twist_end",
            "腰": "waist",
            "左親指0": "left_thumb_0",
            "右足D": "right_leg_d",
            "右ひざD": "right_knee_d",
            "右足首D": "right_ankle_d",
            "左腕D": "left_arm_d",
            "右腕捩D": "right_arm_twist_d",
            "右腕捻Ｄ": "right_arm_twist_d",
            "左ひじD": "left_elbow_d",
            "左肘Ｄ": "left_elbow_d",
            "右足先EX": "right_toe_ex",
            "右足先ＥＸ": "right_toe_ex",
            "胸親": "breast_parent",
            "左肩P": "left_shoulder_p",
            "左肩C": "left_shoulder_c",
            "左腕捩1": "left_arm_twist_1",
        }
        for original, expected in test_cases.items():
            with self.subTest(original=original):
                self.assertEqual(convert_mmd_bone_name_to_ascii(original), expected)

    def test_semistandard_bone_names_are_detected_before_generic_tokenization(self):
        self.assertEqual(convert_semistandard_mmd_bone_name_to_ascii("右足IK親"), "right_leg_ik_parent")
        self.assertEqual(convert_semistandard_mmd_bone_name_to_ascii("右足ＩＫ先"), "right_leg_ik_end")
        self.assertEqual(convert_semistandard_mmd_bone_name_to_ascii("左手捩先"), "left_wrist_twist_end")
        self.assertEqual(convert_semistandard_mmd_bone_name_to_ascii("右肩P"), "right_shoulder_p")
        self.assertEqual(convert_semistandard_mmd_bone_name_to_ascii("右足D"), "right_leg_d")
        self.assertEqual(convert_semistandard_mmd_bone_name_to_ascii("左腕捩"), "left_arm_twist")
        self.assertEqual(convert_semistandard_mmd_bone_name_to_ascii("左親指０"), "left_thumb_0")
        self.assertTrue(has_semistandard_mmd_bone_name("右足先ＥＸ"))
        self.assertFalse(has_semistandard_mmd_bone_name("髪D"))

    def test_unknown_bone_name_tokens_are_hashed_without_dropping_known_tokens(self):
        self.assertEqual(convert_mmd_bone_name_to_ascii("左未知捩1"), "left_HASH1622dc9b_twist_1")

    def test_frequent_local_asset_tokens_avoid_hash_fallback(self):
        test_cases = {
            "右腕捩軸": "right_arm_twist_axis",
            "左足IK調整": "left_leg_ik_adjust",
            "右足IK向き": "right_leg_ik_direction",
            "左腕捩元": "left_arm_twist_base",
            "右ひざD補助": "right_knee_d_assist",
            "左腕捩抽出": "left_arm_twist_extract",
            "右太ももD": "right_thigh_d",
            "左骨盤P": "left_pelvis_p",
        }
        for original, expected in test_cases.items():
            with self.subTest(original=original):
                converted = convert_mmd_bone_name_to_ascii(original)
                self.assertEqual(converted, expected)
                self.assertNotIn("HASH", converted)

    def test_normalize_mmd_bone_name_folds_common_variants(self):
        self.assertEqual(normalize_mmd_bone_name("右腕捻Ｄ"), "右腕捩D")
        self.assertEqual(normalize_mmd_bone_name("左肘ＩＫ"), "左ひじIK")


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


class TestDictionaryLoaderDefaultFallbackLogging(unittest.TestCase):
    """Default-dictionary fallback detail is DEBUG; WARNING/ERROR stay actionable."""

    def test_missing_dictionary_file_uses_debug_for_default_fallback(self):
        logger = MagicMock()
        missing_path = os.path.join(tempfile.gettempdir(), "mmd_tools_missing_dict_xyz.json")
        if os.path.exists(missing_path):
            os.remove(missing_path)

        state = _DictionaryLoader.load(missing_path, logger)

        self.assertIsNotNone(state)
        self.assertTrue(state.unicode_to_ascii)
        warning_msgs = _msgs(logger.warning)
        self.assertTrue(
            any("Dictionary file not found" in str(m) for m in warning_msgs),
            "missing-file WARNING must remain: %r" % (warning_msgs,),
        )
        self.assertIn("Using default dictionary", _msgs(logger.debug))
        self.assertNotIn("Using default dictionary", _msgs(logger.info))

    def test_corrupt_dictionary_file_uses_debug_for_default_fallback(self):
        logger = MagicMock()
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd, b"{not valid json")
            os.close(fd)
            state = _DictionaryLoader.load(path, logger)
        finally:
            if os.path.exists(path):
                os.remove(path)

        self.assertIsNotNone(state)
        self.assertTrue(state.unicode_to_ascii)
        error_msgs = _msgs(logger.error)
        self.assertTrue(
            any("Failed to load dictionary file" in str(m) for m in error_msgs),
            "load-failure ERROR must remain: %r" % (error_msgs,),
        )
        self.assertIn("Using default dictionary", _msgs(logger.debug))
        self.assertNotIn("Using default dictionary", _msgs(logger.info))


if __name__ == "__main__":
    unittest.main()
