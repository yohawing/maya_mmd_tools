"""Unit tests for report-only HumanIK constraint classification."""

import unittest
from unittest.mock import patch

from mmd_tools.core.humanik_constraints import (
    HumanIkConstraintFacts,
    classify_humanik_constraints,
    collect_hik_ownership_report,
    collect_humanik_constraint_facts,
    is_supported_mmd_feedback_row,
    preisolated_mmd_ccdik_nodes_from_disconnected_edges,
)


class TestHumanIkConstraints(unittest.TestCase):
    def test_outputless_ik_is_ignored_but_indirect_writer_stays_blocked(self):
        class Cmds:
            def ls(self, **_kwargs):
                return ["hair_ik"]

            def listConnections(self, *_args, **_kwargs):
                return outputs

        for outputs in ([], ["utility.input"]):
            with self.subTest(outputs=outputs), patch(
                "mmd_tools.core.humanik_constraints._connected_joint_plugs",
                side_effect=[{"|head.rotate"}, set()],
            ):
                facts = collect_humanik_constraint_facts(Cmds(), node_types=("mmdCcdIk",))
                report = classify_humanik_constraints(facts, {"|head"})
                if outputs:
                    self.assertEqual(report["rows"][0]["classification"], "manual")
                else:
                    self.assertEqual(report["rows"], [])

    def test_arm_ik_append_feedback_is_supported_only_for_reviewed_graph(self):
        class Cmds:
            def __init__(self, output, extra_ik_outputs=()):
                self.output = output
                self.ik_outputs = [f"{helper}.rotate", "arm_append.sourceMmdLinkQuaternions", *extra_ik_outputs]

            def listConnections(self, node, **_kwargs):
                return self.ik_outputs if node == "arm_ik" else self.output

            def ls(self, node, long=False):
                return [node]

        for side in ("Left", "Right"):
            with self.subTest(side=side):
                shoulder = f"|model|{side}Shoulder"
                helper = f"{shoulder}|arm_helper"
                arm = f"{shoulder}|arm"
                assignments = [
                    {"joint": shoulder, "hikBone": f"{side}Shoulder"},
                    {"joint": arm, "hikBone": f"{side}Arm"},
                ]
                report = classify_humanik_constraints([
                    HumanIkConstraintFacts(
                        "arm_ik", "mmdCcdIk", reads=(f"{shoulder}.rotate",),
                        writes=(f"{helper}.rotate",),
                    ),
                    HumanIkConstraintFacts(
                        "arm_append", "mmdAppend", reads=(f"{helper}.rotate",),
                        writes=(f"{arm}.rotate",),
                    ),
                ], {shoulder, arm})
                append = next(row for row in report["rows"] if row["node"] == "arm_append")
                self.assertEqual(append["classification"], "feedback_blocker")
                self.assertTrue(is_supported_mmd_feedback_row(
                    append, assignments, report["rows"], Cmds([f"{arm}.rotate"])
                ))
                self.assertFalse(is_supported_mmd_feedback_row(
                    append, assignments, report["rows"], Cmds([f"{arm}.rotate"], ["utility.input"])
                ))

                invalid_cases = (
                    (assignments, report["rows"], [f"{arm}.rotate", "utility.input"]),
                    (assignments, report["rows"], []),
                    (assignments, [append], [f"{arm}.rotate"]),
                    ([{"joint": arm, "hikBone": f"{side}Arm"}], report["rows"], [f"{arm}.rotate"]),
                    ([{"joint": shoulder, "hikBone": "RightShoulder" if side == "Left" else "LeftShoulder"},
                      {"joint": arm, "hikBone": f"{side}Arm"}], report["rows"], [f"{arm}.rotate"]),
                )
                for case_assignments, rows, outputs in invalid_cases:
                    self.assertFalse(is_supported_mmd_feedback_row(
                        append, case_assignments, rows, Cmds(outputs)
                    ))

                solver = next(row for row in report["rows"] if row["node"] == "arm_ik")
                invalid_graphs = (
                    ({**append, "reads": [f"{shoulder}|other_helper.rotate"]}, report["rows"]),
                    ({**append, "reads": [f"|other|{side}Shoulder|arm_helper.rotate"]}, report["rows"]),
                    ({**append, "reads": [f"{helper}.translate"]}, report["rows"]),
                    ({**append, "writes": [f"{arm}.rotate", f"{arm}.translate"]}, report["rows"]),
                    (append, [append, {**solver, "nodeType": "mmdAppend"}]),
                    (append, [append, {**solver, "writeHikJoints": [arm]}]),
                )
                for candidate, rows in invalid_graphs:
                    self.assertFalse(is_supported_mmd_feedback_row(
                        candidate, assignments, rows, Cmds([f"{arm}.rotate"])
                    ))

    def test_ownership_report_excludes_other_models_disconnected_manual_nodes(self):
        facts = [
            HumanIkConstraintFacts(
                "src:left_leg_ik_mmdCcdIk",
                "mmdCcdIk",
                reads=("|src|left_leg_ik.translate",),
                writes=("|src|left_leg.rotate",),
            ),
            HumanIkConstraintFacts(
                "tgt:left_leg_ik_mmdCcdIk",
                "mmdCcdIk",
                reads=("|tgt|left_leg.translate",),
                writes=(),
            ),
        ]

        with patch(
            "mmd_tools.core.humanik_constraints.collect_humanik_constraint_facts",
            return_value=facts,
        ):
            report = collect_hik_ownership_report(
                {"|src|left_leg"},
                cmds_module=object(),
            )

        self.assertEqual(
            [row["node"] for row in report["rows"]],
            ["src:left_leg_ik_mmdCcdIk"],
        )
        self.assertEqual(report["counts"], {"mute_for_hik": 1})

    def test_ownership_report_keeps_same_model_disconnected_manual_node(self):
        fact = HumanIkConstraintFacts(
            "src:left_leg_ik_mmdCcdIk",
            "mmdCcdIk",
            reads=("|src|left_leg.translate",),
            writes=(),
        )

        with patch(
            "mmd_tools.core.humanik_constraints.collect_humanik_constraint_facts",
            return_value=[fact],
        ):
            report = collect_hik_ownership_report(
                {"|src|left_leg"},
                cmds_module=object(),
            )

        self.assertEqual(report["rows"][0]["classification"], "manual")

    def test_preisolated_ccdik_edge_requires_exact_importer_foot_rotate_topology(self):
        valid_node = "|target:left_leg_ik_mmdCcdIk"
        valid_edge = {
            "source": f"{valid_node}.outputRotate[0]",
            "destination": "|target|left_leg.rotate",
            "sourceNodeUuid": "uuid-left-leg-ik",
        }
        assignments = [{"joint": "|target|left_leg", "hikBone": "LeftLeg"}]
        node_uuids = {valid_node: "uuid-left-leg-ik"}
        self.assertEqual(
            preisolated_mmd_ccdik_nodes_from_disconnected_edges(
                [valid_edge], assignments, node_uuids
            ),
            (valid_node,),
        )

        invalid_edges = (
            # (1) A non-foot mmdCcdIk node is never trusted.
            {
                "source": "|target:left_arm_ik_mmdCcdIk.outputRotate[0]",
                "destination": "|target|left_arm.rotate",
                "sourceNodeUuid": "uuid-left-arm-ik",
            },
            # (2) The recorded source must be an outputRotate array plug.
            {
                "source": f"{valid_node}.outputTranslate[0]",
                "destination": "|target|left_leg.rotate",
                "sourceNodeUuid": "uuid-left-leg-ik",
            },
            # (3) The recorded destination must be a rotate channel.
            {
                "source": f"{valid_node}.outputRotate[0]",
                "destination": "|target|left_leg.translate",
                "sourceNodeUuid": "uuid-left-leg-ik",
            },
            # (4) Persisted identity must still match the current Maya node.
            {**valid_edge, "sourceNodeUuid": "stale-uuid"},
            # (5) The recorded destination must be a same-side HIK leg slot.
            {**valid_edge, "destination": "|target|right_leg.rotate"},
            # (6) Malformed/non-object rows are ignored without guessing.
            {},
            None,
            "not-an-edge",
        )
        for edge in invalid_edges:
            with self.subTest(edge=edge):
                self.assertEqual(
                    preisolated_mmd_ccdik_nodes_from_disconnected_edges(
                        [edge], assignments, node_uuids
                    ),
                    (),
                )

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
            [
                HumanIkConstraintFacts("unknown", "mmdAppend", complete=False),
                HumanIkConstraintFacts(
                    "outside_only",
                    "mmdAppend",
                    reads=("|eye.rotateX",),
                    writes=("|eye_helper.rotateX",),
                ),
            ],
            {"|hips"},
        )

        by_node = {row["node"]: row for row in report["rows"]}
        self.assertEqual(by_node["unknown"]["classification"], "manual")
        self.assertEqual(by_node["outside_only"]["classification"], "keep_post")


if __name__ == "__main__":
    unittest.main()
