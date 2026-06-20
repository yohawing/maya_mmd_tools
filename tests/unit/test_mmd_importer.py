"""_scoped_settings_override のユニットテスト（純Python、Maya不要）。"""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.settings import settings  # noqa: E402
from mmd_tools.io.mmd_importer import _scoped_settings_override  # noqa: E402

_ALL_KEYS = (
    "import.model.separate_meshes_by_material",
    "import.model.split_meshes_by_morph_groups",
    "import.model.auto_classify_transparency",
    "import.model.auto_resolve_textures",
    "import.model.disable_backface_culling",
    "import.model.uv_set_name",
    "import.model.texture_search_path",
    "import.rig.add_semi_standard_bones",
    "import.naming.translate_names",
    "import.model.hide_hidden_geometry",
)


class TestScopedSettingsOverride(unittest.TestCase):
    def setUp(self):
        self._saved = {k: settings.get(k) for k in _ALL_KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            settings.set(k, v)

    def test_settings_forced_inside_context(self):
        settings.set("import.model.separate_meshes_by_material", True)
        settings.set("import.model.auto_classify_transparency", True)
        settings.set("import.model.auto_resolve_textures", True)
        settings.set("import.model.disable_backface_culling", False)
        settings.set("import.model.uv_set_name", "myUV")
        settings.set("import.rig.add_semi_standard_bones", True)
        settings.set("import.naming.translate_names", False)

        options = {
            "separate_meshes_by_material": False,
            "auto_classify_transparency": False,
            "auto_resolve_textures": False,
            "disable_backface_culling": True,
            "uv_set_name": "map#",
            "texture_search_path": "",
            "add_semi_standard_bones": False,
            "translate_names": True,
            "hide_hidden_geometry": False,
        }

        with _scoped_settings_override(options):
            self.assertFalse(settings.get("import.model.separate_meshes_by_material"))
            self.assertFalse(settings.get("import.model.auto_classify_transparency"))
            self.assertFalse(settings.get("import.model.auto_resolve_textures"))
            self.assertTrue(settings.get("import.model.disable_backface_culling"))
            self.assertEqual(settings.get("import.model.uv_set_name"), "map#")
            self.assertEqual(settings.get("import.model.texture_search_path"), "")
            self.assertFalse(settings.get("import.rig.add_semi_standard_bones"))
            self.assertTrue(settings.get("import.naming.translate_names"))
            self.assertFalse(settings.get("import.model.hide_hidden_geometry"))

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


if __name__ == "__main__":
    unittest.main()
