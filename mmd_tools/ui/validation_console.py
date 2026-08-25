"""Human-facing renderer for the canonical export ValidationReport."""

import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .qt_compat import QApplication, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget
from .translations import UITranslator
from ..validation.export_validator import ExportValidationReport


_CLEAN_VALIDATION_LINE = "[INFO] Validation passed: no errors or warnings were found."


def _issue_display_wording(issue):
    """Return the v2 English reason/action without a translation lookup."""
    if hasattr(issue, "code"):
        return issue.code, issue.reason, issue.action
    return issue["code"], issue["reason"], issue["action"]


def _console_requires_red(report: Optional[ExportValidationReport]) -> bool:
    """Return whether the complete source report requires red Console styling."""
    if report is None:
        return False
    return bool(report.is_blocking or report.to_dict()["summary"]["fatal"] > 0)


def render_validation_console_text(
    report: Optional[ExportValidationReport],
    metadata: Optional[Mapping[str, Any]] = None,
    include_details: bool = False,
) -> str:
    """Render a concise English Console view, with optional diagnostics."""
    if report is None:
        return ""

    metadata = dict(metadata or {})
    canonical = report.to_canonical_dict(
        target_identity=metadata.get("target_identity"),
        snapshot_fingerprint=metadata.get("payload_fingerprint")
        or metadata.get("snapshot_fingerprint"),
        provenance=metadata.get("provenance", "ExportValidationConsole"),
        evidence=metadata.get("evidence") or metadata,
    )
    summary = canonical["summary"]
    if not canonical["issues"]:
        return _CLEAN_VALIDATION_LINE

    status = canonical["status"].upper()
    lines = []
    if summary["fatal"] == 0 and summary["warning"] == 0:
        lines.extend([_CLEAN_VALIDATION_LINE, ""])
    lines.extend(
        [
            f"[{status}] Validation report",
            f"Format: {canonical['format'] or 'unknown'}",
            f"Export strategy: {canonical['mode'] or 'unknown'}",
            f"Summary: {summary['fatal']} fatal, {summary['warning']} warning, {summary['info']} info",
        ]
    )
    if canonical.get("target_identity"):
        lines.append(f"Target: {canonical['target_identity']}")
    if canonical.get("snapshot_fingerprint"):
        lines.append(f"Snapshot: {canonical['snapshot_fingerprint']}")

    aggregation = canonical.get("issue_aggregation")
    if include_details and aggregation is not None:
        lines.extend(
            [
                "",
                (
                    "Issue occurrences: "
                    f"shown={aggregation['shown_occurrences']} "
                    f"omitted={aggregation['omitted_occurrences']}"
                ),
                (
                    "Issue groups: "
                    f"shown={aggregation['shown_groups']} "
                    f"total={aggregation['total_groups']}"
                ),
            ]
        )

    if not include_details:
        grouped: Dict[Tuple[Any, ...], List[Mapping[str, Any]]] = {}
        for issue in canonical["issues"]:
            _, reason, action = _issue_display_wording(issue)
            key = (
                issue["severity"],
                issue["blocking"],
                issue["code"],
                reason,
                action,
            )
            grouped.setdefault(key, []).append(issue)

        for index, (key, issues) in enumerate(grouped.items(), start=1):
            severity, blocking, _, reason, action = key
            label = severity.upper()
            if blocking:
                label = f"{label}, BLOCKED"
            lines.extend(["", f"{index}. [{label}] Reason: {reason}"])

            affected_bones = []
            for issue in issues:
                details = issue.get("details") or {}
                bone = details.get("bone")
                if not bone:
                    affected_bones = []
                    break
                key_count = details.get("generated_key_count")
                if isinstance(key_count, int):
                    noun = "key" if key_count == 1 else "keys"
                    affected_bones.append(f"{bone} ({key_count} {noun})")
                else:
                    affected_bones.append(str(bone))
            if affected_bones:
                subject_label = "Bone" if len(affected_bones) == 1 else "Affected bones"
                lines.append(f"   {subject_label}: {', '.join(affected_bones)}")
            elif len(issues) > 1:
                occurrence_count = sum(issue.get("occurrence_count", 1) for issue in issues)
                lines.append(f"   Affected items: {occurrence_count}")
            lines.append(f"   Action: {action}")
        return "\n".join(lines)

    for index, issue in enumerate(canonical["issues"], start=1):
        _, reason, action = _issue_display_wording(issue)
        severity = issue["severity"].upper()
        decision = "BLOCKED" if issue["blocking"] else "ALLOW"
        lines.extend(
            [
                "",
                f"{index}. Reason: {reason}",
                f"   Action: {action}",
                f"   [{severity}] {decision}",
                f"   Code: {issue['code']}",
                f"   Severity: {severity}",
                f"   Path: {issue['path'] or 'model_data'}",
            ]
        )
        if "occurrence_count" in issue:
            lines.extend(
                [
                    f"   Occurrences: {issue['occurrence_count']}",
                    f"   Path pattern: {issue['path_pattern']}",
                    f"   Sample paths: {json.dumps(issue['sample_paths'], ensure_ascii=False)}",
                ]
            )
        lines.extend(
            [
                f"   Details: {json.dumps(issue['details'], ensure_ascii=False, sort_keys=True)}",
                f"   Evidence: {json.dumps(issue['evidence'], ensure_ascii=False, sort_keys=True)}",
            ]
        )
    return "\n".join(lines)


class ValidationConsole(QWidget):
    """Display a concise report with optional diagnostics and Copy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("validationConsole")
        self._report: Optional[ExportValidationReport] = None
        self._metadata: Dict[str, Any] = {}

        layout = QVBoxLayout(self)
        self.console_text = QTextEdit()
        self.console_text.setObjectName("validationConsoleText")
        self.console_text.setReadOnly(True)
        layout.addWidget(self.console_text)

        actions = QHBoxLayout()
        self.details_button = QPushButton()
        self.details_button.setObjectName("validationDetailsButton")
        self.details_button.setCheckable(True)
        self.details_button.setVisible(False)
        self.details_button.toggled.connect(self._on_details_toggled)
        actions.addWidget(self.details_button)
        self.copy_button = QPushButton()
        self.copy_button.setObjectName("validationCopyButton")
        self.copy_button.clicked.connect(self.copy_report)
        actions.addWidget(self.copy_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.retranslateUi()

    @property
    def report(self) -> Optional[ExportValidationReport]:
        """Return the currently displayed report."""
        return self._report

    def snapshot_state(self) -> Dict[str, Any]:
        """Capture report and metadata for pane switching."""
        return {"report": self._report, "metadata": dict(self._metadata)}

    def restore_state(self, snapshot: Optional[Mapping[str, Any]]) -> None:
        """Restore a pane snapshot without creating a second Console."""
        if not snapshot:
            self.set_report(None, {})
            return
        self.set_report(snapshot.get("report"), snapshot.get("metadata") or {})

    def set_report(
        self,
        report: Optional[ExportValidationReport],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Replace the displayed report and its associated metadata."""
        self._report = report
        self._metadata = dict(metadata or {})
        self.details_button.setChecked(False)
        self.details_button.setVisible(bool(report and report.issues))
        self._refresh_text()

    def clear_report(self) -> None:
        """Clear stale validation output when workflow inputs change."""
        self.set_report(None, {})

    def _refresh_text(self) -> None:
        text = render_validation_console_text(
            self._report,
            self._metadata,
            include_details=self.details_button.isChecked(),
        )
        self.console_text.setPlainText(text)
        is_red = _console_requires_red(self._report)
        self.console_text.setStyleSheet("color: red;" if is_red else "")

    def copy_report(self) -> None:
        """Copy exactly the text currently visible in the Console."""
        if self._report is None:
            return
        clipboard_owner = QApplication.clipboard()
        if clipboard_owner is not None:
            clipboard_owner.setText(self.console_text.toPlainText())

    def _on_details_toggled(self, _checked: bool) -> None:
        """Switch between the concise user view and technical diagnostics."""
        self._refresh_text()

    def retranslateUi(self) -> None:
        """Refresh normal UI controls without translating Console contents."""
        self.copy_button.setText("Copy")
        self.details_button.setText(
            UITranslator.instance().translate("details", "groups", default="Details")
        )
        self._refresh_text()


__all__ = ["ValidationConsole", "render_validation_console_text"]
