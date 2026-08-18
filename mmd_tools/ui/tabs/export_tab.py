"""PMX/VMD export workflow with independent stacked Model and Motion pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..base_tab import BaseTab
from ..components.category_stack import CategoryStack
from ..qt_compat import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
    Qt,
    Signal,
)
from ..validation_console import ValidationConsole
from ...core import settings_keys
from ...services.export_workflow_service import (
    ExportWorkflowRequest,
    ExportWorkflowResult,
    STATE_EDITING,
)


class _ExportPage(QWidget):
    """Own one format's settings, workflow controls, and validation report."""

    validate_requested = Signal()
    export_requested = Signal()

    def __init__(self, owner, pane: str, title: str):
        super().__init__(owner)
        self.owner = owner
        self.pane = str(pane)
        self.export_format = "pmx" if self.pane == owner.MODEL_PANE else "vmd"
        self.setObjectName(f"export{self.pane.title()}Page")
        self._state = STATE_EDITING
        self._restoring = False
        self._build(title)

    def _button_text(self, action: str) -> str:
        """Return the format-specific primary action label."""
        if self.pane == self.owner.MODEL_PANE:
            key = "validate_model" if action == "validate" else "export_pmx"
        else:
            key = "validate_animation" if action == "validate" else "export_vmd"
        return self.owner.tr(key, "buttons")

    def _build(self, title: str) -> None:
        page_layout = QVBoxLayout(self)
        header = QLabel(title, self)
        header.setObjectName(f"export{self.pane.title()}PageHeader")
        header.setProperty("headingLevel", 2)
        page_layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setObjectName(f"export{self.pane.title()}PageSplitter")
        settings_scroll, settings_widget, settings_layout = self._scroll_container(
            f"export{self.pane.title()}SettingsScroll"
        )
        workflow_scroll, workflow_widget, workflow_layout = self._scroll_container(
            f"export{self.pane.title()}WorkflowScroll"
        )
        self.settings_widget = settings_widget
        self.settings_layout = settings_layout
        self.workflow_widget = workflow_widget
        self.workflow_layout = workflow_layout
        self._build_settings()
        self._build_workflow()
        splitter.addWidget(settings_scroll)
        splitter.addWidget(workflow_scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([300, 600])
        page_layout.addWidget(splitter, 1)

    def _scroll_container(self, object_name):
        scroll = QScrollArea()
        scroll.setObjectName(object_name)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        scroll.setWidget(widget)
        return scroll, widget, layout

    def _build_settings(self) -> None:
        self.settings_group = QGroupBox(self.owner.tr("export", "settings"))
        self.settings_layout.addWidget(self.settings_group)
        if self.pane == self.owner.MODEL_PANE:
            self._model_form = QFormLayout(self.settings_group)
            self.apply_scale_check = QCheckBox(
                self.owner.tr("apply_scale", "checkboxes")
            )
            self.apply_scale_check.setObjectName("modelApplyScale")
            self.apply_scale_check.setChecked(True)
            self.apply_scale_check.toggled.connect(self.owner._mark_editing)
            self._model_form.addRow(
                self.owner.tr("options", "fields"), self.apply_scale_check
            )
        else:
            self._motion_form = QFormLayout(self.settings_group)
            self.mode_label = QLabel(self.owner.tr("vmd_mode", "fields"))
            self.mode_combo = QComboBox()
            self.mode_combo.setObjectName("motionMode")
            self.mode_combo.addItems(["A", "C"])
            self.mode_combo.setCurrentText("C")
            self.mode_combo.currentTextChanged.connect(self.owner._mark_editing)
            self._motion_form.addRow(self.mode_label, self.mode_combo)

            self.frame_range_check = QCheckBox(
                self.owner.tr("use_frame_range", "checkboxes")
            )
            self.frame_range_check.setObjectName("motionUseFrameRange")
            self.frame_range_check.toggled.connect(self.owner._mark_editing)
            self._motion_form.addRow(
                self.owner.tr("range", "fields"), self.frame_range_check
            )
            self.frame_start_spin = QSpinBox()
            self.frame_start_spin.setObjectName("motionFrameStart")
            self.frame_start_spin.setRange(0, 1000000)
            self.frame_start_spin.setValue(0)
            self.frame_start_spin.valueChanged.connect(self.owner._mark_editing)
            self._motion_form.addRow(
                self.owner.tr("start", "fields"), self.frame_start_spin
            )
            self.frame_end_spin = QSpinBox()
            self.frame_end_spin.setObjectName("motionFrameEnd")
            self.frame_end_spin.setRange(0, 1000000)
            self.frame_end_spin.setValue(120)
            self.frame_end_spin.valueChanged.connect(self.owner._mark_editing)
            self._motion_form.addRow(
                self.owner.tr("end", "fields"), self.frame_end_spin
            )
        self.settings_layout.addStretch()

    def _build_workflow(self) -> None:
        self.export_group = QGroupBox(self.owner.tr("export", "groups"))
        export_form = QFormLayout(self.export_group)
        self._export_form = export_form
        self.output_path_edit = QLineEdit()
        self.output_path_edit.setObjectName(
            "exportOutputPath"
            if self.pane == self.owner.MODEL_PANE
            else "exportMotionOutputPath"
        )
        self.output_browse_button = QPushButton(self.owner.tr("browse", "buttons"))
        output_row = QWidget(self)
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_path_edit)
        output_layout.addWidget(self.output_browse_button)
        export_form.addRow(self.owner.tr("file_path", "labels"), output_row)

        buttons = QHBoxLayout()
        self.validate_button = QPushButton(self._button_text("validate"))
        self.validate_button.clicked.connect(self.validate_requested.emit)
        buttons.addWidget(self.validate_button)
        self.export_button = QPushButton(self._button_text("export"))
        self.export_button.clicked.connect(self.export_requested.emit)
        buttons.addWidget(self.export_button)
        self.state_label = QLabel(STATE_EDITING)
        self.state_label.setObjectName(f"export{self.pane.title()}StateLabel")
        buttons.addWidget(self.state_label)
        buttons.addStretch()
        export_form.addRow(buttons)
        self.workflow_layout.addWidget(self.export_group)

        self.validation_console = ValidationConsole(self)
        self.validation_console.revalidate_requested.connect(self.validate_requested.emit)
        self.workflow_layout.addWidget(self.validation_console, 1)
        self.workflow_layout.addStretch()

        self.output_browse_button.clicked.connect(self.owner._browse_output)
        self.output_path_edit.textChanged.connect(self.owner._mark_editing)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.owner.tr("export_mmd_asset", "messages"),
            self.output_path_edit.text(),
            f"{self.export_format.upper()} Files (*.{self.export_format});;All Files (*)",
        )
        if path:
            self.output_path_edit.setText(self._coerce_output_path(path, self.export_format))

    @staticmethod
    def _coerce_output_path(path: str, extension: str) -> str:
        value = str(path or "").strip()
        if not value:
            return ""
        target = f".{extension}"
        current = Path(value)
        if current.suffix.lower() == target:
            return value
        return str(current.with_suffix(target))

    def build_request(self, current_model_root: Optional[str]) -> ExportWorkflowRequest:
        output_path = self.normalize_output_path()
        options: Dict[str, Any] = {
            "export_format": self.export_format,
            "require_target": True,
            "require_current_model": True,
            "current_model_root": str(current_model_root or "") or None,
        }
        if self.pane == self.owner.MODEL_PANE:
            options["apply_scale"] = self.apply_scale_check.isChecked()
        else:
            options["vmd_mode"] = self.mode_combo.currentText().upper()
            if self.frame_range_check.isChecked():
                options["frame_range"] = (
                    self.frame_start_spin.value(),
                    self.frame_end_spin.value(),
                )
        return ExportWorkflowRequest(file_path=output_path, options=options)

    def normalize_output_path(self) -> str:
        """Keep this page's output path aligned with its fixed export format."""
        current = self.output_path_edit.text()
        normalized = self._coerce_output_path(current, self.export_format)
        if normalized != current:
            self._restoring = True
            try:
                self.output_path_edit.setText(normalized)
            finally:
                self._restoring = False
        return normalized

    def _mark_editing(self, *_args) -> None:
        if self._restoring:
            return
        self._state = STATE_EDITING
        self.state_label.setText(self._state)
        self.validation_console.clear_report()
        self.owner._persist_semantic_preferences()

    def set_result(self, result: ExportWorkflowResult) -> None:
        previous_ack = self.validation_console.warnings_acknowledged
        self._state = result.state
        self.state_label.setText(result.state)
        metadata = dict(result.metadata or {})
        if result.snapshot is not None:
            metadata["payload_fingerprint"] = result.snapshot.payload_fingerprint
        action_result = result.action_result
        if action_result is not None:
            metadata.setdefault(
                "payload_fingerprint", getattr(action_result, "payload_fingerprint", None)
            )
        self.validation_console.set_report(result.report, metadata)
        if previous_ack:
            self.validation_console.restore_acknowledgement(True)

    def set_state(self, state: str) -> None:
        self._state = state
        self.state_label.setText(state)

    def set_operation_active(self, active: bool) -> None:
        """Disable every workflow entry point owned by this page."""
        enabled = not bool(active)
        self.validate_button.setEnabled(enabled)
        self.export_button.setEnabled(enabled)
        self.validation_console.revalidate_button.setEnabled(enabled)

    def invalidate(self) -> None:
        self._state = STATE_EDITING
        self.state_label.setText(STATE_EDITING)
        self.validation_console.clear_report()

    def retranslate(self) -> None:
        self.settings_group.setTitle(self.owner.tr("export", "settings"))
        self.export_group.setTitle(self.owner.tr("export", "groups"))
        self.output_browse_button.setText(self.owner.tr("browse", "buttons"))
        self.validate_button.setText(self._button_text("validate"))
        self.export_button.setText(self._button_text("export"))
        self._set_form_label(
            self._export_form,
            self.output_path_edit,
            self.owner.tr("file_path", "labels"),
        )
        if self.pane == self.owner.MODEL_PANE:
            self.apply_scale_check.setText(self.owner.tr("apply_scale", "checkboxes"))
            self._set_form_label(
                self._model_form,
                self.apply_scale_check,
                self.owner.tr("options", "fields"),
            )
        else:
            self.mode_label.setText(self.owner.tr("vmd_mode", "fields"))
            self.frame_range_check.setText(
                self.owner.tr("use_frame_range", "checkboxes")
            )
            self._set_form_label(
                self._motion_form,
                self.frame_range_check,
                self.owner.tr("range", "fields"),
            )
            self._set_form_label(
                self._motion_form,
                self.frame_start_spin,
                self.owner.tr("start", "fields"),
            )
            self._set_form_label(
                self._motion_form,
                self.frame_end_spin,
                self.owner.tr("end", "fields"),
            )
        self.validation_console.retranslateUi()

    @staticmethod
    def _set_form_label(form, field, text: str) -> None:
        label_for_field = getattr(form, "labelForField", None)
        label = label_for_field(field) if callable(label_for_field) else None
        if label is not None:
            label.setText(text)


class ExportTab(BaseTab):
    """Present independent PMX and VMD pages under one category selector."""

    validate_requested = Signal()
    export_requested = Signal()

    MODEL_PANE = "model"
    MOTION_PANE = "motion"

    def __init__(self, parent=None, settings_service=None):
        super().__init__(parent)
        self.settings_service = settings_service
        self._active_pane = self.MODEL_PANE
        self._operation_owner_page = None
        self._build_ui()
        self._load_semantic_preferences()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        self.category_stack = CategoryStack(
            (self.MODEL_PANE, self.MOTION_PANE),
            {
                self.MODEL_PANE: self.tr("export_model", "tabs"),
                self.MOTION_PANE: self.tr("export_motion", "tabs"),
            },
            "exportCategoryStack",
            self,
            navigation="tabs",
        )
        self.category_stack.setObjectName("exportCategoryStack")
        # Compatibility name retained as an API alias for existing presenters
        # and GUI probes; navigation is now the shared tab presentation.
        self.pane_tabs = self.category_stack
        self._pages = {
            self.MODEL_PANE: _ExportPage(
                self, self.MODEL_PANE, self.tr("export_model", "tabs")
            ),
            self.MOTION_PANE: _ExportPage(
                self, self.MOTION_PANE, self.tr("export_motion", "tabs")
            ),
        }
        for pane, page in self._pages.items():
            page.validate_requested.connect(self.validate_requested.emit)
            page.export_requested.connect(self.export_requested.emit)
            self.category_stack.add_page(pane, page)
        self.category_stack.currentChanged.connect(self._on_pane_changed)
        main_layout.addWidget(self.category_stack)

    def _on_pane_changed(self, index: int) -> None:
        """Update the active format when the shared selector changes."""
        outgoing = self._pages.get(self._active_pane)
        if outgoing is not None:
            outgoing.normalize_output_path()
        self._active_pane = (
            self.MODEL_PANE if int(index) == 0 else self.MOTION_PANE
        )

    @property
    def active_pane(self) -> str:
        return self._active_pane

    @property
    def current_export_format(self) -> str:
        return "pmx" if self._active_pane == self.MODEL_PANE else "vmd"

    @property
    def validation_consoles(self):
        """Return both page-owned consoles for presenter signal wiring."""
        return tuple(page.validation_console for page in self._pages.values())

    def _active_page(self) -> _ExportPage:
        return self._pages[self._active_pane]

    def _mark_editing(self, *_args) -> None:
        """Invalidate only the active page through the stable tab contract."""
        self._active_page()._mark_editing()

    def _browse_output(self) -> None:
        """Open the active page's format-specific output dialog."""
        self._active_page()._browse_output()

    def _load_semantic_preferences(self) -> None:
        service = self.settings_service
        getter = getattr(service, "get", None) if service is not None else None
        if not callable(getter):
            return
        model = self._pages[self.MODEL_PANE]
        motion = self._pages[self.MOTION_PANE]
        model.apply_scale_check.setChecked(
            bool(getter(settings_keys.EXPORT_GENERAL_APPLY_SCALE, True))
        )
        mode = str(getter(settings_keys.EXPORT_MOTION_MODE, "C") or "C").upper()
        motion.mode_combo.setCurrentText(mode if mode in ("A", "C") else "C")
        motion.frame_range_check.setChecked(
            bool(getter(settings_keys.EXPORT_MOTION_USE_FRAME_RANGE, False))
        )
        motion.frame_start_spin.setValue(
            int(getter(settings_keys.EXPORT_MOTION_START_FRAME, 0) or 0)
        )
        motion.frame_end_spin.setValue(
            int(getter(settings_keys.EXPORT_MOTION_END_FRAME, 120) or 120)
        )

    def _persist_semantic_preferences(self) -> None:
        service = self.settings_service
        setter = getattr(service, "set", None) if service is not None else None
        if not callable(setter):
            return
        model = self._pages[self.MODEL_PANE]
        motion = self._pages[self.MOTION_PANE]
        setter(settings_keys.EXPORT_GENERAL_APPLY_SCALE, model.apply_scale_check.isChecked())
        setter(settings_keys.EXPORT_MOTION_MODE, motion.mode_combo.currentText().upper())
        setter(
            settings_keys.EXPORT_MOTION_USE_FRAME_RANGE,
            motion.frame_range_check.isChecked(),
        )
        setter(settings_keys.EXPORT_MOTION_START_FRAME, motion.frame_start_spin.value())
        setter(settings_keys.EXPORT_MOTION_END_FRAME, motion.frame_end_spin.value())

    def build_request(self, current_model_root: Optional[str] = None) -> ExportWorkflowRequest:
        return self._active_page().build_request(current_model_root)

    def set_result(self, result: ExportWorkflowResult) -> None:
        self._active_page().set_result(result)

    def set_state(self, state: str) -> None:
        self._active_page().set_state(state)

    def set_operation_active(self, active: bool) -> None:
        """Toggle controls only for the page that owns the active operation."""
        if active:
            self._operation_owner_page = self._active_page()
            self._operation_owner_page.set_operation_active(True)
            return
        owner_page = self._operation_owner_page
        self._operation_owner_page = None
        if owner_page is not None:
            owner_page.set_operation_active(False)

    def invalidate_all_panes(self) -> None:
        """Invalidate each page's report and acknowledgement independently."""
        for page in self._pages.values():
            page.invalidate()

    def retranslateUi(self) -> None:
        labels = {
            self.MODEL_PANE: self.tr("export_model", "tabs"),
            self.MOTION_PANE: self.tr("export_motion", "tabs"),
        }
        self.category_stack.retranslate(labels)
        for page in self._pages.values():
            page.retranslate()
        for pane, page in self._pages.items():
            header = page.findChild(QLabel, f"export{pane.title()}PageHeader")
            if header is not None:
                header.setText(labels[pane])

    def __getattr__(self, name):
        """Expose the active page's legacy controls without shared state."""
        if name in {
            "output_path_edit",
            "output_browse_button",
            "validate_button",
            "export_button",
            "state_label",
            "validation_console",
            "apply_scale_check",
            "mode_label",
            "mode_combo",
            "frame_range_check",
            "frame_start_spin",
            "frame_end_spin",
            "_model_form",
            "_motion_form",
            "_export_form",
            "export_group",
            "settings_group",
        }:
            pages = self.__dict__.get("_pages", {})
            pane_by_attribute = {
                "_model_form": self.MODEL_PANE,
                "apply_scale_check": self.MODEL_PANE,
                "_motion_form": self.MOTION_PANE,
                "mode_label": self.MOTION_PANE,
                "mode_combo": self.MOTION_PANE,
                "frame_range_check": self.MOTION_PANE,
                "frame_start_spin": self.MOTION_PANE,
                "frame_end_spin": self.MOTION_PANE,
            }
            pane = pane_by_attribute.get(
                name, self.__dict__.get("_active_pane", self.MODEL_PANE)
            )
            page = pages.get(pane)
            if page is not None and hasattr(page, name):
                return getattr(page, name)
        raise AttributeError(name)


__all__ = ["ExportTab"]
