"""Standalone modal UI for translating names on one imported MMD model.

The translation policy and scene mutation remain owned by
``mmd_tools.tools.translate_names``.  This module only collects the explicit
user choices, displays a read-only preview, and applies a previously previewed
plan after a second stale-target check.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from mmd_tools.tools.translate_names import (
    NameChange,
    NameTranslationError,
    _node_leaf,
    apply_translation_plan,
    build_translation_plan,
    collect_name_entries,
    load_translation_dictionary,
    resolve_model_root,
)


_SOURCE_ATTRIBUTES = {
    "model": "mmd_model_name",
    "bone": "mmd_bone_name",
    "material": "mmd_material_name",
    "morph": "mmd_morph_name",
}


def build_translation_preview(
    dictionary_path: str,
    *,
    model_root: Optional[str] = None,
    overwrite: bool = False,
    rename_nodes: bool = False,
    cmds_module=None,
) -> Tuple[str, Tuple[NameChange, ...]]:
    """Build the changes shown by the dialog without mutating Maya.

    The helper intentionally mirrors the existing script entry point while
    keeping the dialog's initial display and preview phases read-only.
    """

    if not str(dictionary_path or "").strip():
        raise NameTranslationError("choose a UTF-8 translation dictionary CSV")

    root = resolve_model_root(model_root, cmds_module=cmds_module)
    entries = collect_name_entries(root, cmds_module=cmds_module)
    translations = load_translation_dictionary(dictionary_path)
    target_names = {_node_leaf(entry.node) for entry in entries if entry.rename_allowed}
    cmds = cmds_module
    if cmds is None:
        from maya import cmds as maya_cmds

        cmds = maya_cmds
    scene_names = {_node_leaf(node) for node in (cmds.ls(long=True) or [])}
    plan = build_translation_plan(
        entries,
        translations,
        set_english=True,
        overwrite=overwrite,
        rename_nodes=rename_nodes,
        used_names=scene_names - target_names,
    )
    return root, tuple(change for change in plan if change.has_changes)


def format_dialog_preview(plan: Sequence[NameChange]) -> Tuple[str, ...]:
    """Format preview rows with kind, original name, and planned destinations."""

    lines = []
    for change in plan:
        entry = change.entry
        label = entry.kind
        if entry.index is not None:
            label = f"{label}[{entry.index}]"
        details = [f"{label}: source={entry.source_name!r}", f"node={entry.node}"]
        if change.english_name is not None:
            details.append(f"EnglishName={change.english_name!r}")
        else:
            details.append("EnglishName=(unchanged)")
        if change.maya_name is not None:
            details.append(f"rename={change.maya_name!r}")
        lines.append("; ".join(details))
    return tuple(lines)


def _capture_preview_state(plan: Sequence[NameChange], *, cmds_module):
    """Capture identity and name snapshots that make Apply fail closed."""

    snapshots = {}
    for change in plan:
        entry = change.entry
        source_attr = _SOURCE_ATTRIBUTES.get(entry.kind)
        if source_attr is None:
            raise NameTranslationError(f"unsupported translation target kind: {entry.kind}")
        try:
            uuids = cmds_module.ls(entry.node, uuid=True) or []
            if len(uuids) != 1:
                raise NameTranslationError(f"cannot capture Maya UUID for {entry.node}")
            if not cmds_module.attributeQuery(source_attr, node=entry.node, exists=True):
                raise NameTranslationError(f"stale original-name attribute: {entry.node}.{source_attr}")
            if not cmds_module.attributeQuery(entry.english_attr, node=entry.node, exists=True):
                raise NameTranslationError(f"stale EnglishName attribute: {entry.node}.{entry.english_attr}")
            source_name = str(cmds_module.getAttr(f"{entry.node}.{source_attr}") or "")
            english_name = str(cmds_module.getAttr(f"{entry.node}.{entry.english_attr}") or "")
        except NameTranslationError:
            raise
        except Exception as exc:
            raise NameTranslationError(f"cannot capture translation target {entry.node!r}: {exc}") from exc
        snapshots[entry.node] = (str(uuids[0]), source_name, english_name)
    return snapshots


def _validate_preview_targets(
    plan: Sequence[NameChange],
    *,
    cmds_module,
    preview_state=None,
) -> None:
    """Fail before writes when a node, identity, or name became stale."""

    for change in plan:
        entry = change.entry
        try:
            if not cmds_module.objExists(entry.node):
                raise NameTranslationError(f"stale translation target: {entry.node}")
            matches = cmds_module.ls(entry.node, long=True) or []
            if len(matches) != 1 or str(matches[0]) != entry.node:
                raise NameTranslationError(f"stale translation target: {entry.node}")
            if change.english_name is not None and not cmds_module.attributeQuery(
                entry.english_attr,
                node=entry.node,
                exists=True,
            ):
                raise NameTranslationError(
                    f"stale EnglishName attribute: {entry.node}.{entry.english_attr}"
                )
            if preview_state is not None:
                snapshot = preview_state.get(entry.node)
                if snapshot is None:
                    raise NameTranslationError(f"missing preview state for {entry.node}")
                source_attr = _SOURCE_ATTRIBUTES.get(entry.kind)
                if source_attr is None:
                    raise NameTranslationError(f"unsupported translation target kind: {entry.kind}")
                uuids = cmds_module.ls(entry.node, uuid=True) or []
                current_uuid = str(uuids[0]) if len(uuids) == 1 else None
                current_source = str(cmds_module.getAttr(f"{entry.node}.{source_attr}") or "")
                current_english = str(cmds_module.getAttr(f"{entry.node}.{entry.english_attr}") or "")
                if current_uuid != snapshot[0]:
                    raise NameTranslationError(f"stale translation target identity: {entry.node}")
                if current_source != snapshot[1] or current_source != entry.source_name:
                    raise NameTranslationError(f"stale original MMD name: {entry.node}")
                if current_english != snapshot[2] or current_english != entry.english_name:
                    raise NameTranslationError(f"stale EnglishName: {entry.node}")
        except NameTranslationError:
            raise
        except Exception as exc:
            raise NameTranslationError(f"cannot validate translation target {entry.node!r}: {exc}") from exc


class NameTranslationDialog:
    """Small modal Qt dialog for previewing and applying one translation plan."""

    def __init__(self, *, cmds_module=None, parent=None):
        from .qt_compat import (
            QCheckBox,
            QDialog,
            QFileDialog,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMessageBox,
            QPushButton,
            QTextEdit,
            QVBoxLayout,
        )

        # Keep the Qt imports local to the dialog entry point.  Importing the
        # Maya-independent translation core never requires PySide.
        self._qt = {
            "QFileDialog": QFileDialog,
            "QMessageBox": QMessageBox,
        }
        self._cmds = cmds_module
        self._preview_root = None
        self._preview_plan: Optional[Tuple[NameChange, ...]] = None
        self._preview_state = None
        self._model_root = None

        self._dialog = QDialog(parent)
        self._dialog.setObjectName("MMDNameTranslationDialog")
        self._dialog.setWindowTitle("Translate MMD Names")
        self._dialog.setModal(True)

        self.model_label = QLabel("Model: resolving…", self._dialog)
        self.model_label.setObjectName("modelLabel")
        self.dictionary_edit = QLineEdit(self._dialog)
        self.dictionary_edit.setObjectName("dictionaryPathEdit")
        self.browse_button = QPushButton("Browse…", self._dialog)
        self.browse_button.setObjectName("browseButton")
        self.overwrite_checkbox = QCheckBox("Overwrite existing EnglishName", self._dialog)
        self.overwrite_checkbox.setObjectName("overwriteCheckBox")
        self.rename_checkbox = QCheckBox("Also rename Maya nodes", self._dialog)
        self.rename_checkbox.setObjectName("renameNodesCheckBox")
        self.preview_text = QTextEdit(self._dialog)
        self.preview_text.setObjectName("previewText")
        self.preview_text.setReadOnly(True)
        self.preview_text.setMinimumHeight(240)
        self.status_label = QLabel("Choose a CSV, then click Preview.", self._dialog)
        self.status_label.setObjectName("statusLabel")
        self.preview_button = QPushButton("Preview", self._dialog)
        self.preview_button.setObjectName("previewButton")
        self.apply_button = QPushButton("Apply", self._dialog)
        self.apply_button.setObjectName("applyButton")
        self.cancel_button = QPushButton("Cancel", self._dialog)
        self.cancel_button.setObjectName("cancelButton")
        self.apply_button.setEnabled(False)
        self.preview_button.setDefault(True)

        dictionary_row = QHBoxLayout()
        dictionary_row.addWidget(self.dictionary_edit)
        dictionary_row.addWidget(self.browse_button)
        form = QFormLayout()
        form.addRow(self.model_label)
        form.addRow("Dictionary CSV", dictionary_row)
        form.addRow(self.overwrite_checkbox)
        form.addRow(self.rename_checkbox)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.cancel_button)
        layout = QVBoxLayout(self._dialog)
        layout.addLayout(form)
        layout.addWidget(self.preview_text)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

        self.browse_button.clicked.connect(self._choose_dictionary)
        self.preview_button.clicked.connect(self.preview)
        self.apply_button.clicked.connect(self.apply)
        self.cancel_button.clicked.connect(self._dialog.reject)
        self.dictionary_edit.textChanged.connect(self._invalidate_preview)
        self.overwrite_checkbox.stateChanged.connect(self._invalidate_preview)
        self.rename_checkbox.stateChanged.connect(self._invalidate_preview)

        try:
            self._model_root = resolve_model_root(cmds_module=self._cmds)
            self.model_label.setText(f"Model: {self._model_root}")
        except Exception as exc:
            self._report_error(str(exc), show_dialog=True)

    def __getattr__(self, name):
        """Delegate Qt window methods (show, close, exec, and so on)."""

        return getattr(self._dialog, name)

    @property
    def model_root(self):
        """Return the model root resolved while the dialog is open."""

        return self._model_root

    @property
    def preview_plan(self) -> Optional[Tuple[NameChange, ...]]:
        """Return the last successful preview, if one exists."""

        return self._preview_plan

    def exec_modal(self) -> bool:
        """Execute the modal using the available Qt binding."""

        exec_method = getattr(self._dialog, "exec", None) or getattr(self._dialog, "exec_", None)
        return bool(exec_method()) if callable(exec_method) else False

    def _choose_dictionary(self, *_args):
        path, _ = self._qt["QFileDialog"].getOpenFileName(
            self._dialog,
            "Select MMD name translation dictionary",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            self.dictionary_edit.setText(path)

    def _invalidate_preview(self, *_args):
        self._preview_plan = None
        self._preview_root = None
        self._preview_state = None
        self.apply_button.setEnabled(False)

    def preview(self, *_args):
        """Build and display a read-only plan; never mutate the scene."""

        self._invalidate_preview()
        try:
            root, plan = build_translation_preview(
                self.dictionary_edit.text().strip(),
                model_root=self._model_root,
                overwrite=self.overwrite_checkbox.isChecked(),
                rename_nodes=self.rename_checkbox.isChecked(),
                cmds_module=self._cmds,
            )
            cmds = self._cmds
            if cmds is None:
                from maya import cmds as maya_cmds

                cmds = maya_cmds
            preview_state = _capture_preview_state(plan, cmds_module=cmds)
            self._preview_root = root
            self._preview_plan = plan
            self._preview_state = preview_state
            lines = format_dialog_preview(plan)
            self.preview_text.setPlainText("\n".join(lines) if lines else "No changes planned.")
            self.status_label.setText(f"Preview ready: {len(plan)} change(s). Apply to commit them.")
            self.apply_button.setEnabled(bool(plan))
            return plan
        except Exception as exc:
            self.preview_text.clear()
            self._report_error(str(exc), show_dialog=True)
            return None

    def apply(self, *_args):
        """Apply the successful preview as one existing core undo chunk."""

        plan = self._preview_plan
        if not plan or not self._preview_root or self._preview_state is None:
            return None
        try:
            root = resolve_model_root(self._preview_root, cmds_module=self._cmds)
            if root != self._preview_root:
                raise NameTranslationError("the selected MMD model changed; preview again")
            cmds = self._cmds
            if cmds is None:
                from maya import cmds as maya_cmds

                cmds = maya_cmds

            _validate_preview_targets(
                plan,
                cmds_module=cmds,
                preview_state=self._preview_state,
            )
            changes = apply_translation_plan(plan, cmds_module=cmds)
            self.status_label.setText(f"Applied {len(changes)} change(s).")
            self._preview_plan = None
            self._preview_state = None
            self.apply_button.setEnabled(False)
            self._dialog.accept()
            return changes
        except Exception as exc:
            self._report_error(str(exc), show_dialog=True)
            return None

    def _report_error(self, message: str, *, show_dialog: bool):
        self.status_label.setText(f"Error: {message}")
        if show_dialog:
            warning = getattr(self._qt["QMessageBox"], "warning", None)
            if callable(warning):
                warning(self._dialog, "Translate MMD Names", message)


def show_name_translation_dialog(*, cmds_module=None, parent=None) -> bool:
    """Open the standalone modal dialog and return its accepted state."""

    dialog = NameTranslationDialog(cmds_module=cmds_module, parent=parent)
    return dialog.exec_modal()


__all__ = [
    "NameTranslationDialog",
    "build_translation_preview",
    "format_dialog_preview",
    "show_name_translation_dialog",
]
