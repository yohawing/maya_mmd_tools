"""ExportPresenter Current Model ownership and pane invalidation contracts."""

import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.services.export_workflow_service import (  # noqa: E402
    ExportWorkflowResult,
    STATE_FAILED,
    STATE_PREPARING,
    STATE_READY,
    STATE_SUCCEEDED,
)
from mmd_tools.actions.prepare_vmd_export_action import PrepareVmdExportResult  # noqa: E402
from mmd_tools.ui.qt_compat import QApplication  # noqa: E402
from mmd_tools.ui.presenters.export_presenter import ExportPresenter  # noqa: E402
from mmd_tools.ui.translations import UITranslator  # noqa: E402
from mmd_tools.ui.validation_console import (  # noqa: E402
    ValidationConsole,
    render_validation_console_text,
)
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

    def __init__(self):
        self.report = None
        self.metadata = {}

    def set_report(self, report, metadata=None):
        self.report = report
        self.metadata = dict(metadata or {})


def _create_validation_console():
    """Create the real Console only when a widget-capable app already exists.

    Maya standalone owns a ``QGuiApplication``.  ``QApplication.instance()``
    therefore returns a truthy object even though constructing a ``QWidget``
    is invalid and creating a second ``QApplication`` can crash Maya.
    """
    if _qapplication_instance() is not None:
        return ValidationConsole()
    return _Console()


def _qapplication_instance():
    """Return an existing widget-capable QApplication, if one is owned by the host."""
    instance_factory = getattr(QApplication, "instance", None)
    if not callable(instance_factory):
        return None
    app = instance_factory()
    if app is None:
        return None
    try:
        return app if isinstance(app, QApplication) else None
    except TypeError:
        # The pure-Python Qt stub is intentionally not a QApplication type.
        return None


class _View:
    validate_requested = _Signal()
    export_requested = _Signal()
    prepare_requested = _Signal()
    motion_semantic_changed = _Signal()

    def __init__(self, export_format="pmx"):
        self.validation_console = _Console()
        self.current_export_format = export_format
        self.roots = []
        self.states = []
        self.results = []
        self.invalidations = 0
        self.operation_states = []
        self.prepared_states = []

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

    def set_prepared(self, preparation):
        self.prepared_states.append(preparation)
        self.states.append("Prepared")

    def invalidate_all_panes(self):
        self.invalidations += 1

    def set_operation_active(self, active):
        self.operation_states.append(bool(active))


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
        self.progress = []
        self._progress_token = 0

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


class _Workflow:
    def __init__(self):
        self.validated = []
        self.executed = []
        self.prepared = []
        self.invalidated = []

    @staticmethod
    def _result():
        return ExportWorkflowResult(STATE_READY, ExportValidationReport("pmx", ()), {})

    def validate(self, request, *, progress_callback=None):
        self.validated.append(request)
        if progress_callback is not None:
            progress_callback("scene_preflight")
            progress_callback("payload_collection")
            progress_callback("payload_validation")
            progress_callback("report_ready")
        export_format = request.options.get("export_format", "pmx")
        mode = "C" if export_format == "vmd" else "model"
        return ExportWorkflowResult(
            STATE_READY,
            ExportValidationReport(export_format, (), mode=mode),
            {},
        )

    def execute(self, request, *, acknowledge_warnings=False, progress_callback=None):
        self.executed.append((request, acknowledge_warnings))
        if progress_callback is not None:
            progress_callback("scene_preflight")
            progress_callback("payload_collection")
            progress_callback("payload_validation")
            progress_callback("report_ready")
            progress_callback("writer")
        export_format = request.options.get("export_format", "pmx")
        mode = "C" if export_format == "vmd" else "model"
        return ExportWorkflowResult(
            STATE_SUCCEEDED,
            ExportValidationReport(export_format, (), mode=mode),
            {},
        )

    def prepare_vmd(self, request):
        self.prepared.append(request)
        return PrepareVmdExportResult(status="published", token=object())

    def invalidate_prepared_vmd(self, token=None):
        self.invalidated.append(token)
        return token is not None


class _PrepareFailingWorkflow(_Workflow):
    def prepare_vmd(self, request):
        self.prepared.append(request)
        return PrepareVmdExportResult(status="failed", error=RuntimeError("bake exploded"))


class _PrepareThenFailWorkflow(_Workflow):
    def prepare_vmd(self, request):
        self.prepared.append(request)
        if len(self.prepared) == 1:
            return PrepareVmdExportResult(status="published", token=object())
        return PrepareVmdExportResult(status="failed", error=RuntimeError("second bake exploded"))


class _FailingWorkflow:
    def validate(self, request, *, progress_callback=None):
        del request
        del progress_callback
        raise RuntimeError("validation exploded")

    def execute(self, request, *, acknowledge_warnings=False, progress_callback=None):
        del request, acknowledge_warnings, progress_callback
        raise RuntimeError("execution exploded")


class TestExportPresenter(unittest.TestCase):
    """Presenter must use Current Model and invalidate both panes on change."""

    def test_qapplication_instance_rejects_host_qgui_application(self):
        class _HostGuiApplication:
            pass

        class _FakeQApplication:
            @staticmethod
            def instance():
                return _HostGuiApplication()

        with patch(__name__ + ".QApplication", _FakeQApplication):
            self.assertIsNone(_qapplication_instance())

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
        self.assertEqual(view.operation_states, [True, False, True, False])
        self.assertEqual(app_state.progress[0][0], "begin")
        self.assertEqual(app_state.progress[-1][0], "end")

    def test_prepare_attaches_token_to_mode_c_validate_and_export_then_consumes_it(self):
        view = _View("vmd")
        app_state = _AppState()
        workflow = _Workflow()
        presenter = ExportPresenter(view, app_state, workflow_service=workflow)

        preparation = presenter.prepare()
        self.assertTrue(preparation.succeeded)
        self.assertEqual(view.states[0], STATE_PREPARING)
        self.assertEqual(len(view.prepared_states), 1)
        self.assertIs(presenter.prepared_vmd_token, preparation.token)

        presenter.validate()
        self.assertIs(workflow.validated[-1].prepared_vmd_token, preparation.token)
        presenter.export()
        self.assertIs(workflow.executed[-1][0].prepared_vmd_token, preparation.token)
        self.assertIsNone(presenter.prepared_vmd_token)
        self.assertEqual(workflow.invalidated, [preparation.token])

    def test_prepare_failure_publishes_blocking_result_and_keeps_no_token(self):
        view = _View("vmd")
        app_state = _AppState()
        presenter = ExportPresenter(
            view,
            app_state,
            workflow_service=_PrepareFailingWorkflow(),
        )

        result = presenter.prepare()

        self.assertEqual(result.state, STATE_FAILED)
        self.assertIsNone(presenter.prepared_vmd_token)
        self.assertTrue(result.report.is_blocking)
        self.assertIn("bake exploded", app_state.statuses[-1])
        self.assertEqual(view.operation_states[-2:], [True, False])

    def test_failed_reprepare_discards_the_previous_token(self):
        view = _View("vmd")
        app_state = _AppState()
        workflow = _PrepareThenFailWorkflow()
        presenter = ExportPresenter(
            view,
            app_state,
            workflow_service=workflow,
        )

        first = presenter.prepare()
        self.assertTrue(first.succeeded)
        self.assertIsNotNone(presenter.prepared_vmd_token)

        second = presenter.prepare()

        self.assertEqual(second.state, STATE_FAILED)
        self.assertIsNone(presenter.prepared_vmd_token)
        self.assertEqual(len(workflow.prepared), 2)
        self.assertEqual(len(workflow.invalidated), 1)
        self.assertIs(workflow.invalidated[0], first.token)

    def test_semantic_change_invalidates_prepared_token_but_output_change_does_not(self):
        view = _View("vmd")
        app_state = _AppState()
        workflow = _Workflow()
        presenter = ExportPresenter(view, app_state, workflow_service=workflow)

        presenter.prepare()
        self.assertIsNotNone(presenter.prepared_vmd_token)
        # Output path/report/ack changes have no semantic_changed signal.
        self.assertIsNotNone(presenter.prepared_vmd_token)
        view.motion_semantic_changed.emit()
        self.assertIsNone(presenter.prepared_vmd_token)

        presenter.prepare()
        app_state.current_model_changed.emit("OtherModel_ROOT")
        self.assertIsNone(presenter.prepared_vmd_token)

    def test_progress_labels_include_animation_format_and_writer_transition(self):
        view = _View("vmd")
        app_state = _AppState()
        presenter = ExportPresenter(view, app_state, workflow_service=_Workflow())

        presenter.export()

        labels = [entry[2] for entry in app_state.progress if entry[0] in ("begin", "update")]
        translator = UITranslator.instance()
        self.assertIn(
            translator.translate("animation_scene_preflight", "export_progress"), labels
        )
        self.assertIn(translator.translate("animation_writer", "export_progress"), labels)
        report_ready_label = translator.translate("animation_report_ready", "export_progress")
        report_ready = [entry for entry in app_state.progress if entry[2].endswith(report_ready_label)]
        self.assertEqual(report_ready[-1][3], 100)

    def test_prepare_progress_reports_timeline_and_prepared_payload_stages(self):
        view = _View("vmd")
        app_state = _AppState()
        presenter = ExportPresenter(view, app_state, workflow_service=_Workflow())

        presenter.prepare()

        labels = [entry[2] for entry in app_state.progress if entry[0] in ("begin", "update")]
        translator = UITranslator.instance()
        self.assertIn(
            translator.translate("animation_timeline_bake", "export_progress"), labels
        )
        prepared_label = translator.translate("animation_prepared_payload", "export_progress")
        self.assertIn(prepared_label, labels)
        prepared = [entry for entry in app_state.progress if entry[2] == prepared_label]
        self.assertEqual(prepared[-1][3], 100)

    def test_current_model_changed_invalidates_all_panes(self):
        view = _View()
        app_state = _AppState()
        ExportPresenter(view, app_state, workflow_service=_Workflow())

        app_state.current_model_changed.emit("OtherModel_ROOT")

        self.assertEqual(view.invalidations, 1)

    def test_default_workflow_uses_lazy_production_prepare_action(self):
        view = _View("vmd")
        app_state = _AppState()
        production_action = object()
        with patch(
            "mmd_tools.ui.presenters.export_presenter.create_maya_vmd_prepare_action",
            return_value=production_action,
        ):
            presenter = ExportPresenter(view, app_state)

        self.assertIs(presenter.workflow_service.prepare_vmd_action, production_action)

    def test_validate_exception_publishes_terminal_failed_result(self):
        view = _View()
        app_state = _AppState()
        presenter = ExportPresenter(view, app_state, workflow_service=_FailingWorkflow())

        result = presenter.validate()

        self.assertEqual(result.state, STATE_FAILED)
        self.assertIs(result, view.results[-1])
        self.assertEqual(view.states[-1], STATE_FAILED)
        self.assertIn("validation exploded", app_state.statuses[-1])
        self.assertEqual(view.operation_states, [True, False])
        self.assertEqual(app_state.progress[-1][0], "end")
        self.assertEqual(result.report.export_format, "pmx")
        self.assertEqual(result.report.mode, "model")
        self.assertTrue(result.report.issues[0].blocking)
        self.assertEqual(result.report.issues[0].severity, "fatal")
        self.assertEqual(result.report.issues[0].path, "export.model")
        self.assertIn("validation exploded", result.report.issues[0].message)

    def test_execute_exception_publishes_terminal_failed_result(self):
        app = _qapplication_instance()
        view = _ConsoleView("vmd")
        app_state = _AppState()
        presenter = ExportPresenter(view, app_state, workflow_service=_FailingWorkflow())

        result = presenter.export()

        self.assertEqual(result.state, STATE_FAILED)
        self.assertIs(result, view.results[-1])
        self.assertEqual(view.states[-1], STATE_FAILED)
        self.assertIn("execution exploded", app_state.statuses[-1])
        self.assertEqual(view.operation_states, [True, False])
        self.assertEqual(app_state.progress[-1][0], "end")
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
