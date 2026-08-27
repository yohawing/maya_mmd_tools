"""Unit tests for VPD data types and parser roundtrips."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mmd_tools.core.exceptions import MMDImportException, MMDParseException
from mmd_tools.core.vpd_data import VpdData
from mmd_tools.core.vpd_data.bone_pose import BonePose
from mmd_tools.core.vpd_data.header import VpdHeader
from mmd_tools.converters.vpd_converter import VpdConverter
from mmd_tools.io import vpd_importer

_IMP = "mmd_tools.io.vpd_importer"
_CVT = "mmd_tools.converters.vpd_converter"


def _msgs(mock_log):
    # call[0] is args tuple (Py3.7-safe; _Call.args is 3.8+).
    return [c[0][0] for c in mock_log.call_args_list if c[0]]


def _assert_level(testcase, mock_logger, messages, on_debug):
    """Assert each message appears only on DEBUG (on_debug=True) or only on INFO."""
    debug_msgs, info_msgs = _msgs(mock_logger.debug), _msgs(mock_logger.info)
    primary, other = (debug_msgs, info_msgs) if on_debug else (info_msgs, debug_msgs)
    for msg in messages:
        testcase.assertIn(msg, primary)
        testcase.assertNotIn(msg, other)


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

    def test_str_matches_format(self):
        pose = BonePose()
        pose.bone_index = 1
        pose.bone_name = "head"
        pose.position = [0.0, 10.0, 0.0]
        pose.quaternion = [0.1, 0.2, 0.3, 0.9]

        text = str(pose)

        self.assertIn("Bone1{head", text)
        self.assertIn("0.100000", text)

class TestVpdData(unittest.TestCase):
    """VpdData parser behavior."""

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

    def test_get_target_joints_scopes_to_target_model_descendants(self):
        with patch("mmd_tools.converters.vpd_converter.cmds.objExists", return_value=True):
            with patch("mmd_tools.converters.vpd_converter.cmds.nodeType", return_value="transform"):
                with patch(
                    "mmd_tools.converters.vpd_converter.cmds.listRelatives",
                    return_value=["|root|センター"],
                ) as list_relatives:
                    joints = self.converter._get_target_joints(None, "root")

        self.assertEqual(joints, ["|root|センター"])
        list_relatives.assert_called_once_with("root", allDescendents=True, type="joint", fullPath=True)

    def test_find_maya_joint_uses_mmd_bone_attribute_mapping_first(self):
        self.converter.bone_name_mapping = {"センター": "model:center_joint"}

        joint = self.converter._find_maya_joint("センター", ["model:センター", "model:center_joint"])

        self.assertEqual(joint, "model:center_joint")

    def test_find_maya_joint_falls_back_to_namespaced_joint_basename(self):
        joint = self.converter._find_maya_joint("上半身", ["model:センター", "model:上半身"])

        self.assertEqual(joint, "model:上半身")

    def test_find_maya_joint_falls_back_to_dag_leaf_basename(self):
        joint = self.converter._find_maya_joint("上半身", ["|root|センター", "|root|上半身"])

        self.assertEqual(joint, "|root|上半身")

    def test_find_maya_joint_returns_none_when_unmatched(self):
        joint = self.converter._find_maya_joint("左腕", ["model:センター"])

        self.assertIsNone(joint)

    def test_convert_position_flips_z_axis(self):
        self.assertEqual(self.converter._convert_position_mmd_to_maya([1.0, 2.0, -3.0]), [1.0, 2.0, 3.0])

    def test_convert_returns_false_without_target_joints(self):
        vpd_data = SimpleNamespace(bone_poses=[])

        with patch.object(self.converter, "_build_name_mappings") as build_mappings:
            with patch.object(self.converter, "_get_target_joints", return_value=[]):
                result = self.converter.convert(vpd_data, "model")

        self.assertFalse(result)
        build_mappings.assert_called_once_with("model", None)

    def test_build_name_mappings_summary_is_debug_not_info(self):
        """Mapping summary stays DEBUG; mapping side effect is unchanged."""
        log = MagicMock()
        with ExitStack() as s:
            s.enter_context(patch(_CVT + ".logger", log))
            s.enter_context(patch.object(self.converter, "_get_target_joints", return_value=["model:center"]))
            s.enter_context(patch(_CVT + ".cmds.attributeQuery", return_value=True))
            s.enter_context(patch(_CVT + ".cmds.getAttr", return_value="センター"))
            self.converter._build_name_mappings("model")
        self.assertEqual(self.converter.bone_name_mapping, {"センター": "model:center"})
        _assert_level(self, log, ["Built 1 bone mappings", "Building bone name mapping"], on_debug=True)

    def test_setup_animation_layer_details_are_debug_not_info(self):
        """Existing reuse and new layer creation stay DEBUG; behavior unchanged."""
        cases = (
            (["VPD_Pose", "BaseAnimation"], None, "Using existing animation layer: VPD_Pose", False),
            ([], "VPD_Pose", "Created new animation layer: VPD_Pose", True),
        )
        for existing, create_ret, msg, creates in cases:
            with self.subTest(msg=msg):
                conv, log = VpdConverter(), MagicMock()
                with ExitStack() as s:
                    s.enter_context(patch(_CVT + ".logger", log))
                    s.enter_context(patch(_CVT + ".cmds.ls", return_value=existing))
                    anim = s.enter_context(patch(_CVT + ".cmds.animLayer", return_value=create_ret))
                    conv._setup_animation_layer("VPD_Pose")
                self.assertEqual(conv.anim_layer, "VPD_Pose")
                if creates:
                    anim.assert_called_once_with("VPD_Pose", override=False, weight=1.0)
                else:
                    anim.assert_not_called()
                _assert_level(self, log, [msg], on_debug=True)

    def test_convert_start_and_completion_remain_info(self):
        """Outer conversion start/completion stay INFO."""
        bone = BonePose()
        bone.bone_name = "上半身"
        log = MagicMock()
        with ExitStack() as s:
            s.enter_context(patch(_CVT + ".logger", log))
            s.enter_context(patch.object(self.converter, "_build_name_mappings"))
            s.enter_context(patch.object(self.converter, "_get_target_joints", return_value=["model:上半身"]))
            s.enter_context(patch(_CVT + ".cmds.currentTime", return_value=1.0))
            s.enter_context(patch.object(self.converter, "_validate_pose_conversions"))
            s.enter_context(patch.object(self.converter, "_setup_animation_layer"))
            s.enter_context(patch.object(self.converter, "_apply_bone_pose", return_value="model:上半身"))
            s.enter_context(patch.object(self.converter, "_add_objects_to_layer"))
            result = self.converter.convert(SimpleNamespace(bone_poses=[bone]), "model", {"create_keyframe": True})
        self.assertTrue(result)
        _assert_level(
            self,
            log,
            ["Starting VPD pose conversion", "VPD pose conversion completed: applied 1/1 bones"],
            on_debug=False,
        )

    def test_convert_validates_all_pose_conversions_before_scene_writes(self):
        bone = BonePose()
        bone.bone_name = "上半身"
        apply_pose = MagicMock()
        with ExitStack() as s:
            s.enter_context(patch.object(self.converter, "_build_name_mappings"))
            s.enter_context(
                patch.object(self.converter, "_get_target_joints", return_value=["model:上半身"])
            )
            s.enter_context(patch(_CVT + "._build_rotation_export_context", return_value={}))
            s.enter_context(
                patch.object(
                    self.converter,
                    "_validate_pose_conversions",
                    side_effect=ValueError("invalid pose conversion"),
                )
            )
            s.enter_context(patch.object(self.converter, "_apply_bone_pose", apply_pose))
            with self.assertRaisesRegex(ValueError, "invalid pose conversion"):
                self.converter.convert(SimpleNamespace(bone_poses=[bone]), "model")

        apply_pose.assert_not_called()

    def test_joint_rotate_conversion_rejects_invalid_rotate_order(self):
        self.converter._rotation_export_context = {
            "model:上半身": {"rotateOrder": 6}
        }

        with self.assertRaisesRegex(ValueError, "rotateOrder is invalid"):
            self.converter._convert_quaternion_to_joint_rotate(
                "model:上半身", [0.0, 0.0, 0.0, 1.0]
            )


class TestVpdImporter(unittest.TestCase):
    """VPD importer selection and namespace dispatch behavior."""

    def _import_logged(self, options, *extra, convert_return=True, convert_side_effect=None):
        """import_vpd_file under logger + common stubs. Returns (ok, log, converter)."""
        log, parser = MagicMock(), SimpleNamespace(bone_poses=[])
        with ExitStack() as s:
            s.enter_context(patch(_IMP + ".logger", log))
            for target, kwargs in extra:
                s.enter_context(patch(target, **kwargs))
            s.enter_context(patch(_IMP + ".cmds.currentTime", return_value=1))
            s.enter_context(patch(_IMP + "._create_keyframes_for_target"))
            s.enter_context(patch(_IMP + ".cmds.inViewMessage"))
            conv_cls = s.enter_context(patch(_IMP + ".VpdConverter"))
            conv = conv_cls.return_value
            if convert_side_effect is not None:
                conv.convert.side_effect = convert_side_effect
            else:
                conv.convert.return_value = convert_return
            ok = vpd_importer.import_vpd_file(parser, "pose.vpd", options)
        return ok, log, conv, parser

    def _assert_import_boundaries(self, log, debug_msgs=(), info_msgs=(), info_prefixes=()):
        if debug_msgs:
            _assert_level(self, log, list(debug_msgs), on_debug=True)
        if info_msgs:
            _assert_level(self, log, list(info_msgs), on_debug=False)
        info = _msgs(log.info)
        debug = _msgs(log.debug)
        for prefix in info_prefixes:
            self.assertTrue(
                any(isinstance(m, str) and m.startswith(prefix) for m in info),
                "expected INFO starting with %r, got %r" % (prefix, info),
            )
            self.assertFalse(
                any(isinstance(m, str) and m.startswith(prefix) for m in debug),
                "prefix %r must remain INFO, not DEBUG" % (prefix,),
            )

    def test_is_movable_joint_matches_base_name_keywords(self):
        self.assertTrue(vpd_importer._is_movable_joint("model:Center"))
        self.assertTrue(vpd_importer._is_movable_joint("root_joint"))
        self.assertFalse(vpd_importer._is_movable_joint("model:左腕"))

    def test_import_uses_target_model_namespace(self):
        parser = SimpleNamespace(bone_poses=[])

        with patch("mmd_tools.io.vpd_importer.NamespaceUtils.get_namespace_from_node", return_value="model"):
            with patch("mmd_tools.io.vpd_importer.cmds.currentTime", return_value=12):
                with patch("mmd_tools.io.vpd_importer._create_keyframes_for_target") as create_keyframes:
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
        create_keyframes.assert_called_once_with("model:root", "model", 12)
        in_view_message.assert_called_once()

    def test_import_uses_target_model_scope_without_namespace(self):
        parser = SimpleNamespace(bone_poses=[])
        options = {"target_model": "root", "create_keyframe": True}

        with patch("mmd_tools.io.vpd_importer.NamespaceUtils.get_namespace_from_node", return_value=None):
            with patch("mmd_tools.io.vpd_importer.cmds.currentTime", return_value=12):
                with patch("mmd_tools.io.vpd_importer._create_keyframes_for_target") as create_keyframes:
                    with patch("mmd_tools.io.vpd_importer.cmds.inViewMessage"):
                        with patch("mmd_tools.io.vpd_importer.VpdConverter") as converter_class:
                            converter = converter_class.return_value
                            converter.convert.return_value = True

                            result = vpd_importer.import_vpd_file(parser, "pose.vpd", options)

        self.assertTrue(result)
        converter.convert.assert_called_once_with(parser, None, options)
        create_keyframes.assert_called_once_with("root", None, 12)

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
        # Python 3.7 互換: call[0] で位置引数タプルを取る（_Call.args は使わない）
        self.assertEqual(converter.convert.call_args_list[0][0], (parser, "model_a", options))
        self.assertEqual(converter.convert.call_args_list[1][0], (parser, "model_b", options))

    def test_import_raises_import_exception_on_unexpected_error(self):
        parser = SimpleNamespace(bone_poses=[])

        with patch("mmd_tools.io.vpd_importer.NamespaceUtils.get_namespace_from_node", side_effect=RuntimeError("boom")):
            with self.assertRaises(MMDImportException):
                vpd_importer.import_vpd_file(parser, "pose.vpd", {"target_model": "model:root"})

    def test_target_namespace_route_detail_is_debug_not_info(self):
        """Explicit target_model namespace is DEBUG; import start/completion stay INFO."""
        ok, log, _conv, _p = self._import_logged(
            {"target_model": "model:root", "create_keyframe": True},
            (_IMP + ".NamespaceUtils.get_namespace_from_node", {"return_value": "model"}),
        )
        self.assertTrue(ok)
        self._assert_import_boundaries(
            log,
            debug_msgs=["Target namespace: model"],
            info_msgs=["VPD file import completed"],
            info_prefixes=["Starting VPD file import:"],
        )

    def test_selected_object_namespace_route_detail_is_debug_not_info(self):
        """Selected-object namespace is DEBUG; convert dispatch unchanged."""
        options = {"create_keyframe": True}
        ok, log, conv, parser = self._import_logged(
            options,
            (_IMP + ".cmds.ls", {"return_value": ["model:center"]}),
            (_IMP + ".cmds.nodeType", {"side_effect": lambda n: "joint" if n == "model:center" else "transform"}),
            (_IMP + ".NamespaceUtils.get_namespace_from_node", {"return_value": "model"}),
        )
        self.assertTrue(ok)
        conv.convert.assert_called_once_with(parser, "model", options)
        self._assert_import_boundaries(
            log,
            debug_msgs=["Target namespace from selected object: model"],
            info_msgs=["VPD file import completed"],
            info_prefixes=["Starting VPD file import:"],
        )

    def test_import_apply_to_all_start_and_aggregate_remain_info(self):
        """apply_to_all start and aggregate success stay INFO."""
        ok, log, _conv, _p = self._import_logged(
            {"apply_to_all": True, "create_keyframe": False},
            (_IMP + ".NamespaceUtils.list_model_namespaces", {"return_value": ["model_a", "model_b"]}),
            convert_side_effect=[True, True],
        )
        self.assertTrue(ok)
        self._assert_import_boundaries(
            log,
            info_msgs=["Applying pose to all models", "Applied pose to 2 model(s)", "VPD file import completed"],
        )


if __name__ == "__main__":
    unittest.main()
