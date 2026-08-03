"""Shared Validate -> Export orchestration for UI and headless callers."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..actions.export_model_action import ExportModelAction, ExportModelRequest
from ..actions.export_vmd_action import ExportVmdAction, ExportVmdRequest
from ..validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
    pmd_export_policy_report,
)
from ..validation.scene_preflight import ScenePreflight
from ..validation.snapshot import ExportValidationSnapshot


STATE_EDITING = "Editing"
STATE_VALIDATING = "Validating"
STATE_BLOCKED = "Blocked"
STATE_READY = "Ready"
STATE_EXPORTING = "Exporting"
STATE_SUCCEEDED = "Succeeded"
STATE_FAILED = "Failed"


@dataclass
class ExportWorkflowRequest:
    """Format-neutral export request accepted by the shared workflow."""

    file_path: str
    options: Dict[str, Any]
    model_data: Any = None
    animation_data: Any = None


@dataclass
class ExportWorkflowResult:
    """Validation and export state returned by the workflow service."""

    state: str
    report: ExportValidationReport
    metadata: Dict[str, Any]
    payload: Any = None
    snapshot: Optional[ExportValidationSnapshot] = None
    action_result: Any = None
    error: Optional[Exception] = None

    @property
    def succeeded(self) -> bool:
        """Return whether the final writer and output verifier succeeded."""
        return self.state == STATE_SUCCEEDED

    @property
    def warnings(self):
        """Expose the report issues using the action-compatible field name."""
        return list(self.report.issues)


def _combine_reports(
    first: ExportValidationReport,
    second: Optional[ExportValidationReport],
    *,
    export_format: Optional[str],
    mode: str,
) -> ExportValidationReport:
    """Merge scene, payload, and output issues into one stable report."""
    if second is None or not second.issues:
        return first
    return ExportValidationReport(
        export_format,
        tuple(first.issues) + tuple(second.issues),
        mode=mode,
    )


def _collect_failure_report(
    export_format: Optional[str],
    mode: str,
    error: Exception,
) -> ExportValidationReport:
    """Normalize collector failures without hiding a lower-level report."""
    wrapper_issue = ExportValidationIssue(
        "SCENE_COLLECT_FAILED",
        "fatal",
        True,
        "collector",
        f"scene collector failed: {type(error).__name__}: {error}",
    )
    lower_report = getattr(error, "report", None)
    if isinstance(lower_report, ExportValidationReport):
        lower_issues = tuple(lower_report.issues)
        if any(issue.code == wrapper_issue.code for issue in lower_issues):
            issues = lower_issues
        else:
            issues = (wrapper_issue,) + lower_issues
    else:
        issues = (wrapper_issue,)
    return ExportValidationReport(
        export_format,
        issues,
        mode=mode,
    )


class ExportWorkflowService:
    """Run the same scene/payload/action path for UI and headless callers.

    ``validate`` never writes an output.  ``execute`` always validates again,
    so a report shown in the UI cannot become an implicit stale approval after
    a scene or option change.
    """

    def __init__(
        self,
        *,
        scene_preflight: Optional[ScenePreflight] = None,
        scene_service: Any = None,
        model_action: Optional[ExportModelAction] = None,
        vmd_action: Optional[ExportVmdAction] = None,
    ):
        self.model_action = model_action or ExportModelAction()
        self.vmd_action = vmd_action or ExportVmdAction()
        self.scene_preflight = scene_preflight or ScenePreflight(scene_service=scene_service)

    @staticmethod
    def _options(request: ExportWorkflowRequest) -> Dict[str, Any]:
        """Copy options and make the request path explicit."""
        options = dict(request.options or {})
        options.setdefault("file_path", request.file_path)
        return options

    @staticmethod
    def _target_options(options: Mapping[str, Any], metadata: Mapping[str, Any]) -> Dict[str, Any]:
        """Add scene provenance used by model snapshots and report artifacts."""
        enriched = dict(options)
        if metadata.get("target_identity") is not None:
            enriched.setdefault("target_identity", metadata["target_identity"])
        if metadata.get("scene_revision") is not None:
            enriched.setdefault("scene_revision", metadata["scene_revision"])
        return enriched

    def _collect_model(self, request: ExportWorkflowRequest, options: Mapping[str, Any]) -> Any:
        """Collect model payload through the configured model action boundary."""
        if request.model_data is not None:
            return request.model_data
        if options.get("model_data") is not None:
            return options["model_data"]
        if options.get("maya_data") is not None:
            return options["maya_data"]
        collector = getattr(self.model_action, "_collector", None)
        if collector is None:
            raise ValueError("model export requires model_data or a collector")
        return collector(dict(options))

    def _collect_vmd(self, request: ExportWorkflowRequest, options: Mapping[str, Any]) -> Any:
        """Collect and normalize VMD payload through the configured action."""
        animation_data = request.animation_data
        if animation_data is None:
            animation_data = options.get("animation_data")
        if animation_data is None:
            collector = getattr(self.vmd_action, "_collector", None)
            if collector is None:
                raise ValueError("VMD export requires animation_data or a collector")
            animation_data = collector(dict(options))
        converter = getattr(self.vmd_action, "_to_vmd_data", None)
        if callable(converter):
            return converter(animation_data)
        return animation_data

    def validate(self, request: ExportWorkflowRequest) -> ExportWorkflowResult:
        """Run scene preflight and payload validation without invoking a writer."""
        options = self._options(request)
        scene_result = self.scene_preflight.run(options)
        report = scene_result.report
        metadata = dict(scene_result.metadata)
        export_format = metadata.get("format")
        mode = metadata.get("mode") or "model"
        if report.is_blocking:
            return ExportWorkflowResult(STATE_BLOCKED, report, metadata)
        if export_format == "pmd":
            policy_report = pmd_export_policy_report()
            report = _combine_reports(
                report,
                policy_report,
                export_format=export_format,
                mode=mode,
            )
            return ExportWorkflowResult(STATE_BLOCKED, report, metadata)

        try:
            if export_format == "pmx":
                payload = self._collect_model(request, self._target_options(options, metadata))
                validator = getattr(self.model_action, "_validator", None)
                if validator is None:
                    raise ValueError("model action does not expose a validator")
                payload_report = validator(payload, export_format)
                snapshot = None
                if not payload_report.is_blocking:
                    snapshot = ExportValidationSnapshot.capture(
                        payload,
                        export_format,
                        scene_revision=metadata.get("scene_revision"),
                        target_identity=metadata.get("target_identity"),
                    )
            elif export_format == "vmd":
                payload = self._collect_vmd(request, self._target_options(options, metadata))
                validator = getattr(self.vmd_action, "_validator", None)
                if validator is None:
                    raise ValueError("VMD action does not expose a validator")
                payload_report = self.vmd_action._validate(payload, mode, options)
                snapshot = None
            else:
                return ExportWorkflowResult(STATE_BLOCKED, report, metadata)
        except Exception as exc:
            failure_report = _collect_failure_report(export_format, mode, exc)
            report = _combine_reports(
                report,
                failure_report,
                export_format=export_format,
                mode=mode,
            )
            return ExportWorkflowResult(STATE_BLOCKED, report, metadata, error=exc)

        report = _combine_reports(
            report,
            payload_report,
            export_format=export_format,
            mode=mode,
        )
        state = STATE_BLOCKED if report.is_blocking else STATE_READY
        return ExportWorkflowResult(
            state,
            report,
            metadata,
            payload=payload,
            snapshot=snapshot,
        )

    def execute(
        self,
        request: ExportWorkflowRequest,
        *,
        acknowledge_warnings: bool = False,
    ) -> ExportWorkflowResult:
        """Revalidate and execute the appropriate validated export action."""
        validation = self.validate(request)
        if validation.error is not None or validation.report.is_blocking:
            return validation
        if validation.report.requires_warning_ack and not acknowledge_warnings:
            return validation

        options = self._target_options(self._options(request), validation.metadata)
        if acknowledge_warnings:
            options["ack_warnings"] = True
        export_format = validation.metadata.get("format")
        try:
            if export_format in {"pmx", "pmd"}:
                options["model_data"] = validation.payload
                options["validation_snapshot"] = validation.snapshot
                action_result = self.model_action.execute(
                    ExportModelRequest(request.file_path, options)
                )
            else:
                action_result = self.vmd_action.execute(
                    ExportVmdRequest(
                        request.file_path,
                        options,
                        animation_data=validation.payload,
                    )
                )
        except Exception as exc:
            return ExportWorkflowResult(
                STATE_FAILED,
                validation.report,
                validation.metadata,
                payload=validation.payload,
                snapshot=validation.snapshot,
                error=exc,
            )

        report = _combine_reports(
            validation.report,
            getattr(action_result, "validation_report", None),
            export_format=export_format,
            mode=validation.metadata.get("mode") or "model",
        )
        succeeded = bool(getattr(action_result, "succeeded", False)) and getattr(action_result, "error", None) is None
        return ExportWorkflowResult(
            STATE_SUCCEEDED if succeeded else STATE_FAILED,
            report,
            validation.metadata,
            payload=validation.payload,
            snapshot=validation.snapshot,
            action_result=action_result,
            error=getattr(action_result, "error", None),
        )


__all__ = [
    "ExportWorkflowRequest",
    "ExportWorkflowResult",
    "ExportWorkflowService",
    "STATE_BLOCKED",
    "STATE_EDITING",
    "STATE_EXPORTING",
    "STATE_FAILED",
    "STATE_READY",
    "STATE_SUCCEEDED",
    "STATE_VALIDATING",
]
