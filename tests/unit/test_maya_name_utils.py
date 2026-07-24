import re
import unittest

from mmd_tools.core import maya_name_utils


class TestMayaNameUtils(unittest.TestCase):
    def test_sanitize_maya_name(self):
        """ASCII変換でうまくサニタイズされるか"""
        self.assertEqual(maya_name_utils.sanitize_text("髪"), "hair")
        self.assertEqual(maya_name_utils.sanitize_text("invalid-name!"), "invalid_name_")
        self.assertEqual(maya_name_utils.sanitize_text(" "), "_")
        self.assertEqual(maya_name_utils.sanitize_text(" name"), "_name")
        self.assertEqual(maya_name_utils.sanitize_text("name "), "name_")

    def test_sanitize_bone_name_uses_mmd_bone_rules(self):
        """PMXボーン名は準標準ボーン規則でサニタイズされる。"""
        self.assertEqual(maya_name_utils.sanitize_bone_name("左足IK親"), "left_leg_ik_parent")
        self.assertEqual(maya_name_utils.sanitize_bone_name("右腕捻Ｄ"), "right_arm_twist_d")
        self.assertEqual(maya_name_utils.sanitize_bone_name("001"), "bone_001")

    def test_sanitize_text_handles_material_morph_hazard_names(self):
        """Material/morph source names always become plain Maya identifiers."""
        identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        for source in ("1:髪", "2:髪+", "3:体", "4:装飾", "", ":", "!?", "a:b"):
            with self.subTest(source=source):
                self.assertRegex(maya_name_utils.sanitize_text(source), identifier)

    def test_sanitize_text_adopted_corpus_vocabulary(self):
        """Adopted Material/Morph terms remain exact, safe Maya identifiers."""
        expected = {
            "体": "body",
            "髮": "hair",
            "メガネ": "glasses",
            "ｳｨﾝｸ２右": "wink_2_right",
            "光消": "highlight_off",
        }
        identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        for source, target in expected.items():
            with self.subTest(source=source):
                converted = maya_name_utils.sanitize_text(source)
                self.assertEqual(converted, target)
                self.assertRegex(converted, identifier)
                self.assertNotIn("HASH", converted)

    def test_sanitize_unique_name_is_deterministic_and_preserves_raw_input(self):
        used = set()
        names = [
            maya_name_utils.sanitize_unique_name("にっこり", used),
            maya_name_utils.sanitize_unique_name("にやり", used),
            maya_name_utils.sanitize_unique_name("a:b", used),
            maya_name_utils.sanitize_unique_name("ab", used),
            maya_name_utils.sanitize_unique_name("", used, fallback="morph_4"),
        ]

        self.assertEqual(names, ["grin", "grin_1", "ab", "ab_1", "morph_4"])
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names))


if __name__ == "__main__":
    unittest.main()
