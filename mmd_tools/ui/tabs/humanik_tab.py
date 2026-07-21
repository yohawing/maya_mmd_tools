"""HumanIK tab UI shell.

Status display + buttons that call the existing HumanIK menu actions
(``mmd_tools.ui.humanik_menu_actions``). This tab intentionally does not
reimplement any lifecycle decision: model resolution UX, confirmation
dialogs, and error reporting all live in the menu action layer already and
are reused as-is (see ``HumanIkPresenter``). The tab's own responsibility is
limited to visualizing ``describe_frontend_state()`` and showing why a button
is disabled.
"""

from ..qt_compat import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)
from ..base_tab import BaseTab
from .translation_registry import apply_translation_registry


# Action button attribute -> (translation key, category) for the button text.
_ACTION_BUTTON_SPECS = (
    ("setup_characterize_btn", "humanik_setup_characterize"),
    ("enter_source_btn", "humanik_enter_source"),
    ("enter_target_btn", "humanik_enter_target"),
    ("bake_btn", "humanik_bake"),
    ("create_control_rig_btn", "humanik_create_control_rig"),
    ("restore_btn", "humanik_restore"),
    ("diagnostics_btn", "humanik_diagnostics"),
)

# Maps ``describe_frontend_state()`` action keys to the button attribute that
# represents them on this tab.
ACTION_KEY_TO_BUTTON = {
    "setup_and_characterize": "setup_characterize_btn",
    "enter_source_mode": "enter_source_btn",
    "enter_target_mode": "enter_target_btn",
    "bake_to_mmd_rig": "bake_btn",
    "create_control_rig": "create_control_rig_btn",
    "restore_mmd_rig": "restore_btn",
    "diagnostics": "diagnostics_btn",
}

# reasonCode (from mmd_tools.core.humanik_frontend) -> translation key for the
# user-facing disabled-button explanation. Kept in the View so the reasonCode
# enum stays the single source of truth on the backend and this file is the
# single source of truth for its display text.
REASON_CODE_TRANSLATION_KEYS = {
    "preview_active": "humanik_reason_preview_active",
    "not_characterized": "humanik_reason_not_characterized",
    "no_source": "humanik_reason_no_source",
    "target_is_source": "humanik_reason_target_is_source",
    "profile_mismatch": "humanik_reason_profile_mismatch",
    "model_is_source": "humanik_reason_model_is_source",
    "no_active_preview": "humanik_reason_no_active_preview",
    "already_characterized_other_profile": "humanik_reason_already_characterized_other_profile",
    "nothing_to_restore": "humanik_reason_nothing_to_restore",
    "model_required": "humanik_reason_model_required",
    # ``importLock.reasonCode`` values (mirrors
    # ``humanik_frontend.REASON_IMPORT_BLOCKED_TARGET_PREVIEW`` /
    # ``REASON_IMPORT_BLOCKED_CONTROL_RIG``, and the same codes
    # ``vmd_converter._IMPORT_LOCK_REASON_CODE_BY_BLOCKED`` attaches to a
    # refused VMD import's ``MMDImportException.reason_code``). Reused here
    # via the same ``reason_text`` lookup as the action reasonCodes above.
    "import_blocked_target_preview": "humanik_reason_import_blocked_target_preview",
    "import_blocked_control_rig": "humanik_reason_import_blocked_control_rig",
}

MODE_TRANSLATION_KEYS = {
    "neutral": "humanik_mode_neutral",
    "source": "humanik_mode_source",
    "target_preview": "humanik_mode_target_preview",
    "control_rig": "humanik_mode_control_rig",
}


class HumanIkTab(BaseTab):
    """Experimental HumanIK workflow status + staged action buttons."""

    _TRANSLATION_REGISTRY = (
        ("humanik_status_group", "setTitle", "humanik_status", "groups"),
        ("humanik_actions_group", "setTitle", "humanik_actions", "groups"),
        ("mode_label_title", "setText", "humanik_mode", "labels"),
        ("source_label_title", "setText", "humanik_source", "labels"),
        ("target_label_title", "setText", "humanik_target", "labels"),
        ("control_rigs_label_title", "setText", "humanik_control_rigs", "labels"),
        ("bake_start_label", "setText", "humanik_bake_start", "labels"),
        ("bake_end_label", "setText", "humanik_bake_end", "labels"),
        ("refresh_btn", "setText", "refresh", "buttons"),
        ("experimental_notice_label", "setText", "humanik_experimental_notice", "messages"),
        ("restore_explanation_label", "setText", "humanik_restore_explanation", "messages"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HumanIkTab")
        self._action_buttons = {}
        self._reason_labels = {}

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        self.experimental_notice_label = QLabel(self.tr("humanik_experimental_notice", "messages"))
        self.experimental_notice_label.setWordWrap(True)
        self.experimental_notice_label.setStyleSheet("font-weight: bold;")
        main_layout.addWidget(self.experimental_notice_label)

        main_layout.addWidget(self._create_status_section())
        main_layout.addWidget(self._create_import_lock_warning_section())
        main_layout.addWidget(self._create_orphaned_warning_section())
        main_layout.addWidget(self._create_control_rig_watch_warning_section())
        main_layout.addWidget(self._create_actions_section())

        self.restore_explanation_label = QLabel(self.tr("humanik_restore_explanation", "messages"))
        self.restore_explanation_label.setWordWrap(True)
        main_layout.addWidget(self.restore_explanation_label)

        main_layout.addStretch()

    # -- construction --------------------------------------------------

    def _create_status_section(self):
        self.humanik_status_group = QGroupBox(self.tr("humanik_status", "groups"))
        form = QFormLayout()

        self.mode_label_title = QLabel(self.tr("humanik_mode", "labels"))
        self.mode_value_label = QLabel(self._mode_text("neutral"))
        form.addRow(self.mode_label_title, self.mode_value_label)

        self.source_label_title = QLabel(self.tr("humanik_source", "labels"))
        self.source_value_label = QLabel(self.tr("humanik_none", "labels"))
        form.addRow(self.source_label_title, self.source_value_label)

        self.target_label_title = QLabel(self.tr("humanik_target", "labels"))
        self.target_value_label = QLabel(self.tr("humanik_none", "labels"))
        form.addRow(self.target_label_title, self.target_value_label)

        self.control_rigs_label_title = QLabel(self.tr("humanik_control_rigs", "labels"))
        self.control_rigs_value_label = QLabel(self.tr("humanik_none", "labels"))
        self.control_rigs_value_label.setWordWrap(True)
        form.addRow(self.control_rigs_label_title, self.control_rigs_value_label)

        self.humanik_status_group.setLayout(form)
        return self.humanik_status_group

    def _create_import_lock_warning_section(self):
        # Single-line state-header warning for ``describe_frontend_state()``'s
        # ``importLock`` section (HUMANIK-FRONTEND-1 Phase C): shown whenever
        # scene facts say a VMD import onto the displayed model would be
        # refused by ``vmd_converter._enforce_humanik_import_gate`` right now
        # -- TARGET preview or an active Control Rig. Hidden otherwise, same
        # show/hide pattern as ``orphaned_warning_label`` below.
        self.import_lock_warning_label = QLabel("")
        self.import_lock_warning_label.setWordWrap(True)
        self.import_lock_warning_label.setStyleSheet("color: #b00020; font-weight: bold;")
        self.import_lock_warning_label.hide()
        return self.import_lock_warning_label

    def _create_orphaned_warning_section(self):
        self.orphaned_warning_label = QLabel("")
        self.orphaned_warning_label.setWordWrap(True)
        self.orphaned_warning_label.setStyleSheet("color: #c04b00; font-weight: bold;")
        self.orphaned_warning_label.hide()
        return self.orphaned_warning_label

    def _create_control_rig_watch_warning_section(self):
        # Adjacent to (same styling family as) ``orphaned_warning_label``:
        # this one instead reflects a *live* event from
        # ``humanik_control_rig_watch`` (a Control Rig just created through
        # Maya's own HumanIK UI rather than the mmd_tools menu), pushed via
        # ``show_control_rig_warning`` -- see ``HumanIkPresenter``, which
        # subscribes to the watch module's pluggable callback while this tab
        # is active and unsubscribes while it is not (HUMANIK-FRONTEND-1
        # Phase C). Hidden until the first such event; ``set_state`` never
        # touches this label, since ``describe_frontend_state()`` has no scene
        # scan for this -- it is event-driven, not state-driven.
        self.control_rig_watch_warning_label = QLabel("")
        self.control_rig_watch_warning_label.setWordWrap(True)
        self.control_rig_watch_warning_label.setStyleSheet("color: #c04b00; font-weight: bold;")
        self.control_rig_watch_warning_label.hide()
        return self.control_rig_watch_warning_label

    def show_control_rig_warning(self, *, character=None, model_root=None):
        """Display the out-of-band Control Rig warning banner.

        Called by ``HumanIkPresenter`` when ``humanik_control_rig_watch``
        reports (via its pluggable warning callback) that a Control Rig was
        just created through Maya's own HumanIK UI for a characterized
        mmd_tools model, instead of through the MMD menu. This never mutates
        the scene -- it only surfaces the same fact the watch module already
        logs, in the tab UI.
        """
        label = str(character or model_root or "")
        suffix = f" ({label})" if label else ""
        self.control_rig_watch_warning_label.setText(
            self.tr("humanik_control_rig_watch_warning", "messages") + suffix
        )
        self.control_rig_watch_warning_label.show()

    def _create_actions_section(self):
        self.humanik_actions_group = QGroupBox(self.tr("humanik_actions", "groups"))
        layout = QVBoxLayout()

        toolbar_layout = QHBoxLayout()
        self.refresh_btn = QPushButton(self.tr("refresh", "buttons"))
        toolbar_layout.addWidget(self.refresh_btn)
        toolbar_layout.addStretch()
        layout.addLayout(toolbar_layout)

        for attr, label_key in _ACTION_BUTTON_SPECS[:3]:
            self._add_action_row(layout, attr, label_key)

        bake_row = QHBoxLayout()
        self.bake_start_label = QLabel(self.tr("humanik_bake_start", "labels"))
        self.bake_start_spin = QSpinBox()
        self.bake_start_spin.setRange(-1_000_000, 1_000_000)
        self.bake_end_label = QLabel(self.tr("humanik_bake_end", "labels"))
        self.bake_end_spin = QSpinBox()
        self.bake_end_spin.setRange(-1_000_000, 1_000_000)
        bake_row.addWidget(self.bake_start_label)
        bake_row.addWidget(self.bake_start_spin)
        bake_row.addWidget(self.bake_end_label)
        bake_row.addWidget(self.bake_end_spin)
        layout.addLayout(bake_row)
        self._add_action_row(layout, "bake_btn", "humanik_bake")

        for attr, label_key in _ACTION_BUTTON_SPECS[4:]:
            self._add_action_row(layout, attr, label_key)

        self.humanik_actions_group.setLayout(layout)
        return self.humanik_actions_group

    def _add_action_row(self, layout, attr, label_key):
        row = QHBoxLayout()
        button = QPushButton(self.tr(label_key, "buttons"))
        reason_label = QLabel("")
        reason_label.setWordWrap(True)
        reason_label.setStyleSheet("color: #a05a00;")
        row.addWidget(button)
        row.addWidget(reason_label, 1)
        layout.addLayout(row)
        setattr(self, attr, button)
        self._action_buttons[attr] = button
        self._reason_labels[attr] = reason_label

    # -- state rendering -------------------------------------------------

    def _mode_text(self, mode):
        key = MODE_TRANSLATION_KEYS.get(str(mode), "humanik_mode_neutral")
        return self.tr(key, "messages")

    def reason_text(self, reason_code):
        """Translate a ``describe_frontend_state`` reasonCode for display."""
        if not reason_code:
            return ""
        key = REASON_CODE_TRANSLATION_KEYS.get(str(reason_code))
        if key is None:
            return str(reason_code)
        return self.tr(key, "messages")

    def set_state(self, state):
        """Render a ``describe_frontend_state()`` snapshot (or ``{}``) onto the tab."""
        state = state or {}
        self.mode_value_label.setText(self._mode_text(state.get("mode", "neutral")))

        source = state.get("source")
        self.source_value_label.setText(
            self._format_binding(source) if source else self.tr("humanik_none", "labels")
        )

        target = state.get("target")
        self.target_value_label.setText(
            self._format_binding(target) if target else self.tr("humanik_none", "labels")
        )

        control_rigs = state.get("controlRigs") or []
        if control_rigs:
            self.control_rigs_value_label.setText(
                "\n".join(self._format_binding(row) for row in control_rigs)
            )
        else:
            self.control_rigs_value_label.setText(self.tr("humanik_none", "labels"))

        import_lock = state.get("importLock") or {}
        if import_lock.get("blocked"):
            reason_text = self.reason_text(import_lock.get("reasonCode"))
            self.import_lock_warning_label.setText(
                self.tr("humanik_import_lock_blocked", "messages").format(reason=reason_text)
            )
            self.import_lock_warning_label.show()
        else:
            self.import_lock_warning_label.hide()

        restore_hint = state.get("restoreHint") or {}
        orphaned = restore_hint.get("orphanedControlRigs") or []
        if orphaned:
            names = ", ".join(
                str(row.get("modelRoot") or row.get("controlSetNode") or row.get("character") or "?")
                for row in orphaned
            )
            self.orphaned_warning_label.setText(
                self.tr("humanik_orphaned_warning", "messages") + f" ({names})"
            )
            self.orphaned_warning_label.show()
        else:
            self.orphaned_warning_label.hide()

        actions = state.get("actions") or {}
        for action_key, attr in ACTION_KEY_TO_BUTTON.items():
            button = self._action_buttons.get(attr)
            reason_label = self._reason_labels.get(attr)
            if button is None:
                continue
            action_state = actions.get(action_key) or {}
            allowed = bool(action_state.get("allowed", True))
            button.setEnabled(allowed)
            reason_text = self.reason_text(action_state.get("reasonCode")) if not allowed else ""
            if reason_label is not None:
                reason_label.setText(reason_text)
                reason_label.setVisible(bool(reason_text))
            button.setToolTip(reason_text)

    @staticmethod
    def _format_binding(binding):
        model_root = binding.get("modelRoot") or ""
        character = binding.get("character") or ""
        name = model_root.rsplit("|", 1)[-1] if model_root else ""
        return f"{name} [{character}]" if character else (name or "-")

    def bake_frame_range(self):
        """Return the ``(start, end)`` SpinBox values shown for Bake to MMD Rig."""
        return self.bake_start_spin.value(), self.bake_end_spin.value()

    def set_bake_frame_range(self, start, end):
        for spin, value in ((self.bake_start_spin, start), (self.bake_end_spin, end)):
            previous = spin.blockSignals(True)
            try:
                spin.setValue(int(value))
            finally:
                spin.blockSignals(previous)

    def retranslateUi(self):
        """Re-apply translation registry and re-render the last known dynamic text."""
        apply_translation_registry(self, self._TRANSLATION_REGISTRY)
        for attr, label_key in _ACTION_BUTTON_SPECS:
            button = self._action_buttons.get(attr)
            if button is not None:
                button.setText(self.tr(label_key, "buttons"))
