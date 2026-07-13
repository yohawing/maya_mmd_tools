"""Unit tests for scene-independent HumanIK assignment resolution."""

import unittest

from mmd_tools.config.humanik_mapping import HIK_BONE_INDICES
from mmd_tools.core.humanik_resolver import (
    HumanIkJointCandidate,
    normalize_mmd_bone_name,
    resolve_humanik_assignments,
)


class TestHumanIkResolver(unittest.TestCase):
    """HumanIK resolver behavior tests."""

    def test_normalize_mmd_bone_name_accepts_common_variants(self):
        self.assertEqual(normalize_mmd_bone_name("左肘"), "左ひじ")
        self.assertEqual(normalize_mmd_bone_name("left_elbow"), "左ひじ")
        self.assertEqual(normalize_mmd_bone_name("左親指0"), "左親指０")
        self.assertIsNone(normalize_mmd_bone_name("not_a_standard_bone"))

    def test_resolve_assignments_prefers_import_metadata(self):
        result = resolve_humanik_assignments(
            [
                HumanIkJointCandidate("lower_body_joint", mmd_name="下半身", bone_index=1),
                HumanIkJointCandidate("spine_joint", english_name="upper_body", bone_index=2),
                HumanIkJointCandidate("left_elbow_joint", mmd_name="左肘", bone_index=10),
            ]
        )

        assignments = result.assignments_by_hik_index
        self.assertEqual(assignments[HIK_BONE_INDICES["Hips"]].joint, "lower_body_joint")
        self.assertEqual(assignments[HIK_BONE_INDICES["Spine"]].source, "english_name")
        self.assertEqual(assignments[HIK_BONE_INDICES["LeftForeArm"]].mmd_bone, "左ひじ")
        self.assertNotIn("下半身", result.missing_mmd_bones)
        self.assertIn("右腕", result.missing_mmd_bones)

    def test_resolve_assignments_falls_back_to_joint_name(self):
        result = resolve_humanik_assignments([HumanIkJointCandidate("|root|left_arm")])

        assignment = result.assignments_by_hik_index[HIK_BONE_INDICES["LeftArm"]]
        self.assertEqual(assignment.joint, "|root|left_arm")
        self.assertEqual(assignment.source, "node")

    def test_resolve_assignments_reports_unindexed_bones(self):
        result = resolve_humanik_assignments([HumanIkJointCandidate("eye_joint", mmd_name="左目")])

        self.assertEqual(result.assignments, ())
        self.assertEqual(result.unindexed_mmd_bones, ("左目",))

    def test_resolve_assignments_prefers_lower_source_rank_then_bone_index(self):
        result = resolve_humanik_assignments(
            [
                HumanIkJointCandidate("node_named_left_arm", english_name="left_arm", bone_index=1),
                HumanIkJointCandidate("metadata_left_arm", mmd_name="左腕", bone_index=99),
                HumanIkJointCandidate("later_left_arm", mmd_name="左腕", bone_index=100),
            ]
        )

        assignment = result.assignments_by_hik_index[HIK_BONE_INDICES["LeftArm"]]
        self.assertEqual(assignment.joint, "metadata_left_arm")
        self.assertEqual(assignment.source, "mmd_name")
        self.assertEqual(
            [duplicate.joint for duplicate in result.duplicate_assignments],
            ["later_left_arm", "node_named_left_arm"],
        )

    def test_assignments_are_sorted_by_hik_index(self):
        result = resolve_humanik_assignments(
            [
                HumanIkJointCandidate("head", mmd_name="頭"),
                HumanIkJointCandidate("hips", mmd_name="下半身"),
                HumanIkJointCandidate("right_hand", mmd_name="右手首"),
            ]
        )

        self.assertEqual([assignment.hik_bone for assignment in result.assignments], ["Hips", "RightHand", "Head"])


if __name__ == "__main__":
    unittest.main()
