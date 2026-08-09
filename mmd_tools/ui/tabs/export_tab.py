"""PMX/VMD export workflow view with isolated Model and Motion panes."""

from pathlib import Path
from typing import Any, Dict, Optional

from ..base_tab import BaseTab
from ..qt_compat import (
    QCheckBox,
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
    QTabWidget,
    QComboBox,
    QVBoxLayout,
    QWidget,
    Qt,
    Signal,
)
from ..validation_console import ValidationConsole
from ...services.export_workflow_service import (
    ExportWorkflowRequest,
    ExportWorkflowResult,
    STATE_EDITING,
)
from ...core import settings_keys


class ExportTab(BaseTab):
    """Collect fixed-format PMX/VMD options and show one shared console.

    The two pane dictionaries intentionally contain view state only.  The
    presenter supplies the authoritative current_model_root on every request;
    this view never exposes a target selector or Maya-selection fallback.
    """

    validate_requested = Signal()
    export_requested = Signal()

    MODEL_PANE = "model"
    MOTION_PANE = "motion"

    def __init__(self, parent=None, settings_service=None):
        super().__init__(parent)
        self.settings_service = settings_service
        self._state = STATE_EDITING
        self._active_pane = self.MODEL_PANE
        self._restoring_pane = False
        self._pane_states: Dict[str, Dict[str, Any]] = {
            self.MODEL_PANE: {},
            self.MOTION_PANE: {},
        }
        self._build_ui()
        self._load_semantic_preferences()
        self._capture_pane_state(self.MODEL_PANE)

    def _build_ui(self) -> None:
        """Build pane-specific settings and shared workflow controls."""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        splitter = QSplitter(Qt.Horizontal)

        settings_scroll = QScrollArea()
        settings_scroll.setObjectName("exportSettingsScroll")
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        self.settings_group = QGroupBox(self.tr("export", "settings"))
        group_layout = QVBoxLayout(self.settings_group)

        self.pane_tabs = QTabWidget()
        self.pane_tabs.setObjectName("exportPaneTabs")
        self._model_pane = self._build_model_pane()
        self._motion_pane = self._build_motion_pane()
        self.pane_tabs.addTab(self._model_pane, self.tr("export_model", "tabs"))
        self.pane_tabs.addTab(self._motion_pane, self.tr("export_motion", "tabs"))
        self.pane_tabs.currentChanged.connect(self._on_pane_changed)
        group_layout.addWidget(self.pane_tabs)
        settings_layout.addWidget(self.settings_group)
        settings_layout.addStretch()
        settings_scroll.setWidget(settings_widget)

        workflow_scroll = QScrollArea()
        workflow_scroll.setObjectName("exportWorkflowScroll")
        workflow_scroll.setWidgetResizable(True)
        workflow_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        workflow_widget = QWidget()
        workflow_layout = QVBoxLayout(workflow_widget)

        self.export_group = QGroupBox(self.tr("export", "groups"))
        export_form = QFormLayout(self.export_group)
        self._export_form = export_form
        self.output_path_edit = QLineEdit()
        self.output_path_edit.setObjectName("exportOutputPath")
        self.output_browse_button = QPushButton(self.tr("browse", "buttons"))
        output_row = QWidget(self)
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_path_edit)
        output_layout.addWidget(self.output_browse_button)
        export_form.addRow(self.tr("file_path", "labels"), output_row)

        buttons = QHBoxLayout()
        self.validate_button = QPushButton(self.tr("validate", "buttons"))
        self.validate_button.clicked.connect(self.validate_requested.emit)
        buttons.addWidget(self.validate_button)
        self.export_button = QPushButton(self.tr("export", "buttons"))
        self.export_button.clicked.connect(self.export_requested.emit)
        buttons.addWidget(self.export_button)
        self.state_label = QLabel(STATE_EDITING)
        buttons.addWidget(self.state_label)
        buttons.addStretch()
        export_form.addRow(buttons)
        workflow_layout.addWidget(self.export_group)

        self.validation_console = ValidationConsole(self)
        self.validation_console.revalidate_requested.connect(self.validate_requested.emit)
        workflow_layout.addWidget(self.validation_console, 1)
        workflow_layout.addStretch()
        workflow_scroll.setWidget(workflow_widget)

        splitter.addWidget(settings_scroll)
        splitter.addWidget(workflow_scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([300, 600])
        main_layout.addWidget(splitter)

        self.output_browse_button.clicked.connect(self._browse_output)
        self.output_path_edit.textChanged.connect(self._mark_editing)

    def _build_model_pane(self) -> QWidget:
        """Build the PMX model-only option pane."""
        pane = QWidget()
        layout = QFormLayout(pane)
        self.apply_scale_check = QCheckBox(self.tr("apply_scale", "checkboxes"))
        self.apply_scale_check.setObjectName("modelApplyScale")
        self.apply_scale_check.setChecked(True)
        self.apply_scale_check.toggled.connect(self._mark_editing)
        layout.addRow(self.tr("options", "fields"), self.apply_scale_check)
        return pane

    def _build_motion_pane(self) -> QWidget:
        """Build the VMD Mode A/C and optional frame-range pane."""
        pane = QWidget()
        layout = QFormLayout(pane)
        self.mode_label = QLabel(self.tr("vmd_mode", "fields"))
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("motionMode")
        self.mode_combo.addItems(["A", "C"])
        self.mode_combo.setCurrentText("C")
        self.mode_combo.currentTextChanged.connect(self._mark_editing)
        layout.addRow(self.mode_label, self.mode_combo)

        self.frame_range_check = QCheckBox(self.tr("use_frame_range", "checkboxes"))
        self.frame_range_check.setObjectName("motionUseFrameRange")
        self.frame_range_check.toggled.connect(self._mark_editing)
        self.frame_start_spin = QSpinBox()
        self.frame_start_spin.setObjectName("motionFrameStart")
        self.frame_start_spin.setRange(0, 1000000)
        self.frame_start_spin.setValue(0)
        self.frame_start_spin.valueChanged.connect(self._mark_editing)
        self.frame_end_spin = QSpinBox()
        self.frame_end_spin.setObjectName("motionFrameEnd")
        self.frame_end_spin.setRange(0, 1000000)
        self.frame_end_spin.setValue(120)
        self.frame_end_spin.valueChanged.connect(self._mark_editing)
        layout.addRow(self.tr("range", "fields"), self.frame_range_check)
        layout.addRow(self.tr("start", "fields"), self.frame_start_spin)
        layout.addRow(self.tr("end", "fields"), self.frame_end_spin)
        return pane

    @property
    def active_pane(self) -> str:
        """Return the canonical pane identifier."""
        return self._active_pane

    @property
    def current_export_format(self) -> str:
        """Return the fixed format owned by the active pane."""
        return "pmx" if self._active_pane == self.MODEL_PANE else "vmd"

    def _load_semantic_preferences(self) -> None:
        """Load semantic preferences without consulting legacy format keys."""
        service = self.settings_service
        getter = getattr(service, "get", None) if service is not None else None
        if not callable(getter):
            return
        self.apply_scale_check.setChecked(
            bool(getter(settings_keys.EXPORT_GENERAL_APPLY_SCALE, True))
        )
        mode = str(getter(settings_keys.EXPORT_MOTION_MODE, "C") or "C").upper()
        self.mode_combo.setCurrentText(mode if mode in ("A", "C") else "C")
        self.frame_range_check.setChecked(
            bool(getter(settings_keys.EXPORT_MOTION_USE_FRAME_RANGE, False))
        )
        self.frame_start_spin.setValue(
            int(getter(settings_keys.EXPORT_MOTION_START_FRAME, 0) or 0)
        )
        self.frame_end_spin.setValue(
            int(getter(settings_keys.EXPORT_MOTION_END_FRAME, 120) or 120)
        )

    def _persist_semantic_preferences(self) -> None:
        """Persist options that affect export semantics, not transient view state."""
        service = self.settings_service
        setter = getattr(service, "set", None) if service is not None else None
        if not callable(setter):
            return
        setter(settings_keys.EXPORT_GENERAL_APPLY_SCALE, self.apply_scale_check.isChecked())
        setter(settings_keys.EXPORT_MOTION_MODE, self.mode_combo.currentText().upper())
        setter(settings_keys.EXPORT_MOTION_USE_FRAME_RANGE, self.frame_range_check.isChecked())
        setter(settings_keys.EXPORT_MOTION_START_FRAME, self.frame_start_spin.value())
        setter(settings_keys.EXPORT_MOTION_END_FRAME, self.frame_end_spin.value())

    def _browse_output(self) -> None:
        """Select an output path using the active pane's fixed extension."""
        extension = self.current_export_format
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("export_mmd_asset", "messages"),
            self.output_path_edit.text(),
            f"{extension.upper()} Files (*.{extension});;All Files (*)",
        )
        if path:
            self.output_path_edit.setText(self._coerce_output_path(path, extension))

    @staticmethod
    def _coerce_output_path(path: str, extension: str) -> str:
        """Return a non-empty path with the pane's canonical suffix."""
        value = str(path or "").strip()
        if not value:
            return ""
        target = f".{extension}"
        current = Path(value)
        if current.suffix.lower() == target:
            return value
        return str(current.with_suffix(target))

    def _on_pane_changed(self, index: int) -> None:
        """Save the outgoing pane and restore the incoming report/ack state."""
        if self._restoring_pane:
            return
        self._capture_pane_state(self._active_pane)
        self._active_pane = self.MODEL_PANE if index == 0 else self.MOTION_PANE
        self._restore_pane_state(self._active_pane)

    def _capture_pane_state(self, pane: str) -> None:
        """Capture controls, workflow status, report, and acknowledgement."""
        state = self._pane_states[pane]
        state.update(
            {
                "output_path": self.output_path_edit.text(),
                "state": self._state,
                "report_snapshot": self.validation_console.snapshot_state(),
            }
        )
        if pane == self.MODEL_PANE:
            state["apply_scale"] = self.apply_scale_check.isChecked()
        else:
            state.update(
                {
                    "mode": self.mode_combo.currentText(),
                    "frame_range": self.frame_range_check.isChecked(),
                    "frame_start": self.frame_start_spin.value(),
                    "frame_end": self.frame_end_spin.value(),
                }
            )

    def _restore_pane_state(self, pane: str) -> None:
        """Restore one pane without firing edit invalidation signals."""
        state = self._pane_states[pane]
        self._restoring_pane = True
        try:
            if pane == self.MODEL_PANE and "apply_scale" in state:
                self.apply_scale_check.setChecked(bool(state["apply_scale"]))
            elif pane == self.MOTION_PANE and state:
                self.mode_combo.setCurrentText(str(state.get("mode", "C")))
                self.frame_range_check.setChecked(bool(state.get("frame_range", False)))
                self.frame_start_spin.setValue(int(state.get("frame_start", 0)))
                self.frame_end_spin.setValue(int(state.get("frame_end", 120)))
            self.output_path_edit.setText(
                self._coerce_output_path(
                    state.get("output_path", ""), self.current_export_format
                )
            )
            self._state = state.get("state", STATE_EDITING)
            self.state_label.setText(self._state)
            self.validation_console.restore_state(state.get("report_snapshot"))
        finally:
            self._restoring_pane = False

    def _mark_editing(self, *_args) -> None:
        """Invalidate only the active pane when one of its inputs changes."""
        if self._restoring_pane:
            return
        self._state = STATE_EDITING
        self.state_label.setText(self._state)
        self.validation_console.clear_report()
        self._capture_pane_state(self._active_pane)
        self._persist_semantic_preferences()

    def invalidate_all_panes(self) -> None:
        """Invalidate reports and acknowledgements after Current Model changes."""
        for pane in (self.MODEL_PANE, self.MOTION_PANE):
            self._pane_states[pane]["state"] = STATE_EDITING
            self._pane_states[pane]["report_snapshot"] = None
        self._state = STATE_EDITING
        self.state_label.setText(self._state)
        self.validation_console.clear_report()

    def build_request(self, current_model_root: Optional[str] = None) -> ExportWorkflowRequest:
        """Build an explicit PMX/VMD request for the shared Current Model."""
        export_format = self.current_export_format
        output_path = self._coerce_output_path(
            self.output_path_edit.text(), export_format
        )
        if output_path != self.output_path_edit.text():
            self._restoring_pane = True
            try:
                self.output_path_edit.setText(output_path)
            finally:
                self._restoring_pane = False
        options: Dict[str, Any] = {
            "export_format": export_format,
            "require_target": True,
            "require_current_model": True,
            "current_model_root": str(current_model_root or "") or None,
        }
        if export_format == "pmx":
            options["apply_scale"] = self.apply_scale_check.isChecked()
        else:
            options["vmd_mode"] = self.mode_combo.currentText().upper()
            if self.frame_range_check.isChecked():
                options["frame_range"] = (
                    self.frame_start_spin.value(),
                    self.frame_end_spin.value(),
                )
        return ExportWorkflowRequest(file_path=output_path, options=options)

    def set_result(self, result: ExportWorkflowResult) -> None:
        """Render a workflow result and preserve this pane's acknowledgement."""
        previous_ack = self.validation_console.warnings_acknowledged
        self._state = result.state
        self.state_label.setText(result.state)
        metadata = dict(result.metadata or {})
        if result.snapshot is not None:
            metadata["payload_fingerprint"] = result.snapshot.payload_fingerprint
        action_result = result.action_result
        if action_result is not None:
            metadata.setdefault(
                "payload_fingerprint",
                getattr(action_result, "payload_fingerprint", None),
            )
        self.validation_console.set_report(result.report, metadata)
        if previous_ack:
            self.validation_console.restore_acknowledgement(True)
        self._capture_pane_state(self._active_pane)

    def set_state(self, state: str) -> None:
        """Display an in-flight workflow state without altering the report."""
        self._state = state
        self.state_label.setText(state)

    def retranslateUi(self) -> None:
        """Refresh Export labels after a language change."""
        self.settings_group.setTitle(self.tr("export", "settings"))
        self.export_group.setTitle(self.tr("export", "groups"))
        self.pane_tabs.setTabText(0, self.tr("export_model", "tabs"))
        self.pane_tabs.setTabText(1, self.tr("export_motion", "tabs"))
        self.mode_label.setText(self.tr("vmd_mode", "fields"))
        self.frame_range_check.setText(self.tr("use_frame_range", "checkboxes"))
        self.output_browse_button.setText(self.tr("browse", "buttons"))
        self.validate_button.setText(self.tr("validate", "buttons"))
        self.export_button.setText(self.tr("export", "buttons"))
        self._set_form_label(
            self._export_form,
            self.output_path_edit,
            self.tr("file_path", "labels"),
        )
        self.validation_console.retranslateUi()

    @staticmethod
    def _set_form_label(form, field, text: str) -> None:
        """Set a QFormLayout label when the Qt binding exposes it."""
        label_for_field = getattr(form, "labelForField", None)
        label = label_for_field(field) if label_for_field is not None else None
        if label is not None:
            label.setText(text)


__all__ = ["ExportTab"]
