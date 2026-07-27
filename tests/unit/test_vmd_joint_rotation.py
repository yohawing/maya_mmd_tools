"""Euler branch continuity tests for VMD joint rotation conversion."""

from mmd_tools.converters.vmd_joint_rotation import unwrap_euler_sequence
from tests.common.maya_test_base import MayaTestBase


class TestVmdJointRotation(MayaTestBase):
    """Regression tests for quaternion-derived Maya Euler channels."""

    def test_unwrap_euler_sequence_keeps_180_degree_boundary_continuous(self):
        """Equivalent 179/-179 rotations use adjacent Maya Euler branches."""
        unwrapped = unwrap_euler_sequence(
            [
                (179.0, 0.0, 0.0),
                (-179.0, 0.0, 0.0),
            ]
        )

        self.assertEqual(unwrapped[0], (179.0, 0.0, 0.0))
        self.assertAlmostEqual(unwrapped[1][0], 181.0, places=6)
        self.assertLess(abs(unwrapped[1][0] - unwrapped[0][0]), 5.0)

    def test_unwrap_euler_sequence_unwraps_each_axis_without_mutating_input(self):
        """Each channel is unwrapped independently and source samples stay intact."""
        source = [(0.0, 179.0, -179.0), (0.0, -179.0, 179.0)]
        unwrapped = unwrap_euler_sequence(source)

        self.assertEqual(source, [(0.0, 179.0, -179.0), (0.0, -179.0, 179.0)])
        self.assertEqual(unwrapped, [(0.0, 179.0, -179.0), (0.0, 181.0, -181.0)])

    def test_unwrap_euler_sequence_selects_closest_gimbal_solution(self):
        """Equivalent XYZ solutions stay continuous across the Y=90 singularity."""
        unwrapped = unwrap_euler_sequence(
            [
                (0.0, 89.0, 0.0),
                (180.0, 89.0, 180.0),
            ],
            rotate_order=0,
        )

        self.assertAlmostEqual(unwrapped[1][0], 0.0, places=6)
        self.assertAlmostEqual(unwrapped[1][1], 91.0, places=6)
        self.assertAlmostEqual(unwrapped[1][2], 0.0, places=6)
