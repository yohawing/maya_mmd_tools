"""ImportExportTab の Maya 非依存 helper と model combo 更新を検証する。"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.import_export_view_state import ImportExportViewState  # noqa: E402
from mmd_tools.ui.tabs import import_export_tab  # noqa: E402


class _FakeComboBox:
    def __init__(self, current_index=0):
        self._current_index = current_index
        self.items = []
        self.blocked_states = []

    def currentIndex(self):
        return self._current_index

    def clear(self):
        self.items.clear()

    def addItem(self, text, userData=None):
        self.items.append((text, userData))

    def count(self):
        return len(self.items)

    def setCurrentIndex(self, index):
        self._current_index = index

    def blockSignals(self, blocked):
        self.blocked_states.append(blocked)
        return False


class _FakeViewState:
    def __init__(self, value):
        self._value = value

    def get(self, _key, _default=None):
        return self._value


class _FakeSettingsService:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class _FakeWidget:
    def __init__(self):
        self.visible = None

    def setVisible(self, visible):
        self.visible = visible


class _FakePresenter:
    def __init__(self):
        self.calls = []

    def refresh_model_list(self, restore_selection=False):
        self.calls.append(restore_selection)


class TestImportExportTabModelLabels(unittest.TestCase):
    def test_format_target_model_label_uses_display_name_without_namespace(self):
        label = import_export_tab._format_target_model_label("miku_root", "Miku")

        self.assertEqual(label, "Miku")

    def test_format_target_model_label_adds_namespace_and_root(self):
        label = import_export_tab._format_target_model_label("ModelA:miku_root", "Miku")

        self.assertEqual(label, "Miku [ModelA:miku_root]")

    def test_format_target_model_label_handles_dag_paths(self):
        label = import_export_tab._format_target_model_label("|group|ModelA:miku_root", "Miku")

        self.assertEqual(label, "Miku [ModelA:miku_root]")


class TestImportExportTabRefreshModelList(unittest.TestCase):
    def _make_tab(self, current_index=0, saved_index=0):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.target_model_combo = _FakeComboBox(current_index=current_index)
        tab.view_state = _FakeViewState(saved_index)
        tab.tr = lambda key, _category: f"<{key}>"
        return tab

    def test_set_target_model_items_adds_auto_detect_first(self):
        tab = self._make_tab()

        import_export_tab.ImportExportTab.set_target_model_items(tab, [])

        self.assertEqual(tab.target_model_combo.items, [("<auto_detect>", None)])

    def test_set_target_model_items_shows_namespace_label_but_keeps_model_userdata(self):
        tab = self._make_tab()

        import_export_tab.ImportExportTab.set_target_model_items(tab, [("ModelA:miku_root", "Miku")])

        self.assertEqual(
            tab.target_model_combo.items,
            [("<auto_detect>", None), ("Miku [ModelA:miku_root]", "ModelA:miku_root")],
        )

    def test_set_target_model_items_distinguishes_same_display_name_by_namespace(self):
        tab = self._make_tab()

        import_export_tab.ImportExportTab.set_target_model_items(
            tab,
            [("ModelA:miku_root", "Miku"), ("ModelB:miku_root", "Miku")],
        )

        self.assertEqual(
            tab.target_model_combo.items,
            [
                ("<auto_detect>", None),
                ("Miku [ModelA:miku_root]", "ModelA:miku_root"),
                ("Miku [ModelB:miku_root]", "ModelB:miku_root"),
            ],
        )

    def test_set_target_model_items_restores_saved_index(self):
        tab = self._make_tab(current_index=0, saved_index=1)

        import_export_tab.ImportExportTab.set_target_model_items(
            tab,
            [("ModelA:miku_root", "Miku")],
            restore_selection=True,
        )

        self.assertEqual(tab.target_model_combo.currentIndex(), 1)
        self.assertEqual(tab.target_model_combo.blocked_states, [True, False])

    def test_refresh_model_list_delegates_to_presenter(self):
        tab = self._make_tab()
        tab.presenter = _FakePresenter()

        import_export_tab.ImportExportTab.refresh_model_list(tab, restore_selection=True)

        self.assertEqual(tab.presenter.calls, [True])


class TestImportExportTabDevModeVisibility(unittest.TestCase):
    def test_dev_only_controls_follow_development_mode(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        cpp_rig_nodes_check = _FakeWidget()
        motion_scale_row = _FakeWidget()
        export_settings_tab = _FakeWidget()
        tab._dev_only_widgets = [cpp_rig_nodes_check, motion_scale_row, export_settings_tab]
        tab.settings_service = _FakeSettingsService({"ui.general.development_mode": False})

        import_export_tab.ImportExportTab._apply_dev_mode_visibility(tab)
        self.assertFalse(cpp_rig_nodes_check.visible)
        self.assertFalse(motion_scale_row.visible)
        self.assertFalse(export_settings_tab.visible)

        tab.settings_service.set("ui.general.development_mode", True)
        import_export_tab.ImportExportTab._apply_dev_mode_visibility(tab)
        self.assertTrue(cpp_rig_nodes_check.visible)
        self.assertTrue(motion_scale_row.visible)
        self.assertTrue(export_settings_tab.visible)


class TestImportExportTabExportVisibility(unittest.TestCase):
    def _make_tab(self, development_mode=True):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.export_group = _FakeWidget()
        tab.settings_service = _FakeSettingsService(
            {"ui.general.development_mode": development_mode}
        )
        return tab

    def test_export_group_is_shown_for_pmx_format_in_dev_mode(self):
        tab = self._make_tab(development_mode=True)

        tab.settings_service.set("export.general.export_format", "pmx")
        import_export_tab.ImportExportTab._apply_export_visibility(tab)

        self.assertTrue(tab.export_group.visible)

    def test_export_group_is_shown_for_vmd_format_in_dev_mode(self):
        tab = self._make_tab(development_mode=True)

        tab.settings_service.set("export.general.export_format", "vmd")
        import_export_tab.ImportExportTab._apply_export_visibility(tab)

        self.assertTrue(tab.export_group.visible)

    def test_export_group_is_hidden_in_normal_mode(self):
        tab = self._make_tab(development_mode=False)

        tab.settings_service.set("export.general.export_format", "pmx")
        import_export_tab.ImportExportTab._apply_export_visibility(tab)

        self.assertFalse(tab.export_group.visible)

    def test_export_format_change_updates_settings_and_visibility(self):
        tab = self._make_tab(development_mode=True)

        import_export_tab.ImportExportTab._on_export_format_changed(tab, "vmd")

        self.assertEqual(tab.settings_service.get("export.general.export_format"), "vmd")
        self.assertTrue(tab.export_group.visible)


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


if __name__ == "__main__":
    unittest.main()
