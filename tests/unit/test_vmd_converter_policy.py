"""Small policy tests for VmdConverter.

Scene-editing import/keying tests remain in test_vmd_converter.py. This module
keeps converter decisions that only need in-memory VMD-like data separate.
"""

import unittest

from mmd_tools.converters.vmd_bone_interpolation import parse_vmd_interpolation
from mmd_tools.converters.vmd_converter import VmdConverter


def _bone_interp_bytes_by_channel(**overrides):
    """Build VMD bone interpolation bytes from per-channel control points."""
    default_points = (20, 20, 107, 107)
    channels = ("translate_x", "translate_y", "translate_z", "rotation")
    points_by_channel = {channel: overrides.get(channel, default_points) for channel in channels}
    data = bytearray(64)
    for index, channel in enumerate(channels):
        x1, y1, x2, y2 = points_by_channel[channel]
        data[index] = x1
        data[4 + index] = y1
        data[8 + index] = x2
        data[12 + index] = y2
    return bytes(data)


class TestVmdConverterPolicy(unittest.TestCase):
    """Small converter policy tests that do not edit a Maya scene."""

    def setUp(self):
        self.converter = VmdConverter()

    def test_vmd_frame_to_maya_time_uses_fixed_30fps_source(self):
        """VMD frame numbers are always interpreted as 30fps source frames."""
        self.converter.fps = 60.0
        self.assertEqual(self.converter.vmd_frame_to_maya_time(30), 60.0)

    def test_get_failed_bones(self):
        """Failed bones are reported as a copy of the converter failure set."""
        self.assertEqual(len(self.converter.get_failed_bones()), 0)

        self.converter._failed_bones.add("ボーン1")
        self.converter._failed_bones.add("ボーン2")

        failed = self.converter.get_failed_bones()
        self.assertEqual(len(failed), 2)
        self.assertIn("ボーン1", failed)
        self.assertIn("ボーン2", failed)

        failed.add("ボーン3")
        self.assertEqual(len(self.converter._failed_bones), 2)

    def test_detect_vmd_motion_kind(self):
        """VMD contents are classified as model/camera/light/mixed/empty."""
        def fake(**kwargs):
            defaults = {
                "bone_frames": [],
                "morph_frames": [],
                "camera_frames": [],
                "light_frames": [],
            }
            defaults.update(kwargs)
            return type("FakeVmdData", (), defaults)()

        self.assertEqual(self.converter._detect_vmd_motion_kind(fake()), "empty")
        self.assertEqual(self.converter._detect_vmd_motion_kind(fake(bone_frames=[object()])), "model")
        self.assertEqual(self.converter._detect_vmd_motion_kind(fake(camera_frames=[object()])), "camera")
        self.assertEqual(self.converter._detect_vmd_motion_kind(fake(light_frames=[object()])), "light")
        self.assertEqual(self.converter._detect_vmd_motion_kind(fake(ik_show_hide_frames=[object()])), "model")
        self.assertEqual(
            self.converter._detect_vmd_motion_kind(fake(bone_frames=[object()], camera_frames=[object()])),
            "mixed",
        )

    def test_parse_vmd_bone_interpolation_uses_bone_channel_layout(self):
        """Bone interpolation uses the VMD 4 channel x 4 byte layout."""
        data = _bone_interp_bytes_by_channel(
            translate_x=(20, 100, 100, 20),
            translate_y=(30, 40, 90, 110),
            translate_z=(10, 80, 120, 30),
            rotation=(64, 20, 100, 90),
        )

        parsed = self.converter._parse_vmd_interpolation(data)
        self.assertEqual(parsed, parse_vmd_interpolation(data))
        self.assertAlmostEqual(parsed["translate_x"][0], 20 / 127)
        self.assertAlmostEqual(parsed["translate_x"][1], 100 / 127)
        self.assertAlmostEqual(parsed["translate_y"][0], 30 / 127)
        self.assertAlmostEqual(parsed["translate_y"][1], 40 / 127)
        self.assertAlmostEqual(parsed["translate_z"][2], 120 / 127)
        self.assertAlmostEqual(parsed["rotation"][3], 90 / 127)


if __name__ == "__main__":
    unittest.main()
