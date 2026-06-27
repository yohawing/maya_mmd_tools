"""ImportExportTab の Maya 非依存 helper と model combo 更新を検証する。"""

import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.tabs import import_export_tab  # noqa: E402


class _FakeComboBox:
    def __init__(self, current_index=0):
        self._current_index = current_index
        self.items = []

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


class _FakeSettings:
    def __init__(self, value):
        self._value = value

    def value(self, _key, _default):
        return self._value


class _FakeWidget:
    def __init__(self):
        self.visible = None

    def setVisible(self, visible):
        self.visible = visible


class TestImportExportTabModelLabels(unittest.TestCase):
    def test_format_target_model_label_uses_display_name_without_namespace(self):
        with patch.object(import_export_tab, "get_mmd_model_display_name", return_value="Miku"):
            label = import_export_tab._format_target_model_label("miku_root")

        self.assertEqual(label, "Miku")

    def test_format_target_model_label_adds_namespace_and_root(self):
        with patch.object(import_export_tab, "get_mmd_model_display_name", return_value="Miku"):
            label = import_export_tab._format_target_model_label("ModelA:miku_root")

        self.assertEqual(label, "Miku [ModelA:miku_root]")

    def test_format_target_model_label_handles_dag_paths(self):
        with patch.object(import_export_tab, "get_mmd_model_display_name", return_value="Miku"):
            label = import_export_tab._format_target_model_label("|group|ModelA:miku_root")

        self.assertEqual(label, "Miku [ModelA:miku_root]")


class TestImportExportTabRefreshModelList(unittest.TestCase):
    def _make_tab(self, current_index=0, saved_index=0):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.target_model_combo = _FakeComboBox(current_index=current_index)
        tab.qt_settings = _FakeSettings(saved_index)
        tab.tr = lambda key, _category: f"<{key}>"
        return tab

    def test_refresh_model_list_adds_auto_detect_first(self):
        tab = self._make_tab()

        with patch.object(import_export_tab, "find_all_mmd_models", return_value=[]):
            import_export_tab.ImportExportTab.refresh_model_list(tab)

        self.assertEqual(tab.target_model_combo.items, [("<auto_detect>", None)])

    def test_refresh_model_list_shows_namespace_label_but_keeps_model_userdata(self):
        tab = self._make_tab()

        with patch.object(
            import_export_tab, "find_all_mmd_models", return_value=["ModelA:miku_root"]
        ), patch.object(import_export_tab, "get_mmd_model_display_name", return_value="Miku"):
            import_export_tab.ImportExportTab.refresh_model_list(tab)

        self.assertEqual(
            tab.target_model_combo.items,
            [("<auto_detect>", None), ("Miku [ModelA:miku_root]", "ModelA:miku_root")],
        )

    def test_refresh_model_list_distinguishes_same_display_name_by_namespace(self):
        tab = self._make_tab()

        def display_name(_model):
            return "Miku"

        with patch.object(
            import_export_tab,
            "find_all_mmd_models",
            return_value=["ModelA:miku_root", "ModelB:miku_root"],
        ), patch.object(import_export_tab, "get_mmd_model_display_name", side_effect=display_name):
            import_export_tab.ImportExportTab.refresh_model_list(tab)

        self.assertEqual(
            tab.target_model_combo.items,
            [
                ("<auto_detect>", None),
                ("Miku [ModelA:miku_root]", "ModelA:miku_root"),
                ("Miku [ModelB:miku_root]", "ModelB:miku_root"),
            ],
        )

    def test_refresh_model_list_restores_saved_index(self):
        tab = self._make_tab(current_index=0, saved_index=1)

        with patch.object(
            import_export_tab, "find_all_mmd_models", return_value=["ModelA:miku_root"]
        ), patch.object(import_export_tab, "get_mmd_model_display_name", return_value="Miku"):
            import_export_tab.ImportExportTab.refresh_model_list(tab, restore_selection=True)

        self.assertEqual(tab.target_model_combo.currentIndex(), 1)

    def test_refresh_model_list_handles_exception_gracefully(self):
        tab = self._make_tab()

        with patch.object(import_export_tab, "find_all_mmd_models", side_effect=RuntimeError("boom")):
            import_export_tab.ImportExportTab.refresh_model_list(tab)

        self.assertEqual(tab.target_model_combo.items, [("<auto_detect>", None)])


class TestImportExportTabDevModeVisibility(unittest.TestCase):
    def setUp(self):
        self._old_dev_mode = import_export_tab.settings.get("ui.general.development_mode", False)

    def tearDown(self):
        import_export_tab.settings.set("ui.general.development_mode", self._old_dev_mode)

    def test_dev_only_cpp_rig_node_control_follows_development_mode(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        cpp_rig_nodes_check = _FakeWidget()
        tab._dev_only_widgets = [cpp_rig_nodes_check]

        import_export_tab.settings.set("ui.general.development_mode", False)
        import_export_tab.ImportExportTab._apply_dev_mode_visibility(tab)
        self.assertFalse(cpp_rig_nodes_check.visible)

        import_export_tab.settings.set("ui.general.development_mode", True)
        import_export_tab.ImportExportTab._apply_dev_mode_visibility(tab)
        self.assertTrue(cpp_rig_nodes_check.visible)


class TestImportExportTabExportVisibility(unittest.TestCase):
    def setUp(self):
        self._old_export_format = import_export_tab.settings.get("export.general.export_format", "pmx")

    def tearDown(self):
        import_export_tab.settings.set("export.general.export_format", self._old_export_format)

    def _make_tab(self):
        tab = import_export_tab.ImportExportTab.__new__(import_export_tab.ImportExportTab)
        tab.export_group = _FakeWidget()
        return tab

    def test_export_group_is_hidden_for_pmx_format(self):
        tab = self._make_tab()

        import_export_tab.settings.set("export.general.export_format", "pmx")
        import_export_tab.ImportExportTab._apply_export_visibility(tab)

        self.assertFalse(tab.export_group.visible)

    def test_export_group_is_shown_for_vmd_format(self):
        tab = self._make_tab()

        import_export_tab.settings.set("export.general.export_format", "vmd")
        import_export_tab.ImportExportTab._apply_export_visibility(tab)

        self.assertTrue(tab.export_group.visible)

    def test_export_format_change_updates_settings_and_visibility(self):
        tab = self._make_tab()

        import_export_tab.ImportExportTab._on_export_format_changed(tab, "vmd")

        self.assertEqual(import_export_tab.settings.get("export.general.export_format"), "vmd")
        self.assertTrue(tab.export_group.visible)


if __name__ == "__main__":
    unittest.main()
