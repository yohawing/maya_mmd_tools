from __future__ import annotations

import unittest

from tests.viewport import import_scale_drift_e2e as runner


def _result(scale: float, parser: str = "legacy", *, bounds=None, positions=None):
    return {
        "parser": parser,
        "scale": scale,
        "rootImportScale": scale,
        "rootImportScaleMatches": True,
        "rootScale": [1.0, 1.0, 1.0],
        "visibleMeshCount": 1,
        "invalidMeshBoundsCount": 0,
        "meshWorldBounds": bounds
        or [[-scale, -2.0 * scale, -3.0 * scale, scale, 2.0 * scale, 3.0 * scale]],
        "skinClusterCount": 1,
        "requestedInfluenceCount": 1,
        "invalidInfluenceCount": 0,
        "influenceJointCount": 1,
        "influenceJointWorldPositions": positions or [[0.5 * scale, 2.0 * scale, -scale]],
        "maxBindWorldDelta": 0.0,
    }


class ImportScaleDriftE2ETest(unittest.TestCase):
    def test_default_scales_and_fixed_expectation(self):
        args = runner.parse_args([])

        self.assertEqual(runner.resolve_scales(args.scale), [0.5, 1.0, 1.5])
        self.assertEqual(args.expect, "fixed")
        self.assertEqual(args.parser, "both")

    def test_explicit_scales_replace_defaults(self):
        args = runner.parse_args(["--scale", "1.5", "--scale", "0.75", "--expect", "fixed"])

        self.assertEqual(runner.resolve_scales(args.scale), [1.5, 0.75])

    def test_evaluate_results_passes_linearly_scaled_imports(self):
        results = [
            _result(scale, parser)
            for parser in ("legacy", "native")
            for scale in (0.5, 1.0, 1.5)
        ]

        evaluation = runner.evaluate_results(results, clean_threshold=1.0e-4)

        self.assertEqual(evaluation["status"], "pass")
        self.assertEqual(evaluation["failures"], [])
        self.assertTrue(all(item["status"] == "pass" for item in results))

    def test_evaluate_results_fails_when_a_required_witness_is_missing(self):
        results = [_result(scale) for scale in (0.5, 1.0, 1.5)]
        results[1]["rootImportScaleMatches"] = False
        results[1]["skinClusterCount"] = 0

        evaluation = runner.evaluate_results(results, clean_threshold=1.0e-4)

        self.assertEqual(evaluation["status"], "fail")
        self.assertTrue(any("persisted root mmd_import_scale" in value for value in evaluation["failures"]))
        self.assertTrue(any("no skinCluster" in value for value in evaluation["failures"]))

    def test_evaluate_results_fails_when_scale_normalized_bounds_drift(self):
        results = [_result(scale) for scale in (0.5, 1.0, 1.5)]
        results[2]["meshWorldBounds"][0][3] += 0.01

        evaluation = runner.evaluate_results(results, clean_threshold=1.0e-4)

        self.assertEqual(evaluation["status"], "fail")
        self.assertTrue(any("mesh world bounds are not linear" in value for value in evaluation["failures"]))

    def test_evaluate_results_fails_on_invalid_witness_counts_and_non_finite_bind_delta(self):
        results = [_result(scale) for scale in (0.5, 1.0, 1.5)]
        results[1]["invalidMeshBoundsCount"] = 1
        results[1]["invalidInfluenceCount"] = 1
        results[1]["maxBindWorldDelta"] = float("nan")

        evaluation = runner.evaluate_results(results, clean_threshold=1.0e-4)

        self.assertEqual(evaluation["status"], "fail")
        self.assertTrue(any("world bounds are invalid" in value for value in evaluation["failures"]))
        self.assertTrue(any("invalid bind/world data" in value for value in evaluation["failures"]))
        self.assertTrue(any("maxBindWorldDelta is non-finite" in value for value in evaluation["failures"]))

    def test_evaluate_results_fails_on_non_finite_samples_and_tolerances(self):
        results = [_result(scale) for scale in (0.5, 1.0, 1.5)]
        results[2]["meshWorldBounds"][0][0] = float("nan")

        evaluation = runner.evaluate_results(
            results,
            clean_threshold=-1.0,
            linearity_tolerance=float("nan"),
        )

        self.assertEqual(evaluation["status"], "fail")
        self.assertTrue(any("clean threshold must be finite" in value for value in evaluation["failures"]))
        self.assertTrue(any("linearity tolerance must be finite" in value for value in evaluation["failures"]))
        self.assertTrue(any("mesh world bounds are not linear" in value for value in evaluation["failures"]))


if __name__ == "__main__":
    unittest.main()
