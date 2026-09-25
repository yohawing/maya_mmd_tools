"""Regression cases from the reviewed frequent MMD names (2026-09-25)."""

import unittest

from mmd_tools.core.maya_name_utils import (
    sanitize_bone_name,
    sanitize_text,
    sanitize_unique_name,
)


class TestFrequentNameSanitization(unittest.TestCase):
    def test_bone_dictionary_tokens_preserve_side_digits_and_modifiers(self):
        cases = {
            "裾_1_2": "hem_1_2",
            "右中指１握１": "right_middle_1_grip_1",
            "左親指０拡１": "left_thumb_0_spread_1",
            "左目光": "left_eye_light",
            "右腕+": "right_arm_plus",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(sanitize_bone_name(source), expected)

    def test_morph_exact_names_preserve_qualifiers_and_symbols(self):
        cases = {
            "瞳AL": "pupil_al",
            "上L": "up_l",
            "上左": "up_left",
            "もぐもぐ右": "chew_right",
            "toon暗(緑)": "toon_dark_green",
            "ｳｨﾝｸ右２": "wink_right_2",
            "↓↑": "down_up",
            "↑↓": "up_down",
            "△": "triangle_outline",
            "∞": "infinity",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(sanitize_text(source), expected)

    def test_reviewed_partial_matches_do_not_drop_meaning(self):
        self.assertEqual(sanitize_text("しいたけ"), "shiitake")
        cases = {
            "悲しい": "sad",
            "眼鏡": "glasses",
            "ω□": "omega_square",
            "上歯↑": "upper_teeth_up",
            "下歯↓": "lower_teeth_down",
            "ウィンク２右": "wink_2_right",
            "瞳小左": "pupil_small_left",
            "瞳小右": "pupil_small_right",
            "頬染め2": "cheek_blush_2",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(sanitize_text(source), expected)
        for base in ("困る", "怒り", "にこり"):
            with self.subTest(base=base):
                outputs = [sanitize_text(base + side) for side in ("", "左", "右")]
                self.assertEqual(len(set(outputs)), 3)
                self.assertTrue(outputs[1].endswith("_left"))
                self.assertTrue(outputs[2].endswith("_right"))

    def test_equivalent_spellings_still_get_unique_aliases(self):
        names = set()
        self.assertEqual(sanitize_unique_name("ウィンク右", names), "wink_right")
        self.assertEqual(sanitize_unique_name("ウインク右", names), "wink_right_1")
        self.assertEqual(sanitize_unique_name("ウィンク右", names), "wink_right_2")

    def test_unknown_bone_token_keeps_hash_fallback(self):
        self.assertEqual(sanitize_bone_name("左未知捩1"), "left_HASH1622dc9b_twist_1")
