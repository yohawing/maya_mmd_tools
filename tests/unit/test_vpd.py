"""Unit tests for VPD data types and parser roundtrips."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mmd_tools.core.exceptions import MMDParseException
from mmd_tools.core.vpd_data import VpdData
from mmd_tools.core.vpd_data.bone_pose import BonePose
from mmd_tools.core.vpd_data.header import VpdHeader
from mmd_tools.converters.vpd_converter import VpdConverter
from mmd_tools.io import vpd_importer


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


class TestVpdConverter(unittest.TestCase):
    """VPD pose converter behavior that does not require a live Maya scene."""

    def setUp(self):
        self.converter = VpdConverter()

    def test_get_target_joints_uses_namespace_pattern(self):
        with patch("mmd_tools.converters.vpd_converter.cmds.ls", return_value=["model:センター"]) as ls:
            joints = self.converter._get_target_joints("model")

        self.assertEqual(joints, ["model:センター"])
        ls.assert_called_once_with("model:*", type="joint")

    def test_find_maya_joint_uses_mmd_bone_attribute_mapping_first(self):
        self.converter.bone_name_mapping = {"センター": "model:center_joint"}

        joint = self.converter._find_maya_joint("センター", ["model:センター", "model:center_joint"])

        self.assertEqual(joint, "model:center_joint")

    def test_find_maya_joint_falls_back_to_namespaced_joint_basename(self):
        joint = self.converter._find_maya_joint("上半身", ["model:センター", "model:上半身"])

        self.assertEqual(joint, "model:上半身")

    def test_find_maya_joint_returns_none_when_unmatched(self):
        joint = self.converter._find_maya_joint("左腕", ["model:センター"])

        self.assertIsNone(joint)

    def test_convert_position_flips_z_axis(self):
        self.assertEqual(self.converter._convert_position_mmd_to_maya([1.0, 2.0, -3.0]), [1.0, 2.0, 3.0])

    def test_convert_rotation_flips_z_axis(self):
        self.assertEqual(self.converter._convert_rotation_mmd_to_maya([10.0, 20.0, -30.0]), [10.0, 20.0, 30.0])

    def test_is_movable_bone_accepts_center_and_master_names(self):
        for bone_name in ["センター", "center", "Center", "全ての親", "master", "Master"]:
            with self.subTest(bone_name=bone_name):
                self.assertTrue(self.converter._is_movable_bone(bone_name))

    def test_convert_returns_false_without_target_joints(self):
        vpd_data = SimpleNamespace(bone_poses=[])

        with patch.object(self.converter, "_build_name_mappings") as build_mappings:
            with patch.object(self.converter, "_get_target_joints", return_value=[]):
                result = self.converter.convert(vpd_data, "model")

        self.assertFalse(result)
        build_mappings.assert_called_once_with("model")


class TestVpdImporter(unittest.TestCase):
    """VPD importer selection and namespace dispatch behavior."""

    def test_is_movable_joint_matches_base_name_keywords(self):
        self.assertTrue(vpd_importer._is_movable_joint("model:Center"))
        self.assertTrue(vpd_importer._is_movable_joint("root_joint"))
        self.assertFalse(vpd_importer._is_movable_joint("model:左腕"))

    def test_import_uses_target_model_namespace(self):
        parser = SimpleNamespace(bone_poses=[])

        with patch("mmd_tools.io.vpd_importer.NamespaceUtils.get_namespace_from_node", return_value="model"):
            with patch("mmd_tools.io.vpd_importer.cmds.currentTime", return_value=12):
                with patch("mmd_tools.io.vpd_importer._create_keyframes_for_namespace") as create_keyframes:
                    with patch("mmd_tools.io.vpd_importer.cmds.inViewMessage") as in_view_message:
                        with patch("mmd_tools.io.vpd_importer.VpdConverter") as converter_class:
                            converter = converter_class.return_value
                            converter.convert.return_value = True

                            result = vpd_importer.import_vpd_file(
                                parser,
                                "pose.vpd",
                                {"target_model": "model:root", "create_keyframe": True},
                            )

        self.assertTrue(result)
        converter.convert.assert_called_once_with(parser, "model", {"target_model": "model:root", "create_keyframe": True})
        create_keyframes.assert_called_once_with("model", 12)
        in_view_message.assert_called_once()

    def test_import_warns_when_no_target_selection(self):
        parser = SimpleNamespace(bone_poses=[])

        with patch("mmd_tools.io.vpd_importer.cmds.ls", return_value=[]):
            with patch("mmd_tools.io.vpd_importer.cmds.warning") as warning:
                result = vpd_importer.import_vpd_file(parser, "pose.vpd", {})

        self.assertFalse(result)
        warning.assert_called_once_with("Please select target model joints to apply the pose.")

    def test_import_apply_to_all_tries_namespaces_and_skips_root_namespace(self):
        parser = SimpleNamespace(bone_poses=[])
        options = {"apply_to_all": True, "create_keyframe": False}

        with patch("mmd_tools.io.vpd_importer.NamespaceUtils.list_model_namespaces", return_value=["model_a", "model_b"]):
            with patch("mmd_tools.io.vpd_importer.cmds.currentTime", return_value=1):
                with patch("mmd_tools.io.vpd_importer.cmds.inViewMessage"):
                    with patch("mmd_tools.io.vpd_importer.VpdConverter") as converter_class:
                        converter = converter_class.return_value
                        converter.convert.side_effect = [False, True]

                        result = vpd_importer.import_vpd_file(parser, "pose.vpd", options)

        self.assertTrue(result)
        self.assertEqual(converter.convert.call_args_list[0].args, (parser, "model_a", options))
        self.assertEqual(converter.convert.call_args_list[1].args, (parser, "model_b", options))


if __name__ == "__main__":
    unittest.main()
