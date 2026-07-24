"""Deterministic fixture contracts for the reduction ABI probe."""

from __future__ import annotations

import math
import unittest

from tests.release.reduction_abi_probe import make_dense_fixture


class ReductionAbiProbeFixtureTest(unittest.TestCase):
    """Keep the probe input stable while the native gate exercises the DLL."""

    def test_fixture_is_deterministic_and_dense(self):
        first = make_dense_fixture()
        second = make_dense_fixture()
        self.assertEqual(first, second)
        self.assertEqual(first["frame_count"], 31)
        self.assertEqual(len(first["world_matrices"]), 31 * 2 * 16)
        self.assertEqual(len(first["morph_weights"]), 31)
        self.assertTrue(all(math.isfinite(value) for value in first["world_matrices"]))
        self.assertTrue(all(math.isfinite(value) for value in first["morph_weights"]))

    def test_fixture_has_parent_and_child_world_samples(self):
        fixture = make_dense_fixture()
        self.assertEqual(fixture["parents"], [-1, 0])
        # Column-major translation slots for root and child differ at frame 0,
        # proving the child samples are world matrices rather than local data.
        self.assertEqual(fixture["world_matrices"][12:15], [0.0, 0.0, 0.0])
        child_translation = fixture["world_matrices"][28:31]
        self.assertAlmostEqual(math.hypot(child_translation[0], child_translation[1]), 1.0)
        self.assertAlmostEqual(child_translation[2], 0.0)


if __name__ == "__main__":
    unittest.main()
