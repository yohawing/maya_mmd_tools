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


if __name__ == "__main__":
    unittest.main()
