"""VMD camera math and interpolation tests.

Scene-editing camera import tests remain in test_vmd_converter.py; this module
keeps pure camera-state conversion coverage separate from the large converter
integration-style test class.
"""

import math
import unittest

import maya.api.OpenMaya as om

from mmd_tools.converters.vmd_camera_animation import (
    maya_camera_eye_from_vmd_state,
    maya_camera_rotation_from_vmd_state,
    parse_vmd_camera_interpolation,
)
from mmd_tools.converters.vmd_converter import VmdConverter


class TestVmdCameraAnimationMath(unittest.TestCase):
    """Pure VMD camera conversion tests."""

    def test_camera_roll_does_not_move_eye_position(self):
        """VMD camera roll rotates around the view axis without moving the eye."""
        position = (4.0, 12.0, -6.0)
        base_rotation = (0.35, -0.25, 0.0)
        rolled_rotation = (0.35, -0.25, math.pi / 3.0)
        distance = -30.0

        base_eye = maya_camera_eye_from_vmd_state(position, base_rotation, distance, 1.0)
        rolled_eye = maya_camera_eye_from_vmd_state(position, rolled_rotation, distance, 1.0)

        self.assertAlmostEqual(rolled_eye[0], base_eye[0], places=6)
        self.assertAlmostEqual(rolled_eye[1], base_eye[1], places=6)
        self.assertAlmostEqual(rolled_eye[2], base_eye[2], places=6)

    def test_camera_rotation_matches_three_mmd_loader_yxz_convention(self):
        """MMD camera Euler uses the same orbit convention as three-mmd-loader."""
        rotate_x, rotate_y, rotate_z = maya_camera_rotation_from_vmd_state((0.25, 0.5, 0.0))
        rotation = om.MEulerRotation(rotate_x, rotate_y, rotate_z)
        forward = om.MVector(0.0, 0.0, -1.0) * rotation.asMatrix()

        self.assertAlmostEqual(forward.x, -0.46452136, places=6)
        self.assertAlmostEqual(forward.y, 0.247403959, places=6)
        self.assertAlmostEqual(forward.z, -0.850300645, places=6)

    def test_camera_eye_matches_three_mmd_loader_signed_distance(self):
        """MMD camera distance is applied as the same signed orbit offset."""
        yaw_eye = maya_camera_eye_from_vmd_state((0.0, 0.0, 0.0), (0.0, 0.5, 0.0), -10.0, 1.0)
        pitch_eye = maya_camera_eye_from_vmd_state((0.0, 0.0, 0.0), (math.pi / 4.0, 0.0, 0.0), -45.0, 1.0)

        self.assertAlmostEqual(yaw_eye[0], 4.794255386, places=6)
        self.assertAlmostEqual(yaw_eye[1], 0.0, places=6)
        self.assertAlmostEqual(yaw_eye[2], 8.775825619, places=6)
        self.assertAlmostEqual(pitch_eye[0], 0.0, places=6)
        self.assertAlmostEqual(pitch_eye[1], -31.819805153, places=6)
        self.assertAlmostEqual(pitch_eye[2], 31.819805153, places=6)

    def test_parse_vmd_camera_interpolation_uses_camera_channel_layout(self):
        """Camera interpolation uses the 6 channel x 4 byte VMD layout."""
        data = bytes(
            [
                1, 2, 3, 4,
                5, 6, 7, 8,
                9, 10, 11, 12,
                13, 14, 15, 16,
                17, 18, 19, 20,
                21, 22, 23, 24,
            ]
        )

        parsed = VmdConverter._parse_vmd_camera_interpolation(data)
        self.assertEqual(parsed, parse_vmd_camera_interpolation(data))

        self.assertEqual(parsed["translate_x"], (1 / 127, 2 / 127, 3 / 127, 4 / 127))
        self.assertEqual(parsed["distance"], (17 / 127, 18 / 127, 19 / 127, 20 / 127))
        self.assertEqual(parsed["viewing_angle"], (21 / 127, 22 / 127, 23 / 127, 24 / 127))


if __name__ == "__main__":
    unittest.main()
