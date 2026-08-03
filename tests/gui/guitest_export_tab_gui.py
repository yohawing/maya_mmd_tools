"""ExportTab の実 Maya GUI 契約テスト。

実際の Maya GUI が提供する Qt アプリケーション上で ExportTab を生成し、
形式別の mode UI と Validation Console の catalog 表示を検証する。
"""

import unittest

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.tabs.export_tab import ExportTab
from mmd_tools.validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
)
from mmd_tools.validation.issue_catalog import get_issue_catalog_entry


@requires_gui
class TestExportTabGUI(GuiTestBase):
    """実際の Maya GUI で ExportTab の widget 契約を確認する。"""

    def _create_visible_tab(self):
        """Maya の既存 QApplication 上でテスト用タブを表示する。"""
        tab = ExportTab()
        tab.resize(900, 700)
        tab.show()
        QApplication.processEvents()
        return tab

    def _delete_tab(self, tab):
        """テストで生成した widget を Maya の Qt 階層から確実に外す。"""
        tab.close()
        tab.deleteLater()
        QApplication.processEvents()

    def test_format_combo_has_three_formats_and_vmd_only_shows_mode(self):
        """PMX/PMD/VMD の形式と VMD 専用 mode UI を確認する。"""
        tab = self._create_visible_tab()
        try:
            self.assertEqual(
                [tab.format_combo.itemText(index) for index in range(tab.format_combo.count())],
                ["pmx", "pmd", "vmd"],
            )

            for export_format in ("pmx", "pmd"):
                tab.format_combo.setCurrentText(export_format)
                QApplication.processEvents()
                self.assertFalse(tab.mode_label.isVisible(), export_format)
                self.assertFalse(tab.mode_combo.isVisible(), export_format)

            tab.format_combo.setCurrentText("vmd")
            QApplication.processEvents()
            self.assertTrue(tab.mode_label.isVisible())
            self.assertTrue(tab.mode_combo.isVisible())
        finally:
            self._delete_tab(tab)

    def test_validation_console_renders_catalog_backed_fatal_issue(self):
        """fatal issue の category と監査文言が Console に表示されることを確認する。"""
        tab = self._create_visible_tab()
        try:
            issue_code = "VMD_RAW_PROVENANCE_MISSING"
            observed = "imported raw key provenance was not supplied"
            catalog_entry = get_issue_catalog_entry(issue_code)
            report = ExportValidationReport(
                "vmd",
                (
                    ExportValidationIssue(
                        issue_code,
                        "fatal",
                        True,
                        "raw_provenance",
                        observed,
                    ),
                ),
                mode="A",
            )
            evidence = {
                "fixture": "gui_validation_console",
                "source": "ExportTab GUI test",
            }

            tab.validation_console.set_report(report, evidence)
            QApplication.processEvents()

            console = tab.validation_console
            self.assertEqual(console.issue_list.count(), 1)
            self.assertIn("[FATAL] VMD_RAW_PROVENANCE_MISSING", console.issue_list.item(0).text())

            category_index = console.filter_combo.findData(catalog_entry.category)
            self.assertGreaterEqual(category_index, 0)
            self.assertEqual(console.filter_combo.itemText(category_index), catalog_entry.category)
            console.filter_combo.setCurrentIndex(category_index)
            QApplication.processEvents()
            self.assertEqual(console.issue_list.count(), 1)

            detail = console.detail_text.toPlainText()
            self.assertIn(f"Category: {catalog_entry.category}", detail)
            self.assertIn(f"Observed: {observed}", detail)
            self.assertIn(f"Expected: {catalog_entry.expected}", detail)
            self.assertIn(f"Impact: {catalog_entry.impact}", detail)
            self.assertIn(f"Remediation: {catalog_entry.remediation}", detail)
            self.assertIn("Evidence:", detail)
            self.assertIn("gui_validation_console", detail)
            self.assertIn("ExportTab GUI test", detail)
        finally:
            self._delete_tab(tab)

    def test_validate_and_export_buttons_emit_workflow_requests(self):
        """Validate/Export buttons route into the shared presenter signals."""
        tab = self._create_visible_tab()
        events = []
        tab.validate_requested.connect(lambda: events.append("validate"))
        tab.export_requested.connect(lambda: events.append("export"))
        try:
            tab.validate_button.click()
            tab.export_button.click()
            QApplication.processEvents()
            self.assertEqual(events, ["validate", "export"])
        finally:
            self._delete_tab(tab)


if __name__ == "__main__":
    unittest.main()
