"""Pure-Python tests for animation-layer DG dump normalization helpers."""

import unittest

from mmd_tools.tools.anim_layer_dg_dump import diff_evaluations, normalize_graph, normalize_node_numbers


class TestAnimLayerDgDump(unittest.TestCase):
    def test_normalize_node_numbers_keeps_array_indices(self):
        self.assertEqual(
            normalize_node_numbers("mmdCcdIk12.inputRotate[0].inputRotateElementX"),
            "mmdCcdIk#.inputRotate[0].inputRotateElementX",
        )
        self.assertEqual(normalize_node_numbers("joint3.rotateX"), "joint#.rotateX")

    def test_normalize_graph_sorts_dicts_and_normalizes_strings(self):
        graph = {
            "joint2.rotateX": ["animCurveTA11.output", {"node": "animBlendNodeAdditiveRotation4"}],
            "joint1.rotateY": [],
        }

        normalized = normalize_graph(graph)

        self.assertEqual(
            list(normalized),
            ["joint#.rotateX", "joint#.rotateY"],
        )
        self.assertEqual(normalized["joint#.rotateX"][0], "animCurveTA#.output")
        self.assertEqual(normalized["joint#.rotateX"][1]["node"], "animBlendNodeAdditiveRotation#")

    def test_diff_evaluations_reports_tolerance_failures(self):
        diff = diff_evaluations(
            {"rotateX": {"0.0": 10.0, "5.0": 15.0}},
            {"rotateX": {"0.0": 10.000001, "5.0": 15.1}},
            tolerance=1.0e-4,
        )

        self.assertFalse(diff["matches"])
        self.assertEqual(diff["mismatches"][0]["plug"], "rotateX")
        self.assertEqual(diff["mismatches"][0]["frame"], "5.0")


if __name__ == "__main__":
    unittest.main()
