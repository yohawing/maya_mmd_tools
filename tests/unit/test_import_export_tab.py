"""ImportExportTab の Maya 非依存 helper を検証する。"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.import_export_view_state import ImportExportViewState  # noqa: E402
from mmd_tools.ui.tabs import import_export_tab  # noqa: E402


class _FakeSettingsService:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def is_development_mode(self):
        return bool(self.get("ui.general.development_mode", False))

    def resolve_import_scale(self):
        if self.is_development_mode():
            return float(self.get("import.general.scale_factor", 1.0))
        return 1.0

class _FakeWidget:
    def __init__(self):
        self.visible = None
        self.enabled = None

    def setVisible(self, visible):
        self.visible = visible

    def setEnabled(self, enabled):
        self.enabled = enabled


class _FakeCheck(_FakeWidget):
    def __init__(self, checked=False):
        super().__init__()
        self.checked = checked

    def isChecked(self):
        return self.checked

    def setChecked(self, checked):
        self.checked = checked


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _FakeSpinBox:
    def __init__(self, value=1.0):
        self.value = value
        self.enabled = None
        self.blocked = False
        self.block_calls = []
        self.set_value_calls = []

    def blockSignals(self, blocked):
        previous = self.blocked
        self.blocked = blocked
        self.block_calls.append(blocked)
        return previous

    def setValue(self, value):
        self.set_value_calls.append((value, self.blocked))
        self.value = value

    def setEnabled(self, enabled):
        self.enabled = enabled


class _FakeSlider(_FakeSpinBox):
    def __init__(self, value=100):
        super().__init__(value)
        self.visible = None

    def setVisible(self, visible):
        self.visible = visible


class TestImportExportTabDevModeVisibility(unittest.TestCase):
    def test_mmd_control_rig_option_is_not_development_mode_gated(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab._dev_only_widgets = []
        tab.create_mmd_control_rig_check = _FakeWidget()
        tab.create_mmd_control_rig_check.visible = True
        tab.scale_spin = _FakeSpinBox(value=1.0)
        tab.settings_service = _FakeSettingsService({"ui.general.development_mode": False})

        import_export_tab.ImportExportTab._apply_dev_mode_visibility(tab)
        self.assertTrue(tab.create_mmd_control_rig_check.visible)

        tab.settings_service.set("ui.general.development_mode", True)
        import_export_tab.ImportExportTab._apply_dev_mode_visibility(tab)
        self.assertTrue(tab.create_mmd_control_rig_check.visible)

    def test_dev_only_controls_follow_development_mode(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        scale_row = _FakeWidget()
        cpp_rig_nodes_check = _FakeWidget()
        motion_scale_row = _FakeWidget()
        tab._dev_only_widgets = [scale_row, cpp_rig_nodes_check, motion_scale_row]
        tab.scale_spin = _FakeSpinBox(value=2.5)
        tab.settings_service = _FakeSettingsService(
            {
                "ui.general.development_mode": False,
                "import.general.scale_factor": 2.5,
            }
        )

        import_export_tab.ImportExportTab._apply_dev_mode_visibility(tab)
        self.assertFalse(scale_row.visible)
        self.assertFalse(cpp_rig_nodes_check.visible)
        self.assertFalse(motion_scale_row.visible)

        tab.settings_service.set("ui.general.development_mode", True)
        import_export_tab.ImportExportTab._apply_dev_mode_visibility(tab)
        self.assertTrue(scale_row.visible)
        self.assertTrue(cpp_rig_nodes_check.visible)
        self.assertTrue(motion_scale_row.visible)
    def test_normal_mode_hides_scale_row_and_displays_one_without_writing_settings(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        scale_row = _FakeWidget()
        tab._dev_only_widgets = [scale_row]
        tab.scale_spin = _FakeSpinBox(value=2.5)
        tab.settings_service = _FakeSettingsService(
            {
                "ui.general.development_mode": False,
                "import.general.scale_factor": 2.5,
            }
        )

        import_export_tab.ImportExportTab._apply_dev_mode_visibility(tab)

        self.assertFalse(scale_row.visible)
        self.assertEqual(tab.scale_spin.value, 1.0)
        self.assertIn((1.0, True), tab.scale_spin.set_value_calls)
        self.assertEqual(tab.settings_service.get("import.general.scale_factor"), 2.5)

    def test_dev_mode_restores_persisted_scale_into_spin(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        scale_row = _FakeWidget()
        tab._dev_only_widgets = [scale_row]
        tab.scale_spin = _FakeSpinBox(value=1.0)
        tab.settings_service = _FakeSettingsService(
            {
                "ui.general.development_mode": True,
                "import.general.scale_factor": 2.5,
            }
        )

        import_export_tab.ImportExportTab._apply_dev_mode_visibility(tab)

        self.assertTrue(scale_row.visible)
        self.assertEqual(tab.scale_spin.value, 2.5)
        self.assertEqual(tab.settings_service.get("import.general.scale_factor"), 2.5)

    def test_sync_import_scale_control_does_not_emit_value_changed_writes(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.scale_spin = _FakeSpinBox(value=3.0)
        tab.settings_service = _FakeSettingsService(
            {
                "ui.general.development_mode": False,
                "import.general.scale_factor": 3.0,
            }
        )
        writes = []
        original_set = tab.settings_service.set

        def tracking_set(key, value):
            writes.append((key, value))
            original_set(key, value)

        tab.settings_service.set = tracking_set

        import_export_tab.ImportExportTab._sync_import_scale_control(tab, is_dev=False)

        self.assertEqual(tab.scale_spin.value, 1.0)
        self.assertEqual(writes, [])
        self.assertEqual(tab.settings_service.get("import.general.scale_factor"), 3.0)


class TestImportExportTabNativePhysicsBakeVisibility(unittest.TestCase):
    def test_native_physics_bake_control_follows_motion_bake(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.native_physics_bake_check = _FakeWidget()

        import_export_tab.ImportExportTab._sync_native_physics_bake_enabled(tab, False)
        self.assertFalse(tab.native_physics_bake_check.enabled)

        import_export_tab.ImportExportTab._sync_native_physics_bake_enabled(tab, True)
        self.assertTrue(tab.native_physics_bake_check.enabled)


class TestImportExportTabCppFastLoadVisibility(unittest.TestCase):
    def test_vp2_ownership_requires_cpp_fast_load(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.use_cpp_vp2_ownership_check = _FakeCheck(checked=True)

        import_export_tab.ImportExportTab._sync_cpp_vp2_ownership_enabled(tab, False)

        self.assertFalse(tab.use_cpp_vp2_ownership_check.enabled)
        self.assertFalse(tab.use_cpp_vp2_ownership_check.checked)

        tab.use_cpp_vp2_ownership_check.checked = True
        import_export_tab.ImportExportTab._sync_cpp_vp2_ownership_enabled(tab, True)

        self.assertTrue(tab.use_cpp_vp2_ownership_check.enabled)
        self.assertTrue(tab.use_cpp_vp2_ownership_check.checked)


class TestImportExportTabReducedBakeVisibility(unittest.TestCase):
    def test_reduce_bake_keys_control_follows_motion_bake(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.reduce_bake_keys_check = _FakeWidget()

        import_export_tab.ImportExportTab._sync_reduce_bake_keys_enabled(tab, False)
        self.assertFalse(tab.reduce_bake_keys_check.enabled)
        import_export_tab.ImportExportTab._sync_reduce_bake_keys_enabled(tab, True)
        self.assertTrue(tab.reduce_bake_keys_check.enabled)

    def test_quality_control_reloads_persisted_quality_without_writing(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.reduce_quality_slider = _FakeSlider(value=100)
        tab.reduce_quality_value_label = _FakeLabel()
        tab.settings_service = _FakeSettingsService(
            {
                "ui.general.development_mode": False,
                "import.animation.reduce_quality": 0.333,
            }
        )

        import_export_tab.ImportExportTab._sync_reduce_bake_quality_control(tab)

        self.assertEqual(tab.reduce_quality_slider.value, 33)
        self.assertEqual(tab.reduce_quality_value_label.text, "0.33")
        self.assertEqual(tab.settings_service.get("import.animation.reduce_quality"), 0.333)

    def test_quality_control_clamps_saved_quality(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.reduce_quality_slider = _FakeSlider(value=100)
        tab.reduce_quality_value_label = _FakeLabel()
        tab.settings_service = _FakeSettingsService(
            {"import.animation.reduce_quality": -2.0}
        )

        import_export_tab.ImportExportTab._sync_reduce_bake_quality_control(tab)

        self.assertEqual(tab.reduce_quality_slider.value, 0)
        self.assertEqual(tab.reduce_quality_value_label.text, "0.00")

    def test_quality_slider_persists_01_grid_value(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.settings_service = _FakeSettingsService()
        tab.reduce_quality_value_label = _FakeLabel()

        import_export_tab.ImportExportTab._on_reduce_quality_changed(tab, 33)

        self.assertEqual(tab.settings_service.get("import.animation.reduce_quality"), 0.33)
        self.assertEqual(tab.reduce_quality_value_label.text, "0.33")

    def test_quality_control_visible_only_for_bake_and_reduction(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.bake_mode_check = _FakeWidget()
        tab.reduce_bake_keys_check = _FakeWidget()
        tab.reduce_quality_row = _FakeWidget()
        tab.reduce_quality_slider = _FakeSlider()

        tab.bake_mode_check.isChecked = lambda: False
        tab.reduce_bake_keys_check.isChecked = lambda: True
        import_export_tab.ImportExportTab._sync_reduce_bake_quality_enabled(tab)
        self.assertFalse(tab.reduce_quality_slider.enabled)
        self.assertFalse(tab.reduce_quality_row.visible)

        tab.bake_mode_check.isChecked = lambda: True
        tab.reduce_bake_keys_check.isChecked = lambda: False
        import_export_tab.ImportExportTab._sync_reduce_bake_quality_enabled(tab)
        self.assertFalse(tab.reduce_quality_slider.enabled)
        self.assertFalse(tab.reduce_quality_row.visible)

        tab.reduce_bake_keys_check.isChecked = lambda: True
        import_export_tab.ImportExportTab._sync_reduce_bake_quality_enabled(tab)
        self.assertTrue(tab.reduce_quality_slider.enabled)
        self.assertTrue(tab.reduce_quality_row.visible)

    def test_rotation_time_curve_requires_direct_control_rig_import(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.create_mmd_control_rig_check = _FakeWidget()
        tab.bake_mode_check = _FakeWidget()
        tab.vmd_rotation_time_curve_check = _FakeWidget()

        tab.create_mmd_control_rig_check.isChecked = lambda: False
        tab.bake_mode_check.isChecked = lambda: False
        import_export_tab.ImportExportTab._sync_vmd_rotation_time_curve_enabled(tab)
        self.assertFalse(tab.vmd_rotation_time_curve_check.enabled)

        tab.create_mmd_control_rig_check.isChecked = lambda: True
        import_export_tab.ImportExportTab._sync_vmd_rotation_time_curve_enabled(tab)
        self.assertTrue(tab.vmd_rotation_time_curve_check.enabled)

        tab.bake_mode_check.isChecked = lambda: True
        import_export_tab.ImportExportTab._sync_vmd_rotation_time_curve_enabled(tab)
        self.assertFalse(tab.vmd_rotation_time_curve_check.enabled)


class _FakeQSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class TestImportExportViewState(unittest.TestCase):
    def test_load_history_filters_missing_paths_and_invalid_json(self):
        with TemporaryDirectory() as temp_dir:
            existing_path = str(Path(temp_dir) / "model.pmx")
            missing_path = str(Path(temp_dir) / "missing.pmx")
            Path(existing_path).write_text("", encoding="utf-8")
            store = _FakeQSettings(
                {
                    "history": json.dumps([existing_path, missing_path, None, 123]),
                    "invalid": "{",
                }
            )
            view_state = ImportExportViewState(store)

            self.assertEqual(view_state.load_history("history"), [existing_path])
            self.assertEqual(view_state.load_history("invalid"), [])

    def test_save_history_deduplicates_existing_file_paths(self):
        with TemporaryDirectory() as temp_dir:
            first_path = str(Path(temp_dir) / "first.pmx")
            second_path = str(Path(temp_dir) / "second.pmx")
            Path(first_path).write_text("", encoding="utf-8")
            Path(second_path).write_text("", encoding="utf-8")
            store = _FakeQSettings({"history": json.dumps([first_path, second_path])})
            view_state = ImportExportViewState(store)

            view_state.save_history("history", second_path)

            self.assertEqual(view_state.load_history("history"), [second_path, first_path])

    def test_clear_histories_writes_empty_json_arrays(self):
        store = _FakeQSettings({"a": "[1]", "b": "[2]"})
        view_state = ImportExportViewState(store)

        view_state.clear_histories(("a", "b"))

        self.assertEqual(store.values["a"], "[]")
        self.assertEqual(store.values["b"], "[]")

    def test_unified_history_is_newest_first_across_file_types(self):
        with TemporaryDirectory() as temp_dir:
            model_path = str(Path(temp_dir) / "model.pmx")
            motion_path = str(Path(temp_dir) / "motion.vmd")
            export_path = str(Path(temp_dir) / "export.pmx")
            for path in (model_path, motion_path, export_path):
                Path(path).write_text("", encoding="utf-8")
            view_state = ImportExportViewState(_FakeQSettings())

            view_state.save_file_history("import", model_path)
            view_state.save_file_history("export", export_path)
            view_state.save_file_history("vmd", motion_path)

            self.assertEqual(
                view_state.load_file_history(),
                [
                    {"path": motion_path, "type": "vmd"},
                    {"path": export_path, "type": "export"},
                    {"path": model_path, "type": "import"},
                ],
            )

    def test_unified_history_deduplicates_same_typed_path_and_honors_limit(self):
        with TemporaryDirectory() as temp_dir:
            first_path = str(Path(temp_dir) / "first.pmx")
            second_path = str(Path(temp_dir) / "second.pmx")
            for path in (first_path, second_path):
                Path(path).write_text("", encoding="utf-8")
            view_state = ImportExportViewState(_FakeQSettings())

            view_state.save_file_history("import", first_path)
            view_state.save_file_history("import", second_path)
            view_state.save_file_history("import", first_path)

            self.assertEqual(
                view_state.load_file_history(max_items=1),
                [{"path": first_path, "type": "import"}],
            )

    def test_legacy_histories_migrate_and_clear_with_unified_history(self):
        with TemporaryDirectory() as temp_dir:
            model_path = str(Path(temp_dir) / "model.pmx")
            motion_path = str(Path(temp_dir) / "motion.vmd")
            generic_motion_path = str(Path(temp_dir) / "generic_motion.vmd")
            Path(model_path).write_text("", encoding="utf-8")
            Path(motion_path).write_text("", encoding="utf-8")
            Path(generic_motion_path).write_text("", encoding="utf-8")
            store = _FakeQSettings(
                {
                    "import_path_history": json.dumps([model_path, generic_motion_path]),
                    "vmd_path_history": json.dumps([motion_path]),
                }
            )
            view_state = ImportExportViewState(store)

            self.assertEqual(
                view_state.load_file_history(),
                [
                    {"path": model_path, "type": "import"},
                    {"path": generic_motion_path, "type": "vmd"},
                    {"path": motion_path, "type": "vmd"},
                ],
            )

            view_state.clear_file_history()

            self.assertEqual(view_state.load_file_history(), [])
            self.assertEqual(store.values["import_path_history"], "[]")
            self.assertEqual(store.values["vmd_path_history"], "[]")

    def test_import_history_clear_preserves_hidden_export_history(self):
        with TemporaryDirectory() as temp_dir:
            model_path = str(Path(temp_dir) / "model.pmx")
            export_path = str(Path(temp_dir) / "export.pmx")
            for path in (model_path, export_path):
                Path(path).write_text("", encoding="utf-8")
            store = _FakeQSettings()
            view_state = ImportExportViewState(store)
            view_state.save_file_history("import", model_path)
            view_state.save_file_history("export", export_path)

            view_state.clear_file_history(("import", "vmd"))

            self.assertEqual(
                view_state.load_file_history(),
                [{"path": export_path, "type": "export"}],
            )


class TestNormalModeVisibilitySourceInspection(unittest.TestCase):
    """Verify supported model import controls remain available in normal mode."""

    def setUp(self):
        self.source = Path(import_export_tab.__file__).read_text(encoding="utf-8")

    def test_physics_group_is_not_in_dev_only_widgets(self):
        self.assertIn("self.physics_group", self.source)
        lines = self.source.splitlines()
        in_dev_only = False
        found = False
        for line in lines:
            if "_dev_only_widgets" in line and "[" in line:
                in_dev_only = True
            if in_dev_only and "self.physics_group" in line:
                found = True
                break
            if in_dev_only and "]" in line:
                in_dev_only = False
        self.assertFalse(found, "self.physics_group must remain visible in normal mode")

    def test_separate_meshes_is_not_in_dev_only_widgets(self):
        dev_only_start = self.source.index("self._dev_only_widgets = [")
        dev_only_end = self.source.index("]", dev_only_start)

        self.assertNotIn("self.separate_meshes_check", self.source[dev_only_start:dev_only_end])

    def test_import_tab_has_no_export_controls(self):
        for name in (
            "export_group",
            "export_path_edit",
            "export_path_button",
            "export_button",
            "export_format_combo",
            "apply_scale_check",
        ):
            self.assertNotIn(f"self.{name}", self.source)


class TestControlRigSettingSourceInspection(unittest.TestCase):
    """Ensure the model-scoped control-rig checkbox has one UI owner."""

    def setUp(self):
        self.source = Path(import_export_tab.__file__).read_text(encoding="utf-8")

    def test_control_rig_checkbox_is_declared_in_model_settings(self):
        model_start = self.source.index("# Model Settings Group")
        animation_start = self.source.index("# Animation Import Group (VMD)")
        model_source = self.source[model_start:animation_start]

        self.assertIn("self.create_mmd_control_rig_check = self._bind_checkbox(", model_source)
        self.assertIn("setting_keys.IMPORT_MODEL_CREATE_MMD_CONTROL_RIG", model_source)
        self.assertIn("False", model_source)

    def test_animation_group_does_not_create_a_duplicate_control_rig_checkbox(self):
        animation_start = self.source.index("# Animation Import Group (VMD)")
        animation_source = self.source[animation_start:]

        self.assertNotIn("self.create_mmd_control_rig_check = QCheckBox", animation_source)

    def test_rotation_time_curve_checkbox_is_declared_in_animation_settings(self):
        model_start = self.source.index("# Model Settings Group")
        animation_settings_start = self.source.index("# Animation Import Settings")
        animation_import_start = self.source.index("# Animation Import Group (VMD)")

        model_source = self.source[model_start:animation_settings_start]
        animation_settings_source = self.source[
            animation_settings_start:animation_import_start
        ]

        self.assertNotIn("self.vmd_rotation_time_curve_check", model_source)
        self.assertIn(
            "self.vmd_rotation_time_curve_check = self._bind_checkbox(",
            animation_settings_source,
        )
        self.assertIn(
            "setting_keys.IMPORT_ANIMATION_VMD_ROTATION_TIME_CURVE",
            animation_settings_source,
        )

    def test_rotation_time_curve_is_dev_only_and_defaults_on(self):
        dev_only_start = self.source.index("self._dev_only_widgets = [")
        dev_only_end = self.source.index("]", dev_only_start)
        dev_only_source = self.source[dev_only_start:dev_only_end]

        self.assertIn("self.vmd_rotation_time_curve_check", dev_only_source)
        animation_settings_start = self.source.index("# Animation Import Settings")
        checkbox_start = self.source.index(
            "self.vmd_rotation_time_curve_check = self._bind_checkbox(",
            animation_settings_start,
        )
        checkbox_end = self.source.index(")", checkbox_start)
        self.assertIn("True", self.source[checkbox_start:checkbox_end])

    def test_import_defaults_keep_native_render_routes_off(self):
        defaults_path = (
            Path(import_export_tab.__file__).resolve().parents[2]
            / "config"
            / "default_settings.json"
        )
        defaults = json.loads(defaults_path.read_text(encoding="utf-8"))

        self.assertFalse(defaults["import"]["rig"]["bake_mode"])
        self.assertTrue(defaults["import"]["animation"]["vmd_rotation_time_curve"])
        self.assertFalse(defaults["import"]["native"]["use_cpp_fast_load"])
        self.assertFalse(defaults["import"]["native"]["use_cpp_vp2_ownership"])

    def test_japanese_control_rig_label_uses_katakana(self):
        translation_path = (
            Path(import_export_tab.__file__).resolve().parents[1]
            / "translations"
            / "ja.json"
        )
        translations = json.loads(translation_path.read_text(encoding="utf-8"))

        self.assertEqual(
            translations["checkboxes"]["create_mmd_control_rig"],
            "MMDコントロールリグを作成",
        )
        self.assertEqual(
            translations["checkboxes"]["vmd_rotation_time_curve"],
            "VMD時間補間を時間カーブとして保持",
        )


if __name__ == "__main__":
    unittest.main()
