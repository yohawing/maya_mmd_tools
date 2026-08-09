"""ExportPresenter Current Model ownership and pane invalidation contracts."""

import unittest
from unittest.mock import MagicMock

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.services.export_workflow_service import (  # noqa: E402
    ExportWorkflowResult,
    STATE_FAILED,
    STATE_READY,
)
from mmd_tools.ui.qt_compat import QApplication  # noqa: E402
from mmd_tools.ui.presenters.export_presenter import ExportPresenter  # noqa: E402
from mmd_tools.ui.validation_console import (  # noqa: E402
    ValidationConsole,
    render_validation_console_text,
)
from mmd_tools.ui.translations import UITranslator  # noqa: E402
from mmd_tools.validation.export_validator import ExportValidationReport  # noqa: E402


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _Console:
    acknowledgement_changed = _Signal()
    warnings_acknowledged = False


def _create_validation_console():
    """Create the real Console, filling only unavailable CI widget surfaces."""
    if callable(getattr(QApplication, "instance", None)):
        return ValidationConsole()
    console = ValidationConsole.__new__(ValidationConsole)
    console._translator = UITranslator.instance()
    console._report = None
    console._metadata = {}
    console._visible_issue_indices = []
    console.acknowledge_check = MagicMock()
    console.acknowledge_check.isChecked.return_value = False
    console.filter_combo = MagicMock()
    console.filter_combo.currentData.return_value = "all"
    console.filter_combo.findData.return_value = 0
    console.summary_label = MagicMock()
    console.issue_list = MagicMock()
    console.detail_text = MagicMock()
    return console


class _View:
    validate_requested = _Signal()
    export_requested = _Signal()

    def __init__(self, export_format="pmx"):
        self.validation_console = _Console()
        self.current_export_format = export_format
        self.roots = []
        self.states = []
        self.results = []
        self.invalidations = 0

    def build_request(self, current_model_root=None):
        self.roots.append(current_model_root)
        mode = "C" if self.current_export_format == "vmd" else "model"
        return type(
            "Request",
            (),
            {
                "file_path": f"asset.{self.current_export_format}",
                "options": {
                    "export_format": self.current_export_format,
                    "vmd_mode": mode,
                },
            },
        )()

    def set_state(self, state):
        self.states.append(state)

    def set_result(self, result):
        self.results.append(result)
        self.states.append(result.state)

    def invalidate_all_panes(self):
        self.invalidations += 1


class _ConsoleView(_View):
    """View double that routes results through the real ValidationConsole."""

    def __init__(self, export_format="pmx"):
        super().__init__(export_format)
        self.validation_console = _create_validation_console()

    def set_result(self, result):
        super().set_result(result)
        self.validation_console.set_report(result.report, result.metadata)


class _AppState:
    current_model_root = "CurrentModel_ROOT"

    def __init__(self):
        self.current_model_changed = _Signal()
        self.statuses = []

    def emit_status(self, message):
        self.statuses.append(message)


class _Workflow:
    def __init__(self):
        self.validated = []
        self.executed = []

    @staticmethod
    def _result():
        return ExportWorkflowResult(STATE_READY, ExportValidationReport("pmx", ()), {})

    def validate(self, request):
        self.validated.append(request)
        return self._result()

    def execute(self, request, *, acknowledge_warnings=False):
        self.executed.append((request, acknowledge_warnings))
        return self._result()


class _FailingWorkflow:
    def validate(self, request):
        del request
        raise RuntimeError("validation exploded")

    def execute(self, request, *, acknowledge_warnings=False):
        del request, acknowledge_warnings
        raise RuntimeError("execution exploded")


class TestExportPresenter(unittest.TestCase):
    """Presenter must use Current Model and invalidate both panes on change."""

    def test_validate_and_export_pass_shared_current_model_root(self):
        view = _View()
        app_state = _AppState()
        workflow = _Workflow()
        presenter = ExportPresenter(view, app_state, workflow_service=workflow)

        presenter.validate()
        presenter.export()

        self.assertEqual(view.roots, ["CurrentModel_ROOT", "CurrentModel_ROOT"])
        self.assertEqual(len(workflow.validated), 1)
        self.assertEqual(len(workflow.executed), 1)

    def test_current_model_changed_invalidates_all_panes(self):
        view = _View()
        app_state = _AppState()
        ExportPresenter(view, app_state, workflow_service=_Workflow())

        app_state.current_model_changed.emit("OtherModel_ROOT")

        self.assertEqual(view.invalidations, 1)

    def test_validate_exception_publishes_terminal_failed_result(self):
        view = _View()
        app_state = _AppState()
        presenter = ExportPresenter(view, app_state, workflow_service=_FailingWorkflow())

        result = presenter.validate()

        self.assertEqual(result.state, STATE_FAILED)
        self.assertIs(result, view.results[-1])
        self.assertEqual(view.states[-1], STATE_FAILED)
        self.assertIn("validation exploded", app_state.statuses[-1])
        self.assertEqual(result.report.export_format, "pmx")
        self.assertEqual(result.report.mode, "model")
        self.assertTrue(result.report.issues[0].blocking)
        self.assertEqual(result.report.issues[0].severity, "fatal")
        self.assertEqual(result.report.issues[0].path, "export.model")
        self.assertIn("validation exploded", result.report.issues[0].message)

    def test_execute_exception_publishes_terminal_failed_result(self):
        instance = getattr(QApplication, "instance", None)
        app = (instance() or QApplication([])) if callable(instance) else None
        view = _ConsoleView("vmd")
        app_state = _AppState()
        presenter = ExportPresenter(view, app_state, workflow_service=_FailingWorkflow())

        result = presenter.export()

        self.assertEqual(result.state, STATE_FAILED)
        self.assertIs(result, view.results[-1])
        self.assertEqual(view.states[-1], STATE_FAILED)
        self.assertIn("execution exploded", app_state.statuses[-1])
        self.assertEqual(result.report.export_format, "vmd")
        self.assertEqual(result.report.mode, "C")
        self.assertTrue(result.report.issues[0].blocking)
        self.assertEqual(result.report.issues[0].severity, "fatal")
        self.assertEqual(result.report.issues[0].path, "export.motion")
        self.assertIn("execution exploded", result.report.issues[0].message)
        self.assertIs(view.validation_console.report, result.report)
        rendered = render_validation_console_text(result.report, result.metadata)
        self.assertIn(
            "EXPORT_WORKFLOW_EXCEPTION",
            rendered,
        )
        self.assertIn(
            "execution exploded",
            rendered,
        )
        if app is not None:
            self.assertEqual(view.validation_console.issue_list.count(), 1)
            self.assertIn("vmd / C", view.validation_console.summary_label.text())
            self.assertIn(
                "EXPORT_WORKFLOW_EXCEPTION",
                view.validation_console.detail_text.toPlainText(),
            )
            view.validation_console.close()
            view.validation_console.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
