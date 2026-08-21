"""Human-facing renderer for the canonical export ValidationReport."""

import json
from typing import Any, Dict, Mapping, Optional

from .qt_compat import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    Qt,
    Signal,
)
from ..validation.export_validator import ExportValidationReport
from ..validation.issue_catalog import get_issue_catalog_entry
from .translations import UITranslator


def _issue_display_wording(issue, translator=None):
    """Resolve catalog wording for the human-facing Console view."""
    code = issue.code if hasattr(issue, "code") else issue["code"]
    entry = get_issue_catalog_entry(code)
    if translator is None:
        return entry, entry.category, entry.title, entry.impact, entry.remediation

    category = translator.translate(
        f"validation_categories.{entry.category}.label",
        default=entry.category,
    )
    impact = translator.translate(
        entry.impact_key,
        default=translator.translate(
            f"validation_categories.{entry.category}.impact",
            default=entry.impact,
        ),
    )
    remediation = translator.translate(
        entry.remediation_key,
        default=translator.translate(
            f"validation_categories.{entry.category}.remediation",
            default=entry.remediation,
        ),
    )
    title = translator.translate(entry.title_key, default=entry.title)
    return entry, category, title, impact, remediation


def render_validation_console_text(
    report: ExportValidationReport,
    metadata: Optional[Mapping[str, Any]] = None,
    *,
    localize: bool = False,
) -> str:
    """Render one deterministic Console view from the canonical report."""
    metadata = dict(metadata or {})
    translator = UITranslator.instance() if localize else None

    def label(key: str, fallback: str) -> str:
        if translator is None:
            return fallback
        return translator.translate(f"validation_console.{key}", default=fallback)

    canonical = report.to_canonical_dict(
        target_identity=metadata.get("target_identity"),
        snapshot_fingerprint=metadata.get("payload_fingerprint")
        or metadata.get("snapshot_fingerprint"),
        provenance=metadata.get("provenance", "ExportValidationConsole"),
        evidence=metadata.get("evidence") or metadata,
    )
    summary = canonical["summary"]
    display_mode = canonical["mode"]
    if translator is not None and canonical["format"] == "vmd":
        mode_key = {
            "A": "vmd_preserve_imported",
            "C": "vmd_export_timeline",
        }.get(str(display_mode).upper())
        if mode_key is not None:
            display_mode = translator.translate(
                f"options.{mode_key}", default=display_mode
            )
    lines = [
        label("title", "Export Validation Console"),
        f"{label('status', 'Status')}: {canonical['status'].upper()}",
        f"{label('format', 'Format')}: {canonical['format'] or 'unknown'}",
        f"{label('mode', 'Mode')}: {display_mode}",
        f"{label('target', 'Target')}: {canonical['target_identity'] or 'unspecified'}",
        f"{label('snapshot', 'Snapshot')}: {canonical['snapshot_fingerprint'] or 'unspecified'}",
        (
            f"{label('summary', 'Summary')}: "
            f"{summary['fatal']} {label('fatal', 'fatal')} "
            f"{summary['warning']} {label('warning', 'warning')} "
            f"{summary['info']} {label('info', 'info')} "
            f"ack={str(canonical['requires_warning_ack']).lower()}"
        ),
        "",
    ]
    aggregation = canonical.get("issue_aggregation")
    if aggregation is not None:
        lines.extend(
            [
                (
                    f"{label('issue_occurrences', 'Issue occurrences')}: "
                    f"{label('shown', 'shown')}={aggregation['shown_occurrences']} "
                    f"{label('omitted', 'omitted')}={aggregation['omitted_occurrences']}"
                ),
                (
                    f"{label('issue_groups', 'Issue groups')}: "
                    f"{label('shown', 'shown')}={aggregation['shown_groups']} / "
                    f"{label('total', 'total')}={aggregation['total_groups']}"
                ),
                "",
            ]
        )
    if not canonical["issues"]:
        lines.append(label("no_issues", "No validation issues."))
        return "\n".join(lines)
    for index, issue in enumerate(canonical["issues"], start=1):
        _, category, title, impact, remediation = _issue_display_wording(issue, translator)
        lines.extend(
            [
                f"{index}. [{issue['severity'].upper()}] {issue['code']}",
                f"   {label('title_label', 'Title')}: {title}",
                f"   {label('category', 'Category')}: {category}",
                f"   {label('path', 'Path')}: {issue['path'] or 'model_data'}",
                f"   {label('decision', 'Decision')}: {label('block', 'BLOCK') if issue['blocking'] else label('allow', 'ALLOW')}",
            ]
        )
        if "occurrence_count" in issue:
            lines.extend(
                [
                    f"   {label('occurrences', 'Occurrences')}: {issue['occurrence_count']}",
                    f"   {label('path_pattern', 'Path pattern')}: {issue['path_pattern']}",
                    f"   {label('sample_paths', 'Sample paths')}: {json.dumps(issue['sample_paths'], ensure_ascii=False)}",
                ]
            )
        lines.extend(
            [
                f"   {label('observed', 'Observed')}: {issue['observed']}",
                f"   {label('expected', 'Expected')}: {issue['expected']}",
                f"   {label('impact', 'Impact')}: {impact}",
                f"   {label('remediation', 'Remediation')}: {remediation}",
                f"   {label('evidence', 'Evidence')}: {json.dumps(issue['evidence'], ensure_ascii=False, sort_keys=True)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


class ValidationConsole(QWidget):
    """Display and acknowledge a report without owning validation policy."""

    acknowledgement_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("validationConsole")
        self._translator = UITranslator.instance()
        self._report: Optional[ExportValidationReport] = None
        self._metadata: Dict[str, Any] = {}
        self._visible_issue_indices = []

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.summary_label = QLabel(self._tr("no_report", "No validation report"))
        header.addWidget(self.summary_label)
        header.addStretch()
        self.filter_combo = QComboBox()
        self.filter_combo.setObjectName("validationFilterCombo")
        self.filter_combo.addItem(self._tr("all", "All"), "all")
        self.filter_combo.currentIndexChanged.connect(self._refresh_issue_list)
        header.addWidget(self.filter_combo)
        layout.addLayout(header)

        self.issue_list = QListWidget()
        self.issue_list.setObjectName("validationIssueList")
        self.issue_list.currentRowChanged.connect(self._show_selected_issue)
        layout.addWidget(self.issue_list)

        self.detail_text = QTextEdit()
        self.detail_text.setObjectName("validationDetailEdit")
        self.detail_text.setReadOnly(True)
        layout.addWidget(self.detail_text)

        actions = QHBoxLayout()
        self.acknowledge_check = QCheckBox(
            self._tr("acknowledge_warnings", "Acknowledge warnings")
        )
        self.acknowledge_check.setObjectName("validationAcknowledgeCheck")
        self.acknowledge_check.setEnabled(False)
        self.acknowledge_check.toggled.connect(self.acknowledgement_changed.emit)
        actions.addWidget(self.acknowledge_check)
        self.copy_button = QPushButton(self._tr("copy", "Copy"))
        self.copy_button.setObjectName("validationCopyButton")
        self.copy_button.clicked.connect(self.copy_report)
        actions.addWidget(self.copy_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.retranslateUi()

    def _tr(self, key: str, fallback: str) -> str:
        """Translate one Validation Console label with an English fallback."""
        return self._translator.translate(f"validation_console.{key}", default=fallback)

    def _category_label(self, category: str) -> str:
        """Translate a catalog category label without changing its data value."""
        return self._translator.translate(
            f"validation_categories.{category}.label",
            default=category,
        )

    @property
    def report(self) -> Optional[ExportValidationReport]:
        """Return the currently displayed report."""
        return self._report

    @property
    def warnings_acknowledged(self) -> bool:
        """Return the explicit warning acknowledgement state."""
        return self.acknowledge_check.isChecked()

    def snapshot_state(self) -> Dict[str, Any]:
        """Capture report metadata and acknowledgement for pane switching."""
        return {
            "report": self._report,
            "metadata": dict(self._metadata),
            "acknowledged": self.warnings_acknowledged,
        }

    def restore_acknowledgement(self, acknowledged: bool) -> None:
        """Restore an acknowledgement after set_report resets it."""
        self.acknowledge_check.blockSignals(True)
        self.acknowledge_check.setChecked(
            bool(acknowledged and self._report and self._report.requires_warning_ack)
        )
        self.acknowledge_check.blockSignals(False)

    def restore_state(self, snapshot: Optional[Mapping[str, Any]]) -> None:
        """Restore a pane snapshot without creating a second Console."""
        if not snapshot:
            self.set_report(None, {})
            return
        self.set_report(snapshot.get("report"), snapshot.get("metadata") or {})
        self.restore_acknowledgement(bool(snapshot.get("acknowledged", False)))

    def set_report(
        self,
        report: Optional[ExportValidationReport],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Replace the displayed report and reset acknowledgement state."""
        self._report = report
        self._metadata = dict(metadata or {})
        self.acknowledge_check.blockSignals(True)
        self.acknowledge_check.setChecked(False)
        self.acknowledge_check.setEnabled(bool(report and report.requires_warning_ack))
        self.acknowledge_check.blockSignals(False)
        self._refresh_filters()
        self._refresh_summary()
        self._refresh_issue_list()

    def clear_report(self) -> None:
        """Clear stale validation output when workflow inputs change."""
        self.set_report(None, {})

    def _refresh_filters(self) -> None:
        """Populate category filters from the report, preserving no policy."""
        current = self.filter_combo.currentData() if hasattr(self.filter_combo, "currentData") else "all"
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem(self._tr("all", "All"), "all")
        categories = sorted(
            {
                get_issue_catalog_entry(issue.code).category
                for issue in (self._report.issues if self._report else ())
            }
        )
        for category in categories:
            self.filter_combo.addItem(self._category_label(category), category)
        index = self.filter_combo.findData(current)
        self.filter_combo.setCurrentIndex(index if index >= 0 else 0)
        self.filter_combo.blockSignals(False)

    def _refresh_summary(self) -> None:
        """Update the compact status line from report attributes only."""
        if self._report is None:
            self.summary_label.setText(self._tr("no_report", "No validation report"))
            return
        summary = self._report.to_dict()["summary"]
        self.summary_label.setText(
            f"{self._report.export_format or 'unknown'} / {self._report.mode} — "
            f"{summary['fatal']} {self._tr('fatal', 'fatal')}, "
            f"{summary['warning']} {self._tr('warning', 'warning')}, "
            f"{summary['info']} {self._tr('info', 'info')}"
        )

    def _refresh_issue_list(self, *_args) -> None:
        """Rebuild the issue list without adding UI-specific findings."""
        self.issue_list.clear()
        self._visible_issue_indices = []
        if self._report is None:
            self.detail_text.clear()
            return
        selected_category = self.filter_combo.currentData()
        for index, issue in enumerate(self._report.issues):
            category = get_issue_catalog_entry(issue.code).category
            if selected_category not in (None, "all", category):
                continue
            group = self._report.display_issue_groups[index]
            occurrence_suffix = (
                f" ×{group.count}" if self._report.issue_aggregation is not None else ""
            )
            item = QListWidgetItem(
                f"[{issue.severity.upper()}] {issue.code} — {issue.path}{occurrence_suffix}"
            )
            item.setData(Qt.UserRole, index)
            self.issue_list.addItem(item)
            self._visible_issue_indices.append(index)
        if self._visible_issue_indices:
            self.issue_list.setCurrentRow(0)
        else:
            self.detail_text.clear()

    def _show_selected_issue(self, row: int) -> None:
        """Render the selected issue through the localized Console helper."""
        if self._report is None or row < 0 or row >= len(self._visible_issue_indices):
            self.detail_text.clear()
            return
        issue_index = self._visible_issue_indices[row]
        issue = self._report.issues[issue_index]
        detail_report = ExportValidationReport(
            self._report.export_format,
            (issue,),
            mode=self._report.mode,
        )
        detail = render_validation_console_text(
            detail_report,
            self._metadata,
            localize=True,
        )
        if self._report.issue_aggregation is not None:
            group = self._report.display_issue_groups[issue_index]
            detail = (
                f"Occurrences: {group.count}\n"
                f"Path pattern: {group.path_pattern}\n"
                f"Sample paths: {json.dumps(group.sample_paths, ensure_ascii=False)}\n\n"
                + detail
            )
        self.detail_text.setPlainText(detail)

    def copy_report(self) -> None:
        """Copy the current canonical Console rendering to the clipboard."""
        if self._report is None:
            return
        clipboard_owner = QApplication.clipboard()
        if clipboard_owner is not None:
            clipboard_owner.setText(
                render_validation_console_text(
                    self._report,
                    self._metadata,
                    localize=True,
                )
            )

    def retranslateUi(self) -> None:
        """Refresh labels and the currently selected localized issue detail."""
        self.summary_label.setText(self._tr("no_report", "No validation report"))
        self.acknowledge_check.setText(
            self._tr("acknowledge_warnings", "Acknowledge warnings")
        )
        self.copy_button.setText(self._tr("copy", "Copy"))
        self._refresh_filters()
        self._refresh_summary()
        self._refresh_issue_list()


__all__ = ["ValidationConsole", "render_validation_console_text"]
