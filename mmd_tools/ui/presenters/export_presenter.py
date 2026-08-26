"""Presenter for the single-action PMX/VMD/VPD export workflow."""

from ..qt_compat import QApplication, QMessageBox, QObject
from ...adapters.maya_vmd_prepare_backend import create_maya_bake_timeline_vmd_action
from ...core.logger import get_logger
from ...services.export_workflow_service import (
    ExportWorkflowResult,
    ExportWorkflowService,
    STATE_EXPORTING,
    STATE_CANCELLED,
    STATE_FAILED,
)
from ...validation.export_validator import ExportValidationIssue, ExportValidationReport
from ...validation.vmd_validator import VMD_EXPORT_BAKE_TIMELINE


logger = get_logger(__name__)


class ExportPresenter(QObject):
    """Keep UI state out of the export lifecycle itself."""

    _PROGRESS_LABELS = {
        "scene_preflight": "Validating scene",
        "payload_collection": "Collecting animation",
        "writer": "Writing temporary file",
        "report_ready": "Finalizing",
    }

    def __init__(self, view, app_state, workflow_service=None):
        super().__init__()
        self.view = view
        self.app_state = app_state
        if workflow_service is None:
            workflow_service = ExportWorkflowService(
                scene_service=getattr(app_state, "scene_model_service", None),
                vmd_action=create_maya_bake_timeline_vmd_action(),
            )
        self.workflow_service = workflow_service
        self._cancel_requested = False
        self._cancellable_scene_export = False
        self.view.presenter = self
        self.view.export_requested.connect(self.export)
        cancel_signal = getattr(self.view, "cancel_requested", None)
        if cancel_signal is not None:
            cancel_signal.connect(self.cancel)
        self._active_export_format = None
        current_model_changed = getattr(app_state, "current_model_changed", None)
        if current_model_changed is not None:
            current_model_changed.connect(lambda _root: self.view.invalidate_all_panes())

    def export(self):
        """Run the complete export once, including any warning decision."""

        request = None
        progress_token = None
        operation_active = False
        export_format = self._view_export_format()
        self._cancel_requested = False
        self._active_export_format = export_format
        try:
            self._cancel_requested = False
            request = self.view.build_request(
                getattr(self.app_state, "current_model_root", None)
            )
            export_format = self._request_export_format(request, export_format)
            self._active_export_format = export_format
            target = str((getattr(request, "options", None) or {}).get("export_target") or "").lower()
            self._cancellable_scene_export = export_format == "vmd" and target in {
                "camera",
                "light",
                "camera+light",
                "camera_light",
            }
            if self._cancellable_scene_export:
                request.options["cancel_requested"] = lambda: self._cancel_requested
            elif export_format == "vpd":
                request.options["_cancel_requested"] = lambda: self._cancel_requested
            operation_active = True
            self.view.set_operation_active(True)
            self.view.set_state(STATE_EXPORTING)
            progress_token = self.app_state.begin_progress(
                self._PROGRESS_LABELS["scene_preflight"]
            )

            def update_progress(stage):
                self._update_progress(progress_token, stage)

            progress_callback = (
                update_progress
                if self._cancellable_scene_export or export_format != "vmd"
                else None
            )
            result = self.workflow_service.execute(
                request,
                warning_callback=lambda report: self._confirm_warnings(report, request),
                # The VMD action owns a live Maya scene watch from arm through
                # output verification.  A GUI progress callback repaints by
                # pumping Qt events, which can re-enter Maya and invalidate or
                # stall that synchronous export boundary.  The busy indicator
                # was painted by begin_progress() before the watch was armed;
                # update it again only after execute() has closed the watch.
                progress_callback=progress_callback,
            )
        except Exception as exc:
            logger.error("Export workflow failed before result creation: %s", exc, exc_info=True)
            result = self._publish_failure("Export failed", exc, request, export_format)
        finally:
            self._cancellable_scene_export = False
            if operation_active:
                self.view.set_operation_active(False)
            if progress_token is not None:
                self.app_state.end_progress(progress_token)
            self._active_export_format = None

        self.view.set_result(result)
        self._emit_status(result)
        return result

    def cancel(self) -> None:
        """Request cancellation for an in-flight scene VMD or VPD export."""

        if self._cancellable_scene_export or self._active_export_format == "vpd":
            self._cancel_requested = True

    def _confirm_warnings(self, report: ExportValidationReport, request) -> bool:
        """Show the verified warning report and ask within this export call."""

        self.view.set_result(
            ExportWorkflowResult(
                STATE_EXPORTING,
                report,
                {"output_path": getattr(request, "file_path", None)},
            )
        )
        reasons = [str(issue.reason) for issue in report.issues if issue.severity == "warning"]
        dialog = QMessageBox(self.view)
        dialog.setWindowTitle("Export warnings")
        dialog.setText("Validation completed with warnings.")
        dialog.setInformativeText("\n".join(reasons) or "Review the Validation Console.")
        approve = dialog.addButton("Export Anyway", QMessageBox.AcceptRole)
        cancel = dialog.addButton("Cancel", QMessageBox.RejectRole)
        dialog.exec_()
        return dialog.clickedButton() is approve and dialog.clickedButton() is not cancel

    def _update_progress(self, token, stage: str) -> None:
        update_view = getattr(self.view, "set_progress", None)
        if callable(update_view):
            update_view(stage)
        if self._cancellable_scene_export or self._active_export_format == "vpd":
            # Camera/Light collection polls this callback once per evaluated
            # frame, allowing the visible Cancel button to stop before the
            # private sibling is published. VPD polls at its atomic phase
            # boundaries. Character VMD retains its no-reentry path.
            QApplication.processEvents()
        if token is None:
            return
        label = self._PROGRESS_LABELS.get(stage, stage)
        self.app_state.update_progress_state(token, label, 100 if stage == "report_ready" else None)

    def _view_export_format(self) -> str:
        return str(getattr(self.view, "current_export_format", None) or "pmx").lower()

    @staticmethod
    def _request_export_format(request, fallback: str) -> str:
        options = getattr(request, "options", None) or {}
        return str(options.get("export_format") or fallback or "pmx").lower()

    def _publish_failure(self, status_prefix, error, request, export_format):
        options = dict(getattr(request, "options", None) or {})
        export_format = str(options.get("export_format") or export_format or "").lower() or None
        strategy = {
            "vmd": VMD_EXPORT_BAKE_TIMELINE,
            "vpd": "current_pose",
        }.get(export_format, "model")
        lower_report = getattr(error, "report", None)
        if isinstance(lower_report, ExportValidationReport):
            return ExportWorkflowResult(
                STATE_FAILED,
                lower_report,
                {"output_path": getattr(request, "file_path", None)},
                error=error,
            )
        issue = ExportValidationIssue(
            "INTERNAL_ERROR",
            "fatal",
            True,
            "export.motion" if export_format == "vmd" else "export.model",
            f"{type(error).__name__}: {error}",
            details={
                "phase": "presentation",
                "exception_type": type(error).__name__,
                "aggregation_discriminator": "internal",
            },
        )
        self.app_state.emit_status(f"{status_prefix}: {error}")
        return ExportWorkflowResult(
            STATE_FAILED,
            ExportValidationReport(export_format, (issue,), mode=strategy),
            {"output_path": getattr(request, "file_path", None)},
            error=error,
        )

    def _emit_status(self, result: ExportWorkflowResult) -> None:
        if result.state == STATE_CANCELLED:
            self.app_state.emit_status("Cancelled")
        elif result.succeeded:
            self.app_state.emit_status("Completed")
        elif result.report.is_blocking:
            self.app_state.emit_status("Blocked")
        elif result.error is not None:
            self.app_state.emit_status(f"Export failed: {result.error}")
        else:
            self.app_state.emit_status(result.state)


__all__ = ["ExportPresenter"]
