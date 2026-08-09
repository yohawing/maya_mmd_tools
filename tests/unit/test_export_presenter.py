"""ExportPresenter Current Model ownership and pane invalidation contracts."""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.services.export_workflow_service import (  # noqa: E402
    ExportWorkflowResult,
    STATE_READY,
)
from mmd_tools.ui.presenters.export_presenter import ExportPresenter  # noqa: E402
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


class _View:
    validate_requested = _Signal()
    export_requested = _Signal()

    def __init__(self):
        self.validation_console = _Console()
        self.roots = []
        self.states = []
        self.results = []
        self.invalidations = 0

    def build_request(self, current_model_root=None):
        self.roots.append(current_model_root)
        return type("Request", (), {"file_path": "model.pmx", "options": {}})()

    def set_state(self, state):
        self.states.append(state)

    def set_result(self, result):
        self.results.append(result)

    def invalidate_all_panes(self):
        self.invalidations += 1


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
        presenter = ExportPresenter(view, app_state, workflow_service=_Workflow())

        app_state.current_model_changed.emit("OtherModel_ROOT")

        self.assertEqual(view.invalidations, 1)
        presenter.deleteLater()


if __name__ == "__main__":
    unittest.main()
