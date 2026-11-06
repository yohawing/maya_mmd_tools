"""Geometry controls for measurements of evaluated Maya meshes."""
import unittest
import tempfile
from pathlib import Path

import numpy as np

from tools.maya_gui.skirt_penetration import crossings, prepare_output


class TestSkirtPenetration(unittest.TestCase):
    def setUp(self):
        self.triangle = np.array([[(0., 0., 0.), (2., 0., 0.), (0., 2., 0.)]])

    def test_strict_crossing_in_either_direction(self):
        edge = np.array([[(0.5, 0.5, -1.), (0.5, 0.5, 1.)]])
        self.assertEqual(crossings(edge, self.triangle), [[0.5, 0.5, 0.]])
        self.assertEqual(crossings(edge[:, ::-1], self.triangle), [[0.5, 0.5, 0.]])

    def test_disjoint_and_surface_contact_are_not_crossings(self):
        edges = np.array([
            [(2., 2., -1.), (2., 2., 1.)],
            [(0.5, 0.5, 0.), (0.5, 0.5, 1.)],
            [(0.2, 0.2, 0.), (0.7, 0.7, 0.)],
            [(0.5, 0.5, 1.), (0.5, 0.5, 2.)],
        ])
        self.assertEqual(crossings(edges, self.triangle), [])

    def test_existing_evidence_is_preserved_and_run_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            prepare_output(path)
            evidence = path / "frame-0139.png"
            evidence.write_bytes(b"previous evidence")
            with self.assertRaisesRegex(ValueError, "earlier run"):
                prepare_output(path)
            self.assertEqual(evidence.read_bytes(), b"previous evidence")


if __name__ == "__main__":
    unittest.main()
