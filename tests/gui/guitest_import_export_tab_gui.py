"""
ImportExportTabのGUIテスト
実際のMaya GUI環境でのみ実行可能
"""

import unittest

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.qt_compat import QSettings
from mmd_tools.ui.tabs.import_export_tab import ImportExportTab


@requires_gui
class TestImportExportTabGUI(GuiTestBase):
    """ImportExportTabのGUIテスト（実際のQt環境で実行）"""

    def setUp(self):
        super().setUp()
        # テスト用の設定をクリア
        self.settings = QSettings("maya_mmd_tools", "ImportExportTab")
        self.settings.clear()
        self.settings.sync()

    def tearDown(self):
        # テスト用の設定をクリア
        self.settings.clear()
        self.settings.sync()
        super().tearDown()

    def test_path_persistence_with_real_widgets(self):
        """実際のウィジェットを使用したパスの永続化テスト"""
        # 最初のタブインスタンスを作成
        tab1 = ImportExportTab()

        # パスを設定
        test_import_path = "/test/import/model.pmx"
        test_export_path = "/test/export/model.pmx"

        tab1.import_path_edit.setText(test_import_path)
        tab1.export_path_edit.setText(test_export_path)

        # タブを削除
        tab1.deleteLater()

        # 新しいタブインスタンスを作成
        tab2 = ImportExportTab()

        # 保存されたパスが読み込まれていることを確認
        self.assertEqual(tab2.import_path_edit.text(), test_import_path)
        self.assertEqual(tab2.export_path_edit.text(), test_export_path)

        # クリーンアップ
        tab2.deleteLater()

    def test_text_change_triggers_save(self):
        """テキスト変更が自動保存をトリガーすることをテスト"""
        tab = ImportExportTab()

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
        tab1 = ImportExportTab()
        tab2 = ImportExportTab()

        # 片方でパスを設定
        test_path = "/shared/path/model.pmx"
        tab1.import_path_edit.setText(test_path)

        # もう片方を再作成して読み込み
        tab2.deleteLater()
        tab3 = ImportExportTab()

        # 共有されていることを確認
        self.assertEqual(tab3.import_path_edit.text(), test_path)

        # クリーンアップ
        tab1.deleteLater()
        tab3.deleteLater()

    def test_special_characters_in_gui(self):
        """GUIで特殊文字を含むパスのテスト"""
        tab = ImportExportTab()

        special_paths = ["/path with spaces/model.pmx", "/パス/日本語/モデル.pmx", "C:\\Users\\ユーザー\\Documents\\model.pmx"]

        for path in special_paths:
            # パスを設定
            tab.import_path_edit.setText(path)

            # 保存されたことを確認
            saved_value = self.settings.value("import_path", "")
            self.assertEqual(saved_value, path)

            # 新しいタブで読み込めることを確認
            new_tab = ImportExportTab()
            self.assertEqual(new_tab.import_path_edit.text(), path)
            new_tab.deleteLater()

        # クリーンアップ
        tab.deleteLater()

    def test_retranslate_ui_does_not_crash(self):
        """retranslateUi() が例外なく実行できることを確認する（B-1 回帰防止）。

        以前は存在しない self.joint_name_conversion_check を参照しており、
        言語切り替え（retranslate_all_tabs）時に AttributeError でクラッシュしていた。
        """
        tab = ImportExportTab()
        try:
            tab.retranslateUi()  # 例外が出れば test はエラーになる
        finally:
            tab.deleteLater()

    def test_export_format_combo_excludes_pmd(self):
        """エクスポート形式に未実装の 'pmd' が含まれないことを確認する（B-3）。"""
        tab = ImportExportTab()
        items = [tab.export_format_combo.itemText(i) for i in range(tab.export_format_combo.count())]
        self.assertIn("pmx", items)
        self.assertNotIn("pmd", items)
        tab.deleteLater()

    def test_vpd_ui_is_not_in_import_export_tab(self):
        """VPD は pose apply / D&D 導線で扱い、Import/Export タブには置かない（B-3）。"""
        tab = ImportExportTab()
        self.assertFalse(hasattr(tab, "vpd_group"))
        self.assertFalse(hasattr(tab, "vpd_not_implemented"))
        tab.deleteLater()


if __name__ == "__main__":
    unittest.main()
