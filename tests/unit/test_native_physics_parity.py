"""Tests for catastrophic Maya Bullet/native world-space parity checks."""

from __future__ import annotations

import unittest

from tests.viewport.native_physics_parity import (
    apply_import_scale,
    compare_bullet_world_sanity,
    static_extent_from_positions,
)


def _sample(world: list[float], parent: list[float], *, finite: bool = True) -> dict:
    return {
        "joint": "|model|bone",
        "worldTranslate": world,
        "parentWorldTranslate": parent,
        "worldMatrixScale": [1.0, 1.0, 1.0],
        "finite": finite,
    }


class NativePhysicsParityTest(unittest.TestCase):
    def test_import_scale_resizes_static_extent(self):
        self.assertEqual(apply_import_scale(10.0, 0.1), 1.0)
        self.assertEqual(apply_import_scale(10.0, 10.0), 100.0)

    def test_import_scale_fails_closed_when_invalid(self):
        for scale in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(scale=scale), self.assertRaisesRegex(ValueError, "finite and positive"):
                apply_import_scale(10.0, scale)

    def test_static_vertices_govern_threshold_not_huge_runtime_bbox(self):
        static_extent = static_extent_from_positions([[0.0, 0.0, 0.0], [0.0, 10.0, 0.0]])
        huge_runtime_bbox_extent = 1_000_000.0
        baseline = {"7": {"0": _sample([30.0, 0.0, 0.0], [29.0, 0.0, 0.0])}}
        native = {"7": {"0": _sample([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])}}
        result = compare_bullet_world_sanity(baseline, native, [0], static_extent)
        self.assertFalse(result["passed"])
        self.assertEqual(result["modelExtent"], 10.0)
        self.assertGreater(huge_runtime_bbox_extent, result["displacementLimit"])

    def test_static_extent_fails_closed_without_finite_vertices(self):
        with self.assertRaisesRegex(ValueError, "no finite"):
            static_extent_from_positions([[float("nan"), 0.0, 0.0], None])

    def test_normal_solver_difference_passes(self):
        baseline = {"7": {"0": _sample([1.5, 8.0, 0.0], [0.0, 8.0, 0.0])}}
        native = {"7": {"0": _sample([1.0, 8.0, 0.0], [0.0, 8.0, 0.0])}}
        result = compare_bullet_world_sanity(baseline, native, [0], 10.0)
        self.assertTrue(result["passed"])
        self.assertEqual(result["comparedSamples"], 1)

    def test_world_displacement_explosion_fails_with_evidence(self):
        baseline = {"7": {"3": _sample([30.0, 8.0, 0.0], [29.0, 8.0, 0.0])}}
        native = {"7": {"3": _sample([1.0, 8.0, 0.0], [0.0, 8.0, 0.0])}}
        result = compare_bullet_world_sanity(baseline, native, [3], 10.0)
        self.assertFalse(result["passed"])
        self.assertEqual(result["worst"]["boneIndex"], 7)
        self.assertGreater(result["worst"]["worldDisplacement"], result["worst"]["displacementLimit"])

    def test_parent_separation_explosion_fails(self):
        baseline = {"7": {"0": _sample([0.0, 40.0, 0.0], [0.0, 0.0, 0.0])}}
        native = {"7": {"0": _sample([0.0, 1.0, 0.0], [0.0, 0.0, 0.0])}}
        result = compare_bullet_world_sanity(baseline, native, [0], 10.0)
        self.assertFalse(result["passed"])
        self.assertGreater(
            result["worst"]["bulletParentSeparation"], result["worst"]["separationLimit"]
        )

    def test_non_finite_sample_fails(self):
        baseline = {"7": {"0": _sample([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], finite=False)}}
        native = {"7": {"0": _sample([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])}}
        result = compare_bullet_world_sanity(baseline, native, [0], 10.0)
        self.assertFalse(result["passed"])
        self.assertFalse(result["worst"]["finite"])


if __name__ == "__main__":
    unittest.main()
