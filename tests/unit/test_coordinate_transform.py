"""MMD/Maya 座標変換の共通ヘルパーを検証する。"""

import math
import unittest

from mmd_tools.core.coordinate_transform import (
    maya_euler_degrees_to_mmd_radians,
    maya_point_to_mmd,
    mmd_euler_radians_to_maya_degrees,
    mmd_euler_xyz_to_maya,
    mmd_euler_xyz_to_maya_quaternion,
    mmd_point_to_maya,
)


class TestCoordinateTransform(unittest.TestCase):
    def test_point_conversion_flips_z_in_both_directions(self):
        self.assertEqual(mmd_point_to_maya((1.0, 2.0, 3.0)), (1.0, 2.0, -3.0))
        self.assertEqual(maya_point_to_mmd((1.0, 2.0, -3.0)), (1.0, 2.0, 3.0))

    def test_point_conversion_applies_scale(self):
        self.assertEqual(mmd_point_to_maya((1.0, 2.0, 3.0), 2.0), (2.0, 4.0, -6.0))
        self.assertEqual(maya_point_to_mmd((1.0, 2.0, -3.0), 0.5), (0.5, 1.0, 1.5))

    def test_euler_channel_conversion_conjugates_z_reflection(self):
        self.assertEqual(mmd_euler_xyz_to_maya((10.0, 20.0, -30.0)), (-10.0, -20.0, -30.0))

    def test_pmx_euler_quaternion_matches_three_mmd_loader_oracle(self):
        actual = mmd_euler_xyz_to_maya_quaternion((0.7, 0.4, -0.3))
        expected = (
            -0.30440023509091957,
            -0.23474953511944815,
            -0.20493821485715316,
            0.9001297021701701,
        )
        for actual_value, expected_value in zip(actual, expected):
            self.assertAlmostEqual(actual_value, expected_value, places=15)

    def test_radian_degree_conversion_conjugates_z_reflection(self):
        maya_deg = mmd_euler_radians_to_maya_degrees((math.pi, math.pi / 2.0, -math.pi / 4.0))
        self.assertEqual(tuple(round(v, 6) for v in maya_deg), (-180.0, -90.0, -45.0))

        mmd_rad = maya_euler_degrees_to_mmd_radians((-180.0, -90.0, -45.0))
        self.assertEqual(tuple(round(v, 6) for v in mmd_rad), (round(math.pi, 6), round(math.pi / 2.0, 6), round(-math.pi / 4.0, 6)))


if __name__ == "__main__":
    unittest.main()
