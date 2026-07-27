"""MMD import entry point option handling tests (pure Python, Maya-free)."""

import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.settings import settings  # noqa: E402
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

    def test_pmx_import_requires_native_parse_by_default(self):
        parsed_data = object()
        self._saved_require_native = settings.get("import.native.require_native_pmx_parse")
        settings.set("import.native.require_native_pmx_parse", True)
        try:
            with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data) as parse_file:
                with patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file", return_value="model_root"):
                    result = import_mmd_file("model.pmx", options={})
        finally:
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
                result = import_mmd_file("motion.vmd", options={}, progress_callback=progress_callback)

        self.assertTrue(result)
        self.assertIs(importer.call_args.kwargs["progress_callback"], progress_callback)
        self.assertIn(5, progress)
        self.assertIn(12, progress)

    def test_progress_callback_error_does_not_abort_import(self):
        parsed_data = object()

        def broken_progress(_value):
            raise RuntimeError("progress sink failed")

        with patch("mmd_tools.io.mmd_importer.parse_mmd_file", return_value=parsed_data):
            with patch("mmd_tools.io.mmd_importer.pmx_importer.import_pmx_file", return_value="model_root"):
                result = import_mmd_file("model.pmx", options={}, progress_callback=broken_progress)

        self.assertEqual(result, "model_root")


if __name__ == "__main__":
    unittest.main()
