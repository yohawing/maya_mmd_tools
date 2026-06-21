"""Dialog for post-import MMD texture path issues."""

from __future__ import annotations

import os
from pathlib import Path

from ..core import maya_utils
from ..core.settings import settings
from ..core.texture_path_cache import describe_texture_issue
from .translations.translator import UITranslator
from .qt_compat import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


def update_texture_issues_from_resolution_results(issues, results):
    """Update dialog issue records from Maya texture-resolution results."""

    result_by_file_node = {}
    for result in results or []:
        file_node = getattr(result, "file_node", "")
        if file_node:
            result_by_file_node[file_node] = result

    resolved = 0
    for issue in issues or []:
        file_node = issue.get("file_node")
        result = result_by_file_node.get(file_node)
        if result is None:
            continue
        status = getattr(result, "status", "")
        reason = getattr(result, "reason", "") or status
        if status == "resolved":
            current_path = getattr(result, "cache_path", "") or getattr(result, "file_texture_path", "") or ""
            issue["current_path"] = current_path
            issue["reason"] = "resolved"
            issue["source_reason"] = "resolved"
            issue["resolvable"] = False
            resolved += 1
        else:
            issue["reason"] = reason
            issue["source_reason"] = reason
            issue["resolvable"] = status == "resolvable"
            current_path = getattr(result, "cache_path", "") or getattr(result, "file_texture_path", "") or ""
            if current_path:
                issue["current_path"] = current_path
            source_path = getattr(result, "source_path", "") or ""
            if source_path:
                issue["source_path"] = source_path
    return resolved


def mark_texture_resolution_failed(issues, reason="cache_copy_failed"):
    """Mark currently resolvable dialog issues as failed."""

    updated = 0
    for issue in issues or []:
        if not issue.get("resolvable"):
            continue
        issue["reason"] = reason
        issue["source_reason"] = reason
        issue["resolvable"] = False
        updated += 1
    return updated


class TextureIssueDialog(QDialog):
    """Show imported texture issues and offer user-triggered resolution."""

    def __init__(self, issues, model_path="", app_state=None, parent=None):
        super().__init__(parent)
        self.issues = list(issues or [])
        self.model_path = model_path or ""
        self.app_state = app_state
        self._translator = UITranslator.instance()

        self.setWindowTitle(self.tr("title").format(count=len(self.issues)))
        self.resize(820, 420)
        try:
            self.setSizeGripEnabled(True)
        except AttributeError:
            pass

        layout = QVBoxLayout(self)

        self.intro_label = QLabel(self.tr("intro"), self)
        self.intro_label.setWordWrap(True)
        layout.addWidget(self.intro_label)

        headers = (self.tr("header_material"), self.tr("header_problem"), self.tr("header_texture"))
        self.table = QTableWidget(0, len(headers), self)
        self.table.setHorizontalHeaderLabels(headers)
        try:
            self.table.setSelectionBehavior(QTableWidget.SelectRows)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        except AttributeError:
            pass
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        layout.addWidget(self.table)

        # Full path of the selected row (the table cells elide long paths).
        self.detail_label = QLabel(self.tr("detail_placeholder"), self)
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        self.dont_show_check = QCheckBox(self.tr("dont_show_again"), self)
        layout.addWidget(self.dont_show_check)

        button_layout = QHBoxLayout()
        self.resolve_all_button = QPushButton(self.tr("fix_all"), self)
        self.open_folder_button = QPushButton(self.tr("open_folder"), self)
        self.close_button = QPushButton(self.tr("close"), self)
        button_layout.addWidget(self.resolve_all_button)
        button_layout.addWidget(self.open_folder_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.close_button)
        layout.addLayout(button_layout)

        self.resolve_all_button.clicked.connect(self.resolve_all)
        self.open_folder_button.clicked.connect(self.open_folder)
        self.close_button.clicked.connect(self.accept)
        self.dont_show_check.toggled.connect(self._save_visibility_setting)
        try:
            self.table.itemSelectionChanged.connect(self._update_detail)
        except AttributeError:
            pass
        self._populate()

    def tr(self, key, category="texture_issues"):
        return self._translator.translate(key, category)

    def _reason_text(self, reason):
        """Translate a reason code, falling back to the English core description."""
        code = str(reason or "")
        translated = self._translator.translate(code, "texture_issue_reasons")
        if translated == code:
            return describe_texture_issue(code)
        return translated

    def _populate(self):
        self.table.setRowCount(0)
        for issue in self.issues:
            row = self.table.rowCount()
            self.table.insertRow(row)
            material = issue.get("material_name") or issue.get("material") or ""
            reason = issue.get("reason") or issue.get("source_reason") or ""
            original = issue.get("original_path") or ""
            current = issue.get("current_path") or ""
            cells = (
                (material, material),
                (self._reason_text(reason), str(reason)),
                (current or original, self._full_paths(issue)),
            )
            for column, (text, tooltip) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if tooltip:
                    item.setToolTip(str(tooltip))
                self.table.setItem(row, column, item)
        self._update_detail()

    def _full_paths(self, issue):
        original = issue.get("original_path") or ""
        current = issue.get("current_path") or ""
        parts = []
        if original:
            parts.append(self.tr("detail_original").format(path=original))
        if current:
            parts.append(self.tr("detail_current").format(path=current))
        return "\n".join(parts)

    def _update_detail(self):
        issue = self._selected_issue()
        text = self._full_paths(issue) if issue else ""
        self.detail_label.setText(text or self.tr("detail_placeholder"))

    def _save_visibility_setting(self, checked):
        if checked:
            settings.set("import.model.show_texture_issue_dialog", False)

    def _emit_status(self, message):
        if self.app_state is not None and hasattr(self.app_state, "emit_status"):
            self.app_state.emit_status(message)

    def resolve_all(self):
        resolved = 0
        try:
            results = maya_utils.resolve_scene_mmd_textures()
            resolved = update_texture_issues_from_resolution_results(self.issues, results)
        except Exception:
            mark_texture_resolution_failed(self.issues, "cache_copy_failed")
        finally:
            self._emit_status(self.tr("status_fixed").format(count=resolved))
            self._populate()

    def open_folder(self):
        issue = self._selected_issue()
        folder = self._issue_folder(issue)
        if folder and os.name == "nt":
            os.startfile(str(folder))  # noqa: S606

    def _selected_issue(self):
        row = self.table.currentRow()
        if row is None or row < 0 or row >= len(self.issues):
            return self.issues[0] if self.issues else {}
        return self.issues[row]

    def _issue_folder(self, issue):
        for key in ("source_path", "current_path"):
            path = issue.get(key)
            if path:
                candidate = Path(path)
                if candidate.exists():
                    return candidate if candidate.is_dir() else candidate.parent
        if self.model_path:
            return Path(self.model_path).resolve(strict=False).parent
        return None
