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
        self.requests = []

    def execute(self, request, *, acknowledge_warnings=False):
        self.requests.append(request)
        self.acknowledgements.append(acknowledge_warnings)
        return ExportWorkflowResult(
            STATE_SUCCEEDED,
            self.report,
            {"output_path": "mode-c-warning.vmd"},
        )


class _GuiAppState:
    """Minimal app-state surface needed by ExportPresenter in this test."""

    current_model_root = "model_ROOT"

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
        tab.hide()
        tab.close()
        # ExportTab は親なしのトップレベル widget として生成されるため、
        # 次のテストへ Maya/Qt の所有権を持ち越さないよう明示的に切り離す。
        tab.setParent(None)
        tab.deleteLater()
        app = QApplication.instance()
        if app is None:
            return
        # 生成した widget 自身の DeferredDelete を先に処理してから、
        # Maya の通常イベントを一度だけ進める。他の所有者の queue は触らない。
        app.sendPostedEvents(tab, QtCore.QEvent.DeferredDelete)
        app.processEvents()

    def test_model_motion_tabs_have_fixed_formats_and_no_target_or_format_widgets(self):
        """Model/Motion tabs own PMX/VMD and expose no legacy selectors."""
        tab = self._create_visible_tab()
        try:
            self.assertFalse(hasattr(tab, "target_combo"))
            self.assertFalse(hasattr(tab, "format_combo"))
            self.assertEqual(tab.pane_tabs.count(), 2)
            self.assertEqual(tab.pane_tabs.tabText(0), "モデル")
            self.assertEqual(tab.pane_tabs.tabText(1), "モーション")
            self.assertEqual(tab.build_request("model_ROOT").options["export_format"], "pmx")
            tab.pane_tabs.setCurrentIndex(1)
            self.assertEqual(tab.mode_combo.currentText(), "C")
            tab.mode_combo.setCurrentText("A")
            tab.frame_range_check.setChecked(True)
            tab.frame_start_spin.setValue(12)
            tab.frame_end_spin.setValue(42)
            request = tab.build_request("model_ROOT")
            self.assertEqual(request.options["export_format"], "vmd")
            self.assertEqual(request.options["current_model_root"], "model_ROOT")
            self.assertEqual(request.options["vmd_mode"], "A")
            self.assertEqual(request.options["frame_range"], (12, 42))
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
            self.assertEqual(tab.apply_scale_check.text(), "スケールを適用")
            self.assertEqual(
                tab._model_form.labelForField(tab.apply_scale_check).text(),
                "オプション",
            )
            self.assertEqual(
                tab._motion_form.labelForField(tab.frame_range_check).text(),
                "範囲",
            )
            self.assertEqual(
                tab._motion_form.labelForField(tab.frame_start_spin).text(),
                "開始",
            )
            self.assertEqual(
                tab._motion_form.labelForField(tab.frame_end_spin).text(),
                "終了",
            )
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
            self.assertEqual(tab.apply_scale_check.text(), "Apply Scale")
            self.assertEqual(
                tab._model_form.labelForField(tab.apply_scale_check).text(),
                "Options",
            )
            self.assertEqual(
                tab._motion_form.labelForField(tab.frame_range_check).text(),
                "Range",
            )
            self.assertEqual(
                tab._motion_form.labelForField(tab.frame_start_spin).text(),
                "Start",
            )
            self.assertEqual(
                tab._motion_form.labelForField(tab.frame_end_spin).text(),
                "End",
            )
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
            tab.pane_tabs.setCurrentIndex(1)
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
            self.assertEqual(workflow.requests[0].options["export_format"], "vmd")
            self.assertEqual(workflow.requests[0].options["current_model_root"], "model_ROOT")
            self.assertEqual(tab.state_label.text(), STATE_SUCCEEDED)
        finally:
            presenter.deleteLater()
            app = QApplication.instance()
            if app is not None:
                app.sendPostedEvents(presenter, QtCore.QEvent.DeferredDelete)
            self._delete_tab(tab)

    def test_pane_report_ack_and_output_state_are_isolated(self):
        """Switching panes restores each report/ack/path without mixing them."""
        tab = self._create_visible_tab()
        try:
            tab.output_path_edit.setText("model.vmd")
            model_report = ExportValidationReport(
                "pmx",
                (ExportValidationIssue("VMD_MODE_C_RAW_LOSS", "warning", False, "mode", "model"),),
                mode="model",
            )
            tab.validation_console.set_report(model_report)
            tab.validation_console.acknowledge_check.setChecked(True)
            self.assertTrue(tab.build_request("model_ROOT").file_path.endswith("model.pmx"))

            tab.pane_tabs.setCurrentIndex(1)
            self.assertIsNone(tab.validation_console.report)
            self.assertFalse(tab.validation_console.warnings_acknowledged)
            tab.output_path_edit.setText("motion.pmx")
            motion_report = ExportValidationReport(
                "vmd",
                (ExportValidationIssue("VMD_MODE_C_RAW_LOSS", "warning", False, "mode", "motion"),),
                mode="C",
            )
            tab.validation_console.set_report(motion_report)
            tab.validation_console.acknowledge_check.setChecked(True)

            tab.pane_tabs.setCurrentIndex(0)
            self.assertIs(tab.validation_console.report, model_report)
            self.assertTrue(tab.validation_console.warnings_acknowledged)
            self.assertEqual(tab.output_path_edit.text(), "model.pmx")

            tab.apply_scale_check.setChecked(not tab.apply_scale_check.isChecked())
            self.assertIsNone(tab.validation_console.report)
            tab.pane_tabs.setCurrentIndex(1)
            self.assertIs(tab.validation_console.report, motion_report)
            self.assertTrue(tab.validation_console.warnings_acknowledged)
            self.assertEqual(tab.output_path_edit.text(), "motion.vmd")
        finally:
            self._delete_tab(tab)

    def test_current_model_change_invalidates_both_panes(self):
        """Current Model changes clear both pane reports and acknowledgements."""
        tab = self._create_visible_tab()
        try:
            report = ExportValidationReport(
                "vmd",
                (ExportValidationIssue("VMD_MODE_C_RAW_LOSS", "warning", False, "mode", "x"),),
                mode="C",
            )
            tab.validation_console.set_report(report)
            tab.pane_tabs.setCurrentIndex(1)
            tab.validation_console.set_report(report)
            tab.invalidate_all_panes()
            self.assertIsNone(tab.validation_console.report)
            tab.pane_tabs.setCurrentIndex(0)
            self.assertIsNone(tab.validation_console.report)
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
