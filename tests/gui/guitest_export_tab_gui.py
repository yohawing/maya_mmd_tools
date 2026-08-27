"""ExportTab の実 Maya GUI 契約テスト。

実際の Maya GUI が提供する Qt アプリケーション上で ExportTab を生成し、
形式別の書き出し方式UIとValidation Consoleのcatalog表示を検証する。
"""

import json
import unittest

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.qt_compat import QApplication, QtCore
from mmd_tools.ui.presenters.export_presenter import ExportPresenter
from mmd_tools.ui.tabs.export_tab import ExportTab
from mmd_tools.services.export_workflow_service import (
    ExportWorkflowResult,
    STATE_SUCCEEDED,
)
from mmd_tools.validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
)
from mmd_tools.ui.translations import UITranslator
from mmd_tools.validation.vmd_validator import (
    VMD_EXPORT_BAKE_TIMELINE,
    VMD_EXPORT_PRESERVE_KEYS,
)
from tests.common.ui_action_coverage import (
    ActionInvocationSpy,
    QtSignalInvocationSpy,
    build_surface_witness,
)


def _emit_witness(surface_id, locator_key, locator, interaction, oracle, action_spy, control):
    """Emit one deterministic runtime witness for the coverage gate."""

    evidence = build_surface_witness(
        surface_id=surface_id,
        case_id="gui.export_tab",
        interaction=interaction,
        oracle=oracle,
        action_spy=action_spy,
        control=control,
        **{locator_key: locator},
    )
    print(
        "[UI COVERAGE WITNESS] "
        + json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        flush=True,
    )


class _GuiAppState:
    """Minimal app-state surface needed by ExportPresenter in this test."""

    current_model_root = "model_ROOT"

    def __init__(self):
        self.statuses = []
        self._progress_token = 0
        self.progress = []

    def emit_status(self, message):
        self.statuses.append(message)

    def begin_progress(self, label=""):
        self._progress_token += 1
        self.progress.append(("begin", self._progress_token, label, None))
        return self._progress_token

    def update_progress_state(self, token, label="", percentage=None):
        self.progress.append(("update", token, label, percentage))
        return True

    def end_progress(self, token):
        self.progress.append(("end", token, "", None))
        return True


class _PaneSwitchWorkflow:
    """Switch panes while execute is in flight to reproduce GUI reentrancy."""

    def __init__(self, report, switch_pane):
        self.report = report
        self._switch_pane = switch_pane

    def execute(self, request, *, warning_callback=None, progress_callback=None):
        del request, warning_callback
        self._switch_pane()
        if progress_callback is not None:
            progress_callback("report_ready")
        return ExportWorkflowResult(
            STATE_SUCCEEDED,
            self.report,
            {"output_path": "bake-timeline-warning.vmd"},
        )


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

    def test_animation_page_switches_between_vmd_and_vpd(self):
        """Animation owns both timeline VMD and current-frame VPD export."""
        tab = self._create_visible_tab()
        try:
            self.assertFalse(hasattr(tab, "target_combo"))
            self.assertTrue(hasattr(tab, "format_combo"))
            self.assertEqual(tab.pane_tabs.count(), 3)
            self.assertEqual(tab.pane_tabs.tabText(0), "モデル")
            self.assertEqual(tab.pane_tabs.tabText(1), "アニメーション")
            self.assertEqual(tab.pane_tabs.tabText(2), "カメラ")
            self.assertEqual(tab.export_button.text(), "モデルを書き出す")
            self.assertEqual(tab.build_request("model_ROOT").options["export_format"], "pmx")
            pane_spy = QtSignalInvocationSpy(
                "ExportTab.pane_changed", tab.pane_tabs.currentChanged, tab.pane_tabs
            )
            tab.pane_tabs.setCurrentIndex(1)
            pane_spy.stop()
            self.assertTrue(tab.bake_export_check.isChecked())
            self.assertFalse(tab.bake_export_check.isEnabled())
            self.assertFalse(hasattr(tab._pages[tab.MOTION_PANE], "camera_export_check"))
            self.assertFalse(hasattr(tab._pages[tab.MOTION_PANE], "light_export_check"))
            self.assertEqual(tab.export_button.text(), "アニメーションを書き出し")
            tab.bake_export_check.click()
            range_spy = QtSignalInvocationSpy(
                "ExportTab.frame_range_changed", tab.frame_range_check.toggled, tab.frame_range_check
            )
            tab.frame_range_check.setChecked(True)
            start_spy = QtSignalInvocationSpy(
                "ExportTab.frame_range_changed", tab.frame_start_spin.valueChanged, tab.frame_start_spin
            )
            tab.frame_start_spin.setValue(12)
            end_spy = QtSignalInvocationSpy(
                "ExportTab.frame_range_changed", tab.frame_end_spin.valueChanged, tab.frame_end_spin
            )
            tab.frame_end_spin.setValue(42)
            request = tab.build_request("model_ROOT")
            self.assertEqual(request.options["export_format"], "vmd")
            self.assertEqual(request.options["current_model_root"], "model_ROOT")
            self.assertEqual(request.options["export_strategy"], VMD_EXPORT_BAKE_TIMELINE)
            self.assertEqual(request.options["export_target"], "character")
            self.assertTrue(tab.bake_export_check.isChecked())
            self.assertEqual(request.options["frame_range"], (12, 42))
            tab.pane_tabs.setCurrentIndex(2)
            self.assertEqual(tab.export_button.text(), "カメラを書き出し")
            camera_request = tab.build_request(None)
            self.assertEqual(camera_request.options["export_target"], "camera")
            self.assertEqual(
                camera_request.options["export_strategy"],
                VMD_EXPORT_BAKE_TIMELINE,
            )
            self.assertFalse(camera_request.options["require_target"])
            self.assertFalse(camera_request.options["require_current_model"])
            self.assertIsNone(camera_request.options["current_model_root"])
            tab.strategy_combo.setCurrentIndex(
                tab.strategy_combo.findData(VMD_EXPORT_PRESERVE_KEYS)
            )
            self.assertEqual(
                tab.build_request(None).options["export_strategy"],
                VMD_EXPORT_PRESERVE_KEYS,
            )
            self.assertFalse(tab.frame_range_check.isEnabled())
            self.assertFalse(tab.frame_range_check.isChecked())
            tab.light_export_check.setChecked(True)
            self.assertEqual(
                tab.build_request(None).options["export_target"],
                "camera+light",
            )
            tab.set_operation_active(True)
            self.assertFalse(tab.cancel_button.isVisible())
            self.assertFalse(tab.cancel_button.isEnabled())
            tab.set_operation_active(False)
            self.assertFalse(tab.cancel_button.isVisible())
            tab.pane_tabs.setCurrentIndex(1)
            _emit_witness(
                "export.pane_selector",
                "selector",
                "objectName=exportCategoryStack",
                "QTest.setCurrentIndex(objectName=exportCategoryStack, animation)",
                "model, animation, and camera panes expose scoped export formats",
                pane_spy,
                tab.pane_tabs,
            )
            _emit_witness(
                "export.motion_frame_range",
                "selector",
                "objectName=motionUseFrameRange",
                "QTest.setChecked(objectName=motionUseFrameRange, true)",
                "VMD request carries enabled frame range",
                range_spy,
                tab.frame_range_check,
            )
            _emit_witness(
                "export.motion_frame_start",
                "selector",
                "objectName=motionFrameStart",
                "QTest.setValue(objectName=motionFrameStart, 12)",
                "VMD request frame start equals 12",
                start_spy,
                tab.frame_start_spin,
            )
            _emit_witness(
                "export.motion_frame_end",
                "selector",
                "objectName=motionFrameEnd",
                "QTest.setValue(objectName=motionFrameEnd, 42)",
                "VMD request frame end equals 42",
                end_spy,
                tab.frame_end_spin,
            )
            tab.pane_tabs.setCurrentIndex(1)
            format_spy = QtSignalInvocationSpy(
                "ExportTab.format_changed",
                tab.format_combo.currentIndexChanged,
                tab.format_combo,
            )
            tab.format_combo.setCurrentIndex(1)
            pose_request = tab.build_request("model_ROOT")
            self.assertEqual(pose_request.options["export_format"], "vpd")
            self.assertEqual(pose_request.options["export_strategy"], "current_pose")
            self.assertEqual(tab.export_button.text(), "ポーズを書き出し")
            self.assertTrue(tab.pose_help.isVisible())
            self.assertFalse(tab.frame_range_check.isVisible())
            _emit_witness(
                "export.motion_format",
                "selector",
                "objectName=motionExportFormat",
                "QTest.setCurrentIndex(objectName=motionExportFormat, VPD)",
                "VPD request uses current-pose export",
                format_spy,
                tab.format_combo,
            )
        finally:
            self._delete_tab(tab)

    def test_operation_cleanup_restores_the_original_page_after_tab_switch(self):
        """A model operation must not re-enable or disable the switched page."""
        translator = UITranslator.instance()
        tab = self._create_visible_tab()
        try:
            animation_page = tab._pages[tab.MOTION_PANE]
            animation_page.export_button.setEnabled(False)

            tab.pane_tabs.setCurrentIndex(0)
            tab.set_operation_active(True)
            self.assertFalse(tab._pages[tab.MODEL_PANE].export_button.isEnabled())

            tab.pane_tabs.setCurrentIndex(1)
            tab.set_progress("writer")
            self.assertEqual(
                tab._pages[tab.MODEL_PANE].state_label.text(),
                translator.translate("writing_temporary_file", "export_status"),
            )
            self.assertEqual(
                animation_page.state_label.text(),
                translator.translate("editing", "export_status"),
            )
            tab.set_operation_active(False)

            self.assertTrue(tab._pages[tab.MODEL_PANE].export_button.isEnabled())
            self.assertFalse(animation_page.export_button.isEnabled())
        finally:
            self._delete_tab(tab)

    def test_vpd_operation_exposes_cancel_only_while_active(self):
        tab = self._create_visible_tab()
        try:
            tab.pane_tabs.setCurrentIndex(1)
            motion_page = tab._pages[tab.MOTION_PANE]
            motion_page.format_combo.setCurrentIndex(1)
            self.assertTrue(motion_page.cancel_button.isVisible())
            self.assertFalse(motion_page.cancel_button.isEnabled())

            tab.set_operation_active(True)
            self.assertFalse(motion_page.export_button.isEnabled())
            self.assertFalse(motion_page.format_combo.isEnabled())
            self.assertTrue(motion_page.cancel_button.isEnabled())

            tab.set_operation_active(False)
            self.assertTrue(motion_page.export_button.isEnabled())
            self.assertTrue(motion_page.format_combo.isEnabled())
            self.assertFalse(motion_page.cancel_button.isEnabled())
        finally:
            self._delete_tab(tab)

    def test_operation_page_owns_format_and_extension_after_tab_switch(self):
        """A Model click stays PMX-authoritative while another pane is visible."""
        tab = self._create_visible_tab()
        try:
            tab.pane_tabs.setCurrentIndex(0)
            tab.output_path_edit.setText("test.vmd")
            tab.set_operation_active(True)

            tab.pane_tabs.setCurrentIndex(1)
            request = tab.build_request("model_ROOT")

            self.assertEqual(request.options["export_format"], "pmx")
            self.assertTrue(request.file_path.endswith("test.pmx"))
            self.assertNotIn("export_strategy", request.options)
        finally:
            tab.set_operation_active(False)
            self._delete_tab(tab)

    def test_export_result_returns_to_operation_page_after_pane_switch(self):
        """An in-flight Animation result must not land on the Model page."""
        translator = UITranslator.instance()
        tab = self._create_visible_tab()
        report = ExportValidationReport(
            "vmd",
            (),
            mode=VMD_EXPORT_BAKE_TIMELINE,
        )
        workflow = _PaneSwitchWorkflow(
            report,
            lambda: tab.pane_tabs.setCurrentIndex(0),
        )
        app_state = _GuiAppState()
        presenter = ExportPresenter(tab, app_state, workflow_service=workflow)
        try:
            tab.pane_tabs.setCurrentIndex(1)
            model_page = tab._pages[tab.MODEL_PANE]
            motion_page = tab._pages[tab.MOTION_PANE]

            tab.export_button.click()
            QApplication.processEvents()

            self.assertEqual(tab.active_pane, tab.MOTION_PANE)
            self.assertEqual(
                model_page.state_label.text(),
                translator.translate("editing", "export_status"),
            )
            self.assertIsNone(model_page.validation_console.report)
            self.assertEqual(
                motion_page.state_label.text(),
                translator.translate("completed", "export_status"),
            )
            self.assertIs(motion_page.validation_console.report, report)
            self.assertTrue(motion_page.export_button.isEnabled())
        finally:
            presenter.deleteLater()
            app = QApplication.instance()
            if app is not None:
                app.sendPostedEvents(presenter, QtCore.QEvent.DeferredDelete)
            self._delete_tab(tab)

    def test_button_status_follows_one_shot_progress_and_terminal_result(self):
        """The button-adjacent status is translated separately from the Console."""
        translator = UITranslator.instance()
        tab = self._create_visible_tab()
        try:
            expected = {
                "scene_preflight": translator.translate(
                    "validating_scene", "export_status"
                ),
                "payload_collection": translator.translate(
                    "collecting_animation", "export_status"
                ),
                "writer": translator.translate(
                    "writing_temporary_file", "export_status"
                ),
                "report_ready": translator.translate("finalizing", "export_status"),
            }
            for stage, label in expected.items():
                tab.set_progress(stage)
                self.assertEqual(tab.state_label.text(), label)

            tab.set_result(
                ExportWorkflowResult(
                    STATE_SUCCEEDED,
                    ExportValidationReport("pmx", ()),
                    {},
                )
            )
            self.assertEqual(
                tab.state_label.text(),
                translator.translate("completed", "export_status"),
            )
        finally:
            self._delete_tab(tab)

    def test_validation_console_renders_fatal_warning_and_clean_reports(self):
        """One read-only English console is the screen and Copy authority."""
        tab = self._create_visible_tab()
        try:
            console = tab.validation_console
            self.assertFalse(console.details_button.isVisible())
            fatal = ExportValidationReport(
                "vmd",
                (
                    ExportValidationIssue(
                        "EXPORT_OPTIONS_INVALID",
                        "fatal",
                        True,
                        "frame_range",
                        "current scene frame range is invalid",
                        "Choose a valid start and end frame, then retry.",
                        {"start": 42, "end": 12},
                    ),
                ),
                mode="bake_timeline",
            )
            console.set_report(fatal, {"fixture": "gui_validation_console"})
            QApplication.processEvents()
            self.assertTrue(console.details_button.isVisible())
            fatal_text = console.console_text.toPlainText()
            self.assertIn("Reason: current scene frame range is invalid", fatal_text)
            self.assertIn("Action: Choose a valid start and end frame, then retry.", fatal_text)
            self.assertIn("[FATAL, BLOCKED]", fatal_text)
            self.assertNotIn("Details:", fatal_text)
            console.details_button.setChecked(True)
            QApplication.processEvents()
            details_text = console.console_text.toPlainText()
            self.assertIn("[FATAL] BLOCKED", details_text)
            self.assertIn('Details: {"end": 12, "start": 42}', details_text)
            self.assertIn("red", console.console_text.styleSheet().lower())
            self.assertTrue(console.console_text.isReadOnly())
            self.assertFalse(hasattr(console, "filter_combo"))
            self.assertFalse(hasattr(console, "issue_list"))
            self.assertFalse(hasattr(console, "detail_text"))
            self.assertFalse(hasattr(console, "warnings_acknowledged"))
            warning = ExportValidationReport(
                "vmd",
                (
                    ExportValidationIssue(
                        "UNSUPPORTED_FEATURE",
                        "warning",
                        False,
                        "feature",
                        "feature is not supported",
                        "Remove the unsupported feature and retry export.",
                    ),
                ),
                mode="bake_timeline",
            )
            console.set_report(warning)
            self.assertIn("[WARNING] Validation report", console.console_text.toPlainText())
            self.assertNotIn("red", console.console_text.styleSheet().lower())

            clean = ExportValidationReport("vmd", (), mode="bake_timeline")
            console.set_report(clean)
            self.assertEqual(
                console.console_text.toPlainText(),
                "[INFO] Validation passed: no errors or warnings were found.",
            )
        finally:
            self._delete_tab(tab)

    def test_status_controls_translate_but_console_body_stays_english(self):
        """Workflow controls translate while the Validation Console stays English."""
        translator = UITranslator.instance()
        previous_language = translator.get_language()
        translator.set_language("ja")
        tab = self._create_visible_tab()
        try:
            self.assertEqual(tab.export_button.text(), "モデルを書き出す")
            self.assertEqual(tab.apply_scale_check.text(), "スケールを適用")
            tab.pane_tabs.setCurrentIndex(1)
            self.assertEqual(
                tab.bake_export_check.text(),
                "ベイク書き出し",
            )
            tab.pane_tabs.setCurrentIndex(0)
            motion_page = tab._pages[tab.MOTION_PANE]
            self.assertEqual(
                tab._model_form.labelForField(tab.apply_scale_check).text(),
                "オプション",
            )
            self.assertEqual(
                motion_page._motion_form.labelForField(
                    motion_page.frame_range_check
                ).text(),
                "範囲",
            )
            self.assertEqual(
                motion_page._motion_form.labelForField(
                    motion_page.frame_start_spin
                ).text(),
                "開始",
            )
            self.assertEqual(
                motion_page._motion_form.labelForField(
                    motion_page.frame_end_spin
                ).text(),
                "終了",
            )
            self.assertFalse(hasattr(tab.validation_console, "revalidate_button"))
            self.assertFalse(hasattr(tab.validation_console, "save_button"))

            report = ExportValidationReport(
                "vmd",
                (
                    ExportValidationIssue(
                        "EXPORT_OPTIONS_INVALID",
                        "fatal",
                        True,
                        "frame_range",
                        "current scene frame range is invalid",
                    ),
                ),
                mode=VMD_EXPORT_BAKE_TIMELINE,
            )
            tab.validation_console.set_report(report)
            QApplication.processEvents()
            english_console = tab.validation_console.console_text.toPlainText()
            self.assertIn("Export strategy: bake_timeline", english_console)
            self.assertIn("Reason: current scene frame range is invalid", english_console)
            self.assertIn("Action:", english_console)

            translator.set_language("en")
            translate_spy = ActionInvocationSpy.wrap(
                "ExportTab.retranslateUi", tab.retranslateUi, tab.apply_scale_check
            )
            translate_spy()
            self.assertEqual(tab.export_button.text(), "Export Model")
            self.assertEqual(tab.apply_scale_check.text(), "Apply Scale")
            tab.pane_tabs.setCurrentIndex(1)
            self.assertEqual(
                tab.bake_export_check.text(),
                "Bake Export",
            )
            tab.pane_tabs.setCurrentIndex(0)
            self.assertEqual(
                tab._model_form.labelForField(tab.apply_scale_check).text(),
                "Options",
            )
            self.assertEqual(
                motion_page._motion_form.labelForField(
                    motion_page.frame_range_check
                ).text(),
                "Range",
            )
            self.assertEqual(
                motion_page._motion_form.labelForField(
                    motion_page.frame_start_spin
                ).text(),
                "Start",
            )
            self.assertEqual(
                motion_page._motion_form.labelForField(
                    motion_page.frame_end_spin
                ).text(),
                "End",
            )
            self.assertFalse(hasattr(tab.validation_console, "revalidate_button"))
            _emit_witness(
                "export.apply_scale",
                "selector",
                "objectName=modelApplyScale",
                "QTest.inspect(objectName=modelApplyScale)",
                "apply-scale control follows Japanese then English translation",
                translate_spy,
                tab.apply_scale_check,
            )
            tab.pane_tabs.setCurrentIndex(1)
            self.assertEqual(tab.pane_tabs.tabText(1), "Animation")
            self.assertEqual(tab.export_button.text(), "Export Animation")
        finally:
            self._delete_tab(tab)
            translator.set_language(previous_language)

    def test_pane_report_and_output_state_are_isolated(self):
        """Switching panes restores each report/path without mixing them."""
        tab = self._create_visible_tab()
        try:
            model_output_edit = tab.output_path_edit
            # Export paths persist as user preferences.  Normalize first so a
            # repeated GUI run still performs a real text-change interaction.
            tab.output_path_edit.setText("")
            output_spy = QtSignalInvocationSpy(
                "ExportTab.output_path_changed",
                tab.output_path_edit.textChanged,
                tab.output_path_edit,
            )
            tab.output_path_edit.setText("model.vmd")
            output_spy.stop()
            model_report = ExportValidationReport(
                "pmx",
                (),
                mode="model",
            )
            tab.validation_console.set_report(model_report)
            self.assertTrue(tab.build_request("model_ROOT").file_path.endswith("model.pmx"))
            self.assertEqual(tab.output_path_edit.text(), "model.vmd")
            _emit_witness(
                "export.output_path",
                "selector",
                "objectName=exportOutputPath",
                "QTest.setText(objectName=exportOutputPath, model.vmd)",
                "per-pane output paths preserve typed text and coerce only requests",
                output_spy,
                model_output_edit,
            )

            tab.pane_tabs.setCurrentIndex(1)
            self.assertIsNone(tab.validation_console.report)
            tab.output_path_edit.setText("motion.pmx")
            motion_report = ExportValidationReport(
                "vmd",
                (),
                mode="bake_timeline",
            )
            tab.validation_console.set_report(motion_report)

            tab.pane_tabs.setCurrentIndex(0)
            self.assertIs(tab.validation_console.report, model_report)
            self.assertEqual(tab.output_path_edit.text(), "model.vmd")

            tab.apply_scale_check.setChecked(not tab.apply_scale_check.isChecked())
            self.assertIsNone(tab.validation_console.report)
            tab.pane_tabs.setCurrentIndex(1)
            self.assertIs(tab.validation_console.report, motion_report)
            self.assertEqual(tab.output_path_edit.text(), "motion.pmx")
        finally:
            self._delete_tab(tab)

    def test_current_model_change_invalidates_both_panes(self):
        """Current Model changes clear both pane reports."""
        tab = self._create_visible_tab()
        try:
            report = ExportValidationReport(
                "vmd",
                (),
                mode="bake_timeline",
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

    def test_export_button_emits_one_workflow_request(self):
        """The only workflow button routes one request to the presenter."""
        tab = self._create_visible_tab()
        events = []
        tab.export_requested.connect(lambda: events.append("export"))
        try:
            export_spy = QtSignalInvocationSpy(
                "ExportTab.export_requested", tab.export_button.clicked, tab.export_button
            )
            tab.export_button.click()
            QApplication.processEvents()
            self.assertEqual(events, ["export"])
            _emit_witness(
                "export.submit",
                "attribute",
                "export_button",
                "QTest.click(attribute=export_button)",
                "export signal emitted once",
                export_spy,
                tab.export_button,
            )
        finally:
            self._delete_tab(tab)


if __name__ == "__main__":
    unittest.main()
