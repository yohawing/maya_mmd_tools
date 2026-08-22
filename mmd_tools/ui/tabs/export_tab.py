"""PMX/VMD export workflow with independent stacked Model and Motion pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..base_tab import BaseTab
from ..components.category_stack import CategoryStack
from ..import_export_view_state import ImportExportViewState
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
from ...validation.vmd_validator import (
    VMD_EXPORT_BAKE_TIMELINE,
)


class _ExportPage(QWidget):
    """Own one format's settings, workflow controls, and validation report."""

    export_requested = Signal()

    _STATE_STATUS_KEYS = {
        STATE_EDITING: "editing",
        "Exporting": "validating_scene",
        "Succeeded": "completed",
        "Blocked": "blocked",
        "Failed": "blocked",
    }
    _PROGRESS_STATUS_KEYS = {
        "scene_preflight": "validating_scene",
        "payload_collection": "collecting_animation",
        "writer": "writing_temporary_file",
        "report_ready": "finalizing",
    }

    def __init__(self, owner, pane: str, title: str):
        super().__init__(owner)
        self.owner = owner
        self.pane = str(pane)
        self.export_format = "pmx" if self.pane == owner.MODEL_PANE else "vmd"
        self.setObjectName(f"export{self.pane.title()}Page")
        self._state = STATE_EDITING
        self._status_key = self._STATE_STATUS_KEYS[STATE_EDITING]
        self._restoring = False
        self._operation_active = False
        self._build(title)

    def _button_text(self, action: str) -> str:
        """Return the format-specific primary action label."""
        if self.pane == self.owner.MODEL_PANE:
            key = "export_pmx"
        else:
            key = "export_vmd"
        return self.owner.tr(key, "buttons")

    def _set_status_key(self, key: str) -> None:
        self._status_key = key
        self.state_label.setText(self.owner.tr(key, "export_status"))

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
            self.bake_export_check = QCheckBox(
                self.owner.tr("vmd_bake_export", "checkboxes")
            )
            self.bake_export_check.setObjectName("motionBakeExport")
            self.bake_export_check.setChecked(True)
            # Current character motion is always exported as a timeline bake.
            # Keep this visible as a contract indicator, not a user-selectable
            # strategy switch.
            self.bake_export_check.setEnabled(False)
            self._motion_form.addRow(self.bake_export_check)
            # Keep the explanation available as a tooltip without spending a
            # persistent row on an already unambiguous Bake-only option.
            self.bake_export_check.setToolTip(
                self.owner.tr("vmd_bake_export_help", "messages")
            )

            self.frame_range_check = QCheckBox(
                self.owner.tr("use_frame_range", "checkboxes")
            )
            self.frame_range_check.setObjectName("motionUseFrameRange")
            self.frame_range_check.toggled.connect(self._on_semantic_input_changed)
            self.frame_range_check.toggled.connect(
                lambda *_args: self._sync_frame_range_enabled()
            )
            self._motion_form.addRow(
                self.owner.tr("range", "fields"), self.frame_range_check
            )
            self.frame_start_spin = QSpinBox()
            self.frame_start_spin.setObjectName("motionFrameStart")
            self.frame_start_spin.setRange(0, 1000000)
            self.frame_start_spin.setValue(0)
            self.frame_start_spin.valueChanged.connect(self._on_semantic_input_changed)
            self._motion_form.addRow(
                self.owner.tr("start", "fields"), self.frame_start_spin
            )
            self.frame_end_spin = QSpinBox()
            self.frame_end_spin.setObjectName("motionFrameEnd")
            self.frame_end_spin.setRange(0, 1000000)
            self.frame_end_spin.setValue(120)
            self.frame_end_spin.valueChanged.connect(self._on_semantic_input_changed)
            self._motion_form.addRow(
                self.owner.tr("end", "fields"), self.frame_end_spin
            )
            self._sync_frame_range_enabled()
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
        self.output_path_edit.setText(self.owner._load_output_path(self.pane))
        self.output_browse_button = QPushButton(self.owner.tr("browse", "buttons"))
        output_row = QWidget(self)
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_path_edit)
        output_layout.addWidget(self.output_browse_button)
        export_form.addRow(self.owner.tr("file_path", "labels"), output_row)

        buttons = QHBoxLayout()
        self.export_button = QPushButton(self._button_text("export"))
        self.export_button.clicked.connect(self.export_requested.emit)
        buttons.addWidget(self.export_button)
        self.state_label = QLabel(self.owner.tr(self._status_key, "export_status"))
        self.state_label.setObjectName(f"export{self.pane.title()}StateLabel")
        buttons.addWidget(self.state_label)
        buttons.addStretch()
        export_form.addRow(buttons)
        self.workflow_layout.addWidget(self.export_group)

        self.validation_console = ValidationConsole(self)
        self.workflow_layout.addWidget(self.validation_console, 1)
        self.workflow_layout.addStretch()

        self.output_browse_button.clicked.connect(self.owner._browse_output)
        self.output_path_edit.textChanged.connect(self._on_output_path_changed)

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
        # Coerce the extension only in the request.  The line edit remains an
        # exact record of what the user typed, including a non-native suffix.
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
            options["export_strategy"] = VMD_EXPORT_BAKE_TIMELINE
            if self.frame_range_check.isChecked():
                options["frame_range"] = (
                    self.frame_start_spin.value(),
                    self.frame_end_spin.value(),
                )
        return ExportWorkflowRequest(file_path=output_path, options=options)

    def normalize_output_path(self) -> str:
        """Return a format-safe request path without mutating the line edit."""
        return self._coerce_output_path(self.output_path_edit.text(), self.export_format)

    def _on_output_path_changed(self, *_args) -> None:
        """Persist exact typed text and invalidate the active report."""
        self.owner._save_output_path(self.pane, self.output_path_edit.text())
        self._mark_editing()

    def _mark_editing(self, *_args) -> None:
        if self._restoring:
            return
        self._state = STATE_EDITING
        self._set_status_key(self._STATE_STATUS_KEYS[STATE_EDITING])
        self.validation_console.clear_report()
        self.owner._persist_semantic_preferences()

    def _on_semantic_input_changed(self, *_args) -> None:
        """Invalidate the visible report for timeline-affecting inputs."""
        self._mark_editing()

    def set_result(self, result: ExportWorkflowResult) -> None:
        previous_ack = self.validation_console.warnings_acknowledged
        self._state = result.state
        self._set_status_key(self._STATE_STATUS_KEYS.get(result.state, "blocked"))
        metadata = dict(result.metadata or {})
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
        self._set_status_key(self._STATE_STATUS_KEYS.get(state, "blocked"))

    def set_progress(self, stage: str) -> None:
        """Show the current one-shot export boundary beside the button."""
        self._set_status_key(self._PROGRESS_STATUS_KEYS.get(stage, "finalizing"))

    def set_operation_active(self, active: bool) -> None:
        """Disable every workflow entry point owned by this page."""
        self._operation_active = bool(active)
        enabled = not self._operation_active
        self.export_button.setEnabled(enabled)

    def invalidate(self) -> None:
        self._state = STATE_EDITING
        self._set_status_key(self._STATE_STATUS_KEYS[STATE_EDITING])
        self.validation_console.clear_report()

    def _sync_frame_range_enabled(self) -> None:
        """Only expose editable frame bounds when range export is enabled."""
        enabled = self.frame_range_check.isChecked()
        self.frame_start_spin.setEnabled(enabled)
        self.frame_end_spin.setEnabled(enabled)

    def _export_strategy(self) -> str:
        """Return the sole VMD export strategy."""

        if self.pane != self.owner.MOTION_PANE:
            return ""
        return VMD_EXPORT_BAKE_TIMELINE

    def set_export_strategy(self, export_strategy: str) -> None:
        """Migrate legacy settings while keeping the Bake indicator fixed."""
        del export_strategy
        self.bake_export_check.setChecked(True)
        self.bake_export_check.setEnabled(False)

    def retranslate(self) -> None:
        self.settings_group.setTitle(self.owner.tr("export", "settings"))
        self.export_group.setTitle(self.owner.tr("export", "groups"))
        self.output_browse_button.setText(self.owner.tr("browse", "buttons"))
        self.export_button.setText(self._button_text("export"))
        self._set_status_key(self._status_key)
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
            self.bake_export_check.setText(
                self.owner.tr("vmd_bake_export", "checkboxes")
            )
            self.bake_export_check.setToolTip(
                self.owner.tr("vmd_bake_export_help", "messages")
            )
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

    export_requested = Signal()

    MODEL_PANE = "model"
    MOTION_PANE = "motion"

    def __init__(
        self,
        parent=None,
        settings_service=None,
        view_state=None,
        maya_cmds=None,
        timeline_range_provider=None,
    ):
        super().__init__(parent)
        self.settings_service = settings_service
        self.view_state = view_state if view_state is not None else ImportExportViewState()
        self._maya_cmds = maya_cmds
        self._timeline_range_provider = timeline_range_provider
        self._active_pane = self.MODEL_PANE
        self._operation_owner_page = None
        self._result_owner_page = None
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
            page.export_requested.connect(self.export_requested.emit)
            self.category_stack.add_page(pane, page)
        self.category_stack.currentChanged.connect(self._on_pane_changed)
        main_layout.addWidget(self.category_stack)

    def _load_output_path(self, pane: str) -> str:
        """Load one pane's exact output text from the view-only state store."""
        key = "export_model_path" if pane == self.MODEL_PANE else "export_motion_path"
        value = self.view_state.get(key, "")
        return str(value or "")

    def _save_output_path(self, pane: str, value: str) -> None:
        """Persist one pane's exact output text without extension coercion."""
        key = "export_model_path" if pane == self.MODEL_PANE else "export_motion_path"
        self.view_state.set(key, str(value or ""))

    def _timeline_range(self):
        """Return Maya playback range lazily, with a Maya-independent fallback."""
        try:
            provider = self._timeline_range_provider
            if callable(provider):
                result = provider()
            else:
                cmds = self._maya_cmds
                if cmds is None:
                    from maya import cmds
                else:
                    cmds = self._maya_cmds
                result = (
                    cmds.playbackOptions(query=True, minTime=True),
                    cmds.playbackOptions(query=True, maxTime=True),
                )
            start, end = (int(round(float(value))) for value in result)
            if end < start:
                raise ValueError("Maya playback range is reversed")
            return start, end
        except Exception:
            return 0, 120

    def _on_pane_changed(self, index: int) -> None:
        """Update the active format when the shared selector changes."""
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
            def getter(_key, default=None):
                return default
        model = self._pages[self.MODEL_PANE]
        motion = self._pages[self.MOTION_PANE]
        model.apply_scale_check.setChecked(
            bool(getter(settings_keys.EXPORT_GENERAL_APPLY_SCALE, True))
        )
        # Read the legacy key only to consume old settings; its value cannot
        # select a second mode after the export contract was simplified.
        motion.set_export_strategy(getter(settings_keys.EXPORT_MOTION_STRATEGY, VMD_EXPORT_BAKE_TIMELINE))
        motion.frame_range_check.setChecked(
            bool(getter(settings_keys.EXPORT_MOTION_USE_FRAME_RANGE, False))
        )
        range_initialized = bool(
            getter(settings_keys.EXPORT_MOTION_RANGE_INITIALIZED, False)
        )
        if range_initialized:
            start = getter(settings_keys.EXPORT_MOTION_START_FRAME, 0)
            end = getter(settings_keys.EXPORT_MOTION_END_FRAME, 120)
        else:
            start, end = self._timeline_range()
        motion.frame_start_spin.setValue(int(start or 0))
        motion.frame_end_spin.setValue(int(end if end is not None else 120))
        motion._sync_frame_range_enabled()

    def _persist_semantic_preferences(self) -> None:
        service = self.settings_service
        setter = getattr(service, "set", None) if service is not None else None
        if not callable(setter):
            return
        model = self._pages[self.MODEL_PANE]
        motion = self._pages[self.MOTION_PANE]
        setter(settings_keys.EXPORT_GENERAL_APPLY_SCALE, model.apply_scale_check.isChecked())
        setter(settings_keys.EXPORT_MOTION_STRATEGY, VMD_EXPORT_BAKE_TIMELINE)
        setter(
            settings_keys.EXPORT_MOTION_USE_FRAME_RANGE,
            motion.frame_range_check.isChecked(),
        )
        setter(settings_keys.EXPORT_MOTION_START_FRAME, motion.frame_start_spin.value())
        setter(settings_keys.EXPORT_MOTION_END_FRAME, motion.frame_end_spin.value())
        setter(settings_keys.EXPORT_MOTION_RANGE_INITIALIZED, True)

    def build_request(self, current_model_root: Optional[str] = None) -> ExportWorkflowRequest:
        return self._active_page().build_request(current_model_root)

    def set_result(self, result: ExportWorkflowResult) -> None:
        owner_page = self._result_owner_page
        self._result_owner_page = None
        if owner_page is not None:
            # A workflow service may synchronously touch the category selector
            # while it is executing.  Restore the page that owns the result
            # before publishing it; otherwise the user sees an empty console
            # on the accidentally selected page and loses the operation
            # context.  The owner is a page object, so this also keeps model
            # and motion results independent when both pages exist.
            owner_index = 0 if owner_page.pane == self.MODEL_PANE else 1
            if self.category_stack.currentIndex() != owner_index:
                self.category_stack.setCurrentIndex(owner_index)
            owner_page.set_result(result)
            return
        self._active_page().set_result(result)

    def set_state(self, state: str) -> None:
        owner_page = self._operation_owner_page or self._result_owner_page
        (owner_page if owner_page is not None else self._active_page()).set_state(state)

    def set_progress(self, stage: str) -> None:
        owner_page = self._operation_owner_page or self._result_owner_page
        (owner_page if owner_page is not None else self._active_page()).set_progress(stage)

    def set_operation_active(self, active: bool) -> None:
        """Toggle controls only for the page that owns the active operation."""
        if active:
            self._operation_owner_page = self._active_page()
            self._result_owner_page = self._operation_owner_page
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
            "export_button",
            "state_label",
            "validation_console",
            "apply_scale_check",
            "bake_export_check",
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
                "bake_export_check": self.MOTION_PANE,
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
