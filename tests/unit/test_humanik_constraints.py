"""Unit tests for report-only HumanIK constraint classification."""

import unittest

from mmd_tools.core.humanik_constraints import (
    HumanIkConstraintFacts,
    classify_humanik_constraints,
)


class TestHumanIkConstraints(unittest.TestCase):
    def test_connection_ownership_overrides_misleading_node_names(self):
        report = classify_humanik_constraints(
            [
                HumanIkConstraintFacts(
                    "looks_like_post_twist",
                    "mmdAppend",
                    reads=("|outside.rotateX",),
                    writes=("|hips.rotateX",),
                ),
                HumanIkConstraintFacts(
                    "generic_node",
                    "mmdAppend",
                    reads=("|hips.rotateX",),
                    writes=("|twist_helper.rotateX",),
                ),
            ],
            {"|hips"},
        )

        by_node = {row["node"]: row for row in report["rows"]}
        self.assertEqual(by_node["looks_like_post_twist"]["classification"], "mute_for_hik")
        self.assertEqual(by_node["generic_node"]["classification"], "keep_post")
        self.assertEqual(
            report["writerIndex"]["|hips.rotateX"][0]["node"],
            "looks_like_post_twist",
        )

    def test_feedback_and_physics_are_blockers(self):
        report = classify_humanik_constraints(
            [
                HumanIkConstraintFacts(
                    "hik_to_helper",
                    "mmdAppend",
                    reads=("|hips.rotateX",),
                    writes=("|helper.rotateX",),
                ),
                HumanIkConstraintFacts(
                    "helper_to_hik",
                    "mmdCcdIk",
                    reads=("|helper.rotateX",),
                    writes=("|hips.rotateX",),
                ),
                HumanIkConstraintFacts(
                    "physics_to_hik",
                    "mmdPhysicsBoneDriver",
                    writes=("|hips.rotateX",),
                ),
            ],
            {"|hips"},
        )

        by_node = {row["node"]: row for row in report["rows"]}
        self.assertEqual(by_node["helper_to_hik"]["classification"], "feedback_blocker")
        self.assertEqual(by_node["physics_to_hik"]["classification"], "physics_blocker")

    def test_incomplete_or_unrelated_node_is_manual(self):
        report = classify_humanik_constraints(
            [HumanIkConstraintFacts("unknown", "mmdAppend", complete=False)],
            {"|hips"},
        )

        self.assertEqual(report["rows"][0]["classification"], "manual")


if __name__ == "__main__":
    unittest.main()
