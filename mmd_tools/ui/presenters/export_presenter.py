"""Presenter for the top-level validated Export workflow."""

from ..qt_compat import QObject
from ...core.logger import get_logger
from ...services.export_workflow_service import (
    ExportWorkflowResult,
    ExportWorkflowService,
    STATE_EXPORTING,
    STATE_FAILED,
    STATE_VALIDATING,
)
from ...validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
)


logger = get_logger(__name__)


class ExportPresenter(QObject):
    """Keep ExportTab presentation thin and route all decisions to the service."""

    def __init__(self, view, app_state, workflow_service=None):
        super().__init__()
        self.view = view
        self.app_state = app_state
        self.workflow_service = workflow_service or ExportWorkflowService(
            scene_service=getattr(app_state, "scene_model_service", None),
        )
        self.view.presenter = self
        self.view.validate_requested.connect(self.validate)
        self.view.export_requested.connect(self.export)
        self.view.validation_console.acknowledgement_changed.connect(
            self._on_acknowledgement_changed
        )
        current_model_changed = getattr(app_state, "current_model_changed", None)
        if current_model_changed is not None:
            current_model_changed.connect(self._on_current_model_changed)

    def _on_current_model_changed(self, model_root):
        """Invalidate both panes when the shared Current Model changes."""
        del model_root
        self.view.invalidate_all_panes()

    def _on_acknowledgement_changed(self, _acknowledged):
        """Acknowledgement changes affect only the next service execution."""
        return None

    def validate(self):
        """Run preflight and payload validation without writing an output."""
        self.view.set_state(STATE_VALIDATING)
        request = None
        try:
            request = self.view.build_request(
                getattr(self.app_state, "current_model_root", None)
            )
            result = self.workflow_service.validate(request)
        except Exception as exc:
            logger.error("Export validation failed before result creation: %s", exc, exc_info=True)
            return self._publish_failure("Export validation failed", exc, request)
        self.view.set_result(result)
        self._emit_status(result)
        return result

    def export(self):
        """Revalidate and execute the validated export action."""
        self.view.set_state(STATE_EXPORTING)
        request = None
        try:
            request = self.view.build_request(
                getattr(self.app_state, "current_model_root", None)
            )
            result = self.workflow_service.execute(
                request,
                acknowledge_warnings=self.view.validation_console.warnings_acknowledged,
            )
        except Exception as exc:
            logger.error("Export workflow failed before result creation: %s", exc, exc_info=True)
            return self._publish_failure("Export failed", exc, request)
        self.view.set_result(result)
        self._emit_status(result)
        return result

    def _publish_failure(self, status_prefix, error, request=None):
        """Replace stale validation UI with one terminal workflow failure."""
        options = dict(getattr(request, "options", None) or {})
        export_format = options.get("export_format") or getattr(
            self.view, "current_export_format", None
        )
        export_format = str(export_format or "").lower() or None
        is_motion = export_format == "vmd"
        mode = str(options.get("vmd_mode") or "C") if is_motion else "model"
        issue = ExportValidationIssue(
            "EXPORT_WORKFLOW_EXCEPTION",
            "fatal",
            True,
            "export.motion" if is_motion else "export.model",
            f"{type(error).__name__}: {error}",
        )
        result = ExportWorkflowResult(
            STATE_FAILED,
            ExportValidationReport(
                export_format,
                (issue,),
                mode=mode,
            ),
            {"output_path": getattr(request, "file_path", None)},
            error=error,
        )
        self.view.set_result(result)
        self.app_state.emit_status(f"{status_prefix}: {error}")
        return result

    def _emit_status(self, result):
        """Expose workflow state without replacing the Validation Console."""
        if result.succeeded:
            self.app_state.emit_status(f"Export complete: {result.metadata.get('output_path') or ''}")
        elif result.error is not None:
            self.app_state.emit_status(f"Export failed: {result.error}")
        elif result.report.is_blocking:
            self.app_state.emit_status(
                f"Export blocked: {len(result.report.blocking_issues)} blocking issue(s)"
            )
        elif result.report.requires_warning_ack:
            self.app_state.emit_status("Export requires warning acknowledgement")
        else:
            self.app_state.emit_status(f"Export state: {result.state}")


__all__ = ["ExportPresenter"]
