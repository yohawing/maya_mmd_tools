"""Unit tests for HumanIK bone mapping tables."""

import unittest

from mmd_tools.config.humanik_mapping import (
    HIK_BONE_INDICES,
    MMD_TO_HIK_BONE,
    MMD_TO_HIK_BONE_INDEX,
    MMD_TO_HIK_UNINDEXED_BONES,
)
from mmd_tools.validation.bone_validator import BoneValidator


def _finger_standard_bones():
    for side_fingers in BoneValidator.FINGER_BONES.values():
        for finger_bones in side_fingers.values():
            yield from finger_bones.keys()


class TestHumanIkMapping(unittest.TestCase):
    """HumanIK bone mapping table tests."""

    def test_representative_standard_mappings(self):
        self.assertNotIn("センター", MMD_TO_HIK_BONE)
        self.assertEqual(MMD_TO_HIK_BONE["下半身"], "Hips")
        self.assertEqual(MMD_TO_HIK_BONE["上半身"], "Spine")
        self.assertEqual(MMD_TO_HIK_BONE["上半身2"], "Spine1")
        self.assertEqual(MMD_TO_HIK_BONE["左腕"], "LeftArm")
        self.assertEqual(MMD_TO_HIK_BONE["右ひじ"], "RightForeArm")
        self.assertEqual(MMD_TO_HIK_BONE["左つま先"], "LeftToeBase")

    def test_representative_finger_mappings(self):
        self.assertEqual(MMD_TO_HIK_BONE["左親指０"], "LeftHandThumb1")
        self.assertEqual(MMD_TO_HIK_BONE["左親指１"], "LeftHandThumb2")
        self.assertEqual(MMD_TO_HIK_BONE["左親指２"], "LeftHandThumb3")
        self.assertEqual(MMD_TO_HIK_BONE["左人指１"], "LeftHandIndex1")
        self.assertEqual(MMD_TO_HIK_BONE["右薬指３"], "RightHandRing3")
        self.assertEqual(MMD_TO_HIK_BONE["右小指３"], "RightHandPinky3")

    def test_representative_semi_standard_mappings(self):
        self.assertEqual(MMD_TO_HIK_BONE["左腕捻"], "LeftArmRoll")
        self.assertEqual(MMD_TO_HIK_BONE["左手捻"], "LeftForeArmRoll")
        self.assertEqual(MMD_TO_HIK_BONE["右腕捻"], "RightArmRoll")
        self.assertEqual(MMD_TO_HIK_BONE["右手捻"], "RightForeArmRoll")

    def test_mapping_keys_are_bone_validator_standard_names(self):
        known_bones = (
            set(BoneValidator.STANDARD_BONES)
            | set(BoneValidator.SEMI_STANDARD_BONES)
            | set(_finger_standard_bones())
        )

        self.assertLessEqual(set(MMD_TO_HIK_BONE), known_bones)

    def test_required_body_and_finger_bones_are_mapped(self):
        required_standard_without_ik = {
            bone
            for bone in BoneValidator.STANDARD_BONES
            if "ＩＫ" not in bone and bone not in {"センター"}
        }
        required_fingers = set(_finger_standard_bones())

        self.assertLessEqual(required_standard_without_ik, set(MMD_TO_HIK_BONE))
        self.assertLessEqual(required_fingers, set(MMD_TO_HIK_BONE))

    def test_hik_indices_are_unique_and_cover_indexed_mappings(self):
        self.assertEqual(len(set(HIK_BONE_INDICES.values())), len(HIK_BONE_INDICES))
        self.assertEqual(HIK_BONE_INDICES["LeftForeArmRoll"], 46)
        self.assertEqual(HIK_BONE_INDICES["RightArmRoll"], 47)

        indexed_hik_bones = set(MMD_TO_HIK_BONE.values()) - set(MMD_TO_HIK_UNINDEXED_BONES.values())
        self.assertLessEqual(indexed_hik_bones, set(HIK_BONE_INDICES))

    def test_derived_mmd_to_hik_index_table_matches_hik_indices(self):
        for mmd_bone, hik_bone in MMD_TO_HIK_BONE.items():
            if hik_bone in MMD_TO_HIK_UNINDEXED_BONES.values():
                self.assertNotIn(mmd_bone, MMD_TO_HIK_BONE_INDEX)
            else:
                self.assertEqual(MMD_TO_HIK_BONE_INDEX[mmd_bone], HIK_BONE_INDICES[hik_bone])

    def test_unindexed_eye_bones_are_explicit(self):
        self.assertEqual(MMD_TO_HIK_UNINDEXED_BONES, {"左目": "LeftEye", "右目": "RightEye"})
