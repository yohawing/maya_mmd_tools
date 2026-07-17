from __future__ import annotations

import unittest

from mmd_tools.core.collider_display import (
    COLLISION_GROUP_PALETTE,
    collision_group_color,
    physics_mode_line_style,
)


class TestColliderDisplay(unittest.TestCase):
    def test_palette_has_sixteen_unique_stable_colors(self):
        self.assertEqual(len(COLLISION_GROUP_PALETTE), 16)
        self.assertEqual(len(set(COLLISION_GROUP_PALETTE)), 16)
        self.assertEqual(COLLISION_GROUP_PALETTE[0], (0.121, 0.466, 0.705))
        self.assertEqual(COLLISION_GROUP_PALETTE[15], (0.769, 0.612, 0.580))

    def test_mode_never_changes_group_hue(self):
        for group in range(16):
            colors = [collision_group_color(group, mode) for mode in range(3)]
            self.assertEqual(colors[0][:3], colors[1][:3])
            self.assertEqual(colors[1][:3], colors[2][:3])
            self.assertGreater(colors[0][3], colors[1][3])
            self.assertGreater(colors[1][3], colors[2][3])

    def test_modes_all_use_solid_line_style(self):
        self.assertEqual(
            [physics_mode_line_style(mode) for mode in range(3)],
            [0, 0, 0],
        )

    def test_invalid_values_are_clamped(self):
        self.assertEqual(collision_group_color(-1, -1), collision_group_color(0, 0))
        self.assertEqual(collision_group_color(99, 99), collision_group_color(15, 2))


if __name__ == "__main__":
    unittest.main()
