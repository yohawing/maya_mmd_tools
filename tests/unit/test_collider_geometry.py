from __future__ import annotations

import unittest

from mmd_tools.core.collider_geometry import box_draw_size, capsule_dimensions, collider_half_extents


class TestColliderGeometry(unittest.TestCase):
    def test_capsule_total_height_adds_two_radii(self):
        self.assertEqual(capsule_dimensions((0.75, 1.25, 99.0)), (0.75, 1.25, 2.75))
        self.assertEqual(collider_half_extents(2, (0.75, 1.25, 99.0)), (0.75, 1.375, 0.75))

    def test_zero_cylinder_height_is_a_sphere(self):
        self.assertEqual(capsule_dimensions((2.0, 0.0, 0.0)), (2.0, 0.0, 4.0))
        self.assertEqual(collider_half_extents(2, (2.0, 0.0, 0.0)), (2.0, 2.0, 2.0))

    def test_sphere_and_box_use_pmx_half_extents(self):
        self.assertEqual(collider_half_extents(0, (2.0, 9.0, 8.0)), (2.0, 2.0, 2.0))
        self.assertEqual(collider_half_extents(1, (2.0, 4.0, 6.0)), (2.0, 4.0, 6.0))
        self.assertEqual(box_draw_size((2.0, 4.0, 6.0)), (4.0, 8.0, 12.0))

    def test_negative_dimensions_collapse_without_inverting_bounds(self):
        self.assertEqual(collider_half_extents(2, (-1.0, -2.0, 0.0)), (0.0, 0.0, 0.0))
        self.assertEqual(box_draw_size((-1.0, -2.0, -3.0)), (0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
