"""
Unit tests for BoneValidator class.
"""

import unittest
from mmd_tools.validation.bone_validator import BoneValidator


class TestBoneValidator(unittest.TestCase):
    """BoneValidatorクラスのテスト"""

    def setUp(self):
        """テストのセットアップ"""
        self.validator = BoneValidator()

    def test_init(self):
        """初期化のテスト"""
        self.assertIsNotNone(self.validator.name_to_standard)
        self.assertGreater(len(self.validator.name_to_standard), 0)

    def test_validate_bones_all_present(self):
        """全ての標準ボーンが存在する場合のテスト"""
        # 全ての標準ボーンを含むリスト
        bones = [
            "センター",
            "下半身",
            "上半身",
            "首",
            "頭",
            "上半身2",
            "左目",
            "右目",
            "左肩",
            "左腕",
            "左ひじ",
            "左手首",
            "右肩",
            "右腕",
            "右ひじ",
            "右手首",
            "左足",
            "左ひざ",
            "左足首",
            "左足ＩＫ",
            "左つま先",
            "左つま先ＩＫ",
            "右足",
            "右ひざ",
            "右足首",
            "右足ＩＫ",
            "右つま先",
            "右つま先ＩＫ",
            # 指ボーン（左）
            "左親指０",
            "左親指１",
            "左親指２",
            "左人指１",
            "左人指２",
            "左人指３",
            "左中指１",
            "左中指２",
            "左中指３",
            "左薬指１",
            "左薬指２",
            "左薬指３",
            "左小指１",
            "左小指２",
            "左小指３",
            # 指ボーン（右）
            "右親指０",
            "右親指１",
            "右親指２",
            "右人指１",
            "右人指２",
            "右人指３",
            "右中指１",
            "右中指２",
            "右中指３",
            "右薬指１",
            "右薬指２",
            "右薬指３",
            "右小指１",
            "右小指２",
            "右小指３",
        ]

        missing, issues, mapping = self.validator.validate_bones(bones)

        self.assertEqual(len(missing), 0)
        self.assertEqual(len(issues), 0)
        self.assertGreater(len(mapping), 0)

    def test_validate_bones_missing_core(self):
        """コアボーンが不足している場合のテスト"""
        bones = ["上半身", "首", "頭"]  # センターと下半身が不足

        missing, issues, mapping = self.validator.validate_bones(bones)

        self.assertIn("センター", missing)
        self.assertIn("下半身", missing)
        self.assertEqual(len(mapping), 3)

    def test_validate_bones_english_names(self):
        """英語名のボーンを認識できるかのテスト"""
        bones = ["center", "upper_body", "lower_body", "neck", "head"]

        missing, issues, mapping = self.validator.validate_bones(bones)

        self.assertEqual(mapping["center"], "センター")
        self.assertEqual(mapping["upper_body"], "上半身")
        self.assertEqual(mapping["lower_body"], "下半身")
        self.assertEqual(mapping["neck"], "首")
        self.assertEqual(mapping["head"], "頭")

    def test_validate_bones_naming_issues(self):
        """命名規則の問題を検出するテスト"""
        bones = ["左足ＩＫ", "右足IK"]  # 全角IKと半角IKの混在

        missing, issues, mapping = self.validator.validate_bones(bones)

        # 右足IKが半角なので命名規則の問題として検出される
        self.assertEqual(len(issues), 0)  # 個別のボーンでは問題なし
        self.assertEqual(len(mapping), 2)

    def test_validate_bones_fullwidth_halfwidth_numbers(self):
        """全角・半角数字の混在をチェック"""
        bones = ["上半身2", "左親指０", "左親指1", "左親指２"]  # 半角と全角の混在

        missing, issues, mapping = self.validator.validate_bones(bones)

        # 数字の全角・半角が混在しているボーンは検出されない（個別では問題なし）
        self.assertGreater(len(mapping), 0)

    def test_get_bone_info_standard(self):
        """標準ボーンの情報取得テスト"""
        info = self.validator.get_bone_info("センター")

        self.assertIsNotNone(info)
        self.assertEqual(info["standard_name"], "センター")
        self.assertEqual(info["type"], "standard")
        self.assertEqual(info["preferred_name"], "center")

    def test_get_bone_info_semi_standard(self):
        """準標準ボーンの情報取得テスト"""
        info = self.validator.get_bone_info("グルーブ")

        self.assertIsNotNone(info)
        self.assertEqual(info["standard_name"], "グルーブ")
        self.assertEqual(info["type"], "semi_standard")
        self.assertEqual(info["preferred_name"], "groove")

    def test_get_bone_info_finger(self):
        """指ボーンの情報取得テスト"""
        info = self.validator.get_bone_info("左親指０")

        self.assertIsNotNone(info)
        self.assertEqual(info["standard_name"], "左親指０")
        self.assertEqual(info["type"], "finger")
        self.assertEqual(info["preferred_name"], "left_thumb_0")

    def test_get_bone_info_not_found(self):
        """存在しないボーンの情報取得テスト"""
        info = self.validator.get_bone_info("不明なボーン")

        self.assertIsNone(info)

    def test_case_insensitive_matching(self):
        """大文字小文字を区別しない検索のテスト"""
        bones = ["CENTER", "Upper_Body", "LOWER_BODY"]

        missing, issues, mapping = self.validator.validate_bones(bones)

        self.assertEqual(mapping["CENTER"], "センター")
        self.assertEqual(mapping["Upper_Body"], "上半身")
        self.assertEqual(mapping["LOWER_BODY"], "下半身")

    def test_validate_bones_missing_fingers(self):
        """指ボーンが不足している場合のテスト"""
        bones = [
            "センター",
            "下半身",
            "上半身",
            "首",
            "頭",
            "左肩",
            "左腕",
            "左ひじ",
            "左手首",
            # 指ボーンなし
        ]

        missing, issues, mapping = self.validator.validate_bones(bones)

        # 左手の指ボーンが全て不足として検出される
        self.assertIn("左親指０", missing)
        self.assertIn("左親指１", missing)
        self.assertIn("左親指２", missing)
        self.assertIn("左人指１", missing)

    def test_generate_report(self):
        """レポート生成のテスト"""
        bones = ["センター", "上半身", "首", "頭", "左足IK", "右足ＩＫ", "不明なボーン"]

        report = self.validator.generate_report(bones)

        self.assertIn("MMDボーン構造検証レポート", report)
        self.assertIn("検証対象ボーン数: 7", report)
        self.assertIn("不足している標準ボーン", report)
        self.assertIn("下半身", report)  # 不足ボーンとして含まれる

    def test_alternative_names(self):
        """代替名称の認識テスト"""
        bones = ["左肘", "右膝", "左人差指１"]  # 異体字

        missing, issues, mapping = self.validator.validate_bones(bones)

        self.assertEqual(mapping["左肘"], "左ひじ")
        self.assertEqual(mapping["右膝"], "右ひざ")
        self.assertEqual(mapping["左人差指１"], "左人指１")

    def test_semi_standard_bones(self):
        """準標準ボーンの認識テスト"""
        bones = ["全ての親", "グルーブ", "腰", "操作中心"]

        missing, issues, mapping = self.validator.validate_bones(bones)

        self.assertEqual(mapping["全ての親"], "全ての親")
        self.assertEqual(mapping["グルーブ"], "グルーブ")
        self.assertEqual(mapping["腰"], "腰")
        self.assertEqual(mapping["操作中心"], "操作中心")


if __name__ == "__main__":
    unittest.main()
