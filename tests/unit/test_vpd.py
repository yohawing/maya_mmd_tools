"""Unit tests for VPD data types and parser roundtrips."""

import tempfile
import unittest
from pathlib import Path

from mmd_tools.core.exceptions import MMDParseException
from mmd_tools.core.vpd_data import VpdData
from mmd_tools.core.vpd_data.bone_pose import BonePose
from mmd_tools.core.vpd_data.header import VpdHeader


class TestVpdHeader(unittest.TestCase):
    """VpdHeader behavior."""

    def test_defaults(self):
        header = VpdHeader()

        self.assertEqual(header.signature, "Vocaloid Pose Data file")
        self.assertEqual(header.parent_file, "")
        self.assertEqual(header.bone_count, 0)

    def test_string_representation(self):
        header = VpdHeader()
        header.parent_file = "model.osm"
        header.bone_count = 10

        text = str(header)

        self.assertIn("VPD Header", text)
        self.assertIn("model.osm", text)
        self.assertIn("10", text)

    def test_repr_includes_parent_and_count(self):
        header = VpdHeader()
        header.parent_file = "model.osm"
        header.bone_count = 5

        text = repr(header)

        self.assertIn("model.osm", text)
        self.assertIn("5", text)


class TestBonePose(unittest.TestCase):
    """BonePose behavior."""

    def test_defaults(self):
        pose = BonePose()

        self.assertEqual(pose.bone_index, 0)
        self.assertEqual(pose.bone_name, "")
        self.assertEqual(pose.position, [0.0, 0.0, 0.0])
        self.assertEqual(pose.quaternion, [0.0, 0.0, 0.0, 1.0])

    def test_repr_includes_name_and_index(self):
        pose = BonePose()
        pose.bone_index = 3
        pose.bone_name = "center"
        pose.position = [1.0, 2.0, 3.0]

        text = repr(pose)

        self.assertIn("center", text)
        self.assertIn("3", text)

    def test_to_vpd_format(self):
        pose = BonePose()
        pose.bone_index = 5
        pose.bone_name = "左腕"
        pose.position = [1.0, 2.0, 3.0]
        pose.quaternion = [0.0, 0.707107, 0.0, 0.707107]

        text = pose.to_vpd_format()

        self.assertIn("Bone5{左腕", text)
        self.assertIn("1.000000,2.000000,3.000000;", text)
        self.assertIn("0.000000,0.707107,0.000000,0.707107;", text)
        self.assertTrue(text.endswith("}\n"))

    def test_str_matches_format(self):
        pose = BonePose()
        pose.bone_index = 1
        pose.bone_name = "head"
        pose.position = [0.0, 10.0, 0.0]
        pose.quaternion = [0.1, 0.2, 0.3, 0.9]

        text = str(pose)

        self.assertIn("Bone1{head", text)
        self.assertIn("0.100000", text)

    def test_vpd_format_multiple_bones(self):
        poses = []
        for index, name in enumerate(["center", "upper_body", "head"]):
            pose = BonePose()
            pose.bone_index = index
            pose.bone_name = name
            pose.position = [float(index), 0.0, 0.0]
            poses.append(pose)

        text = "".join(pose.to_vpd_format() for pose in poses)

        self.assertIn("Bone0{center", text)
        self.assertIn("Bone1{upper_body", text)
        self.assertIn("Bone2{head", text)


class TestVpdData(unittest.TestCase):
    """VpdData parser and writer behavior."""

    def setUp(self):
        self.vpd_data = VpdData()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init(self):
        self.assertIsInstance(self.vpd_data.header, VpdHeader)
        self.assertEqual(len(self.vpd_data.bone_poses), 0)

    def test_parse_simple_vpd(self):
        vpd_content = """Vocaloid Pose Data file

test.osm;
2;

Bone0{センター
  1.000000,2.000000,3.000000;
  0.000000,0.000000,0.000000,1.000000;
}

Bone1{上半身
  0.000000,0.000000,0.000000;
  0.707107,0.000000,0.000000,0.707107;
}
"""
        test_file = self.test_dir / "test.vpd"
        test_file.write_text(vpd_content, encoding="shift-jis")

        self.vpd_data.parse_file(str(test_file))

        self.assertEqual(self.vpd_data.header.signature, "Vocaloid Pose Data file")
        self.assertEqual(self.vpd_data.header.parent_file, "test.osm")
        self.assertEqual(self.vpd_data.header.bone_count, 2)
        self.assertEqual(len(self.vpd_data.bone_poses), 2)

        bone0 = self.vpd_data.bone_poses[0]
        self.assertEqual(bone0.bone_index, 0)
        self.assertEqual(bone0.bone_name, "センター")
        self.assertEqual(bone0.position, [1.0, 2.0, 3.0])
        self.assertEqual(bone0.quaternion, [0.0, 0.0, 0.0, 1.0])

        bone1 = self.vpd_data.bone_poses[1]
        self.assertEqual(bone1.bone_index, 1)
        self.assertEqual(bone1.bone_name, "上半身")
        self.assertEqual(bone1.position, [0.0, 0.0, 0.0])
        self.assertAlmostEqual(bone1.quaternion[0], 0.707107, places=5)
        self.assertAlmostEqual(bone1.quaternion[3], 0.707107, places=5)

    def test_parse_without_header_counts_bones(self):
        vpd_content = """Vocaloid Pose Data file

Bone0{センター
  0.000000,0.000000,0.000000;
  0.000000,0.000000,0.000000,1.000000;
}
"""
        test_file = self.test_dir / "test_no_header.vpd"
        test_file.write_text(vpd_content, encoding="shift-jis")

        self.vpd_data.parse_file(str(test_file))

        self.assertEqual(self.vpd_data.header.bone_count, 1)
        self.assertEqual(len(self.vpd_data.bone_poses), 1)

    def test_write_file_roundtrip(self):
        self.vpd_data.header.parent_file = "output.osm"

        pose = BonePose()
        pose.bone_index = 0
        pose.bone_name = "テストボーン"
        pose.position = [1.5, 2.5, 3.5]
        pose.quaternion = [0.0, 0.707107, 0.0, 0.707107]
        self.vpd_data.bone_poses.append(pose)

        output_file = self.test_dir / "output.vpd"
        self.vpd_data.write_file(str(output_file))

        parsed = VpdData()
        parsed.parse_file(str(output_file))

        self.assertEqual(parsed.header.parent_file, "output.osm")
        self.assertEqual(len(parsed.bone_poses), 1)
        self.assertEqual(parsed.bone_poses[0].bone_name, "テストボーン")
        self.assertAlmostEqual(parsed.bone_poses[0].position[0], 1.5, places=5)

    def test_invalid_file(self):
        with self.assertRaises(FileNotFoundError):
            self.vpd_data.parse_file("nonexistent.vpd")

        test_file = self.test_dir / "invalid.vpd"
        test_file.write_text("This is not a VPD file", encoding="utf-8")

        with self.assertRaises(MMDParseException):
            self.vpd_data.parse_file(str(test_file))


if __name__ == "__main__":
    unittest.main()
