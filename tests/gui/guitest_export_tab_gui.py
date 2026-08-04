"""ExportTab の実 Maya GUI 契約テスト。

実際の Maya GUI が提供する Qt アプリケーション上で ExportTab を生成し、
形式別の mode UI と Validation Console の catalog 表示を検証する。
"""

import unittest

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.qt_compat import QApplication, QtCore
from mmd_tools.ui.presenters.export_presenter import ExportPresenter
from mmd_tools.ui.tabs.export_tab import ExportTab
from mmd_tools.services.export_workflow_service import (
    ExportWorkflowResult,
    STATE_SUCCEEDED,
)
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
)
from mmd_tools.validation.issue_catalog import get_issue_catalog_entry
from mmd_tools.ui.translations import UITranslator
from mmd_tools.validation.vmd_validator import VMD_MODE_C, validate_vmd_data


class _WarningWorkflow:
    """Capture the UI acknowledgement while returning a successful result."""

    def __init__(self, report):
        self.report = report
        self.acknowledgements = []

    def execute(self, _request, *, acknowledge_warnings=False):
        self.acknowledgements.append(acknowledge_warnings)
        return ExportWorkflowResult(
            STATE_SUCCEEDED,
            self.report,
            {"output_path": "mode-c-warning.vmd"},
        )


class _GuiAppState:
    """Minimal app-state surface needed by ExportPresenter in this test."""

    available_models = []
    current_model_root = None

    def __init__(self):
        self.statuses = []

    def emit_status(self, message):
        self.statuses.append(message)


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
        app = QApplication.instance()
        if app is None:
            return
        app.processEvents()
        app.sendPostedEvents(tab, QtCore.QEvent.DeferredDelete)

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
        translator = UITranslator.instance()
        previous_language = translator.get_language()
        translator.set_language("en")
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
            self.assertEqual(
                console.filter_combo.itemText(category_index),
                translator.translate(
                    f"validation_categories.{catalog_entry.category}.label",
                    default=catalog_entry.category,
                ),
            )
            console.filter_combo.setCurrentIndex(category_index)
            QApplication.processEvents()
            self.assertEqual(console.issue_list.count(), 1)

            detail = console.detail_text.toPlainText()
            category_label = translator.translate(
                f"validation_categories.{catalog_entry.category}.label",
                default=catalog_entry.category,
            )
            self.assertIn(f"Category: {category_label}", detail)
            self.assertIn(f"Observed: {observed}", detail)
            self.assertIn(f"Expected: {catalog_entry.expected}", detail)
            self.assertIn(f"Impact: {catalog_entry.impact}", detail)
            self.assertIn(f"Remediation: {catalog_entry.remediation}", detail)
            self.assertIn("Evidence:", detail)
            self.assertIn("gui_validation_console", detail)
            self.assertIn("ExportTab GUI test", detail)
        finally:
            self._delete_tab(tab)
            translator.set_language(previous_language)

    def test_validation_labels_and_catalog_wording_follow_japanese_translation(self):
        """Validation controls and issue wording follow the active UI language."""
        translator = UITranslator.instance()
        previous_language = translator.get_language()
        translator.set_language("ja")
        tab = self._create_visible_tab()
        try:
            self.assertEqual(tab.validate_button.text(), "検証")
            self.assertEqual(tab.validation_console.revalidate_button.text(), "再検証")
            self.assertEqual(tab.validation_console.acknowledge_check.text(), "警告を確認済みにする")
            self.assertEqual(tab.validation_console.save_button.text(), "レポートを保存")

            report = validate_vmd_data(
                VmdData(),
                VMD_MODE_C,
                raw_provenance={
                    "raw_bone_interpolation": [
                        {
                            "bone_name": "センター",
                            "frame_number": 0,
                            "interpolation": [20] * 64,
                        }
                    ]
                },
            )
            tab.validation_console.set_report(report)
            QApplication.processEvents()
            detail = tab.validation_console.detail_text.toPlainText()
            self.assertIn("タイトル: VMD Mode C の元アニメーション情報の損失", detail)
            self.assertIn("影響: 密なベイクにより", detail)
            self.assertIn("対処方法: 未編集のモーションは Mode A", detail)

            translator.set_language("en")
            tab.retranslateUi()
            self.assertEqual(tab.validate_button.text(), "Validate")
            self.assertEqual(tab.validation_console.revalidate_button.text(), "Revalidate")
        finally:
            self._delete_tab(tab)
            translator.set_language(previous_language)

    def test_mode_c_warning_is_acknowledged_before_successful_export_route(self):
        """Real Maya widgets show the warning and route an explicit ack to export."""
        tab = self._create_visible_tab()
        report = validate_vmd_data(
            VmdData(),
            VMD_MODE_C,
            raw_provenance={
                "raw_bone_interpolation_complete": True,
                "raw_bone_interpolation": [
                    {
                        "bone_name": "センター",
                        "frame_number": 0,
                        "interpolation": [20] * 64,
                    }
                ],
            },
        )
        workflow = _WarningWorkflow(report)
        app_state = _GuiAppState()
        presenter = ExportPresenter(tab, app_state, workflow_service=workflow)
        try:
            tab.validation_console.set_report(report, {"fixture": "mode-c-raw-loss"})
            QApplication.processEvents()

            console = tab.validation_console
            self.assertTrue(report.requires_warning_ack)
            self.assertTrue(console.acknowledge_check.isEnabled())
            self.assertIn("[WARNING] VMD_MODE_C_RAW_LOSS", console.issue_list.item(0).text())
            console.acknowledge_check.setChecked(True)
            self.assertTrue(console.warnings_acknowledged)
            tab.export_button.click()
            QApplication.processEvents()

            self.assertEqual(workflow.acknowledgements, [True])
            self.assertEqual(tab.state_label.text(), STATE_SUCCEEDED)
        finally:
            presenter.deleteLater()
            QApplication.processEvents()
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
