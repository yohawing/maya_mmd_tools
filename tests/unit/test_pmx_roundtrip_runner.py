"""Focused tests for PMX parser-writer roundtrip comparison semantics."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.roundtrip.pmx_roundtrip_runner import _effective_vertex_influences


class PmxRoundtripRunnerTest(unittest.TestCase):
    def test_bdef2_and_padded_bdef4_have_equal_effective_influences(self):
        bdef2 = SimpleNamespace(
            weight_transform_type=1,
            bone_indices=[59, 61],
            bone_weights=[0.352],
        )
        bdef4 = SimpleNamespace(
            weight_transform_type=2,
            bone_indices=[59, 61, 59, 59],
            bone_weights=[0.352, 0.648, 0.0, 0.0],
        )

        self.assertEqual(
            _effective_vertex_influences(bdef2),
            _effective_vertex_influences(bdef4),
        )

    def test_duplicate_nonzero_influences_are_aggregated(self):
        vertex = SimpleNamespace(
            weight_transform_type=2,
            bone_indices=[3, 1, 3, 1],
            bone_weights=[0.25, 0.5, 0.25, 0.0],
        )

        self.assertEqual(
            _effective_vertex_influences(vertex),
            [[1, 0.5], [3, 0.5]],
        )


if __name__ == "__main__":
    unittest.main()
