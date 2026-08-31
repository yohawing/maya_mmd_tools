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
import re
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_maya_stub


install_maya_stub()

# Now safe to import the module under test
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.core.settings import settings
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.io import cpp_fast_importer
from mmd_tools.io.cpp_fast_importer import (
    _apply_basic_materials,
    _apply_fast_morph_metadata,
    _apply_fast_skeleton_skin,
    _apply_fast_root_metadata,
    _allocate_fast_material_name,
    _create_standard_material,
    _sanitize_node_name,
    fast_import,
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
            is_pmd=True,
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

    @patch("mmd_tools.io.mmd_importer.maya_viewport_utils.setup_mmd_native_color_management")
    @patch("mmd_tools.io.mmd_importer.fast_import")
    def test_native_vp2_import_sets_native_color_management(
        self,
        mock_fast: MagicMock,
        mock_setup_color_management: MagicMock,
    ):
        """The UI-owned native VP2 route selects its gamma-space output mode."""
        mock_fast.return_value = "cpp_root"

        result = import_mmd_file(
            "model.pmx",
            options={
                "scale": 1.0,
                "use_cpp_fast_load": True,
                "use_cpp_vp2_ownership": True,
            },
        )

        mock_fast.assert_called_once_with(
            "model.pmx",
            base_name="model",
            scale=1.0,
            mesh_only=True,
            include_morphs=True,
            vp2_ownership=True,
        )
        mock_setup_color_management.assert_called_once_with()
        self.assertEqual(result, "cpp_root")

    @patch("mmd_tools.io.mmd_importer.fast_import", return_value=None)
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_native_vp2_failure_blocks_python_mesh_fallback(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """An explicit VP2 request must not silently become an ordinary mesh."""
        options = {
            "scale": 1.0,
            "use_cpp_fast_load": True,
            "use_cpp_vp2_ownership": True,
        }

        with self.assertRaisesRegex(MMDImportException, "Python mesh fallback is blocked"):
            import_mmd_file("model.pmx", options=options)

        mock_fast.assert_called_once()
        mock_parse.assert_not_called()
        mock_import_pmx.assert_not_called()
        self.assertEqual(
            options["profile"]["native_import"],
            {
                "requested": True,
                "route": "cpp_fast_load_vp2",
                "status": "failed",
                "fallback": "blocked",
                "code": "NATIVE_VP2_OWNERSHIP_UNAVAILABLE",
                "reason": "fast importer returned no model root",
            },
        )

    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_native_vp2_request_with_fast_load_disabled_is_fail_closed(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
    ):
        """A lost Fast Load flag must not turn a VP2 request into Python import."""
        options = {
            "scale": 1.0,
            "use_cpp_fast_load": False,
            "use_cpp_vp2_ownership": True,
        }

        with self.assertRaisesRegex(MMDImportException, "C\\+\\+ Fast Load is disabled"):
            import_mmd_file("model.pmx", options=options)

        mock_parse.assert_not_called()
        mock_import_pmx.assert_not_called()
        self.assertEqual(options["profile"]["native_import"]["fallback"], "blocked")

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

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_fast_import_vp2_ownership_passes_render_override_flag(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """The UI RenderOverride opt-in is forwarded to the native importer."""
        mock_fast.return_value = "cpp_render_root"

        result = import_mmd_file(
            "model.pmx",
            options={
                "scale": 1.0,
                "use_cpp_fast_load": True,
                "use_cpp_vp2_ownership": True,
            },
        )

        mock_fast.assert_called_once_with(
            "model.pmx",
            base_name="model",
            scale=1.0,
            mesh_only=True,
            include_morphs=True,
            vp2_ownership=True,
        )
        mock_parse.assert_not_called()
        mock_import_pmx.assert_not_called()
        self.assertEqual(result, "cpp_render_root")

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
            "mmd_tools.io.cpp_fast_importer.maya_mesh_utils.apply_vertex_weights"
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
        # New joints do not have authored metadata before the importer writes it.
        # MagicMock's default return value is truthy, which would make the
        # immutable bind-translate helper incorrectly treat the attribute as
        # pre-existing and skip authoring it.  The skin policy attributes,
        # however, already exist on a Maya-created skinCluster.
        def _attribute_query(attribute, node=None, **_kwargs):
            return node == "skinCluster1" and attribute in {
                "deformUserNormals",
                "blockGPU",
            }

        cmds.attributeQuery.side_effect = _attribute_query
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
                {
                    "name": "IK target",
                    "englishName": "ik_target",
                    "parentIndex": 0,
                    "position": [0.0, 0.0, 0.0],
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

        with patch(
            "mmd_tools.io.cpp_fast_importer.maya_mesh_utils.has_materially_different_authored_normals",
            return_value=False,
        ) as mock_normal_difference:
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

        # Skeleton keeps all joints, including the zero-weight IK target.
        self.assertEqual(cmds.joint.call_count, 3)
        call_names = [c[1]["name"] for c in cmds.joint.call_args_list]
        self.assertIn("center", call_names)
        self.assertIn("arm_L", call_names)

        # Parent joint 1 under joint 0
        parent_args = [c.args for c in cmds.parent.call_args_list]
        self.assertTrue(
            any("arm_L" in str(a) for a in parent_args)
        )

        # skinCluster contains only the two positive-weight joints.
        skin_call = cmds.skinCluster.call_args
        self.assertIsNotNone(skin_call)
        joints_arg = skin_call[0][0]
        self.assertEqual(len(joints_arg), 2)

        mock_normal_difference.assert_called_once_with("mesh1")
        cmds.setAttr.assert_any_call("skinCluster1.deformUserNormals", True)
        self.assertFalse(
            any(
                call.args == ("skinCluster1.blockGPU", True)
                for call in cmds.setAttr.call_args_list
            )
        )

        # segmentScaleCompensate set to False on both
        ssc_calls = [c for c in cmds.setAttr.call_args_list
                     if "segmentScaleCompensate" in str(c)]
        self.assertEqual(len(ssc_calls), 3)
        bind_translate_calls = [
            c for c in cmds.setAttr.call_args_list
            if "mmd_vmd_bind_translate" in str(c)
        ]
        self.assertEqual(len(bind_translate_calls), 3)
        self.mock_apply_weights.assert_called_once_with(
            "skinCluster1",
            "mesh1",
            [
                [0.8, 0.2],
                [1.0, 0.0],
            ],
        )

    def test_fast_import_scale_is_applied_to_basic_skeleton(self):
        """Fast mesh and skeleton imports must share the requested scale."""
        metadata_json = json.dumps({
            "bones": [{
                "name": "center",
                "englishName": "center",
                "parentIndex": -1,
                "position": [2.0, 10.0, 3.0],
            }]
        })
        mock_parsed = MagicMock()
        mock_parsed.metadata_json = metadata_json
        mock_parsed.skin_indices = [(0, 0, 0, 0)]
        mock_parsed.skin_weights = [(1.0, 0.0, 0.0, 0.0)]
        self.mock_parsed_cls.from_pmx_bytes.return_value = mock_parsed

        cmds = self._make_cmds_mock()
        cmds.mmdFastLoad.return_value = ["root1", "mesh1"]
        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"

        maya_module = __import__("maya")
        with patch.object(maya_module, "cmds", cmds), patch.dict(
            "sys.modules", {"maya.cmds": cmds}
        ), patch.object(
            cpp_fast_importer, "_candidate_plugin_paths", return_value=[plugin_path]
        ), patch.object(Path, "exists", return_value=True), patch.object(
            cpp_fast_importer, "_setup_plugin_directory"
        ), patch.object(
            cpp_fast_importer, "_apply_basic_materials", return_value=None
        ), patch.object(cpp_fast_importer, "_apply_fast_root_metadata"), patch.object(
            cpp_fast_importer, "_apply_fast_morph_metadata"
        ), patch(
            "mmd_tools.core.model_registry.ensure_model_registry"
        ):
            result = fast_import(
                "model.pmx",
                base_name="my_model",
                scale=0.5,
                mesh_only=False,
            )

        self.assertEqual(result, "root1")
        # The C++ mesh command already receives the requested import scale.
        cmds.mmdFastLoad.assert_called_once_with(
            f="model.pmx",
            n="my_model",
            s=0.5,
            mo=True,
        )
        # The skeleton must occupy the same scaled space (and retain Maya's
        # handedness conversion on Z) as the mesh produced above.
        self.assertEqual(
            cmds.joint.call_args[1]["position"],
            (1.0, 5.0, -1.5),
        )

    def test_fast_import_keeps_root_identity_without_persisting_scale(self):
        """Fast import preserves PMX header metadata without root scale state."""
        raw_metadata = {
            "metadata": {
                "name": "Raw model",
                "englishName": "Raw Model EN",
                "comment": "Raw comment",
                "englishComment": "Raw comment EN",
            }
        }
        original_metadata = json.loads(json.dumps(raw_metadata))
        cmds = self._make_cmds_mock()
        cmds.mmdFastLoad.return_value = ["root1", "mesh1"]
        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"

        maya_module = __import__("maya")
        with patch.object(maya_module, "cmds", cmds), patch.dict(
            "sys.modules", {"maya.cmds": cmds}
        ), patch.object(
            cpp_fast_importer, "_candidate_plugin_paths", return_value=[plugin_path]
        ), patch.object(Path, "exists", return_value=True), patch.object(
            cpp_fast_importer, "_setup_plugin_directory"
        ), patch.object(
            cpp_fast_importer, "_apply_basic_materials", return_value=raw_metadata
        ), patch(
            "mmd_tools.core.model_registry.ensure_model_registry"
        ):
            result = fast_import(
                "model.pmx",
                base_name="my_model",
                scale=0.5,
                mesh_only=True,
                include_morphs=False,
            )

        self.assertEqual(result, "root1")
        cmds.mmdFastLoad.assert_called_once_with(
            f="model.pmx",
            n="my_model",
            s=0.5,
            mo=False,
        )
        # Import scale is applied to spatial values at their import boundaries,
        # not persisted as a root attribute.
        cmds.scale.assert_not_called()
        self.assertFalse(
            any(
                call.args
                and call.args[0] == "root1.mmd_import_scale"
                for call in cmds.setAttr.call_args_list
            )
        )
        self.assertFalse(
            any(
                call.args
                and isinstance(call.args[0], str)
                and call.args[0].startswith("root1.scale")
                for call in cmds.setAttr.call_args_list
            )
        )
        self.assertEqual(raw_metadata, original_metadata)
        raw_writes = {
            call.args[0]: call.args[1]
            for call in cmds.setAttr.call_args_list
            if call.args and call.args[0].startswith("root1.mmd_")
        }
        self.assertEqual(raw_writes["root1.mmd_model_name"], "Raw model")
        self.assertEqual(raw_writes["root1.mmd_model_name_en"], "Raw Model EN")
        self.assertEqual(raw_writes["root1.mmd_comment"], "Raw comment")
        self.assertEqual(raw_writes["root1.mmd_comment_en"], "Raw comment EN")

    def test_skeleton_skin_blocks_gpu_for_authored_normal_difference(self):
        """Only a materially different authored normal opts the deformer out of GPU."""
        metadata_json = json.dumps({
            "bones": [{
                "name": "center",
                "englishName": "center",
                "parentIndex": -1,
                "position": [0.0, 0.0, 0.0],
            }]
        })
        mock_parsed = MagicMock()
        mock_parsed.metadata_json = metadata_json
        mock_parsed.skin_indices = [(0, 0, 0, 0)]
        mock_parsed.skin_weights = [(1.0, 0.0, 0.0, 0.0)]
        self.mock_parsed_cls.from_pmx_bytes.return_value = mock_parsed

        cmds = self._make_cmds_mock()
        with patch(
            "mmd_tools.io.cpp_fast_importer.maya_mesh_utils.has_materially_different_authored_normals",
            return_value=True,
        ) as mock_normal_difference:
            _apply_fast_skeleton_skin(
                "model.pmx", "mesh1", "root1", "my_model", cmds
            )

        mock_normal_difference.assert_called_once_with("mesh1")
        cmds.setAttr.assert_any_call("skinCluster1.deformUserNormals", True)
        cmds.setAttr.assert_any_call("skinCluster1.blockGPU", True)

    def test_basic_materials_returns_header_metadata_from_single_parsed_model(self):
        """Root metadata reuses the parsed-model JSON instead of reparsing PMX."""
        mock_parsed = MagicMock()
        mock_parsed.metadata_json = json.dumps(
            {
                "metadata": {
                    "name": "モデルJP",
                    "englishName": "Model EN",
                    "comment": "コメントJP",
                    "englishComment": "Comment EN",
                },
                "materials": [],
            }
        )
        mock_parsed.material_groups = []
        self.mock_parsed_cls.from_pmx_bytes.return_value = mock_parsed

        metadata = _apply_basic_materials("model.pmx", "mesh1", MagicMock())

        self.assertEqual(metadata["metadata"]["comment"], "コメントJP")
        self.mock_parsed_cls.from_pmx_bytes.assert_called_once_with(b"fake pmx bytes")
        mock_parsed.free.assert_called_once_with()

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
        result = _sanitize_node_name("123bone")
        self.assertRegex(result, r"^[A-Za-z_][A-Za-z0-9_]*$")
        self.assertNotEqual(result[0], "1")

    def test_unicode_replaced(self):
        # Shared conversion may transliterate known terms; it must remain safe.
        result = _sanitize_node_name("\u30bb\u30f3\u30bf\u30fc")
        self.assertNotIn("\u30bb", result)
        self.assertRegex(result, r"^[A-Za-z_][A-Za-z0-9_]*$")

    def test_mixed(self):
        result = _sanitize_node_name("center_\u30bb\u30f3\u30bf\u30fc")
        # The center part survives
        self.assertIn("center", result)

    def test_empty(self):
        self.assertEqual(_sanitize_node_name(""), "unnamed")

    def test_hazardous_names_are_safe_and_collision_free(self):
        used = set()
        names = [
            _allocate_fast_material_name("1:髪", 0, used),
            _allocate_fast_material_name("2:髪+", 1, used),
            _allocate_fast_material_name("a:b", 2, used),
            _allocate_fast_material_name("ab", 3, used),
            _allocate_fast_material_name("", 4, used),
        ]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names))
        self.assertTrue(all(f"{name}SG" in used for name in names))


class TestFastMorphMetadata(unittest.TestCase):
    """Verify the Python-side alias/raw-name transaction for C++ morphs."""

    @staticmethod
    def _cmds(weight_count, aliases=None):
        cmds = MagicMock()
        cmds.listHistory.return_value = ["blendShape1"]
        cmds.nodeType.return_value = "blendShape"
        cmds.blendShape.return_value = weight_count
        aliases = aliases or {}
        alias_state = {
            f"blendShape1.weight[{index}]": alias
            for index, alias in aliases.items()
        }

        def alias_attr(*args, **kwargs):
            if kwargs.get("query"):
                return alias_state.get(args[0])
            if kwargs.get("remove"):
                old_alias = args[0].split(".", 1)[-1]
                for plug, alias in list(alias_state.items()):
                    if alias == old_alias:
                        alias_state.pop(plug, None)
                return None
            alias_state[args[1]] = args[0]
            return None

        cmds.aliasAttr.side_effect = alias_attr
        cmds.attributeQuery.return_value = False
        cmds._alias_state = alias_state
        return cmds

    @staticmethod
    def _source():
        return {
            "vertex_count": 4,
            "spans": [(0, 1, 0), (0, 1, 2), (0, 1, 3), (0, 1, 4)],
            "names": ["1:髪", "a:b", "a_b", ""],
            "morphs": [
                {"name": "1:髪", "type": "vertex", "vertexOffsets": [{"vertexIndex": 0}]},
                {"name": "bone", "type": "bone", "vertexOffsets": []},
                {"name": "a:b", "type": "vertex", "vertexOffsets": [{"vertexIndex": 1}]},
                {"name": "a_b", "type": "vertex", "vertexOffsets": [{"vertexIndex": 2}]},
                {"name": "", "type": "vertex", "vertexOffsets": [{"vertexIndex": 3}]},
            ],
        }

    @patch("mmd_tools.io.cpp_fast_importer._load_fast_morph_source")
    def test_hazard_collision_aliases_and_raw_global_indices(self, mock_source):
        mock_source.return_value = self._source()
        cmds = self._cmds(4, {0: "1____", 1: "a_b", 2: "a_b_1", 3: "morph_3"})

        _apply_fast_morph_metadata("model.pmx", "meshShape1", cmds)

        aliases = [
            cmds._alias_state.get(f"blendShape1.weight[{index}]")
            for index in range(4)
        ]
        self.assertEqual(len(aliases), len(set(aliases)))
        self.assertTrue(all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias) for alias in aliases))
        mapping_call = next(
            call for call in cmds.setAttr.call_args_list
            if call.args and ".mmd_blendshape_morph_names_json" in call.args[0]
        )
        mapping = json.loads(mapping_call.args[1])
        self.assertEqual(
            mapping,
            {
                "0": {"name": "1:髪", "index": 0},
                "1": {"name": "a:b", "index": 2},
                "2": {"name": "a_b", "index": 3},
            },
        )

    @patch("mmd_tools.io.cpp_fast_importer._load_fast_morph_source")
    def test_count_mismatch_does_not_mutate_aliases_or_json(self, mock_source):
        mock_source.return_value = self._source()
        cmds = self._cmds(3, {0: "cpp_a", 1: "cpp_b", 2: "cpp_c"})

        _apply_fast_morph_metadata("model.pmx", "meshShape1", cmds)

        self.assertEqual(
            cmds._alias_state,
            {
                "blendShape1.weight[0]": "cpp_a",
                "blendShape1.weight[1]": "cpp_b",
                "blendShape1.weight[2]": "cpp_c",
            },
        )
        self.assertFalse(any(
            call.args and ".mmd_blendshape_morph_names_json" in call.args[0]
            for call in cmds.setAttr.call_args_list
        ))
        self.assertFalse(any(call.kwargs.get("remove") for call in cmds.aliasAttr.call_args_list))

    @patch("mmd_tools.io.cpp_fast_importer._load_fast_morph_source")
    def test_parser_exception_does_not_mutate(self, mock_source):
        mock_source.side_effect = RuntimeError("parser unavailable")
        cmds = self._cmds(1, {0: "cpp_alias"})

        _apply_fast_morph_metadata("model.pmx", "meshShape1", cmds)

        self.assertEqual(cmds._alias_state, {"blendShape1.weight[0]": "cpp_alias"})
        self.assertFalse(cmds.addAttr.called)

    @patch("mmd_tools.io.cpp_fast_importer.parse_pmx_native")
    @patch("mmd_tools.io.cpp_fast_importer._mmd_parsed_model_class")
    @patch("mmd_tools.io.cpp_fast_importer.Path.read_bytes", return_value=b"fake pmx")
    def test_parsed_model_unavailable_uses_native_morph_fallback(
        self,
        _read_bytes,
        parsed_class,
        parse_native,
    ):
        parsed_class.return_value.from_pmx_bytes.return_value = None
        parse_native.return_value = SimpleNamespace(
            vertices=[object(), object()],
            morphs=[
                SimpleNamespace(
                    name="native_vertex",
                    morph_type=1,
                    offsets=[{"vertex_index": 1}],
                ),
                SimpleNamespace(name="native_bone", morph_type=2, offsets=[]),
            ],
        )

        source = cpp_fast_importer._load_fast_morph_source("model.pmx")

        self.assertEqual(source["vertex_count"], 2)
        self.assertIsNone(source["spans"])
        self.assertEqual(source["morphs"][0], {
            "name": "native_vertex",
            "type": "vertex",
            "vertexOffsets": [{"vertexIndex": 1}],
        })
        parsed_class.return_value.from_pmx_bytes.assert_called_once_with(b"fake pmx")
        parse_native.assert_called_once_with("model.pmx")

    @patch("mmd_tools.io.cpp_fast_importer.parse_pmx_native", side_effect=RuntimeError("native parser unavailable"))
    @patch("mmd_tools.io.cpp_fast_importer._mmd_parsed_model_class")
    @patch("mmd_tools.io.cpp_fast_importer.Path.read_bytes", return_value=b"fake pmx")
    def test_parser_and_native_fallback_exception_does_not_mutate(
        self,
        _read_bytes,
        parsed_class,
        _parse_native,
    ):
        parsed_class.return_value.from_pmx_bytes.side_effect = RuntimeError("ffi unavailable")
        cmds = self._cmds(1, {0: "cpp_alias"})

        _apply_fast_morph_metadata("model.pmx", "meshShape1", cmds)

        self.assertEqual(cmds._alias_state, {"blendShape1.weight[0]": "cpp_alias"})
        self.assertFalse(cmds.addAttr.called)

    @patch("mmd_tools.io.cpp_fast_importer._load_fast_morph_source")
    def test_alias_failure_rolls_back_previous_aliases(self, mock_source):
        mock_source.return_value = self._source()
        cmds = self._cmds(4, {0: "cpp_a", 1: "cpp_b", 2: "cpp_c", 3: "cpp_d"})
        original_alias_attr = cmds.aliasAttr.side_effect

        def fail_weight_one(*args, **kwargs):
            if (
                not kwargs.get("query")
                and not kwargs.get("remove")
                and args[1] == "blendShape1.weight[1]"
                and args[0] != "cpp_b"
            ):
                raise RuntimeError("alias write failed")
            return original_alias_attr(*args, **kwargs)

        cmds.aliasAttr.side_effect = fail_weight_one

        _apply_fast_morph_metadata("model.pmx", "meshShape1", cmds)

        self.assertEqual(
            cmds._alias_state,
            {
                "blendShape1.weight[0]": "cpp_a",
                "blendShape1.weight[1]": "cpp_b",
                "blendShape1.weight[2]": "cpp_c",
                "blendShape1.weight[3]": "cpp_d",
            },
        )
        self.assertFalse(any(
            call.args and ".mmd_blendshape_morph_names_json" in call.args[0]
            for call in cmds.setAttr.call_args_list
        ))

    @patch("mmd_tools.io.cpp_fast_importer._load_fast_morph_source")
    def test_empty_raw_names_keep_aliases_without_creating_mapping(self, mock_source):
        mock_source.return_value = {
            "vertex_count": 1,
            "spans": [(0, 1, 0)],
            "names": [""],
            "morphs": [{"name": "", "type": "vertex", "vertexOffsets": [{"vertexIndex": 0}]}],
        }
        cmds = self._cmds(1, {0: "cpp_alias"})

        _apply_fast_morph_metadata("model.pmx", "meshShape1", cmds)

        self.assertRegex(cmds._alias_state["blendShape1.weight[0]"], r"^[A-Za-z_][A-Za-z0-9_]*$")
        cmds.addAttr.assert_not_called()
        self.assertFalse(any(
            call.args and ".mmd_blendshape_morph_names_json" in call.args[0]
            for call in cmds.setAttr.call_args_list
        ))

    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_morph_metadata")
    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_root_metadata")
    @patch("mmd_tools.io.cpp_fast_importer._apply_basic_materials")
    @patch("mmd_tools.io.cpp_fast_importer._candidate_plugin_paths")
    @patch("mmd_tools.io.cpp_fast_importer._setup_plugin_directory")
    def test_include_morphs_false_skips_post_pass(
        self,
        _setup,
        candidates,
        basic_materials,
        root_metadata,
        morph_metadata,
    ):
        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        candidates.return_value = [plugin_path]
        basic_materials.return_value = None
        cmds = types.SimpleNamespace(
            loadPlugin=MagicMock(),
            mmdFastLoad=MagicMock(return_value=["root", "mesh"]),
        )
        with patch.object(Path, "exists", return_value=True), patch.dict(
            "sys.modules", {"maya.cmds": cmds}
        ):
            fast_import("model.pmx", include_morphs=False)

        morph_metadata.assert_not_called()

    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_root_metadata")
    @patch("mmd_tools.io.cpp_fast_importer._apply_basic_materials")
    @patch("mmd_tools.io.cpp_fast_importer._candidate_plugin_paths")
    @patch("mmd_tools.io.cpp_fast_importer._setup_plugin_directory")
    def test_vp2_ownership_passes_only_when_explicitly_enabled(
        self,
        _setup,
        candidates,
        basic_materials,
        root_metadata,
    ):
        import sys

        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        candidates.return_value = [plugin_path]
        basic_materials.return_value = None
        cmds_mod = sys.modules["maya.cmds"]
        cmds_mod.nodeType = MagicMock(return_value="mmdRenderShape")
        with patch.object(Path, "exists", return_value=True), patch.object(
            cmds_mod, "loadPlugin", create=True
        ) as load_plugin, patch.object(
            cmds_mod,
            "mmdFastLoad",
            create=True,
            return_value=["root", "sourceMesh", "renderShape"],
        ) as fast_load:
            result = fast_import("model.pmx", vp2_ownership=True)

        self.assertEqual(result, "root")
        cmds_mod.nodeType.assert_called_once_with("renderShape")
        load_plugin.assert_called_once()
        fast_load.assert_called_once_with(
            f="model.pmx",
            n="mmd_fast_model",
            s=1.0,
            mo=True,
            vp2Ownership=True,
        )
        root_metadata.assert_called_once()

    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_root_metadata")
    @patch("mmd_tools.io.cpp_fast_importer._apply_basic_materials")
    @patch("mmd_tools.io.cpp_fast_importer._candidate_plugin_paths")
    @patch("mmd_tools.io.cpp_fast_importer._setup_plugin_directory")
    def test_vp2_rejects_legacy_two_item_plugin_result(
        self,
        _setup,
        candidates,
        basic_materials,
        root_metadata,
    ):
        """An older plugin must not be accepted for an explicit VP2 request."""
        import sys

        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        candidates.return_value = [plugin_path]
        cmds_mod = sys.modules["maya.cmds"]
        cmds_mod.delete = MagicMock()
        with patch.object(Path, "exists", return_value=True), patch.object(
            cmds_mod, "loadPlugin", create=True
        ), patch.object(
            cmds_mod, "mmdFastLoad", create=True, return_value=["root", "mesh"]
        ) as fast_load:
            result = fast_import("model.pmx", vp2_ownership=True)

        self.assertIsNone(result)
        fast_load.assert_called_once()
        cmds_mod.delete.assert_called_once_with("root")
        basic_materials.assert_not_called()
        root_metadata.assert_not_called()

    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_root_metadata")
    @patch("mmd_tools.io.cpp_fast_importer._apply_basic_materials")
    @patch("mmd_tools.io.cpp_fast_importer._candidate_plugin_paths")
    @patch("mmd_tools.io.cpp_fast_importer._setup_plugin_directory")
    def test_vp2_rejects_non_render_shape_result_and_cleans_root(
        self,
        _setup,
        candidates,
        basic_materials,
        root_metadata,
    ):
        """A wrong proxy node type is rejected and the created root is removed."""
        import sys

        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        candidates.return_value = [plugin_path]
        cmds_mod = sys.modules["maya.cmds"]
        cmds_mod.nodeType = MagicMock(return_value="mesh")
        cmds_mod.delete = MagicMock()
        with patch.object(Path, "exists", return_value=True), patch.object(
            cmds_mod, "loadPlugin", create=True
        ), patch.object(
            cmds_mod,
            "mmdFastLoad",
            create=True,
            return_value=["root", "sourceMesh", "wrongShape"],
        ) as fast_load:
            result = fast_import("model.pmx", vp2_ownership=True)

        self.assertIsNone(result)
        fast_load.assert_called_once()
        cmds_mod.delete.assert_called_once_with("root")
        basic_materials.assert_not_called()
        root_metadata.assert_not_called()

    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_skeleton_skin")
    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_morph_metadata")
    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_root_metadata")
    @patch("mmd_tools.io.cpp_fast_importer._apply_basic_materials")
    @patch("mmd_tools.io.cpp_fast_importer._candidate_plugin_paths")
    @patch("mmd_tools.io.cpp_fast_importer._setup_plugin_directory")
    def test_vp2_post_processing_targets_source_mesh(
        self,
        _setup,
        candidates,
        basic_materials,
        root_metadata,
        morph_metadata,
        skeleton_skin,
    ):
        """Materials, morph metadata, and skinning use the source mesh item."""
        import sys

        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        candidates.return_value = [plugin_path]
        basic_materials.return_value = {"materials": []}
        cmds_mod = sys.modules["maya.cmds"]
        cmds_mod.nodeType = MagicMock(return_value="mmdRenderShape")
        with patch.object(Path, "exists", return_value=True), patch.object(
            cmds_mod, "loadPlugin", create=True
        ), patch.object(
            cmds_mod,
            "mmdFastLoad",
            create=True,
            return_value=["root", "sourceMesh", "renderShape"],
        ):
            result = fast_import(
                "model.pmx",
                base_name="demo",
                mesh_only=False,
                include_morphs=True,
                vp2_ownership=True,
            )

        self.assertEqual(result, "root")
        basic_materials.assert_called_once_with("model.pmx", "sourceMesh", cmds_mod)
        root_metadata.assert_called_once()
        morph_metadata.assert_called_once_with("model.pmx", "sourceMesh", cmds_mod)
        skeleton_skin.assert_called_once_with(
            "model.pmx",
            "sourceMesh",
            "root",
            "demo",
            cmds_mod,
            scale=1.0,
        )

    def test_standard_material_preserves_raw_names(self):
        cmds = MagicMock()
        cmds.ls.return_value = []
        cmds.attributeQuery.return_value = False
        cmds.shadingNode.side_effect = ["ab_fast", "ab_1_fast"]
        used = set()
        first = _create_standard_material(
            {"name": "a:b", "englishName": "a:b_en", "diffuse": [1, 0, 0, 1]},
            0,
            cmds,
            used,
        )
        second = _create_standard_material(
            {"name": "ab", "englishName": "ab_en", "diffuse": [0, 1, 0, 1]},
            1,
            cmds,
            used,
        )

        self.assertEqual((first, second), ("ab_fast", "ab_1_fast"))
        raw_writes = {
            call[0][0]: call[0][1]
            for call in cmds.setAttr.call_args_list
            if len(call[0]) >= 2 and ".mmd_material_name" in call[0][0]
        }
        self.assertEqual(raw_writes["ab_fast.mmd_material_name"], "a:b")
        self.assertEqual(raw_writes["ab_1_fast.mmd_material_name"], "ab")

    def test_root_metadata_preserves_japanese_english_and_empty_comments(self):
        cmds = MagicMock()
        cmds.attributeQuery.return_value = False
        metadata = {
            "metadata": {
                "name": "モデルJP",
                "englishName": "Model EN",
                "comment": "コメントJP",
                "englishComment": "Comment EN",
            }
        }
        _apply_fast_root_metadata("model.pmx", "root", metadata, cmds)

        writes = {call[0][0]: call[0][1] for call in cmds.setAttr.call_args_list if len(call[0]) >= 2}
        self.assertEqual(writes["root.mmd_model_name"], "モデルJP")
        self.assertEqual(writes["root.mmd_model_name_en"], "Model EN")
        self.assertEqual(writes["root.mmd_comment"], "コメントJP")
        self.assertEqual(writes["root.mmd_comment_en"], "Comment EN")

        cmds.reset_mock()
        _apply_fast_root_metadata(
            "model.pmx",
            "empty_root",
            {"metadata": {"name": "", "englishName": "", "comment": "", "englishComment": ""}},
            cmds,
        )
        empty_writes = {call[0][0]: call[0][1] for call in cmds.setAttr.call_args_list if len(call[0]) >= 2}
        self.assertEqual(empty_writes["empty_root.mmd_comment"], "")
        self.assertEqual(empty_writes["empty_root.mmd_comment_en"], "")

    def test_root_metadata_preserves_soft_body_count(self):
        """Fast-import roots retain unsupported PMX 2.1 soft-body provenance."""
        cmds = MagicMock()
        cmds.attributeQuery.return_value = False
        metadata = {
            "metadata": {
                "name": "Model",
                "englishName": "Model",
                "comment": "",
                "englishComment": "",
                "counts": {"softBodies": 2},
            }
        }

        _apply_fast_root_metadata("model.pmx", "root", metadata, cmds)

        soft_body_writes = [
            call
            for call in cmds.setAttr.call_args_list
            if call[0] and call[0][0] == "root.mmd_pmx_soft_body_count"
        ]
        self.assertEqual(len(soft_body_writes), 1)
        self.assertEqual(soft_body_writes[0][0][1], 2)

    @patch("mmd_tools.io.cpp_fast_importer.parse_pmx_native")
    def test_root_metadata_native_parser_fallback_is_called_once(self, mock_parse_native):
        mock_parse_native.return_value = SimpleNamespace(
            header=SimpleNamespace(
                model_name="Native JP",
                model_name_english="Native EN",
                comment="Native comment",
                comment_english="Native comment EN",
            )
        )
        cmds = MagicMock()
        cmds.attributeQuery.return_value = False

        _apply_fast_root_metadata("model.pmx", "root", None, cmds)

        mock_parse_native.assert_called_once_with("model.pmx")
        self.assertTrue(any("root.mmd_comment" in call[0][0] for call in cmds.setAttr.call_args_list))

    @patch("mmd_tools.io.cpp_fast_importer.parse_pmx_native", side_effect=RuntimeError("parser unavailable"))
    def test_root_metadata_parser_exception_is_best_effort(self, _mock_parse_native):
        _apply_fast_root_metadata("model.pmx", "root", None, MagicMock())


class TestCppFastImporterDebugLogging(unittest.TestCase):
    """Internal cpp_fast_importer diagnostics must use DEBUG, not INFO.

    Outer mmd_importer already owns the user-facing INFO success/fallback
    summary; this module should only emit internal detail at DEBUG.
    """

    @staticmethod
    def _message_templates(mock_log):
        # call[0] is args tuple (Py3.7-safe; _Call.args is 3.8+)
        return [call[0][0] for call in mock_log.call_args_list if call[0]]

    def test_fallback_reason_uses_debug_not_info(self):
        """Top-level fallback (plugin missing) is DEBUG-only."""
        missing = Path("nonexistent_mmd_tools_cpp.mll")
        with patch.object(
            cpp_fast_importer, "logger"
        ) as mock_logger, patch.object(
            cpp_fast_importer,
            "_candidate_plugin_paths",
            return_value=[missing],
        ):
            result = fast_import("model.pmx")

        self.assertIsNone(result)
        debug_messages = self._message_templates(mock_logger.debug)
        info_messages = self._message_templates(mock_logger.info)
        expected = (
            "C++ plugin not found – falling back to Python importer. "
            "Checked paths:\n%s"
        )
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)


    def test_success_completion_uses_debug_not_info(self):
        """Successful internal completion is DEBUG-only."""
        import sys

        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        # Work with whatever maya.cmds is present (stub MagicMock or real mayapy).
        cmds_mod = sys.modules.get("maya.cmds")
        if cmds_mod is None:
            import maya.cmds as cmds_mod  # noqa: F401

            cmds_mod = sys.modules["maya.cmds"]

        with patch.object(
            cpp_fast_importer, "logger"
        ) as mock_logger, patch.object(
            cpp_fast_importer,
            "_candidate_plugin_paths",
            return_value=[plugin_path],
        ), patch.object(
            Path, "exists", return_value=True
        ), patch.object(
            cpp_fast_importer, "_setup_plugin_directory"
        ), patch.object(
            cpp_fast_importer, "_apply_basic_materials"
        ), patch.object(
            cmds_mod,
            "mmdFastLoad",
            create=True,
            return_value=["root_xform", "meshShape1"],
        ), patch.object(
            cmds_mod,
            "loadPlugin",
            create=True,
        ):
            result = fast_import("model.pmx", base_name="demo", mesh_only=True)

        self.assertEqual(result, "root_xform")
        debug_messages = self._message_templates(mock_logger.debug)
        info_messages = self._message_templates(mock_logger.info)
        expected = "Fast import succeeded: transform node = %s"
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_material_detail_uses_debug_not_info(self):
        """Optional material detail path is DEBUG-only."""
        with patch.object(
            cpp_fast_importer, "logger"
        ) as mock_logger, patch.object(
            Path, "read_bytes", return_value=b"fake"
        ), patch.object(
            cpp_fast_importer, "_mmd_parsed_model_class"
        ) as mock_cls:
            mock_cls.return_value.from_pmx_bytes.return_value = None
            _apply_basic_materials("model.pmx", "mesh1", MagicMock())

        debug_messages = self._message_templates(mock_logger.debug)
        info_messages = self._message_templates(mock_logger.info)
        expected = (
            "Native parsed-model metadata unavailable; "
            "skipping fast material assignment"
        )
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)


class TestCppPluginLocatorIntegration(unittest.TestCase):
    """Version-specific native overrides use the shared locator contract."""

    def test_version_specific_config_precedes_generic_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(cpp_fast_importer, "ROOT", root), patch.object(
                cpp_fast_importer, "_running_maya_major_version", return_value="2026"
            ), patch.dict(
                "os.environ",
                {
                    "MMD_TOOLS_CPP_PLUGIN_2026": "",
                    "MMD_TOOLS_CPP_PLUGIN": "",
                    "MMD_TOOLS_CPP_CONFIG_2026": "Release",
                    "MMD_TOOLS_CPP_CONFIG": "Debug",
                },
                clear=False,
            ):
                candidates = cpp_fast_importer._candidate_plugin_paths()

        self.assertEqual(
            candidates[:3],
            [
                root / "plug-ins" / "2026" / "Release" / "mmd_tools_cpp.mll",
                root / "plug-ins" / "2026" / "Release" / "mmd_tools_cpp.bundle",
                root / "plug-ins" / "2026" / "Release" / "mmd_tools_cpp.so",
            ],
        )


if __name__ == "__main__":
    unittest.main()
