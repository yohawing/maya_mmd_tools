"""
ImportExportTabのGUIテスト
実際のMaya GUI環境でのみ実行可能
"""

import os
import tempfile
import unittest

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.qt_compat import QSettings, Qt
from mmd_tools.ui.import_export_view_state import ImportExportViewState
from mmd_tools.ui.tabs.import_export_tab import ImportExportTab


@requires_gui
class TestImportExportTabGUI(GuiTestBase):
    """ImportExportTabのGUIテスト（実際のQt環境で実行）"""

    def setUp(self):
        super().setUp()
        # 本番の QSettings を消さないよう、テストごとの一時 INI に隔離する。
        self._settings_dir = tempfile.TemporaryDirectory()
        settings_path = os.path.join(self._settings_dir.name, "import_export_tab.ini")
        self.settings = QSettings(settings_path, QSettings.IniFormat)
        self.settings.clear()
        self.settings.sync()
        self.view_state = ImportExportViewState(self.settings)

    def tearDown(self):
        try:
            self.settings.clear()
            self.settings.sync()
            del self.view_state
            del self.settings
            self._settings_dir.cleanup()
        finally:
            super().tearDown()

    def _create_tab(self):
        """テスト専用の view state を使うタブを作成する。"""
        return ImportExportTab(view_state=self.view_state)

    def test_settings_store_is_isolated_from_user_profile(self):
        """GUIテストが実ユーザーのファイル履歴ストアを使わないことを確認する。"""
        user_settings = QSettings("maya_mmd_tools", "ImportExportTab")
        self.assertNotEqual(self.settings.fileName(), user_settings.fileName())

    def test_path_persistence_with_real_widgets(self):
        """実際のウィジェットを使用したパスの永続化テスト"""
        # 最初のタブインスタンスを作成
        tab1 = self._create_tab()

        # パスを設定
        test_import_path = "/test/import/model.pmx"
        tab1.import_path_edit.setText(test_import_path)

        # タブを削除
        tab1.deleteLater()

        # 新しいタブインスタンスを作成
        tab2 = self._create_tab()

        # 保存されたパスが読み込まれていることを確認
        self.assertEqual(tab2.import_path_edit.text(), test_import_path)

        # クリーンアップ
        tab2.deleteLater()

    def test_text_change_triggers_save(self):
        """テキスト変更が自動保存をトリガーすることをテスト"""
        tab = self._create_tab()

        # 初期状態を確認
        self.assertEqual(tab.import_path_edit.text(), "")

        # テキストを変更
        new_path = "/new/test/path.pmx"
        tab.import_path_edit.setText(new_path)

        # QSettingsから直接読み込んで確認
        saved_value = self.settings.value("import_path", "")
        self.assertEqual(saved_value, new_path)

        # クリーンアップ
        tab.deleteLater()

    def test_multiple_instances_share_settings(self):
        """複数のインスタンスが設定を共有することをテスト"""
        # 複数のタブを同時に作成
        tab1 = self._create_tab()
        tab2 = self._create_tab()

        # 片方でパスを設定
        test_path = "/shared/path/model.pmx"
        tab1.import_path_edit.setText(test_path)

        # もう片方を再作成して読み込み
        tab2.deleteLater()
        tab3 = self._create_tab()

        # 共有されていることを確認
        self.assertEqual(tab3.import_path_edit.text(), test_path)

        # クリーンアップ
        tab1.deleteLater()
        tab3.deleteLater()

    def test_special_characters_in_gui(self):
        """GUIで特殊文字を含むパスのテスト"""
        tab = self._create_tab()

        special_paths = ["/path with spaces/model.pmx", "/パス/日本語/モデル.pmx", "C:\\Users\\ユーザー\\Documents\\model.pmx"]

        for path in special_paths:
            # パスを設定
            tab.import_path_edit.setText(path)

            # 保存されたことを確認
            saved_value = self.settings.value("import_path", "")
            self.assertEqual(saved_value, path)

            # 新しいタブで読み込めることを確認
            new_tab = self._create_tab()
            self.assertEqual(new_tab.import_path_edit.text(), path)
            new_tab.deleteLater()

        # クリーンアップ
        tab.deleteLater()

    def test_retranslate_ui_does_not_crash(self):
        """retranslateUi() が例外なく実行できることを確認する（B-1 回帰防止）。

        以前は存在しない self.joint_name_conversion_check を参照しており、
        言語切り替え（retranslate_all_tabs）時に AttributeError でクラッシュしていた。
        """
        tab = self._create_tab()
        try:
            tab.retranslateUi()  # 例外が出れば test はエラーになる
        finally:
            tab.deleteLater()

    def test_import_tab_contains_only_import_controls(self):
        """Export workflow controls are owned by the dedicated Export tab."""
        tab = self._create_tab()
        try:
            for attr in (
                "export_group",
                "export_path_edit",
                "export_path_button",
                "export_button",
                "export_format_combo",
                "apply_scale_check",
            ):
                self.assertFalse(hasattr(tab, attr), attr)
        finally:
            tab.deleteLater()

    def test_vpd_ui_is_not_in_import_export_tab(self):
        """VPD は pose apply / D&D 導線で扱い、Import タブには置かない（B-3）。"""
        tab = self._create_tab()
        self.assertFalse(hasattr(tab, "vpd_group"))
        self.assertFalse(hasattr(tab, "vpd_not_implemented"))
        tab.deleteLater()

    def test_animation_history_filters_vmd_and_populates_motion_path(self):
        """Animation tab owns the typed VMD history and double-click route."""
        tab = self._create_tab()
        try:
            with tempfile.TemporaryDirectory() as directory:
                model_path = os.path.join(directory, "model.pmx")
                motion_path = os.path.join(directory, "motion.vmd")
                with open(model_path, "w", encoding="utf-8"):
                    pass
                with open(motion_path, "w", encoding="utf-8"):
                    pass
                self.view_state.save_file_history("import", model_path)
                self.view_state.save_file_history("vmd", motion_path)
                tab.import_category_stack.setCurrentIndex(1)
                self.assertEqual(tab.import_category_stack.count(), 2)
                self.assertEqual(tab.unified_history_list.count(), 1)
                item = tab.unified_history_list.item(0)
                self.assertEqual(item.data(Qt.UserRole + 1), "vmd")
                tab._on_history_item_double_clicked(item)
                self.assertEqual(tab.vmd_path_edit.text(), motion_path)
        finally:
            tab.deleteLater()

    def test_new_model_entrypoint_is_one_button_next_to_import(self):
        """The inline template fields are gone; one modal entrypoint shares the import row."""
        tab = self._create_tab()
        try:
            self.assertFalse(hasattr(tab, "create_model_group"))
            self.assertFalse(hasattr(tab, "create_model_template_combo"))
            self.assertTrue(hasattr(tab, "new_model_button"))
            self.assertEqual(tab.import_button.parentWidget(), tab.new_model_button.parentWidget())
            # The buttons live in a QHBoxLayout nested inside the group's
            # QFormLayout, so the parent widget's top-level layout does not
            # contain them directly.
            def find_layout_containing(layout, widget):
                for index in range(layout.count()):
                    item = layout.itemAt(index)
                    if item is None:
                        continue
                    if item.widget() is widget:
                        return layout
                    child_layout = item.layout()
                    if child_layout is not None:
                        found = find_layout_containing(child_layout, widget)
                        if found is not None:
                            return found
                return None

            button_layout = find_layout_containing(tab.model_import_group.layout(), tab.import_button)
            self.assertIsNotNone(button_layout)
            self.assertEqual(button_layout.indexOf(tab.import_button) + 1, button_layout.indexOf(tab.new_model_button))
        finally:
            tab.deleteLater()


if __name__ == "__main__":
    unittest.main()
