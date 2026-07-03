"""Tests for the C++ fast import integration in mmd_importer.

Verifies that ``import_mmd_file`` correctly routes through / around the
``fast_import`` path depending on the ``use_cpp_fast_load`` and
``cpp_fast_load_mesh_only`` options.  Also tests the skeleton/skin creation
path inside ``_apply_fast_skeleton_skin``.

NOTE: Maya/PyMel is unavailable in CI, so the shared Maya stub is installed
before importing the modules under test.
"""

from __future__ import annotations

import json
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_maya_stub


install_maya_stub()

# Now safe to import the module under test
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.core.settings import settings
from mmd_tools.io.cpp_fast_importer import (
    _apply_fast_skeleton_skin,
    _sanitize_node_name,
)


class TestCppFastImportRouting(unittest.TestCase):
    """Routing scenarios for .pmx files with the C++ fast-import option."""

    def setUp(self):
        self._old_cpp = settings.get("import.native.use_cpp_fast_load", False)
        self._old_mesh_only = settings.get("import.native.cpp_fast_load_mesh_only", True)
        self._old_scale = settings.get("import.general.scale_factor", 1.0)
        settings.set("import.general.scale_factor", 1.0)

    def tearDown(self):
        settings.set("import.native.use_cpp_fast_load", self._old_cpp)
        settings.set("import.native.cpp_fast_load_mesh_only", self._old_mesh_only)
        settings.set("import.general.scale_factor", self._old_scale)

    # ------------------------------------------------------------------
    # Scenario 1: option disabled → uses parse_mmd_file
    # ------------------------------------------------------------------

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_option_disabled_uses_python_parser(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """When use_cpp_fast_load is False (default), the Python parser is used."""
        mock_parse.return_value = object()
        mock_import_pmx.return_value = "python_root"

        result = import_mmd_file(
            "model.pmx",
            options={"scale": 1.0, "use_cpp_fast_load": False},
        )

        mock_fast.assert_not_called()
        mock_parse.assert_called_once()
        mock_import_pmx.assert_called_once()
        self.assertEqual(result, "python_root")

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_python_pmx_path_honors_explicit_scale_argument(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """Python PMX path uses explicit scale before options/settings."""
        settings.set("import.general.scale_factor", 9.0)
        parsed = object()
        mock_parse.return_value = parsed
        mock_import_pmx.return_value = "python_root"

        result = import_mmd_file(
            "model.pmx",
            scale=3.0,
            options={"scale": 2.0, "use_cpp_fast_load": False},
        )

        mock_fast.assert_not_called()
        mock_import_pmx.assert_called_once_with(
            parsed,
            "model.pmx",
            3.0,
            {"scale": 2.0, "use_cpp_fast_load": False},
            progress_callback=None,
        )
        self.assertEqual(result, "python_root")

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_python_pmd_path_honors_options_scale_before_settings(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """Python PMD path uses PMX importer with options scale after PMD-to-PMX parse."""
        settings.set("import.general.scale_factor", 9.0)
        parsed = object()
        mock_parse.return_value = parsed
        mock_import_pmx.return_value = "pmd_root"

        result = import_mmd_file(
            "model.pmd",
            options={"scale": 2.5, "use_cpp_fast_load": True},
        )

        mock_fast.assert_not_called()
        mock_import_pmx.assert_called_once_with(
            parsed,
            "model.pmd",
            2.5,
            {"scale": 2.5, "use_cpp_fast_load": True},
            progress_callback=None,
        )
        self.assertEqual(result, "pmd_root")

    # ------------------------------------------------------------------
    # Scenario 2: option enabled + fast import succeeds → bypass Python
    # ------------------------------------------------------------------

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_fast_import_success_bypasses_python(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """When use_cpp_fast_load is True and fast_import succeeds,
        parse_mmd_file / import_pmx_file must NOT be called."""
        mock_fast.return_value = "cpp_root"
        progress = []

        result = import_mmd_file(
            "model.pmx",
            options={"scale": 1.0, "use_cpp_fast_load": True},
            progress_callback=progress.append,
        )

        mock_fast.assert_called_once_with(
            "model.pmx",
            base_name="model",
            scale=1.0,
            mesh_only=True,
            include_morphs=True,
        )
        mock_parse.assert_not_called()
        mock_import_pmx.assert_not_called()
        self.assertEqual(result, "cpp_root")
        self.assertEqual(progress, [5, 10, 90])

    # ------------------------------------------------------------------
    # Scenario 3: option enabled + fast import fails → fallback
    # ------------------------------------------------------------------

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_fast_import_failure_falls_back(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """When use_cpp_fast_load is True but fast_import returns None,
        the Python parser should be used as a fallback."""
        mock_fast.return_value = None
        mock_parse.return_value = object()
        mock_import_pmx.return_value = "fallback_root"
        progress = []

        result = import_mmd_file(
            "model.pmx",
            options={"scale": 1.0, "use_cpp_fast_load": True},
            progress_callback=progress.append,
        )

        mock_fast.assert_called_once()
        mock_parse.assert_called_once()
        mock_import_pmx.assert_called_once()
        self.assertEqual(result, "fallback_root")
        self.assertEqual(progress, [5, 10, 12])

    # ------------------------------------------------------------------
    # Scenario 4: mesh_only=False → fast import receives mesh_only=False
    # ------------------------------------------------------------------

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_fast_import_mesh_only_false_calls_with_param(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """When cpp_fast_load_mesh_only is False, fast_import is called
        with mesh_only=False to request skeleton+skin."""
        mock_fast.return_value = "cpp_root"

        result = import_mmd_file(
            "model.pmx",
            options={
                "scale": 1.0,
                "use_cpp_fast_load": True,
                "cpp_fast_load_mesh_only": False,
            },
        )

        mock_fast.assert_called_once_with(
            "model.pmx",
            base_name="model",
            scale=1.0,
            mesh_only=False,
            include_morphs=True,
        )
        mock_parse.assert_not_called()
        mock_import_pmx.assert_not_called()
        self.assertEqual(result, "cpp_root")

    # ------------------------------------------------------------------
    # Scenario 5: mesh_only=True (default) → fast import receives mesh_only=True
    # ------------------------------------------------------------------

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_fast_import_mesh_only_default(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """When cpp_fast_load_mesh_only is not specified, fast_import is
        called with mesh_only=True (default)."""
        mock_fast.return_value = "cpp_root"

        result = import_mmd_file(
            "model.pmx",
            options={
                "scale": 1.0,
                "use_cpp_fast_load": True,
            },
        )

        mock_fast.assert_called_once_with(
            "model.pmx",
            base_name="model",
            scale=1.0,
            mesh_only=True,
            include_morphs=True,
        )
        self.assertEqual(result, "cpp_root")

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_fast_import_honors_import_morphs_option(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """import_morphs=False disables C++ vertex morph creation."""
        mock_fast.return_value = "cpp_root"

        result = import_mmd_file(
            "model.pmx",
            options={
                "scale": 1.0,
                "use_cpp_fast_load": True,
                "import_morphs": False,
            },
        )

        mock_fast.assert_called_once_with(
            "model.pmx",
            base_name="model",
            scale=1.0,
            mesh_only=True,
            include_morphs=False,
        )
        mock_parse.assert_not_called()
        mock_import_pmx.assert_not_called()
        self.assertEqual(result, "cpp_root")

    # ------------------------------------------------------------------
    # Scenario 6: .pmd files: fast import is never attempted
    # ------------------------------------------------------------------

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_pmd_never_uses_fast_import(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """.pmd files must never attempt the C++ fast import path."""
        mock_parse.return_value = object()
        mock_import_pmx.return_value = "pmd_root"

        result = import_mmd_file(
            "model.pmd",
            options={"scale": 1.0, "use_cpp_fast_load": True},
        )

        mock_fast.assert_not_called()
        mock_parse.assert_called_once()
        mock_import_pmx.assert_called_once()
        self.assertEqual(result, "pmd_root")


class TestFastSkeletonSkin(unittest.TestCase):
    """Unit tests for _apply_fast_skeleton_skin with mocked Maya modules."""

    def setUp(self):
        # MmdParsedModel will be fully mocked for each test
        self.mock_parsed_patcher = patch(
            "mmd_tools.io.cpp_fast_importer.MmdParsedModel"
        )
        self.mock_parsed_cls = self.mock_parsed_patcher.start()

        # The function calls Path(filepath).read_bytes() — mock that too
        self.mock_read_bytes_patcher = patch.object(Path, "read_bytes")
        self.mock_read_bytes = self.mock_read_bytes_patcher.start()
        self.mock_read_bytes.return_value = b"fake pmx bytes"

        self.mock_apply_weights_patcher = patch(
            "mmd_tools.io.cpp_fast_importer.maya_utils.apply_vertex_weights"
        )
        self.mock_apply_weights = self.mock_apply_weights_patcher.start()

    def tearDown(self):
        self.mock_parsed_patcher.stop()
        self.mock_read_bytes_patcher.stop()
        self.mock_apply_weights_patcher.stop()

    def _make_cmds_mock(self):
        """Build a MagicMock that behaves like a Maya cmds module."""
        cmds = MagicMock()

        def _joint_side_effect(name=None, position=None, **kwargs):
            return name or "joint1"

        cmds.joint.side_effect = _joint_side_effect
        cmds.group.return_value = "skeleton_group1"
        cmds.skinCluster.return_value = ["skinCluster1"]
        cmds.objExists.return_value = True
        return cmds

    def test_skeleton_skin_happy_path(self):
        """Verify joints, skeleton group, and skinCluster are created."""
        metadata_json = json.dumps({
            "bones": [
                {
                    "name": "\u30bb\u30f3\u30bf\u30fc",
                    "englishName": "center",
                    "parentIndex": -1,
                    "position": [0.0, 10.0, 0.0],
                },
                {
                    "name": "\u5de6\u8155",
                    "englishName": "arm_L",
                    "parentIndex": 0,
                    "position": [2.0, 10.0, 0.0],
                },
            ]
        })

        mock_parsed = MagicMock()
        mock_parsed.metadata_json = metadata_json
        # Two vertices, each with 4 bone influences
        mock_parsed.skin_indices = [
            (0, 1, 0, 0),
            (0, 0, 0, 0),
        ]
        mock_parsed.skin_weights = [
            (0.8, 0.2, 0.0, 0.0),
            (1.0, 0.0, 0.0, 0.0),
        ]
        self.mock_parsed_cls.from_pmx_bytes.return_value = mock_parsed

        cmds = self._make_cmds_mock()

        _apply_fast_skeleton_skin(
            "model.pmx", "mesh1", "root1", "my_model", cmds
        )

        # ---- assertions ----
        # Skeleton group created
        cmds.group.assert_called_once_with(
            empty=True,
            name="my_model_skeleton_fast",
            parent="root1",
        )

        # Two joints created
        self.assertEqual(cmds.joint.call_count, 2)
        call_names = [c[1]["name"] for c in cmds.joint.call_args_list]
        self.assertIn("center", call_names)
        self.assertIn("arm_L", call_names)

        # Parent joint 1 under joint 0
        parent_args = [c.args for c in cmds.parent.call_args_list]
        self.assertTrue(
            any("arm_L" in str(a) for a in parent_args)
        )

        # skinCluster created with both joints
        skin_call = cmds.skinCluster.call_args
        self.assertIsNotNone(skin_call)
        joints_arg = skin_call[0][0]
        self.assertEqual(len(joints_arg), 2)

        # segmentScaleCompensate set to False on both
        ssc_calls = [c for c in cmds.setAttr.call_args_list
                     if "segmentScaleCompensate" in str(c)]
        self.assertEqual(len(ssc_calls), 2)
        self.mock_apply_weights.assert_called_once_with(
            "skinCluster1",
            "mesh1",
            [
                [0.8, 0.2],
                [1.0, 0.0],
            ],
        )

    @patch("mmd_tools.io.cpp_fast_importer.parse_pmx_native")
    def test_skeleton_skin_falls_back_to_native_pmx_parser(self, mock_parse_native: MagicMock):
        """ParsedModel ABI がない環境では native PMX parser から skeleton/skin を作る。"""
        self.mock_parsed_cls.from_pmx_bytes.return_value = None

        bone = types.SimpleNamespace(
            name="センター",
            name_english="center",
            parent_bone_index=-1,
            position=(0.0, 10.0, 0.0),
        )
        vertex = types.SimpleNamespace(
            weight_transform_type=0,
            bone_indices=[0],
            bone_weights=[],
        )
        mock_parse_native.return_value = types.SimpleNamespace(
            bones=[bone],
            vertices=[vertex],
        )

        cmds = self._make_cmds_mock()

        _apply_fast_skeleton_skin(
            "model.pmx", "mesh1", "root1", "my_model", cmds
        )

        cmds.group.assert_called_once()
        cmds.joint.assert_called_once()
        cmds.skinCluster.assert_called_once()
        self.mock_apply_weights.assert_called_once_with(
            "skinCluster1",
            "mesh1",
            [[1.0]],
        )

    def test_skeleton_skin_no_bones(self):
        """When bones list is empty, skip skeleton/skin creation."""
        mock_parsed = MagicMock()
        mock_parsed.metadata_json = json.dumps({"bones": []})
        mock_parsed.skin_indices = [(0, 0, 0, 0)]
        mock_parsed.skin_weights = [(1.0, 0.0, 0.0, 0.0)]
        self.mock_parsed_cls.from_pmx_bytes.return_value = mock_parsed

        cmds = MagicMock()

        _apply_fast_skeleton_skin(
            "model.pmx", "mesh1", "root1", "my_model", cmds
        )

        cmds.joint.assert_not_called()
        cmds.group.assert_not_called()
        cmds.skinCluster.assert_not_called()

    def test_skeleton_skin_no_skin_data(self):
        """When skin_indices is None, skip skeleton/skin creation."""
        mock_parsed = MagicMock()
        mock_parsed.metadata_json = json.dumps({
            "bones": [{
                "name": "center",
                "parentIndex": -1,
                "position": [0.0, 0.0, 0.0],
            }]
        })
        mock_parsed.skin_indices = None
        mock_parsed.skin_weights = None
        self.mock_parsed_cls.from_pmx_bytes.return_value = mock_parsed

        cmds = MagicMock()

        _apply_fast_skeleton_skin(
            "model.pmx", "mesh1", "root1", "my_model", cmds
        )

        cmds.joint.assert_not_called()
        cmds.group.assert_not_called()
        cmds.skinCluster.assert_not_called()

    def test_skeleton_skin_parsed_model_none(self):
        """When MmdParsedModel.from_pmx_bytes returns None, skip silently."""
        self.mock_parsed_cls.from_pmx_bytes.return_value = None

        cmds = MagicMock()

        _apply_fast_skeleton_skin(
            "model.pmx", "mesh1", "root1", "my_model", cmds
        )

        cmds.joint.assert_not_called()
        cmds.group.assert_not_called()
        cmds.skinCluster.assert_not_called()

    def test_skeleton_skin_no_metadata_json(self):
        """When metadata_json is None, skip skeleton/skin creation."""
        mock_parsed = MagicMock()
        mock_parsed.metadata_json = None
        mock_parsed.skin_indices = [(0, 0, 0, 0)]
        mock_parsed.skin_weights = [(1.0, 0.0, 0.0, 0.0)]
        self.mock_parsed_cls.from_pmx_bytes.return_value = mock_parsed

        cmds = MagicMock()

        _apply_fast_skeleton_skin(
            "model.pmx", "mesh1", "root1", "my_model", cmds
        )

        cmds.joint.assert_not_called()
        cmds.group.assert_not_called()
        cmds.skinCluster.assert_not_called()

    def test_skeleton_skin_mesh_not_found(self):
        """When mesh node doesn't exist, skip skinCluster."""
        metadata_json = json.dumps({
            "bones": [{
                "name": "center",
                "parentIndex": -1,
                "position": [0.0, 0.0, 0.0],
            }]
        })

        mock_parsed = MagicMock()
        mock_parsed.metadata_json = metadata_json
        mock_parsed.skin_indices = [(0, 0, 0, 0)]
        mock_parsed.skin_weights = [(1.0, 0.0, 0.0, 0.0)]
        self.mock_parsed_cls.from_pmx_bytes.return_value = mock_parsed

        cmds = MagicMock()
        cmds.objExists.return_value = False  # mesh doesn't exist

        _apply_fast_skeleton_skin(
            "model.pmx", "mesh1", "root1", "my_model", cmds
        )

        # Joints + group still created
        self.assertEqual(cmds.joint.call_count, 1)
        cmds.group.assert_called_once()
        # But skinCluster should NOT be called
        cmds.skinCluster.assert_not_called()


class TestSanitizeNodeName(unittest.TestCase):
    """Unit tests for the node-name sanitizer."""

    def test_ascii_alphanumeric(self):
        self.assertEqual(_sanitize_node_name("hello"), "hello")

    def test_leading_digit_prefixed(self):
        self.assertEqual(_sanitize_node_name("123bone"), "m_123bone")

    def test_unicode_replaced(self):
        # Full-width katakana and kanji become underscores
        result = _sanitize_node_name("\u30bb\u30f3\u30bf\u30fc")
        self.assertNotIn("\u30bb", result)
        self.assertTrue(all(c in "_" for c in result) or result == "")

    def test_mixed(self):
        result = _sanitize_node_name("center_\u30bb\u30f3\u30bf\u30fc")
        # The center part survives
        self.assertIn("center", result)

    def test_empty(self):
        self.assertEqual(_sanitize_node_name(""), "")


if __name__ == "__main__":
    unittest.main()
