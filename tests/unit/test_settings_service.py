"""SettingsService の設定 I/O と option dict 構築を検証する。"""

import copy
import os
import sys
import unittest
from unittest.mock import mock_open, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mmd_tools.services.settings_service import SettingsService  # noqa: E402


class _FakeSettingsStore:
    def __init__(self):
        self.data = {
            "import": {
                "general": {"scale_factor": 2.0, "use_namespace": True},
                "model": {
                    "import_models": False,
                    "create_mmd_shaders": False,
                    "separate_meshes_by_material": True,
                    "auto_classify_transparency": True,
                    "auto_resolve_textures": True,
                    "disable_backface_culling": False,
                    "uv_set_name": "customUV",
                    "texture_search_path": "/textures",
                    "show_texture_issue_dialog": False,
                },
                "physics": {"import_physics": True},
                "morph": {"import_morphs": False},
                "rig": {"add_semi_standard_bones": True, "bake_mode": False},
                "native": {
                    "use_cpp_fast_load": True,
                    "cpp_fast_load_mesh_only": False,
                    "use_cpp_rig_nodes": True,
                },
                "naming": {"translate_names": False},
                "animation": {
                    "animation_start_frame": 12,
                    "vmd_fps": 60,
                    "import_animations": False,
                    "import_morph_animation": False,
                    "import_camera_animation": False,
                    "import_light_animation": False,
                    "motion_scale": 2.5,
                    "clear_existing_motion": True,
                    "resample_curves": True,
                },
            },
            "export": {"general": {"export_format": "pmd", "apply_scale": False}},
            "logging": {"enabled": False, "level": "ERROR", "log_file_path": "custom.log"},
            "ui": {"general": {"development_mode": False, "language": "en"}},
            "internal": {"ignored": True},
        }
        self.saved = 0
        self.reset_called = 0
        self.set_calls = []

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            try:
                value = value[key]
            except (KeyError, TypeError):
                return default
        return value

    def set(self, key_path, value):
        keys = key_path.split(".")
        target = self.data
        for key in keys[:-1]:
            target = target.setdefault(key, {})
        target[keys[-1]] = value
        self.set_calls.append((key_path, value))
        self.save()

    def save(self):
        self.saved += 1

    def reset(self):
        self.reset_called += 1
        self.save()


class TestSettingsServiceDelegation(unittest.TestCase):
    def setUp(self):
        self.store = _FakeSettingsStore()
        self.service = SettingsService(self.store)

    def test_get_set_save_reset_delegate_to_store(self):
        self.assertEqual(self.service.get("ui.general.language"), "en")

        self.service.set("ui.general.language", "ja")
        self.service.save()
        self.service.reset()

        self.assertEqual(self.service.get("ui.general.language"), "ja")
        self.assertIn(("ui.general.language", "ja"), self.store.set_calls)
        self.assertEqual(self.store.saved, 3)
        self.assertEqual(self.store.reset_called, 1)

    def test_set_development_mode_log_levels_sets_logging_key(self):
        level = self.service.set_development_mode_log_levels(True)

        self.assertEqual(level, "INFO")
        self.assertEqual(self.service.get("logging.level"), "INFO")

        level = self.service.set_development_mode_log_levels(False)

        self.assertEqual(level, "WARNING")
        self.assertEqual(self.service.get("logging.level"), "WARNING")
        self.assertNotIn("log_level", self.store.data["ui"]["general"])

    def test_load_and_save_settings_tab_state_preserve_keys(self):
        state = self.service.load_settings_tab_state()
        self.assertEqual(
            state,
            {
                "development_mode": False,
                "command_port": 3939,
                "logging_enabled": False,
                "logging_level": "ERROR",
                "log_file_path": "custom.log",
                "language": "en",
            },
        )

        self.service.save_settings_tab_state(
            {
                "development_mode": True,
                "command_port": 7788,
                "logging_enabled": True,
                "logging_level": "INFO",
                "log_file_path": "next.log",
                "language": "ja",
            }
        )

        self.assertTrue(self.service.get("ui.general.development_mode"))
        self.assertEqual(self.service.get("ui.dev.command_port"), 7788)
        self.assertEqual(self.service.get("ui.general.language"), "ja")
        self.assertTrue(self.service.get("logging.enabled"))
        self.assertEqual(self.service.get("logging.level"), "INFO")
        self.assertEqual(self.service.get("logging.log_file_path"), "next.log")


class TestSettingsServiceJson(unittest.TestCase):
    def setUp(self):
        self.store = _FakeSettingsStore()
        self.service = SettingsService(self.store)

    def test_export_settings_data_uses_expected_categories_only(self):
        data = self.service.export_settings_data()

        self.assertEqual(set(data), {"import", "export", "logging", "ui"})
        self.assertNotIn("internal", data)

    def test_write_and_import_settings_json(self):
        path = "settings.json"
        exported_data = copy.deepcopy(self.service.export_settings_data())
        with patch("builtins.open", mock_open()) as mocked_open:
            self.service.write_settings_json(path)

        mocked_open.assert_called_with(path, "w", encoding="utf-8")

        self.store.data["logging"]["level"] = "DEBUG"
        with patch.object(self.service, "read_settings_json", return_value=exported_data):
            self.service.import_settings_json(path)

        self.assertEqual(self.service.get("logging.level"), "ERROR")
        self.assertIn(("logging.level", "ERROR"), self.store.set_calls)


class TestSettingsServiceImportOptions(unittest.TestCase):
    def setUp(self):
        self.store = _FakeSettingsStore()
        self.service = SettingsService(self.store)

    def test_build_pmx_import_options_applies_normal_mode_overrides(self):
        options = self.service.build_pmx_import_options(custom_namespace="ns")

        self.assertEqual(options["scale"], 2.0)
        self.assertTrue(options["use_namespace"])
        self.assertEqual(options["custom_namespace"], "ns")
        self.assertTrue(options["import_models"])
        self.assertFalse(options["import_physics"])
        self.assertFalse(options["separate_meshes_by_material"])
        self.assertNotIn("split_meshes_by_morph_groups", options)
        self.assertNotIn("hide_hidden_geometry", options)
        self.assertFalse(options["auto_classify_transparency"])
        self.assertTrue(options["auto_resolve_textures"])
        self.assertTrue(options["disable_backface_culling"])
        self.assertEqual(options["uv_set_name"], "map#")
        self.assertEqual(options["texture_search_path"], "")
        self.assertFalse(options["add_semi_standard_bones"])
        self.assertTrue(options["translate_names"])
        self.assertNotIn("setup_rig", options)
        self.assertNotIn("setup_bone_orientation", options)
        self.assertTrue(options["use_cpp_fast_load"])
        self.assertFalse(options["cpp_fast_load_mesh_only"])
        self.assertTrue(options["use_cpp_rig_nodes"])

    def test_build_pmx_import_options_preserves_dev_mode_saved_values(self):
        self.service.set("ui.general.development_mode", True)

        options = self.service.build_pmx_import_options()

        self.assertFalse(options["import_models"])
        self.assertTrue(options["import_physics"])
        self.assertTrue(options["separate_meshes_by_material"])
        self.assertNotIn("split_meshes_by_morph_groups", options)
        self.assertNotIn("hide_hidden_geometry", options)
        self.assertTrue(options["auto_classify_transparency"])
        self.assertTrue(options["auto_resolve_textures"])
        self.assertFalse(options["disable_backface_culling"])
        self.assertEqual(options["uv_set_name"], "customUV")
        self.assertEqual(options["texture_search_path"], "/textures")
        self.assertTrue(options["add_semi_standard_bones"])
        self.assertFalse(options["translate_names"])
        self.assertNotIn("setup_rig", options)
        self.assertNotIn("setup_bone_orientation", options)

    def test_build_vmd_import_options_forces_resample_curves_in_normal_mode(self):
        options = self.service.build_vmd_import_options(target_model="model")

        self.assertEqual(options["start_frame"], 12)
        self.assertEqual(options["vmd_fps"], 60)
        self.assertFalse(options["import_bone_animation"])
        self.assertFalse(options["import_morph_animation"])
        self.assertFalse(options["import_camera_animation"])
        self.assertFalse(options["import_light_animation"])
        self.assertEqual(options["motion_scale"], 2.5)
        self.assertTrue(options["clear_existing_motion"])
        self.assertFalse(options["resample_curves"])
        self.assertFalse(options["bake_mode"])
        self.assertEqual(options["target_model"], "model")

    def test_build_vmd_import_options_preserves_resample_curves_in_dev_mode(self):
        self.service.set("ui.general.development_mode", True)

        options = self.service.build_vmd_import_options()

        self.assertTrue(options["resample_curves"])
        self.assertFalse(options["bake_mode"])
        self.assertIsNone(options["target_model"])

    def test_build_export_options_and_texture_dialog_setting(self):
        options = self.service.build_export_options("out.pmx")

        self.assertEqual(options, {"file_path": "out.pmx", "export_format": "pmd", "apply_scale": False})
        self.assertFalse(self.service.should_show_texture_issue_dialog())


if __name__ == "__main__":
    unittest.main()
