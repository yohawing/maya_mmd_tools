"""Tests for the C++ fast import integration in mmd_importer.

Verifies that ``import_mmd_file`` correctly routes through / around the
``fast_import`` path depending on the ``use_cpp_fast_load`` and
``cpp_fast_load_mesh_only`` options.  Also tests the skeleton/skin creation
shared PMX authoring pipeline.

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
from unittest.mock import MagicMock, call, patch

from tests.common.maya_stub import install_maya_stub


install_maya_stub()

# Now safe to import the module under test
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.core.settings import settings
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.io import cpp_fast_importer
from mmd_tools.io.cpp_fast_importer import (
    _apply_basic_materials,
    _apply_fast_material_morph_runtime,
    _apply_fast_morph_metadata,
    _apply_fast_root_metadata,
    _allocate_fast_material_name,
    _create_standard_material,
    _fast_model_scene_name,
    _organize_fast_dag,
    _sanitize_node_name,
    _set_fast_double3_attr,
    fast_import,
)
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.pmx_data import PmxData


class TestCppFastImportRouting(unittest.TestCase):
    """Routing scenarios for .pmx files with the C++ fast-import option."""

    def setUp(self):
        light_patch = patch(
            "mmd_tools.io.model_import_pipeline.create_mmd_light_controller",
            return_value="|mmd_light",
        )
        self.mock_create_light = light_patch.start()
        self.addCleanup(light_patch.stop)
        select_patch = patch("maya.cmds.select")
        self.mock_select = select_patch.start()
        self.addCleanup(select_patch.stop)
        old_light = settings.get("import.light.create_controller", True)
        self.addCleanup(settings.set, "import.light.create_controller", old_light)
        settings.set("import.light.create_controller", True)
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
            mesh_only=False,
            include_morphs=True,
            options={"scale": 1.0, "use_cpp_fast_load": True},
            progress_callback=progress.append,
        )
        mock_parse.assert_not_called()
        mock_import_pmx.assert_not_called()
        self.assertEqual(result, "cpp_root")
        self.assertEqual(progress, [5, 10, 90])
        self.mock_create_light.assert_called_once_with()
        self.mock_select.assert_called_once_with("cpp_root", replace=True)

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
            mesh_only=False,
            include_morphs=True,
            vp2_ownership=True,
            options={"scale": 1.0, "use_cpp_fast_load": True, "use_cpp_vp2_ownership": True,
                     "profile": {"native_import": {"requested": True, "route": "cpp_fast_load_vp2",
                                                  "status": "succeeded", "fallback": "not_used"}}},
        )
        mock_setup_color_management.assert_called_once_with()
        self.assertEqual(result, "cpp_root")
        self.mock_create_light.assert_called_once_with()

    @patch("mmd_tools.io.mmd_importer.fast_import", return_value="cpp_root")
    def test_fast_import_respects_light_controller_opt_out(self, mock_fast):
        """Fast Load preserves the shared scene-light opt-out setting."""
        settings.set("import.light.create_controller", False)
        self.assertEqual(
            import_mmd_file("model.pmx", options={"use_cpp_fast_load": True}),
            "cpp_root",
        )
        mock_fast.assert_called_once()
        self.mock_create_light.assert_not_called()

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

    @patch(
        "mmd_tools.io.mmd_importer.fast_import",
        side_effect=RuntimeError(
            "C++ VP2 RenderOverride requires DirectX 11; restart Maya"
        ),
    )
    def test_native_vp2_device_error_reaches_the_ui_import_boundary(
        self,
        mock_fast: MagicMock,
    ):
        """The actionable device reason must survive importer error wrapping."""
        options = {
            "scale": 1.0,
            "use_cpp_fast_load": True,
            "use_cpp_vp2_ownership": True,
        }

        with self.assertRaisesRegex(
            MMDImportException,
            r"requires DirectX 11; restart Maya",
        ):
            import_mmd_file("model.pmx", options=options)

        mock_fast.assert_called_once()
        self.assertEqual(
            options["profile"]["native_import"]["reason"],
            "fast importer error: C++ VP2 RenderOverride requires DirectX 11; "
            "restart Maya",
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
    # Scenario 4: mesh_only=True → fast import receives mesh_only=True
    # ------------------------------------------------------------------

    @patch("mmd_tools.io.mmd_importer.fast_import")
    @patch("mmd_tools.io.mmd_importer.parse_mmd_file")
    @patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file")
    def test_fast_import_mesh_only_true_calls_with_param(
        self,
        mock_import_pmx: MagicMock,
        mock_parse: MagicMock,
        mock_fast: MagicMock,
    ):
        """When cpp_fast_load_mesh_only is True, fast_import is called
        with mesh_only=True to request the explicit geometry-only API."""
        mock_fast.return_value = "cpp_root"

        result = import_mmd_file(
            "model.pmx",
            options={
                "scale": 1.0,
                "use_cpp_fast_load": True,
                "cpp_fast_load_mesh_only": True,
            },
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

    # ------------------------------------------------------------------
    # Scenario 5: full authoring is the default
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
        called with mesh_only=False even with legacy saved settings."""
        settings.set("import.native.cpp_fast_load_mesh_only", True)
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
            mesh_only=False,
            include_morphs=True,
            options={"scale": 1.0, "use_cpp_fast_load": True},
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
            mesh_only=False,
            include_morphs=False,
            options={"scale": 1.0, "use_cpp_fast_load": True, "import_morphs": False},
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
            mesh_only=False,
            include_morphs=True,
            vp2_ownership=True,
            options={"scale": 1.0, "use_cpp_fast_load": True, "use_cpp_vp2_ownership": True,
                     "profile": {"native_import": {"requested": True, "route": "cpp_fast_load_vp2",
                                                  "status": "succeeded", "fallback": "not_used"}}},
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


class TestFastImportMetadata(unittest.TestCase):
    """Unit tests for native metadata and shared full import."""

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

    def tearDown(self):
        self.mock_parsed_patcher.stop()
        self.mock_read_bytes_patcher.stop()

    def _make_cmds_mock(self):
        """Build a MagicMock that behaves like a Maya cmds module."""
        cmds = MagicMock()

        def _joint_side_effect(name=None, position=None, **kwargs):
            return name or "joint1"

        cmds.joint.side_effect = _joint_side_effect
        cmds.group.return_value = "skeleton_group1"
        cmds.skinCluster.return_value = ["skinCluster1"]
        cmds.objExists.return_value = True
        cmds.polyEvaluate.return_value = 1
        cmds.listRelatives.return_value = ["meshTransform1"]
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


    def test_full_fast_import_shares_options_and_scale_with_pmx_pipeline(self):
        """Native geometry retains ordinary physics, morph and scale options."""
        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        parsed = object()
        options = {"import_physics": True, "setup_rig": False, "custom_namespace": "hero",
                   "separate_meshes_by_material": False}
        progress = MagicMock()
        with patch.object(cpp_fast_importer, "_candidate_plugin_paths", return_value=[plugin_path]), patch.object(
            Path, "exists", return_value=True
        ), patch.object(cpp_fast_importer, "parse_pmx_native", return_value=parsed), patch(
            "mmd_tools.io.pmx_importer.import_pmx_file", return_value="root1"
        ) as import_pmx, patch("maya.cmds.mmdFastLoad", create=True, return_value=["nativeRoot", "nativeMesh"]), patch.object(
            cpp_fast_importer.cpp_plugin_locator, "is_plugin_loaded", return_value=True
        ):
            result = fast_import("model.pmx", scale=0.5, mesh_only=False, options=options, progress_callback=progress)
        self.assertEqual(result, "root1")
        args = import_pmx.call_args.args
        self.assertEqual(args[:3], (parsed, "model.pmx", 0.5))
        self.assertIs(import_pmx.call_args.kwargs["progress_callback"], progress)
        self.assertTrue(args[3]["_cpp_fast_load_geometry"])
        self.assertTrue(args[3]["import_physics"])
        self.assertFalse(args[3]["setup_rig"])
        self.assertEqual(args[3]["custom_namespace"], "hero")
        self.assertNotIn("_cpp_fast_load_geometry", options)

    def test_full_native_unavailability_returns_before_authoring(self):
        """Optional native failures leave the ordinary parser fallback reachable."""
        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        for failure in ("parse", "geometry"):
            with self.subTest(failure=failure), patch.object(
                cpp_fast_importer, "_candidate_plugin_paths", return_value=[plugin_path]
            ), patch.object(Path, "exists", return_value=True), patch.object(
                cpp_fast_importer.cpp_plugin_locator, "is_plugin_loaded", return_value=True
            ), patch.object(
                cpp_fast_importer, "parse_pmx_native", return_value=None if failure == "parse" else object()
            ), patch("maya.cmds.mmdFastLoad", create=True, side_effect=RuntimeError("unavailable")), patch(
                "mmd_tools.io.pmx_importer.import_pmx_file"
            ) as author:
                self.assertIsNone(fast_import("model.pmx", mesh_only=False))
                author.assert_not_called()

    def test_vp2_command_failure_preserves_original_error(self):
        """Required VP2 imports must surface the command's actual rejection."""
        for mesh_only in (False, True):
            with self.subTest(mesh_only=mesh_only), patch.object(
                cpp_fast_importer, "_candidate_plugin_paths", return_value=[Path("plugin.mll")]
            ), patch.object(Path, "exists", return_value=True), patch.object(
                cpp_fast_importer.cpp_plugin_locator, "is_plugin_loaded", return_value=True
            ), patch.object(cpp_fast_importer, "parse_pmx_native", return_value=object()), patch.object(
                cpp_fast_importer, "_require_dx11_for_vp2_ownership"
            ), patch("maya.cmds.mmdFastLoad", create=True, side_effect=RuntimeError("non-finite normal")):
                with self.assertRaisesRegex(RuntimeError, "non-finite normal"):
                    fast_import("model.pmx", mesh_only=mesh_only, vp2_ownership=True)

    def test_failed_full_import_removes_separated_proxy_owner(self):
        with patch.object(cpp_fast_importer, "_candidate_plugin_paths", return_value=[Path("plugin.mll")]), patch.object(
            Path, "exists", return_value=True
        ), patch.object(cpp_fast_importer, "parse_pmx_native", return_value=object()), patch.object(
            cpp_fast_importer, "_require_dx11_for_vp2_ownership"
        ), patch.object(cpp_fast_importer.cpp_plugin_locator, "is_plugin_loaded", return_value=True), patch(
            "maya.cmds.mmdFastLoad", create=True, return_value=["source", "mesh", "proxy"]
        ), patch("maya.cmds.ls", side_effect=[["sourceUuid"], ["proxyUuid"], ["renamedSource"], ["movedProxy"]]), patch(
            "maya.cmds.listRelatives", return_value=["proxyOwner"]
        ), patch("maya.cmds.delete") as delete, patch(
            "mmd_tools.io.pmx_importer.import_pmx_file", side_effect=RuntimeError("authoring rejected")
        ):
            with self.assertRaisesRegex(RuntimeError, "authoring rejected"):
                fast_import("model.pmx", mesh_only=False, vp2_ownership=True,
                            options={"separate_meshes_by_material": False})
        delete.assert_called_once_with(["renamedSource", "proxyOwner"])

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

    def test_basic_materials_uses_shared_native_pmx_without_reparsing(self):
        """Shared native material data drives aliases, textures, faces and root metadata."""
        pmx = SimpleNamespace(
            header=SimpleNamespace(model_name="モデルJP", model_name_english="Model EN",
                                   comment="コメントJP", comment_english="Comment EN"),
            vertices=[], faces=[(0, 1, 2)], textures=["tex/diffuse.png"],
            materials=[SimpleNamespace(name="材質", name_english="Material EN",
                                       face_count=3, texture_index=0)],
            soft_bodies=[object()],
        )
        cmds = MagicMock()
        cmds.ls.return_value = []
        converter = MagicMock()
        converter._material_name_by_index = {}
        converter._create_material.return_value = "shader"

        with patch("mmd_tools.converters.mesh_converter.MeshConverter", return_value=converter) as converter_cls, patch(
            "mmd_tools.converters.material_morph_runtime.bind_standard_material", return_value=True
        ):
            metadata = _apply_basic_materials("model.pmx", "mesh1", cmds, native_pmx=pmx)

        self.mock_read_bytes.assert_not_called()
        self.mock_parsed_cls.from_pmx_bytes.assert_not_called()
        converter_cls.assert_called_once_with("model.pmx")
        self.assertEqual(metadata["metadata"]["comment"], "コメントJP")
        self.assertEqual(metadata["metadata"]["counts"]["softBodies"], 1)
        self.assertEqual(metadata["materials"][0]["englishName"], "Material EN")
        self.assertEqual(converter._material_name_by_index[0], "Material_EN_fast")
        self.assertEqual(converter._create_material.call_args.kwargs["texture_path"], "tex/diffuse.png")
        cmds.sets.assert_any_call("mesh1.f[0:0]", edit=True, forceElement="shaderSG")

    def test_basic_materials_keeps_lazy_soft_bodies_unresolved(self):
        """PMX 2.1 uses parsed metadata without triggering the legacy soft-body loader."""
        pmx = PmxData()
        pmx.materials = [SimpleNamespace(texture_index=0)]
        pmx.textures = ["tex/diffuse.png"]
        loader = MagicMock(side_effect=AssertionError("legacy PMX reparse"))
        pmx.soft_body_loader = loader
        parsed = MagicMock()
        parsed.metadata_json = json.dumps({
            "metadata": {"name": "Model", "counts": {"softBodies": 2}},
            "materials": [{"name": "材質", "englishName": "Material EN"}],
        })
        parsed.material_groups = [(0, 3, 0)]
        self.mock_parsed_cls.from_pmx_bytes.return_value = parsed
        cmds = MagicMock()
        cmds.ls.return_value = []
        converter = MagicMock()
        converter._material_name_by_index = {}
        converter._create_material.return_value = "shader"

        with patch("mmd_tools.converters.mesh_converter.MeshConverter", return_value=converter), patch(
            "mmd_tools.converters.material_morph_runtime.bind_standard_material", return_value=True
        ):
            metadata = _apply_basic_materials("model.pmx", "mesh1", cmds, native_pmx=pmx)

        loader.assert_not_called()
        self.assertIs(pmx.soft_body_loader, loader)
        self.mock_read_bytes.assert_called_once_with()
        self.mock_parsed_cls.from_pmx_bytes.assert_called_once_with(b"fake pmx bytes")
        parsed.free.assert_called_once_with()
        self.assertEqual(metadata["metadata"]["counts"]["softBodies"], 2)
        self.assertEqual(converter._material_name_by_index[0], "Material_EN_fast")
        self.assertEqual(converter._create_material.call_args.kwargs["texture_path"], "tex/diffuse.png")
        cmds.sets.assert_any_call("mesh1.f[0:0]", edit=True, forceElement="shaderSG")

    def test_basic_materials_falls_back_when_shared_pmx_has_no_materials(self):
        """Incomplete shared native data keeps the parsed-model material path."""
        parsed = MagicMock()
        parsed.metadata_json = json.dumps({"metadata": {"name": "Parsed"},
                                           "materials": [{"name": "mat"}]})
        parsed.material_groups = [(0, 3, 0)]
        self.mock_parsed_cls.from_pmx_bytes.return_value = parsed
        cmds = MagicMock()
        cmds.ls.return_value = []

        with patch.object(cpp_fast_importer, "_create_standard_material", return_value="shader") as create, patch(
            "mmd_tools.converters.mesh_converter.MeshConverter"
        ) as converter_cls:
            metadata = _apply_basic_materials(
                "model.pmx", "mesh1", cmds, native_pmx=SimpleNamespace(materials=[])
            )

        self.assertEqual(metadata["metadata"]["name"], "Parsed")
        self.mock_read_bytes.assert_called_once_with()
        self.mock_parsed_cls.from_pmx_bytes.assert_called_once_with(b"fake pmx bytes")
        parsed.free.assert_called_once_with()
        create.assert_called_once()
        converter_cls.assert_not_called()
        cmds.sets.assert_any_call("mesh1.f[0:0]", edit=True, forceElement="shaderSG")

    @patch("mmd_tools.io.cpp_fast_importer.parse_pmx_native")
    def test_basic_materials_falls_back_to_current_native_parser(self, mock_parse_native):
        """The current parser ABI supplies materials when parsed-model is unavailable."""
        self.mock_parsed_cls.from_pmx_bytes.return_value = None
        mock_parse_native.return_value = SimpleNamespace(
            header=SimpleNamespace(
                model_name="モデルJP",
                model_name_english="Model EN",
                comment="コメントJP",
                comment_english="Comment EN",
            ),
            materials=[SimpleNamespace(
                name="mat",
                name_english="Mat",
                diffuse=(0.2, 0.3, 0.4, 0.35),
                specular=(0.1, 0.2, 0.3),
                ambient=(0.01, 0.02, 0.03),
                specular_coefficient=12.0,
                edge_color=(0.4, 0.5, 0.6, 0.7),
                edge_size=1.8,
                face_count=3,
            )],
            soft_bodies=[object(), object()],
        )
        cmds = MagicMock()
        cmds.attributeQuery.return_value = False
        cmds.shadingNode.return_value = "mat_fast"

        metadata = _apply_basic_materials("model.pmx", "mesh1", cmds)

        self.assertEqual(metadata["metadata"]["englishName"], "Model EN")
        self.assertEqual(metadata["metadata"]["counts"]["softBodies"], 2)
        self.assertEqual(metadata["materials"][0]["diffuse"][3], 0.35)
        self.assertEqual(metadata["materials"][0]["ambient"], [0.01, 0.02, 0.03])
        self.assertEqual(metadata["materials"][0]["specularPower"], 12.0)
        self.assertEqual(metadata["materials"][0]["edgeColor"], [0.4, 0.5, 0.6, 0.7])
        self.assertEqual(metadata["materials"][0]["edgeSize"], 1.8)
        cmds.shadingNode.assert_called_once_with(
            "standardSurface",
            asShader=True,
            name="Mat_fast",
        )
        cmds.sets.assert_any_call(
            "mesh1.f[0:0]",
            edit=True,
            forceElement="mat_fastSG",
        )
        self.assertTrue(mock_parse_native.called)


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


class TestFastMaterialMorphRuntime(unittest.TestCase):
    """Verify the fast path preserves the existing morph/controller topology."""

    def test_runtime_passes_existing_blend_shapes_to_global_controller(self):
        converter = MagicMock()
        converter._convert_material_morph_pmx.return_value = {
            "success": True,
            "morph_node": "materialMorph0",
        }
        converter.build_morph_controller.return_value = "morphController"
        pipeline = MagicMock()
        pmx = SimpleNamespace(
            morphs=[SimpleNamespace(morph_type=PmxMorphType.MaterialMorph)]
        )
        graph = {
            "success": True,
            "native_alpha": {"success": True, "skipped": []},
            "skipped": [],
        }

        with patch.object(cpp_fast_importer, "parse_pmx_native", return_value=pmx), patch(
            "mmd_tools.converters.MorphConverter", return_value=converter
        ), patch(
            "mmd_tools.io.model_import_pipeline.ModelImportPipeline",
            return_value=pipeline,
        ), patch(
            "mmd_tools.converters.material_morph_runtime.build_material_morph_graph",
            return_value=graph,
        ):
            result = _apply_fast_material_morph_runtime(
                "model.pmx",
                "root",
                model_registry="registry",
                blend_shape_nodes=["sourceBlendShape"],
            )

        self.assertTrue(result["success"])
        converter.validate_runtime_requirements.assert_called_once_with(pmx)
        morph_result = pipeline.connect_morph_nodes_to_root.call_args.args[1]
        self.assertEqual(morph_result["blend_shape_nodes"], ["sourceBlendShape"])
        self.assertEqual(morph_result["total_morphs"], 1)
        self.assertEqual(morph_result["vertex_morph_nodes"], [])
        pipeline.connect_morph_nodes_to_root.assert_called_once_with(
            "root",
            morph_result,
            model_registry="registry",
        )
        converter.build_morph_controller.assert_called_once_with(
            pmx,
            "root",
            morph_result,
        )

    def test_runtime_preflight_stops_scene_mutation_when_plugin_is_missing(self):
        converter = MagicMock()
        converter.validate_runtime_requirements.side_effect = RuntimeError(
            "Load or reload the maya_mmd_tools plugin before importing a PMX with morphs."
        )
        pipeline = MagicMock()
        pmx = SimpleNamespace(
            morphs=[SimpleNamespace(morph_type=PmxMorphType.MaterialMorph)]
        )

        with patch.object(cpp_fast_importer, "parse_pmx_native", return_value=pmx), patch(
            "mmd_tools.converters.MorphConverter", return_value=converter
        ), patch(
            "mmd_tools.io.model_import_pipeline.ModelImportPipeline",
            return_value=pipeline,
        ):
            result = _apply_fast_material_morph_runtime("model.pmx", "root")

        self.assertFalse(result["success"])
        self.assertTrue(
            any(
                item.startswith("material_morph_runtime_failed:Load or reload")
                for item in result["skipped"]
            )
        )
        converter.validate_runtime_requirements.assert_called_once_with(pmx)
        converter._convert_material_morph_pmx.assert_not_called()
        converter.build_morph_controller.assert_not_called()
        pipeline.connect_morph_nodes_to_root.assert_not_called()

    @patch.object(cpp_fast_importer, "parse_pmx_native", return_value=None)
    def test_runtime_parser_unavailable_is_a_failure(self, _parse_native):
        result = _apply_fast_material_morph_runtime("model.pmx", "root")

        self.assertFalse(result["success"])
        self.assertEqual(result["skipped"], ["native_pmx_unavailable"])

    def test_runtime_material_conversion_failure_is_not_no_material_morphs(self):
        converter = MagicMock()
        converter._convert_material_morph_pmx.side_effect = [
            {"success": True, "morph_node": "materialMorph0"},
            {"success": False},
        ]
        pmx = SimpleNamespace(
            morphs=[
                SimpleNamespace(morph_type=PmxMorphType.MaterialMorph),
                SimpleNamespace(morph_type=PmxMorphType.MaterialMorph),
            ]
        )

        with patch.object(cpp_fast_importer, "parse_pmx_native", return_value=pmx), patch(
            "mmd_tools.converters.MorphConverter", return_value=converter
        ):
            result = _apply_fast_material_morph_runtime("model.pmx", "root")

        self.assertFalse(result["success"])
        self.assertEqual(result["material_morph_nodes"], ["materialMorph0"])
        self.assertIn("material_morph_conversion_failed:1", result["skipped"])
        self.assertNotIn("no_material_morphs", result["skipped"])
        converter.build_morph_controller.assert_not_called()

    def test_standard_material_stores_authored_alpha_and_original_index(self):
        cmds = MagicMock()
        cmds.ls.return_value = []
        cmds.attributeQuery.return_value = False
        cmds.shadingNode.return_value = "material_fast"

        _create_standard_material(
            {
                "name": "mat",
                "englishName": "mat",
                "diffuse": [0.2, 0.3, 0.4, 0.35],
                "specular": [0.5, 0.6, 0.7],
                "specularPower": 12.0,
                "ambient": [0.01, 0.02, 0.03],
                "edgeColor": [0.4, 0.5, 0.6, 0.7],
                "edgeSize": 1.8,
            },
            7,
            cmds,
            set(),
        )

        writes = {
            call.args[0]: call.args[1]
            for call in cmds.setAttr.call_args_list
            if len(call.args) >= 2
        }
        self.assertEqual(writes["material_fast.mmd_material"], 1)
        self.assertEqual(writes["material_fast.mmd_material_index"], 7)
        self.assertEqual(writes["material_fast.mmd_diffuse_alpha"], 0.35)
        self.assertEqual(writes["material_fast.shininess"], 12.0)
        self.assertEqual(writes["material_fast.mmd_edge_alpha"], 0.7)
        self.assertEqual(writes["material_fast.mmd_edge_size"], 1.8)

    def test_fast_double3_metadata_creates_numeric_children(self):
        """RGB authored metadata is a valid Maya double3 compound."""
        cmds = MagicMock()
        cmds.attributeQuery.return_value = False

        _set_fast_double3_attr(cmds, "material_fast", "diffuse_color", (0.95, 0.82, 0.28))

        cmds.addAttr.assert_has_calls([
            call("material_fast", longName="diffuse_color", attributeType="double3"),
            call(
                "material_fast",
                longName="diffuse_colorX",
                attributeType="double",
                parent="diffuse_color",
            ),
            call(
                "material_fast",
                longName="diffuse_colorY",
                attributeType="double",
                parent="diffuse_color",
            ),
            call(
                "material_fast",
                longName="diffuse_colorZ",
                attributeType="double",
                parent="diffuse_color",
            ),
        ])
        cmds.setAttr.assert_called_once_with(
            "material_fast.diffuse_color",
            0.95,
            0.82,
            0.28,
            type="double3",
        )


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
        with patch.object(
            cpp_fast_importer, "_require_dx11_for_vp2_ownership"
        ), patch.object(
            Path, "exists", return_value=True
        ), patch.object(
            cmds_mod, "nodeType", return_value="mmdRenderShape"
        ) as node_type, patch.object(
            cmds_mod, "loadPlugin", create=True
        ) as load_plugin, patch.object(
            cmds_mod,
            "mmdFastLoad",
            create=True,
            return_value=["root", "sourceMesh", "renderShape"],
        ) as fast_load, patch.object(
            cpp_fast_importer,
            "_apply_fast_material_morph_runtime",
            return_value={"success": True},
        ):
            result = fast_import("model.pmx", vp2_ownership=True)

        self.assertEqual(result, "root")
        node_type.assert_called_once_with("renderShape")
        load_plugin.assert_called_once()
        fast_load.assert_called_once_with(
            f="model.pmx",
            n="mmd_fast_model",
            s=1.0,
            mo=True,
            vp2Ownership=True,
        )
        root_metadata.assert_called_once()

    @patch("mmd_tools.io.cpp_fast_importer._candidate_plugin_paths")
    @patch("mmd_tools.io.cpp_fast_importer._setup_plugin_directory")
    def test_vp2_ownership_rejects_confirmed_opengl_before_fast_load(
        self,
        _setup,
        candidates,
    ):
        """OpenGL must explain the required preference instead of showing clay."""
        import sys

        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        candidates.return_value = [plugin_path]
        cmds_mod = sys.modules["maya.cmds"]
        with patch.object(Path, "exists", return_value=True), patch.object(
            cmds_mod, "loadPlugin", create=True
        ), patch.object(
            cmds_mod,
            "ogs",
            create=True,
            return_value="API : OpenGL V.4.6",
        ), patch.object(
            cmds_mod,
            "mmdFastLoad",
            create=True,
        ) as fast_load:
            with self.assertRaisesRegex(
                RuntimeError,
                r"requires DirectX 11.*Display > Viewport 2\.0.*restart Maya",
            ):
                fast_import("model.pmx", vp2_ownership=True)

        fast_load.assert_not_called()

    @patch("mmd_tools.io.cpp_fast_importer._candidate_plugin_paths")
    @patch("mmd_tools.io.cpp_fast_importer._setup_plugin_directory")
    def test_vp2_ownership_rejects_any_confirmed_non_dx11_api(
        self,
        _setup,
        candidates,
    ):
        """A confirmed Metal device must not fall through to a gray proxy."""
        import sys

        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        candidates.return_value = [plugin_path]
        cmds_mod = sys.modules["maya.cmds"]
        for device_info in (
            ["Adapter : Apple GPU", "API : Metal"],
            "API: Metal",
            ["Adapter supports DX11 translation", "API : Metal"],
        ):
            with self.subTest(device_info=device_info), patch.object(
                Path, "exists", return_value=True
            ), patch.object(
                cmds_mod, "loadPlugin", create=True
            ), patch.object(
                cmds_mod, "ogs", create=True, return_value=device_info
            ), patch.object(
                cmds_mod, "mmdFastLoad", create=True
            ) as fast_load:
                with self.assertRaisesRegex(
                    RuntimeError, r"requires DirectX 11.*Metal"
                ):
                    fast_import("model.pmx", vp2_ownership=True)

            fast_load.assert_not_called()

    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_root_metadata")
    @patch("mmd_tools.io.cpp_fast_importer._apply_basic_materials")
    @patch("mmd_tools.io.cpp_fast_importer._candidate_plugin_paths")
    @patch("mmd_tools.io.cpp_fast_importer._setup_plugin_directory")
    def test_normal_fast_load_is_not_rejected_on_opengl(
        self,
        _setup,
        candidates,
        basic_materials,
        root_metadata,
    ):
        """The device guard applies only to explicit RenderOverride ownership."""
        import sys

        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        candidates.return_value = [plugin_path]
        basic_materials.return_value = None
        cmds_mod = sys.modules["maya.cmds"]
        with patch.object(Path, "exists", return_value=True), patch.object(
            cmds_mod, "loadPlugin", create=True
        ), patch.object(
            cmds_mod,
            "ogs",
            create=True,
            return_value="API : OpenGL V.4.6",
        ) as ogs, patch.object(
            cmds_mod,
            "mmdFastLoad",
            create=True,
            return_value=["root", "mesh"],
        ) as fast_load:
            result = fast_import("model.pmx")

        self.assertEqual(result, "root")
        ogs.assert_not_called()
        fast_load.assert_called_once()

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
        with patch.object(
            cpp_fast_importer, "_require_dx11_for_vp2_ownership"
        ), patch.object(
            Path, "exists", return_value=True
        ), patch.object(cmds_mod, "delete") as delete, patch.object(
            cmds_mod, "loadPlugin", create=True
        ), patch.object(
            cmds_mod, "mmdFastLoad", create=True, return_value=["root", "mesh"]
        ) as fast_load:
            result = fast_import("model.pmx", vp2_ownership=True)

        self.assertIsNone(result)
        fast_load.assert_called_once()
        delete.assert_called_once_with("root")
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
        with patch.object(
            cpp_fast_importer, "_require_dx11_for_vp2_ownership"
        ), patch.object(
            Path, "exists", return_value=True
        ), patch.object(
            cmds_mod, "nodeType", return_value="mesh"
        ) as node_type, patch.object(cmds_mod, "delete") as delete, patch.object(
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
        node_type.assert_called_once_with("wrongShape")
        delete.assert_called_once_with("root")
        basic_materials.assert_not_called()
        root_metadata.assert_not_called()

    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_material_morph_runtime")
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
        material_runtime,
    ):
        """Materials, morph metadata, and skinning use the source mesh item."""
        import sys

        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        candidates.return_value = [plugin_path]
        basic_materials.return_value = {"materials": []}
        morph_metadata.return_value = ["sourceBlendShape"]
        material_runtime.return_value = {"success": True}
        cmds_mod = sys.modules["maya.cmds"]
        shared_pmx = SimpleNamespace()
        with patch.object(
            cpp_fast_importer, "_require_dx11_for_vp2_ownership"
        ), patch.object(
            Path, "exists", return_value=True
        ), patch.object(
            cmds_mod, "nodeType", return_value="mmdRenderShape"
        ), patch.object(
            cmds_mod, "loadPlugin", create=True
        ), patch.object(
            cmds_mod,
            "mmdFastLoad",
            create=True,
            return_value=["root", "sourceMesh", "renderShape"],
        ), patch.object(
            cpp_fast_importer, "parse_pmx_native", return_value=shared_pmx
        ) as parse_native:
            result = fast_import(
                "model.pmx",
                base_name="demo",
                mesh_only=True,
                include_morphs=True,
                vp2_ownership=True,
            )

        self.assertEqual(result, "root")
        basic_materials.assert_called_once()
        self.assertIs(basic_materials.call_args.kwargs["native_pmx"], shared_pmx)
        root_metadata.assert_called_once()
        morph_metadata.assert_called_once()
        self.assertIs(morph_metadata.call_args.kwargs["native_pmx"], shared_pmx)
        material_runtime.assert_called_once()
        self.assertEqual(material_runtime.call_args.args[:2], ("model.pmx", "root"))
        self.assertIs(material_runtime.call_args.kwargs["native_pmx"], shared_pmx)
        self.assertEqual(
            material_runtime.call_args.kwargs["blend_shape_nodes"],
            ["sourceBlendShape"],
        )
        self.assertEqual(parse_native.call_count, 1)

    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_material_morph_runtime")
    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_morph_metadata", return_value=[])
    @patch("mmd_tools.io.cpp_fast_importer._apply_fast_root_metadata")
    @patch("mmd_tools.io.cpp_fast_importer._apply_basic_materials", return_value=None)
    @patch("mmd_tools.io.cpp_fast_importer._candidate_plugin_paths")
    @patch("mmd_tools.io.cpp_fast_importer._setup_plugin_directory")
    def test_vp2_material_runtime_failure_cleans_owned_nodes_and_raises(
        self,
        _setup,
        candidates,
        _basic_materials,
        _root_metadata,
        _morph_metadata,
        material_runtime,
    ):
        import sys

        plugin_path = Path("fake_plugin_dir") / "mmd_tools_cpp.mll"
        candidates.return_value = [plugin_path]
        material_runtime.return_value = {
            "success": False,
            "material_morph_nodes": ["materialMorph0"],
            "material_morph_graph": {"evaluator_nodes": ["materialEval0"]},
            "morph_controller": None,
            "skipped": ["create_failed:shader"],
        }
        cmds_mod = sys.modules["maya.cmds"]

        def node_type(node):
            return "mmdMorphController" if node == "partialController" else "mmdRenderShape"

        with patch.object(
            cpp_fast_importer, "_require_dx11_for_vp2_ownership"
        ), patch.object(
            Path, "exists", return_value=True
        ), patch.object(
            cmds_mod, "loadPlugin", create=True
        ), patch.object(
            cmds_mod,
            "mmdFastLoad",
            create=True,
            return_value=["root", "sourceMesh", "renderShape"],
        ), patch.object(
            cmds_mod, "nodeType", side_effect=node_type
        ), patch.object(
            cmds_mod, "listConnections", return_value=["partialController"]
        ), patch.object(
            cmds_mod, "objExists", return_value=True
        ), patch.object(
            cmds_mod, "delete"
        ) as delete, patch(
            "mmd_tools.core.model_registry.ensure_model_registry",
            return_value="registry0",
        ):
            with self.assertRaisesRegex(
                RuntimeError, "Fast VP2 material morph runtime failed"
            ):
                fast_import("model.pmx", include_morphs=True, vp2_ownership=True)

        deleted = [entry[0][0] for entry in delete.call_args_list]
        self.assertEqual(
            deleted,
            ["materialMorph0", "materialEval0", "partialController", "registry0", "root"],
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


class TestFastDagOrganization(unittest.TestCase):
    """Visible FastLoad model hierarchy stays at the Python importer boundary."""

    class _Cmds:
        def __init__(self):
            self.calls = []

        def ls(self, node, long=False):
            self.calls.append(("ls", node, long))
            return [f"|{node}"] if long else [node]

        def group(self, **kwargs):
            self.calls.append(("group", kwargs))
            if kwargs.get("parent"):
                return f"{kwargs['parent']}|{kwargs['name']}"
            return kwargs["name"]

        def setAttr(self, plug, value):
            self.calls.append(("setAttr", plug, value))

        def rename(self, node, name):
            self.calls.append(("rename", node, name))
            return name

        def parent(self, node, parent, absolute=False):
            self.calls.append(("parent", node, parent, absolute))
            return [f"{parent}|{node}"]

        def listRelatives(self, node, **kwargs):
            self.calls.append(("listRelatives", node, kwargs))
            return ["|Hero_Model_root|Geometry|Hero_Model_mesh|Hero_Model_meshShape"]

    def test_organize_places_source_and_vp2_proxy_owner_under_geometry(self):
        cmds = self._Cmds()
        root, mesh = _organize_fast_dag(
            "fast_source",
            "fast_sourceShape",
            {"metadata": {"name": "モデル", "englishName": "Hero Model"}},
            "fallback",
            cmds,
        )

        self.assertEqual(root, "Hero_Model_root")
        self.assertEqual(mesh, "|Hero_Model_root|Geometry|Hero_Model_mesh|Hero_Model_meshShape")
        self.assertIn(("group", {"empty": True, "name": "Hero_Model_root"}), cmds.calls)
        self.assertIn(
            ("group", {"empty": True, "name": "Geometry", "parent": "Hero_Model_root"}),
            cmds.calls,
        )
        self.assertIn(("setAttr", "Hero_Model_root|Geometry.inheritsTransform", False), cmds.calls)
        self.assertIn(("rename", "|fast_source", "Hero_Model_mesh"), cmds.calls)
        self.assertIn(
            ("parent", "Hero_Model_mesh", "Hero_Model_root|Geometry", True),
            cmds.calls,
        )

    def test_scene_name_prefers_english_header_and_falls_back_to_command_name(self):
        self.assertEqual(
            _fast_model_scene_name(
                {"metadata": {"name": "モデル", "englishName": "Hero Model"}}, "fallback"
            ),
            "Hero_Model",
        )
        self.assertEqual(_fast_model_scene_name(None, "fallback model"), "fallback_model")

    def test_proxy_is_separated_before_morph_transform_duplication(self):
        cmds = self._Cmds()
        original_relatives = cmds.listRelatives
        cmds.listRelatives = lambda node, **kwargs: (
            ["|Hero_Model_root|Geometry|Hero_Model_mesh|proxy"]
            if kwargs.get("type") == "mmdRenderShape"
            else original_relatives(node, **kwargs)
        )
        cmds.parent = MagicMock(return_value=["proxy"])
        cmds.connectAttr = MagicMock()
        _organize_fast_dag(
            "fast_source", "fast_sourceShape",
            {"metadata": {"englishName": "Hero Model"}}, "fallback", cmds,
            render_shape="|fast_source|proxy",
        )
        cmds.parent.assert_any_call(
            "|Hero_Model_root|Geometry|Hero_Model_mesh|proxy",
            "Hero_Model_root|Geometry|Hero_Model_mesh_render", shape=True, relative=True,
        )
        cmds.connectAttr.assert_any_call(
            "Hero_Model_mesh.worldMatrix[0]",
            "Hero_Model_root|Geometry|Hero_Model_mesh_render.offsetParentMatrix",
        )
        cmds.connectAttr.assert_any_call(
            "Hero_Model_mesh.visibility",
            "Hero_Model_root|Geometry|Hero_Model_mesh_render.visibility",
        )


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
            "trying current native PMX parser"
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
