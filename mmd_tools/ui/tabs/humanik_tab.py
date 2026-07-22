"""HumanIK tab UI shell.

Pair-specified retarget layout (HUMANIK-FRONTEND-1 Phase B4), modeled after
Maya's own HumanIK "Character Controls" panel: a "Character" combo (the MMD
model this window currently acts on) and a "Source" combo ("None" plus every
other scene MMD model -- picking a model there *is* the retarget-connect
trigger, picking "None" disconnects), followed by a compact one-line status
label and a flat, always-visible column of action buttons.

Phase B5 (user feedback) simplified the layout further: the Refresh button
moved to the top (next to the Experimental notice), the four-row Mode/
Source/Target/Control Rigs status table collapsed into a single status
label, and the three collapsible ``QGroupBox`` action sections were flattened
into a plain vertical stack of buttons -- there is nothing left to expand or
collapse.

The single-model "Enter Source Mode"/"Enter Target Mode"/"Setup / Characterize"
buttons from the previous layout are gone from this View entirely -- the two
combos now drive that lifecycle (see ``HumanIkPresenter``). The plugin menu's
seven standalone actions are unchanged and still call the same
``mmd_tools.ui.humanik_menu_actions`` functions this tab dispatches to; this
tab's own responsibility is still limited to visualizing
``describe_frontend_state()`` and showing why a button is disabled.
"""

from ..combo_box_utils import configure_model_combo_width
from ..qt_compat import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)

try:  # ``qt_compat`` intentionally exposes only the shared widget surface.
    from PySide6.QtWidgets import QRadioButton
except ImportError:  # pragma: no cover - exercised only on Maya/PySide2.
    from PySide2.QtWidgets import QRadioButton
from ..base_tab import BaseTab
from .translation_registry import apply_translation_registry


# Action button attribute -> (translation key) for the button text. Only the
# four actions that still have a standalone button on this tab (Setup /
# Characterize, Enter Source Mode, and Enter Target Mode moved to the
# Character/Source combos, see the module docstring).
_ACTION_BUTTON_SPECS = (
    ("create_control_rig_btn", "humanik_create_control_rig"),
    ("bake_btn", "humanik_bake_execute"),
    ("restore_btn", "humanik_restore"),
    ("diagnostics_btn", "humanik_diagnostics"),
)

# Maps ``describe_frontend_state()`` action keys to the button attribute that
# represents them on this tab. ``setup_and_characterize``/``enter_source_mode``/
# ``enter_target_mode`` have no button anymore -- ``set_state`` simply skips
# any action key with no entry here.
ACTION_KEY_TO_BUTTON = {
    "create_control_rig": "create_control_rig_btn",
    "bake_to_mmd_rig": "bake_btn",
    "restore_mmd_rig": "restore_btn",
    "diagnostics": "diagnostics_btn",
}

BAKE_DESTINATION_ACTIONS = {
    "mmd_rig": "bake_to_mmd_rig",
    "control_rig": "bake_to_control_rig",
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
    "no_active_control_rig": "humanik_reason_no_active_control_rig",
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
    """Pair-specified HumanIK retarget UI: Character/Source combos + status + actions."""

    _TRANSLATION_REGISTRY = (
        ("character_combo_label", "setText", "humanik_character", "labels"),
        ("source_combo_label", "setText", "humanik_source", "labels"),
        ("source_combo", "setToolTip", "humanik_source_tooltip", "messages"),
        ("bake_start_label", "setText", "humanik_bake_start", "labels"),
        ("bake_end_label", "setText", "humanik_bake_end", "labels"),
        ("bake_section_label", "setText", "humanik_bake_section", "labels"),
        (
            "bake_to_control_rig_radio",
            "setText",
            "humanik_bake_to_control_rig",
            "labels",
        ),
        (
            "bake_to_mmd_rig_radio",
            "setText",
            "humanik_bake_to_mmd_rig",
            "labels",
        ),
        ("refresh_btn", "setText", "refresh", "buttons"),
        ("experimental_notice_label", "setText", "humanik_experimental_notice", "messages"),
        ("restore_explanation_label", "setText", "humanik_restore_explanation", "messages"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HumanIkTab")
        self._action_buttons = {}
        self._reason_labels = {}
        self._last_mode = "neutral"
        self._last_control_rig_count = 0
        self._last_state = {}

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # Top row: Experimental notice (left, stretches) + Refresh button
        # (right-aligned, same row) -- moved up from the bottom of the tab
        # per HUMANIK-FRONTEND-1 Phase B5 user feedback so it is reachable
        # without scrolling past every action button first.
        top_row = QHBoxLayout()
        self.experimental_notice_label = QLabel(self.tr("humanik_experimental_notice", "messages"))
        self.experimental_notice_label.setWordWrap(True)
        self.experimental_notice_label.setStyleSheet("font-weight: bold;")
        top_row.addWidget(self.experimental_notice_label, 1)
        self.refresh_btn = QPushButton(self.tr("refresh", "buttons"))
        top_row.addWidget(self.refresh_btn)
        main_layout.addLayout(top_row)

        main_layout.addLayout(self._create_model_selection_section())
        main_layout.addWidget(self._create_status_label())
        main_layout.addWidget(self._create_import_lock_warning_section())
        main_layout.addWidget(self._create_orphaned_warning_section())
        main_layout.addWidget(self._create_control_rig_watch_warning_section())

        self._build_actions(main_layout)

        main_layout.addStretch()

    # -- construction --------------------------------------------------

    def _create_model_selection_section(self):
        """Build the Character/Source combo row pair.

        Mirrors Maya's own HumanIK Character Controls panel: "Character" is
        the MMD model this window currently acts on; "Source" is "None" plus
        every other scene MMD model -- selecting a model there is the
        retarget-connect trigger (see ``HumanIkPresenter``), selecting "None"
        disconnects. Neither combo carries a leading "(none)" placeholder for
        Character -- see the presenter's sticky/follow/auto-adopt selection
        logic for why one is always resolvable whenever the scene has any MMD
        model.
        """
        form = QFormLayout()

        self.character_combo_label = QLabel(self.tr("humanik_character", "labels"))
        self.character_combo = QComboBox()
        configure_model_combo_width(self.character_combo)
        form.addRow(self.character_combo_label, self.character_combo)

        self.source_combo_label = QLabel(self.tr("humanik_source", "labels"))
        self.source_combo = QComboBox()
        configure_model_combo_width(self.source_combo)
        form.addRow(self.source_combo_label, self.source_combo)

        return form

    def _create_status_label(self):
        """Build the single-line status label (HUMANIK-FRONTEND-1 Phase B5).

        Replaces the previous four-row Mode/Source/Target/Control Rigs
        status table: SOURCE/TARGET are already visible via the Character/
        Source combos above, so the only information this label still needs
        to carry is the current mode plus, when at least one Control Rig
        exists, a compact count suffix (see ``_status_text``).
        """
        self.status_label = QLabel(self._status_text("neutral", 0))
        self.status_label.setWordWrap(True)
        return self.status_label

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

    def _build_actions(self, main_layout):
        """Lay out the action buttons as a flat vertical stack.

        HUMANIK-FRONTEND-1 Phase B5 removed the three collapsible
        ``QGroupBox`` sections (Control Rig / Bake / Restore-Diagnostics) --
        there was nothing to actually collapse in practice, so the buttons
        are added directly to ``main_layout`` instead: Create Control Rig,
        then the Bake destination/range section followed by one Execute Bake
        button, then
        Restore MMD Rig plus its explanation text, then Diagnostics.
        """
        self._add_action_row(main_layout, "create_control_rig_btn", "humanik_create_control_rig")

        bake_section = QGroupBox()
        bake_section.setObjectName("HumanIkBakeSection")
        self.bake_section = bake_section
        bake_section_layout = QVBoxLayout(bake_section)
        self.bake_section_label = QLabel(self.tr("humanik_bake_section", "labels"))
        self.bake_section_label.setStyleSheet("font-weight: bold;")
        bake_section_layout.addWidget(self.bake_section_label)

        bake_row = QHBoxLayout()
        self.bake_start_label = QLabel(self.tr("humanik_bake_start", "labels"))
        self.bake_start_spin = QSpinBox()
        self.bake_start_spin.setRange(-1_000_000, 1_000_000)
        self.bake_start_spin.setSuffix(" F")
        self.bake_end_label = QLabel(self.tr("humanik_bake_end", "labels"))
        self.bake_end_spin = QSpinBox()
        self.bake_end_spin.setRange(-1_000_000, 1_000_000)
        self.bake_end_spin.setSuffix(" F")
        bake_row.addWidget(self.bake_start_label)
        bake_row.addWidget(self.bake_start_spin)
        bake_row.addWidget(self.bake_end_label)
        bake_row.addWidget(self.bake_end_spin)
        bake_section_layout.addLayout(bake_row)

        destination_row = QHBoxLayout()
        self.bake_to_control_rig_radio = QRadioButton(
            self.tr("humanik_bake_to_control_rig", "labels")
        )
        self.bake_to_control_rig_radio.setObjectName("BakeToControlRig")
        self.bake_to_mmd_rig_radio = QRadioButton(
            self.tr("humanik_bake_to_mmd_rig", "labels")
        )
        self.bake_to_mmd_rig_radio.setObjectName("BakeToMmdRig")
        # QRadioButtons sharing a parent are mutually exclusive.  Keep MMD
        # as the safe/default destination, matching the existing Bake action.
        self.bake_to_mmd_rig_radio.setChecked(True)
        self.bake_to_control_rig_radio.toggled.connect(self._on_bake_destination_toggled)
        self.bake_to_mmd_rig_radio.toggled.connect(self._on_bake_destination_toggled)
        destination_row.addWidget(self.bake_to_control_rig_radio)
        destination_row.addWidget(self.bake_to_mmd_rig_radio)
        destination_row.addStretch()
        bake_section_layout.addLayout(destination_row)
        self._add_action_row(bake_section_layout, "bake_btn", "humanik_bake_execute")
        # ``bake_btn`` remains the compatibility name used by older callers;
        # the explicit name makes the single Execute action discoverable.
        self.bake_execute_btn = self.bake_btn
        main_layout.addWidget(bake_section)

        self._add_action_row(main_layout, "restore_btn", "humanik_restore")
        self.restore_explanation_label = QLabel(self.tr("humanik_restore_explanation", "messages"))
        self.restore_explanation_label.setWordWrap(True)
        self.restore_explanation_label.setStyleSheet("color: #808080; font-size: 90%;")
        main_layout.addWidget(self.restore_explanation_label)

        self._add_action_row(main_layout, "diagnostics_btn", "humanik_diagnostics")

    def _add_action_row(self, layout, attr, label_key):
        """Add a full-width action button plus its (initially hidden) reason label.

        The reason label sits on its own row below the button -- rather than
        beside it -- so the button itself can stretch to the tab's full
        width (per HUMANIK-FRONTEND-1 Phase B5's flat layout), while the
        disabled-button explanation still has room to wrap.
        """
        button = QPushButton(self.tr(label_key, "buttons"))
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(button)
        reason_label = QLabel("")
        reason_label.setWordWrap(True)
        reason_label.setStyleSheet("color: #a05a00;")
        reason_label.hide()
        layout.addWidget(reason_label)
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

    def _status_text(self, mode, control_rig_count):
        """Compose the single-line status label's text.

        HUMANIK-FRONTEND-1 Phase B5: SOURCE/TARGET are already visible via
        the Character/Source combos, so the status line only carries the
        mode plus, when at least one Control Rig exists, a compact
        "/ Control Rig: N" suffix.
        """
        text = self._mode_text(mode)
        if control_rig_count:
            text += self.tr("humanik_status_control_rig_suffix", "messages").format(
                count=control_rig_count
            )
        return text

    def set_state(self, state):
        """Render a ``describe_frontend_state()`` snapshot (or ``{}``) onto the tab."""
        state = state or {}
        self._last_state = state
        mode = state.get("mode", "neutral")
        control_rigs = state.get("controlRigs") or []
        self._last_mode = mode
        self._last_control_rig_count = len(control_rigs)
        self.status_label.setText(self._status_text(mode, self._last_control_rig_count))

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
            # Bake destinations share one Execute button.  Render that button
            # once below from whichever destination is currently selected.
            if action_key in BAKE_DESTINATION_ACTIONS.values():
                continue
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
        self._apply_bake_action_state(actions)

    def _selected_bake_destination(self):
        """Return the stable destination identifier selected in the Bake UI."""
        radio = getattr(self, "bake_to_control_rig_radio", None)
        if radio is not None and radio.isChecked():
            return "control_rig"
        return "mmd_rig"

    def bake_destination(self):
        """Return the selected bake destination for presenter dispatch."""
        return self._selected_bake_destination()

    def _on_bake_destination_toggled(self, checked):
        # ``toggled(False)`` is emitted for the destination being deselected;
        # only the newly selected route needs to update the Execute gate.
        if checked:
            self._apply_bake_action_state((self._last_state or {}).get("actions") or {})

    def _apply_bake_action_state(self, actions):
        """Gate Execute Bake and show the selected route's reason text."""
        action_key = BAKE_DESTINATION_ACTIONS[self._selected_bake_destination()]
        action_state = actions.get(action_key) or {}
        allowed = bool(action_state.get("allowed", True))
        button = getattr(self, "bake_execute_btn", None) or self._action_buttons.get("bake_btn")
        if button is None:
            return
        reason_label = self._reason_labels.get("bake_btn")
        button.setEnabled(allowed)
        reason_text = ""
        if not allowed:
            reason_text = self.reason_text(action_state.get("reasonCode"))
            if not reason_text or reason_text == str(action_state.get("reasonCode")):
                reason_text = str(action_state.get("reasonText") or reason_text)
        if reason_label is not None:
            reason_label.setText(reason_text)
            reason_label.setVisible(bool(reason_text))
        button.setToolTip(reason_text)

    # -- Character/Source combo rendering --------------------------------

    def set_character_options(self, options, selected_value):
        """Rebuild the Character combo and select ``selected_value``.

        ``options`` is a sequence of ``(label, model_root)`` pairs. Rebuilding
        happens with signals blocked so a presenter-driven refresh never
        re-triggers the combo's own change handler.
        """
        self._populate_combo(self.character_combo, options, selected_value)

    def set_source_options(self, options, selected_value):
        """Rebuild the Source combo and select ``selected_value`` (``None`` for "None").

        Same signal-blocking contract as ``set_character_options``. The
        selected value is always driven by backend truth (the session's
        actual SOURCE binding), never by what the user last clicked -- see
        ``HumanIkPresenter.refresh``.
        """
        self._populate_combo(self.source_combo, options, selected_value)

    @staticmethod
    def _populate_combo(combo, options, selected_value):
        previous = combo.blockSignals(True)
        try:
            combo.clear()
            selected_index = 0
            for index, (label, value) in enumerate(options):
                combo.addItem(label, value)
                if value == selected_value:
                    selected_index = index
            if combo.count():
                combo.setCurrentIndex(selected_index)
        finally:
            combo.blockSignals(previous)

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
        self.status_label.setText(self._status_text(self._last_mode, self._last_control_rig_count))
