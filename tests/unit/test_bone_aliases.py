"""Unit tests for shared MMD bone alias tables."""

import unittest

from mmd_tools.config.bone_aliases import get_bone_aliases, get_original_bone_name_aliases
from mmd_tools.validation.bone_validator import BoneValidator


class TestBoneAliases(unittest.TestCase):
    """Bone alias table behavior."""

    def test_aliases_include_bone_validator_names(self):
        self.assertLessEqual(set(BoneValidator.STANDARD_BONES["下半身"]), set(get_bone_aliases("下半身")))
        self.assertLessEqual(set(BoneValidator.SEMI_STANDARD_BONES["腰"]), set(get_bone_aliases("腰")))

    def test_aliases_keep_rig_converter_compatibility_names(self):
        expected_aliases = {
            "センター": {"center", "センター", "centre"},
            "下半身": {"lower_body", "下半身", "lowerbody"},
            "左足": {"left_leg", "左足", "leftleg", "left_thigh", "左もも"},
            "右足": {"right_leg", "右足", "rightleg", "right_thigh", "右もも"},
            "腰": {"waist", "腰", "koshi"},
        }

        for standard_name, expected in expected_aliases.items():
            self.assertLessEqual(expected, set(get_bone_aliases(standard_name)))

    def test_aliases_include_finger_bones(self):
        self.assertLessEqual(set(BoneValidator.FINGER_BONES["左"]["親指"]["左親指０"]), set(get_bone_aliases("左親指０")))

    def test_original_bone_aliases_are_non_ascii_only(self):
        original_names = get_original_bone_name_aliases("全ての親")

        self.assertIn("全ての親", original_names)
        self.assertIn("マスター", original_names)
        self.assertNotIn("master", original_names)
        self.assertTrue(all(not name.isascii() for name in original_names))

    def test_unknown_bone_falls_back_to_requested_name(self):
        self.assertEqual(get_bone_aliases("独自ボーン"), ("独自ボーン",))
        self.assertEqual(get_original_bone_name_aliases("custom_bone"), ())


if __name__ == "__main__":
    unittest.main()
