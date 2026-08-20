"""Presenter for the top-level validated Export workflow."""

from ..qt_compat import QObject
from ...adapters.maya_vmd_prepare_backend import create_maya_vmd_prepare_action
from ...core.logger import get_logger
from ...services.export_workflow_service import (
    ExportWorkflowRequest,
    ExportWorkflowResult,
    ExportWorkflowService,
    STATE_EXPORTING,
    STATE_FAILED,
    STATE_BLOCKED,
    STATE_PREPARING,
    STATE_PREPARED,
    STATE_VALIDATING,
)
from ...validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
)
from ..translations import UITranslator


logger = get_logger(__name__)


class ExportPresenter(QObject):
    """Keep ExportTab presentation thin and route all decisions to the service."""

    _PROGRESS_STAGES = {
        "scene_preflight",
        "timeline_bake",
        "payload_collection",
        "payload_validation",
        "prepared_payload",
        "report_ready",
        "writer",
    }

    def __init__(self, view, app_state, workflow_service=None):
        super().__init__()
        self.view = view
        self.app_state = app_state
        self._prepared_vmd_token = None
        if workflow_service is None:
            # Construction is Maya-import safe; the adapter loads maya.cmds
            # only when the user actually requests a Mode C preparation.
            workflow_service = ExportWorkflowService(
                scene_service=getattr(app_state, "scene_model_service", None),
                prepare_vmd_action=create_maya_vmd_prepare_action(),
            )
        self.workflow_service = workflow_service
        self.view.presenter = self
        prepare_requested = getattr(self.view, "prepare_requested", None)
        if prepare_requested is not None:
            prepare_requested.connect(self.prepare)
        self.view.validate_requested.connect(self.validate)
        self.view.export_requested.connect(self.export)
        motion_semantic_changed = getattr(self.view, "motion_semantic_changed", None)
        if motion_semantic_changed is not None:
            motion_semantic_changed.connect(self._on_motion_semantic_changed)
        consoles = getattr(self.view, "validation_consoles", None)
        if consoles is None:
            consoles = (self.view.validation_console,)
        seen = set()
        for console in consoles:
            if id(console) in seen:
                continue
            seen.add(id(console))
            console.acknowledgement_changed.connect(self._on_acknowledgement_changed)
        current_model_changed = getattr(app_state, "current_model_changed", None)
        if current_model_changed is not None:
            current_model_changed.connect(self._on_current_model_changed)

    def _on_current_model_changed(self, model_root):
        """Invalidate both panes when the shared Current Model changes."""
        del model_root
        self._clear_prepared_token()
        self.view.invalidate_all_panes()

    def _on_motion_semantic_changed(self):
        """A timeline or Mode change makes the prepared payload unusable."""
        self._clear_prepared_token()

    def _clear_prepared_token(self):
        """Discard the handle and close its host watch without touching output settings."""
        token = self._prepared_vmd_token
        if token is None:
            return
        self._prepared_vmd_token = None
        invalidate = getattr(self.workflow_service, "invalidate_prepared_vmd", None)
        if not callable(invalidate):
            return
        try:
            invalidate(token)
        except Exception:
            # The UI must not retain a token after a teardown error.  The
            # action itself is fail-closed and logs/owns host cleanup.
            logger.error("Failed to invalidate prepared VMD token", exc_info=True)

    @property
    def prepared_vmd_token(self):
        """Expose the opaque handle for presenters/tests without exposing payload data."""
        return self._prepared_vmd_token

    def prepare(self):
        """Bake/collect the reusable Mode C payload before validation or export."""
        self._clear_prepared_token()
        self.view.set_state(STATE_PREPARING)
        request = None
        export_format = self._view_export_format()
        token = None
        operation_enabled = False
        try:
            operation_enabled = True
            self.view.set_operation_active(True)
            token = self.app_state.begin_progress(
                self._progress_label(export_format, "timeline_bake")
            )
            request = self.view.build_request(
                getattr(self.app_state, "current_model_root", None)
            )
            export_format = self._request_export_format(request, export_format)
            options = getattr(request, "options", None) or {}
            mode = str(options.get("vmd_mode") or "").upper()
            if export_format != "vmd" or mode != "C":
                raise ValueError("Prepare is available only for VMD Mode C")
            self._update_progress(token, export_format, "timeline_bake")
            preparation = self.workflow_service.prepare_vmd(request)
            self._update_progress(token, export_format, "prepared_payload")
            if not getattr(preparation, "succeeded", False):
                preparation_report = getattr(preparation, "report", None)
                if isinstance(preparation_report, ExportValidationReport):
                    result = ExportWorkflowResult(
                        STATE_BLOCKED,
                        preparation_report,
                        {"output_path": getattr(request, "file_path", None)},
                        error=getattr(preparation, "error", None),
                    )
                    self.view.set_result(result)
                    self._emit_status(result)
                    return preparation
                error = getattr(preparation, "error", None)
                raise RuntimeError(error or "VMD preparation did not publish a token")
            self._prepared_vmd_token = preparation.token
            set_prepared = getattr(self.view, "set_prepared", None)
            if callable(set_prepared):
                set_prepared(preparation)
            else:
                self.view.set_state(STATE_PREPARED)
            # Keep the verified combined report visible immediately after
            # Prepare.  The motion page's set_prepared hook resets its console
            # for the old pre-prepare report, so restore the token-owned report
            # after the prepared state is established.  Acknowledge controls
            # remain owned by final Export.
            preparation_report = getattr(preparation, "report", None)
            if preparation_report is None:
                preparation_report = getattr(self._prepared_vmd_token, "validation_report", None)
            if isinstance(preparation_report, ExportValidationReport):
                self.view.set_result(
                    ExportWorkflowResult(
                        STATE_PREPARED,
                        preparation_report,
                        {"output_path": getattr(request, "file_path", None)},
                    )
                )
            self.app_state.emit_status(
                UITranslator.instance().translate(
                    "prepare_mode_c_complete",
                    "messages",
                    default="VMD Mode C preparation complete",
                )
            )
            return preparation
        except Exception as exc:
            self._clear_prepared_token()
            logger.error("VMD preparation failed before result creation: %s", exc, exc_info=True)
            return self._publish_failure("VMD preparation failed", exc, request)
        finally:
            try:
                if operation_enabled:
                    self.view.set_operation_active(False)
            finally:
                if token is not None:
                    self.app_state.end_progress(token)

    def _on_acknowledgement_changed(self, _acknowledged):
        """Acknowledgement changes affect only the next service execution."""
        return None

    def validate(self):
        """Run preflight and payload validation without writing an output."""
        self.view.set_state(STATE_VALIDATING)
        request = None
        export_format = self._view_export_format()
        token = None
        operation_enabled = False
        try:
            operation_enabled = True
            self.view.set_operation_active(True)
            token = self.app_state.begin_progress(self._progress_label(export_format, "scene_preflight"))
            request = self.view.build_request(
                getattr(self.app_state, "current_model_root", None)
            )
            export_format = self._request_export_format(request, export_format)
            request = self._attach_prepared_token(request, export_format)
            result = self.workflow_service.validate(
                request,
                progress_callback=lambda stage: self._update_progress(
                    token, export_format, stage
                ),
            )
        except Exception as exc:
            logger.error("Export validation failed before result creation: %s", exc, exc_info=True)
            return self._publish_failure("Export validation failed", exc, request)
        finally:
            try:
                if operation_enabled:
                    self.view.set_operation_active(False)
            finally:
                if token is not None:
                    self.app_state.end_progress(token)
        self.view.set_result(result)
        self._emit_status(result)
        return result

    def export(self):
        """Revalidate and execute the validated export action."""
        self.view.set_state(STATE_EXPORTING)
        request = None
        export_format = self._view_export_format()
        token = None
        operation_enabled = False
        try:
            operation_enabled = True
            self.view.set_operation_active(True)
            token = self.app_state.begin_progress(self._progress_label(export_format, "scene_preflight"))
            request = self.view.build_request(
                getattr(self.app_state, "current_model_root", None)
            )
            export_format = self._request_export_format(request, export_format)
            request = self._attach_prepared_token(request, export_format)
            result = self.workflow_service.execute(
                request,
                acknowledge_warnings=self.view.validation_console.warnings_acknowledged,
                progress_callback=lambda stage: self._update_progress(
                    token, export_format, stage
                ),
            )
        except Exception as exc:
            logger.error("Export workflow failed before result creation: %s", exc, exc_info=True)
            return self._publish_failure("Export failed", exc, request)
        finally:
            try:
                if operation_enabled:
                    self.view.set_operation_active(False)
            finally:
                if token is not None:
                    self.app_state.end_progress(token)
        self.view.set_result(result)
        self._emit_status(result)
        if result.succeeded and request is not None and getattr(request, "prepared_vmd_token", None) is not None:
            self._clear_prepared_token()
        return result

    def _attach_prepared_token(self, request, export_format):
        """Attach a prepared token only to a VMD Mode C request."""
        if str(export_format or "").lower() != "vmd":
            return request
        options = getattr(request, "options", None) or {}
        if str(options.get("vmd_mode") or "").upper() != "C":
            return request
        if self._prepared_vmd_token is None:
            return request
        if hasattr(request, "prepared_vmd_token"):
            request.prepared_vmd_token = self._prepared_vmd_token
            return request
        return ExportWorkflowRequest(
            file_path=request.file_path,
            options=dict(options),
            model_data=getattr(request, "model_data", None),
            animation_data=getattr(request, "animation_data", None),
            prepared_vmd_token=self._prepared_vmd_token,
        )

    def _view_export_format(self):
        """Read the active page format before a request is built."""
        return str(getattr(self.view, "current_export_format", None) or "pmx").lower()

    @staticmethod
    def _request_export_format(request, fallback):
        options = getattr(request, "options", None) or {}
        return str(options.get("export_format") or fallback or "pmx").lower()

    @staticmethod
    def _format_key(export_format):
        return "animation" if str(export_format or "").lower() == "vmd" else "model"

    def _progress_label(self, export_format, stage):
        if stage not in self._PROGRESS_STAGES:
            return stage
        key = f"{self._format_key(export_format)}_{stage}"
        fallback = {
            "scene_preflight": "Checking scene",
            "timeline_bake": "Evaluating and preparing Mode C motion",
            "payload_collection": "Collecting export data",
            "payload_validation": "Validating export data",
            "prepared_payload": "Prepared motion payload",
            "report_ready": "Validation report ready",
            "writer": "Writing export",
        }[stage]
        return UITranslator.instance().translate(key, "export_progress", default=fallback)

    def _update_progress(self, token, export_format, stage):
        percentage = 100 if stage in {"report_ready", "prepared_payload"} else None
        self.app_state.update_progress_state(
            token,
            self._progress_label(export_format, stage),
            percentage,
        )

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
