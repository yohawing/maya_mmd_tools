"""MMD import entry point option handling tests (pure Python, Maya-free)."""

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.settings import settings  # noqa: E402
from mmd_tools.core.exceptions import MMDImportException  # noqa: E402
from mmd_tools.io.mmd_importer import _scoped_settings_override, import_mmd_file  # noqa: E402

_ALL_KEYS = (
    "import.model.separate_meshes_by_material",
    "import.model.auto_resolve_textures",
    "import.model.disable_backface_culling",
    "import.model.uv_set_name",
    "import.model.texture_search_path",
    "import.rig.add_semi_standard_bones",
    "import.morph.import_morphs",
    "import.naming.translate_names",
)


class TestScopedSettingsOverride(unittest.TestCase):
    def setUp(self):
        self._saved = {k: settings.get(k) for k in _ALL_KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            settings.set(k, v)

    def test_settings_forced_inside_context(self):
        settings.set("import.model.separate_meshes_by_material", True)
        settings.set("import.model.auto_resolve_textures", True)
        settings.set("import.model.disable_backface_culling", False)
        settings.set("import.model.uv_set_name", "myUV")
        settings.set("import.rig.add_semi_standard_bones", True)
        settings.set("import.morph.import_morphs", True)
        settings.set("import.naming.translate_names", False)

        options = {
            "separate_meshes_by_material": False,
            "auto_resolve_textures": False,
            "disable_backface_culling": True,
            "uv_set_name": "map#",
            "texture_search_path": "",
            "add_semi_standard_bones": False,
            "import_morphs": False,
            "translate_names": True,
        }

        with _scoped_settings_override(options):
            self.assertFalse(settings.get("import.model.separate_meshes_by_material"))
            self.assertFalse(settings.get("import.model.auto_resolve_textures"))
            self.assertTrue(settings.get("import.model.disable_backface_culling"))
            self.assertEqual(settings.get("import.model.uv_set_name"), "map#")
            self.assertEqual(settings.get("import.model.texture_search_path"), "")
            self.assertFalse(settings.get("import.rig.add_semi_standard_bones"))
            self.assertFalse(settings.get("import.morph.import_morphs"))
            self.assertTrue(settings.get("import.naming.translate_names"))

    def test_original_values_restored_after_context(self):
        settings.set("import.model.separate_meshes_by_material", True)
        settings.set("import.rig.add_semi_standard_bones", True)
        settings.set("import.naming.translate_names", False)

        options = {
            "separate_meshes_by_material": False,
            "add_semi_standard_bones": False,
            "translate_names": True,
        }

        with _scoped_settings_override(options):
            pass

        self.assertTrue(settings.get("import.model.separate_meshes_by_material"))
        self.assertTrue(settings.get("import.rig.add_semi_standard_bones"))
        self.assertFalse(settings.get("import.naming.translate_names"))

    def test_original_values_restored_after_exception(self):
        settings.set("import.model.disable_backface_culling", False)

        options = {"disable_backface_culling": True}

        with self.assertRaises(RuntimeError):
            with _scoped_settings_override(options):
                self.assertTrue(settings.get("import.model.disable_backface_culling"))
                raise RuntimeError("simulated error")

        self.assertFalse(settings.get("import.model.disable_backface_culling"))

    def test_only_keys_in_options_are_overridden(self):
        settings.set("import.model.separate_meshes_by_material", True)
        settings.set("import.rig.add_semi_standard_bones", True)

        options = {"separate_meshes_by_material": False}

        with _scoped_settings_override(options):
            self.assertFalse(settings.get("import.model.separate_meshes_by_material"))
            self.assertTrue(settings.get("import.rig.add_semi_standard_bones"))

    def test_empty_options_changes_nothing(self):
        settings.set("import.model.uv_set_name", "customUV")

        with _scoped_settings_override({}):
            self.assertEqual(settings.get("import.model.uv_set_name"), "customUV")

    def test_unknown_option_keys_are_ignored(self):
        options = {"nonexistent_key": "value", "another_unknown": 42}
        with _scoped_settings_override(options):
            pass  # should not raise


class TestImportMmdFileScalePrecedence(unittest.TestCase):
    def setUp(self):
        self._saved_scale = settings.get("import.general.scale_factor")

    def tearDown(self):
        settings.set("import.general.scale_factor", self._saved_scale)

    def _assert_model_import_scale(self, extension, importer_patch, expected_scale, **kwargs):
        parsed_data = object()

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data):
            with patch(importer_patch, return_value="model_root") as import_model:
                result = import_mmd_file(f"model{extension}", **kwargs)

        self.assertEqual(result, "model_root")
        self.assertEqual(import_model.call_args.args[0], parsed_data)
        self.assertEqual(import_model.call_args.args[2], expected_scale)

    def test_explicit_scale_argument_overrides_options_and_settings_for_pmx(self):
        settings.set("import.general.scale_factor", 3.0)

        self._assert_model_import_scale(
            ".pmx",
            "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file",
            2.0,
            scale=2.0,
            options={"scale": 4.0},
        )

    def test_explicit_scale_argument_overrides_options_and_settings_for_pmd(self):
        settings.set("import.general.scale_factor", 3.0)

        self._assert_model_import_scale(
            ".pmd",
            "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file",
            2.0,
            scale=2.0,
            options={"scale": 4.0},
        )

    def test_options_scale_overrides_settings_for_model_imports(self):
        settings.set("import.general.scale_factor", 3.0)

        for extension, importer_patch in (
            (".pmx", "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file"),
            (".pmd", "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file"),
        ):
            with self.subTest(extension=extension):
                self._assert_model_import_scale(
                    extension,
                    importer_patch,
                    4.0,
                    options={"scale": 4.0},
                )

    def test_policy_scale_is_used_when_no_explicit_scale_is_given(self):
        """No scale= / options.scale → mode-aware policy (normal mode forces 1.0)."""
        self._saved_dev = settings.get("ui.general.development_mode", False)
        try:
            settings.set("import.general.scale_factor", 3.0)
            settings.set("ui.general.development_mode", False)

            for extension, importer_patch in (
                (".pmx", "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file"),
                (".pmd", "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file"),
            ):
                with self.subTest(extension=extension, mode="normal"):
                    self._assert_model_import_scale(
                        extension,
                        importer_patch,
                        1.0,
                        options={},
                    )

            settings.set("ui.general.development_mode", True)
            for extension, importer_patch in (
                (".pmx", "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file"),
                (".pmd", "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file"),
            ):
                with self.subTest(extension=extension, mode="dev"):
                    self._assert_model_import_scale(
                        extension,
                        importer_patch,
                        3.0,
                        options={},
                    )
        finally:
            settings.set("ui.general.development_mode", self._saved_dev)
    def test_explicit_scale_kwarg_remains_public_override(self):
        """scale= is an intentional public API override even in normal mode."""
        self._saved_dev = settings.get("ui.general.development_mode", False)
        try:
            settings.set("ui.general.development_mode", False)
            settings.set("import.general.scale_factor", 9.0)
            self._assert_model_import_scale(
                ".pmx",
                "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file",
                2.0,
                scale=2.0,
                options={},
            )
        finally:
            settings.set("ui.general.development_mode", self._saved_dev)

    def test_pmx_import_forwards_required_native_parse_option(self):
        parsed_data = object()
        options = {"require_native_pmx_parse": True}

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data) as parse_file:
            with patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file", return_value="model_root"):
                result = import_mmd_file("model.pmx", options=options)

        self.assertEqual(result, "model_root")
        parse_file.assert_called_once_with(
            "model.pmx",
            use_native_pmx_parse=None,
            require_native_pmx_parse=True,
        )

    def test_pmd_import_forwards_pmd_material_semantics(self):
        parsed_data = object()

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data):
            with patch(
                "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file",
                return_value="model_root",
            ) as importer:
                result = import_mmd_file("model.pmd", options={})

        self.assertEqual(result, "model_root")
        self.assertTrue(importer.call_args.kwargs["is_pmd"])

    def test_pmx_import_requires_native_parse_by_default(self):
        parsed_data = object()
        self._saved_dev = settings.get("ui.general.development_mode", False)
        self._saved_require_native = settings.get("import.native.require_native_pmx_parse")
        try:
            settings.set("ui.general.development_mode", True)
            settings.set("import.native.require_native_pmx_parse", True)
            with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data) as parse_file:
                with patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file", return_value="model_root"):
                    result = import_mmd_file("model.pmx", options={})
        finally:
            settings.set("ui.general.development_mode", self._saved_dev)
            settings.set("import.native.require_native_pmx_parse", self._saved_require_native)

        self.assertEqual(result, "model_root")
        parse_file.assert_called_once_with(
            "model.pmx",
            use_native_pmx_parse=None,
            require_native_pmx_parse=True,
        )

    def test_progress_callback_is_forwarded_to_model_importer(self):
        parsed_data = object()
        progress = []
        progress_callback = progress.append

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data):
            with patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file", return_value="model_root") as importer:
                result = import_mmd_file("model.pmx", options={}, progress_callback=progress_callback)

        self.assertEqual(result, "model_root")
        self.assertIs(importer.call_args.kwargs["progress_callback"], progress_callback)
        self.assertIn(5, progress)
        self.assertIn(12, progress)

    def test_progress_callback_is_forwarded_to_vmd_importer(self):
        parsed_data = object()
        progress = []
        progress_callback = progress.append

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data):
            with patch("mmd_tools.io.mmd_importer.vmd_importer.import_vmd_file", return_value=True) as importer:
                result = import_mmd_file(
                    "motion.vmd",
                    options={"target_model": "model_root"},
                    progress_callback=progress_callback,
                )

        self.assertTrue(result)
        self.assertIs(importer.call_args.kwargs["progress_callback"], progress_callback)
        self.assertIn(5, progress)
        self.assertIn(12, progress)

    def test_camera_only_vmd_automatically_uses_scene_animation_route(self):
        parsed_data = SimpleNamespace(
            bone_frames=[],
            morph_frames=[],
            ik_show_hide_frames=[],
            camera_frames=[object()],
            light_frames=[],
        )
        options = {"target_model": "current_model"}

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data), patch(
            "mmd_tools.io.mmd_importer.vmd_importer.import_vmd_file",
            return_value=True,
        ) as importer:
            result = import_mmd_file("camera.vmd", options=options)

        self.assertTrue(result)
        self.assertTrue(options["scene_animation_only"])
        self.assertNotIn("target_model", options)
        self.assertIs(importer.call_args.args[0], parsed_data)

    def test_light_only_vmd_needs_no_current_model(self):
        parsed_data = SimpleNamespace(
            bone_frames=[],
            morph_frames=[],
            ik_show_hide_frames=[],
            camera_frames=[],
            light_frames=[object()],
        )
        options = {}

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data), patch(
            "mmd_tools.io.mmd_importer.vmd_importer.import_vmd_file",
            return_value=True,
        ):
            result = import_mmd_file("light.vmd", options=options)

        self.assertTrue(result)
        self.assertTrue(options["scene_animation_only"])

    def test_model_vmd_without_current_model_reports_explicit_error(self):
        parsed_data = SimpleNamespace(
            bone_frames=[object()],
            morph_frames=[],
            ik_show_hide_frames=[],
            camera_frames=[],
            light_frames=[],
        )

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data), patch(
            "mmd_tools.io.mmd_importer.vmd_importer.import_vmd_file"
        ) as importer, self.assertRaisesRegex(MMDImportException, "requires a current model"):
            import_mmd_file("model_motion.vmd", options={})

        importer.assert_not_called()

    def test_mixed_vmd_keeps_current_model_route(self):
        parsed_data = SimpleNamespace(
            bone_frames=[object()],
            morph_frames=[],
            ik_show_hide_frames=[],
            camera_frames=[object()],
            light_frames=[],
        )
        options = {"target_model": "current_model", "scene_animation_only": True}

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data), patch(
            "mmd_tools.io.mmd_importer.vmd_importer.import_vmd_file",
            return_value=True,
        ):
            result = import_mmd_file("mixed.vmd", options=options)

        self.assertTrue(result)
        self.assertFalse(options["scene_animation_only"])
        self.assertEqual(options["target_model"], "current_model")

    def test_progress_callback_error_does_not_abort_import(self):
        parsed_data = object()

        def broken_progress(_value):
            raise RuntimeError("progress sink failed")

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data):
            with patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file", return_value="model_root"):
                result = import_mmd_file("model.pmx", options={}, progress_callback=broken_progress)

        self.assertEqual(result, "model_root")


class TestModelImportControlRig(unittest.TestCase):
    """Opt-in Control Rig creation is shared by every model import route."""

    def test_python_model_import_builds_control_rig_once(self):
        parsed_data = object()
        profile = {}
        build_result = SimpleNamespace(
            created=True,
            control_group="Controls",
            selection_set="Controls_SET",
            state="ATTACHED",
            owner="MMD_OWNED",
            controls={"center": "center_CTRL"},
        )

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data):
            with patch(
                "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file",
                return_value="python_root",
            ):
                with patch(
                    "mmd_tools.io.mmd_importer.build_mmd_control_rig",
                    return_value=build_result,
                ) as build, patch(
                    "mmd_tools.io.mmd_importer.enter_mmd_control_rig_edit",
                    return_value={"state": "EDIT", "owner": "CONTROL_OWNED"},
                ) as bind:
                    options = {"create_mmd_control_rig": True, "profile": profile}
                    result = import_mmd_file("model.pmx", options=options)

        self.assertEqual(result, "python_root")
        build.assert_called_once_with("python_root")
        bind.assert_called_once_with("python_root")
        self.assertEqual(profile["mmd_control_rig"]["succeeded"], True)
        self.assertEqual(profile["mmd_control_rig"]["created"], True)
        self.assertEqual(profile["mmd_control_rig"]["bound"], True)
        self.assertEqual(profile["mmd_control_rig"]["state"], "EDIT")
        self.assertEqual(profile["mmd_control_rig"]["owner"], "CONTROL_OWNED")
        self.assertEqual(profile["mmd_control_rig"]["control_count"], 1)

    def test_cpp_model_import_uses_the_same_post_import_builder(self):
        with patch("mmd_tools.io.mmd_importer.fast_import", return_value="cpp_root") as fast:
            with patch("mmd_tools.io.mmd_importer.parse_mmd_file") as parse_file:
                with patch("mmd_tools.io.mmd_importer.build_mmd_control_rig") as build, patch(
                    "mmd_tools.io.mmd_importer.enter_mmd_control_rig_edit",
                    return_value={"state": "EDIT", "owner": "CONTROL_OWNED"},
                ) as bind:
                    options = {
                        "create_mmd_control_rig": True,
                        "cpp_fast_load_mesh_only": True,
                    }
                    result = import_mmd_file(
                        "model.pmx",
                        options={**options, "use_cpp_fast_load": True},
                    )

        self.assertEqual(result, "cpp_root")
        self.assertEqual(fast.call_args.kwargs["mesh_only"], False)
        build.assert_called_once_with("cpp_root")
        bind.assert_called_once_with("cpp_root")
        parse_file.assert_not_called()

    def test_disabled_option_does_not_build_for_python_model(self):
        parsed_data = object()
        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data):
            with patch(
                "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file",
                return_value="model_root",
            ):
                with patch("mmd_tools.io.mmd_importer.build_mmd_control_rig") as build, patch(
                    "mmd_tools.io.mmd_importer.enter_mmd_control_rig_edit"
                ) as bind:
                    result = import_mmd_file("model.pmd", options={})

        self.assertEqual(result, "model_root")
        build.assert_not_called()
        bind.assert_not_called()

    def test_builder_failure_preserves_model_and_records_partial_warning(self):
        parsed_data = object()
        profile = {}
        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data):
            with patch(
                "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file",
                return_value="model_root",
            ):
                with patch(
                    "mmd_tools.io.mmd_importer.build_mmd_control_rig",
                    side_effect=RuntimeError("missing role binding"),
                ), patch("mmd_tools.io.mmd_importer.enter_mmd_control_rig_edit") as bind:
                    options = {"create_mmd_control_rig": True, "profile": profile}
                    result = import_mmd_file("model.pmx", options=options)

        self.assertEqual(result, "model_root")
        self.assertEqual(profile["mmd_control_rig"]["succeeded"], False)
        self.assertEqual(profile["mmd_control_rig"]["error"], "missing role binding")
        warning = profile["warnings"][0]
        self.assertEqual(warning["source"], "mmd_importer")
        self.assertEqual(warning["code"], "control_rig_create_failed")
        self.assertEqual(warning["model_root"], "model_root")
        self.assertEqual(warning["severity"], "warning")
        self.assertEqual(warning["fallback"], "model_imported_without_control_rig")
        bind.assert_not_called()

    def test_bind_failure_preserves_created_attached_rig_and_records_warning(self):
        parsed_data = object()
        profile = {}
        build_result = SimpleNamespace(
            created=True,
            control_group="Controls",
            selection_set="Controls_SET",
            state="ATTACHED",
            owner="MMD_OWNED",
            controls={"center": "center_CTRL"},
        )
        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data), patch(
            "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file",
            return_value="model_root",
        ), patch(
            "mmd_tools.io.mmd_importer.build_mmd_control_rig",
            return_value=build_result,
        ), patch(
            "mmd_tools.io.mmd_importer.enter_mmd_control_rig_edit",
            side_effect=RuntimeError("bind route failed"),
        ):
            options = {"create_mmd_control_rig": True, "profile": profile}
            result = import_mmd_file("model.pmx", options=options)

        self.assertEqual(result, "model_root")
        rig_profile = profile["mmd_control_rig"]
        self.assertFalse(rig_profile["succeeded"])
        self.assertTrue(rig_profile["created"])
        self.assertFalse(rig_profile["bound"])
        self.assertEqual(rig_profile["state"], "ATTACHED")
        warning = profile["warnings"][0]
        self.assertEqual(warning["code"], "control_rig_bind_failed")
        self.assertEqual(warning["fallback"], "model_imported_with_attached_control_rig")


class TestUVEditorRefreshAfterModelImport(unittest.TestCase):
    """Ensure model imports schedule one deferred UV cache refresh."""

    def _run_model_import(self, extension):
        deferred_callbacks = []
        parsed_data = object()
        importer_patch = "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file"

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data), patch(
            importer_patch,
            return_value="model_root",
        ), patch("maya.utils.executeDeferred", side_effect=deferred_callbacks.append) as execute_deferred:
            result = import_mmd_file(f"model{extension}", options={})

        self.assertEqual(result, "model_root")
        execute_deferred.assert_called_once()
        self.assertEqual(len(deferred_callbacks), 1)
        return deferred_callbacks[0]

    def test_pmx_and_pmd_each_schedule_one_refresh_callback(self):
        for extension in (".pmx", ".pmd"):
            with self.subTest(extension=extension), patch(
                "maya.cmds.getPanel",
                return_value=["uvEditorA", "uvEditorB"],
            ) as get_panel, patch("maya.cmds.textureWindow") as texture_window:
                refresh = self._run_model_import(extension)
                refresh()

            get_panel.assert_called_once_with(type="polyTexturePlacementPanel")
            texture_window.assert_has_calls(
                [
                    call("uvEditorA", edit=True, forceRebake=True),
                    call("uvEditorA", edit=True, refresh=True),
                    call("uvEditorB", edit=True, forceRebake=True),
                    call("uvEditorB", edit=True, refresh=True),
                ]
            )
            self.assertEqual(texture_window.call_count, 4)

    def test_failed_model_import_does_not_schedule_refresh(self):
        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=object()), patch(
            "mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file",
            side_effect=RuntimeError("import failed"),
        ), patch("maya.utils.executeDeferred") as execute_deferred:
            with self.assertRaises(MMDImportException):
                import_mmd_file("model.pmx", options={})

        execute_deferred.assert_not_called()

if __name__ == "__main__":
    unittest.main()
