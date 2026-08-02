"""Top-level Export workflow tab backed by the shared validation service."""

from typing import Optional

from ..base_tab import BaseTab
from ..qt_compat import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
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
        """Build the workflow controls without embedding validation policy."""
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.target_combo = QComboBox()
        self.target_combo.addItem("Current selection", "")
        form.addRow("Target", self.target_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItems(["pmx", "pmd", "vmd"])
        self.format_combo.currentTextChanged.connect(self._sync_mode_visibility)
        form.addRow("Format", self.format_combo)

        self.mode_label = QLabel("VMD mode")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["C", "A"])
        mode_row = QWidget(self)
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(self.mode_combo)
        mode_layout.addStretch()
        form.addRow(self.mode_label, mode_row)

        self.frame_range_check = QCheckBox("Use frame range")
        self.frame_start_spin = QSpinBox()
        self.frame_start_spin.setRange(0, 1000000)
        self.frame_start_spin.setValue(0)
        self.frame_end_spin = QSpinBox()
        self.frame_end_spin.setRange(0, 1000000)
        self.frame_end_spin.setValue(120)
        range_row = QWidget(self)
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.addWidget(self.frame_range_check)
        range_layout.addWidget(QLabel("Start"))
        range_layout.addWidget(self.frame_start_spin)
        range_layout.addWidget(QLabel("End"))
        range_layout.addWidget(self.frame_end_spin)
        range_layout.addStretch()
        form.addRow("Range", range_row)

        self.apply_scale_check = QCheckBox("Apply scale")
        self.apply_scale_check.setChecked(True)
        form.addRow("Options", self.apply_scale_check)

        self.output_path_edit = QLineEdit()
        self.output_browse_button = QPushButton("Browse")
        output_row = QWidget(self)
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_path_edit)
        output_layout.addWidget(self.output_browse_button)
        form.addRow("Output", output_row)
        layout.addLayout(form)

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
        layout.addLayout(buttons)

        self.validation_console = ValidationConsole(self)
        self.validation_console.revalidate_requested.connect(self.validate_requested.emit)
        layout.addWidget(self.validation_console)

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
        self.target_combo.addItem("Current selection", "")
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
