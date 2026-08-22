"""ExportPresenter's single-button operation boundary."""

import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.services.export_workflow_service import (  # noqa: E402
    ExportWorkflowRequest,
    ExportWorkflowResult,
    STATE_FAILED,
    STATE_SUCCEEDED,
)
from mmd_tools.ui.presenters import export_presenter  # noqa: E402
from mmd_tools.ui.presenters.export_presenter import ExportPresenter  # noqa: E402
from mmd_tools.validation.export_validator import (  # noqa: E402
    ExportValidationIssue,
    ExportValidationReport,
)


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _View:
    def __init__(self):
        self.export_requested = _Signal()
        self.current_export_format = "vmd"
        self.states = []
        self.operation_states = []
        self.results = []
        self.invalidations = 0

    def build_request(self, root):
        return ExportWorkflowRequest(
            "motion.vmd",
            {"export_format": "vmd", "current_model_root": root},
        )

    def set_state(self, state):
        self.states.append(state)

    def set_operation_active(self, active):
        self.operation_states.append(active)

    def set_result(self, result):
        self.results.append(result)

    def invalidate_all_panes(self):
        self.invalidations += 1


class _AppState:
    current_model_root = "model_ROOT"

    def __init__(self):
        self.current_model_changed = _Signal()
        self.progress = []
        self.statuses = []

    def begin_progress(self, label):
        self.progress.append(("begin", label))
        return 1

    def update_progress_state(self, token, label, percentage):
        self.progress.append(("update", token, label, percentage))

    def end_progress(self, token):
        self.progress.append(("end", token))

    def emit_status(self, message):
        self.statuses.append(message)


class _Workflow:
    def __init__(self):
        self.requests = []

    def execute(self, request, *, warning_callback=None, progress_callback=None, **_kwargs):
        self.requests.append(request)
        progress_callback("scene_preflight")
        progress_callback("payload_collection")
        progress_callback("writer")
        progress_callback("report_ready")
        return ExportWorkflowResult(
            STATE_SUCCEEDED,
            ExportValidationReport("vmd", (), mode="bake_timeline"),
            {"output_path": request.file_path},
        )


class _WarningWorkflow(_Workflow):
    """Invoke the presenter's callback within the same execute call."""

    def __init__(self):
        super().__init__()
        self.callback_results = []
        self.report = ExportValidationReport(
            "vmd",
            (ExportValidationIssue("OUTPUT_VERIFY_FAILED", "warning", False, "output", "confirm"),),
            mode="bake_timeline",
        )

    def execute(self, request, *, warning_callback=None, **kwargs):
        self.requests.append(request)
        approved = bool(warning_callback(self.report)) if callable(warning_callback) else False
        self.callback_results.append(approved)
        return ExportWorkflowResult(
            STATE_SUCCEEDED if approved else STATE_FAILED,
            self.report,
            {"output_path": request.file_path},
        )


class _DialogButton:
    def __init__(self, text):
        self.text = text


class _WarningDialog:
    AcceptRole = 1
    RejectRole = 2
    choose_accept = True
    created = []

    def __init__(self, *_args):
        self.buttons = []
        self.clicked = None
        type(self).created.append(self)

    def setWindowTitle(self, _title):
        return None

    def setText(self, _text):
        return None

    def setInformativeText(self, _text):
        return None

    def addButton(self, text, role):
        button = _DialogButton(text)
        self.buttons.append((button, role))
        return button

    def exec_(self):
        selected_role = self.AcceptRole if self.choose_accept else self.RejectRole
        self.clicked = next(button for button, role in self.buttons if role == selected_role)

    def clickedButton(self):
        return self.clicked


class ExportPresenterTests(unittest.TestCase):
    def test_only_export_signal_is_bound_and_runs_one_workflow_call(self):
        view = _View()
        app_state = _AppState()
        workflow = _Workflow()
        presenter = ExportPresenter(view, app_state, workflow)

        self.assertEqual(len(view.export_requested.slots), 1)
        result = presenter.export()

        self.assertTrue(result.succeeded)
        self.assertEqual(len(workflow.requests), 1)
        self.assertEqual(view.operation_states, [True, False])
        self.assertEqual(view.results[-1], result)
        self.assertEqual(app_state.statuses[-1], "Completed")

    def test_current_model_change_only_invalidates_visible_reports(self):
        view = _View()
        app_state = _AppState()
        ExportPresenter(view, app_state, _Workflow())

        app_state.current_model_changed.slots[0]("other_ROOT")

        self.assertEqual(view.invalidations, 1)

    def test_warning_dialog_approves_or_cancels_inside_one_export_call(self):
        for approve in (True, False):
            with self.subTest(approve=approve):
                view = _View()
                app_state = _AppState()
                workflow = _WarningWorkflow()
                _WarningDialog.choose_accept = approve
                _WarningDialog.created = []
                with patch.object(export_presenter, "QMessageBox", _WarningDialog):
                    result = ExportPresenter(view, app_state, workflow).export()

                self.assertEqual(len(workflow.requests), 1)
                self.assertEqual(workflow.callback_results, [approve])
                self.assertEqual(result.state, STATE_SUCCEEDED if approve else STATE_FAILED)
                self.assertEqual(
                    [button.text for button, _role in _WarningDialog.created[0].buttons],
                    ["Export Anyway", "Cancel"],
                )


if __name__ == "__main__":
    unittest.main()
