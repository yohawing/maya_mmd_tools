"""Top-level Export workflow tab backed by the shared validation service."""

from typing import Optional

from ..base_tab import BaseTab
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
from ...services.export_workflow_service import (
    ExportWorkflowRequest,
    ExportWorkflowResult,
    STATE_EDITING,
)


class ExportTab(BaseTab):
    """Collect export options and display one canonical Validation Console."""

    validate_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent=None, settings_service=None):
        super().__init__(parent)
        self.settings_service = settings_service
        self._state = STATE_EDITING
        self._build_ui()

    def _build_ui(self) -> None:
        """Build the Import-style settings sidebar and export workflow pane."""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        splitter = QSplitter(Qt.Horizontal)

        # Left side: export settings, matching the Import tab's sidebar.
        settings_scroll = QScrollArea()
        settings_scroll.setObjectName("exportSettingsScroll")
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        self.settings_group = QGroupBox(self.tr("export", "settings"))
        settings_form = QFormLayout()

        self.target_combo = QComboBox()
        self.target_combo.addItem(self.tr("current_model", "fields").rstrip(":"), "")
        settings_form.addRow(self.tr("target_model", "fields"), self.target_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItems(["pmx", "pmd", "vmd"])
        self.format_combo.currentTextChanged.connect(self._sync_mode_visibility)
        settings_form.addRow(self.tr("format", "fields"), self.format_combo)

        self.mode_label = QLabel("VMD mode")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["C", "A"])
        mode_row = QWidget(self)
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(self.mode_combo)
        mode_layout.addStretch()
        settings_form.addRow(self.mode_label, mode_row)

        self.frame_range_check = QCheckBox("Use frame range")
        self.frame_start_spin = QSpinBox()
        self.frame_start_spin.setRange(0, 1000000)
        self.frame_start_spin.setValue(0)
        self.frame_end_spin = QSpinBox()
        self.frame_end_spin.setRange(0, 1000000)
        self.frame_end_spin.setValue(120)
        settings_form.addRow("Range", self.frame_range_check)
        settings_form.addRow("Start", self.frame_start_spin)
        settings_form.addRow("End", self.frame_end_spin)

        self.apply_scale_check = QCheckBox(self.tr("apply_scale", "checkboxes"))
        self.apply_scale_check.setChecked(True)
        settings_form.addRow("Options", self.apply_scale_check)

        self.settings_group.setLayout(settings_form)
        settings_layout.addWidget(self.settings_group)
        settings_layout.addStretch()
        settings_scroll.setWidget(settings_widget)

        # Right side: output/action controls followed by the validation console.
        workflow_scroll = QScrollArea()
        workflow_scroll.setObjectName("exportWorkflowScroll")
        workflow_scroll.setWidgetResizable(True)
        workflow_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        workflow_widget = QWidget()
        workflow_layout = QVBoxLayout(workflow_widget)

        self.export_group = QGroupBox(self.tr("export", "groups"))
        export_form = QFormLayout()

        self.output_path_edit = QLineEdit()
        self.output_browse_button = QPushButton(self.tr("browse", "buttons"))
        output_row = QWidget(self)
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_path_edit)
        output_layout.addWidget(self.output_browse_button)
        export_form.addRow(self.tr("file_path", "labels"), output_row)

        buttons = QHBoxLayout()
        self.validate_button = QPushButton("Validate")
        self.validate_button.clicked.connect(self.validate_requested.emit)
        buttons.addWidget(self.validate_button)
        self.export_button = QPushButton("Export")
        self.export_button.clicked.connect(self.export_requested.emit)
        buttons.addWidget(self.export_button)
        self.state_label = QLabel(STATE_EDITING)
        buttons.addWidget(self.state_label)
        buttons.addStretch()
        export_form.addRow(buttons)
        self.export_group.setLayout(export_form)
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
        for widget, signal_name in (
            (self.target_combo, "currentTextChanged"),
            (self.format_combo, "currentTextChanged"),
            (self.mode_combo, "currentTextChanged"),
            (self.frame_range_check, "toggled"),
            (self.frame_start_spin, "valueChanged"),
            (self.frame_end_spin, "valueChanged"),
            (self.apply_scale_check, "toggled"),
            (self.output_path_edit, "textChanged"),
        ):
            getattr(getattr(widget, signal_name), "connect")(self._mark_editing)
        self._sync_mode_visibility(self.format_combo.currentText())

    def _browse_output(self) -> None:
        """Select an output path using a format-aware file dialog."""
        extension = self.format_combo.currentText()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export MMD asset",
            self.output_path_edit.text(),
            f"{extension.upper()} Files (*.{extension});;All Files (*)",
        )
        if path:
            self.output_path_edit.setText(path)

    def _sync_mode_visibility(self, export_format: str) -> None:
        """Keep Mode A/C visible only for VMD without hiding policy in UI."""
        visible = export_format == "vmd"
        self.mode_label.setVisible(visible)
        self.mode_combo.setVisible(visible)

    def _mark_editing(self, *_args) -> None:
        """Invalidate the displayed report when workflow inputs change."""
        self._state = STATE_EDITING
        self.state_label.setText(self._state)
        self.validation_console.clear_report()

    def set_targets(self, targets, current_target: Optional[str] = None) -> None:
        """Refresh live model targets while retaining the current selection."""
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        self.target_combo.addItem(self.tr("current_model", "fields").rstrip(":"), "")
        for target in targets or []:
            self.target_combo.addItem(str(target), str(target))
        if current_target:
            index = self.target_combo.findData(current_target)
            if index >= 0:
                self.target_combo.setCurrentIndex(index)
        self.target_combo.blockSignals(False)

    def current_target(self) -> Optional[str]:
        """Return the selected model root, or None for current Maya selection."""
        value = self.target_combo.currentData()
        return str(value) if value else None

    def build_request(self) -> ExportWorkflowRequest:
        """Build the format-neutral request consumed by ExportWorkflowService."""
        export_format = self.format_combo.currentText().lower()
        options = {
            "export_format": export_format,
            "require_target": True,
            "target_model": self.current_target(),
            "apply_scale": self.apply_scale_check.isChecked(),
        }
        if export_format == "vmd":
            options["vmd_mode"] = self.mode_combo.currentText().upper()
        if self.frame_range_check.isChecked():
            options["frame_range"] = (
                self.frame_start_spin.value(),
                self.frame_end_spin.value(),
            )
        return ExportWorkflowRequest(
            file_path=self.output_path_edit.text().strip(),
            options=options,
        )

    def set_result(self, result: ExportWorkflowResult) -> None:
        """Render a workflow result and its state in the same console."""
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

    def set_state(self, state: str) -> None:
        """Display an in-flight workflow state without altering the report."""
        self._state = state
        self.state_label.setText(state)

    def retranslateUi(self) -> None:
        """Keep the tab API compatible with the main-window language switch."""
        return None


__all__ = ["ExportTab"]
