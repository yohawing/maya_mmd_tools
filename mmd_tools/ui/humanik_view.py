"""HumanIK editor view.

Pair-specified retarget layout (HUMANIK-FRONTEND-1 Phase B4), modeled after
Maya's own HumanIK "Character Controls" panel: a "Character" combo (the MMD
model this window currently acts on) and a "Source" combo ("None" plus every
other scene MMD model -- picking a model there *is* the retarget-connect
trigger, picking "None" disconnects), followed by a compact one-line status
label and a flat, always-visible column of action buttons.

Phase B5 (user feedback) simplified the layout further: the Refresh button
moved to the top, the four-row Mode/
Source/Target/Control Rigs status table collapsed into a single status
label, and the action sections were flattened into a compact stack. The Bake
controls now retain one explicit collapsible section so they can be hidden
when they are not in use.

"Enter Source Mode"/"Enter Target Mode" remain implicit in the Source combo,
but Setup / Characterize is an explicit button: only already-characterized
models appear in either combo, and a scene with none shows Character as
"(none)" until the user selects an MMD model and runs Setup. The backend
remains authoritative for every action guard. Setup, Create Control Rig, and
Restore reflect the backend's read-only action preflight; Create Control Rig
keeps its tooltip (including a denial reason when available) even while
disabled. Mutating calls still report failures to Maya's Script Editor.
"""

from .combo_box_utils import configure_model_combo_width
from .qt_compat import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

try:  # ``qt_compat`` intentionally exposes only the shared widget surface.
    from PySide6.QtWidgets import QRadioButton
except ImportError:  # pragma: no cover - exercised only on Maya/PySide2.
    from PySide2.QtWidgets import QRadioButton
from .base_tab import BaseTab
from .components.symbol_tool_button import MaterialSymbolToolButton
from .tabs.translation_registry import apply_translation_registry


# Action button attribute -> translation key for each standalone action.
# Source/Target mode remain combo-driven; Setup is explicit so the empty
# characterized-model state still has a clear entry point.
_ACTION_BUTTON_SPECS = (
    ("setup_characterize_btn", "humanik_setup_selected_model"),
    ("create_control_rig_btn", "humanik_create_control_rig"),
    ("bake_btn", "humanik_bake_execute"),
    ("restore_btn", "humanik_restore"),
)

_FRONTEND_ACTION_TO_BUTTON = {
    "setup_and_characterize": "setup_characterize_btn",
    "create_control_rig": "create_control_rig_btn",
    "restore_mmd_rig": "restore_btn",
}

MODE_TRANSLATION_KEYS = {
    "neutral": "humanik_mode_neutral",
    "source": "humanik_mode_source",
    "target_preview": "humanik_mode_target_preview",
    "control_rig": "humanik_mode_control_rig",
}


def _configure_compact_model_combo(combo):
    """Keep long model names elided while letting the field fill its row."""
    configure_model_combo_width(combo, minimum_width=0, minimum_contents_length=12)
    policy = getattr(QComboBox, "AdjustToMinimumContentsLengthWithIcon", None)
    if policy is None and hasattr(QComboBox, "SizeAdjustPolicy"):
        policy = getattr(
            QComboBox.SizeAdjustPolicy,
            "AdjustToMinimumContentsLengthWithIcon",
            None,
        )
    if policy is not None:
        combo.setSizeAdjustPolicy(policy)
    combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


class HumanIkView(BaseTab):
    """Pair-specified HumanIK retarget UI: Character/Source combos + status + actions."""

    _TRANSLATION_REGISTRY = (
        ("character_combo_label", "setText", "humanik_character", "labels"),
        ("source_combo_label", "setText", "humanik_source", "labels"),
        ("source_combo", "setToolTip", "humanik_source_tooltip", "messages"),
        (
            "create_control_rig_btn",
            "setToolTip",
            "humanik_create_control_rig_tooltip",
            "messages",
        ),
        (
            "setup_characterize_btn",
            "setToolTip",
            "humanik_setup_selected_model_tooltip",
            "messages",
        ),
        ("bake_start_label", "setText", "humanik_bake_start", "labels"),
        ("bake_end_label", "setText", "humanik_bake_end", "labels"),
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
        ("restore_btn", "setToolTip", "humanik_restore_tooltip", "messages"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HumanIkView")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._action_buttons = {}
        self._last_mode = "neutral"
        self._last_control_rig_count = 0
        self._last_state = {}
        self._last_action_states = {}

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(7)

        # The window title already carries ``(Experimental)``. Keep this row
        # to the refresh action only so the compact editor does not repeat a
        # second experimental-feature notice inside its content.
        top_row = QHBoxLayout()
        top_row.addStretch()
        self.refresh_btn = MaterialSymbolToolButton("refresh", self.tr("refresh", "buttons"))
        top_row.addWidget(self.refresh_btn)
        main_layout.addLayout(top_row)

        main_layout.addLayout(self._create_model_selection_section())
        main_layout.addWidget(self._create_status_label())

        self._build_actions(main_layout)

        main_layout.addStretch()

    # -- construction --------------------------------------------------

    def _create_model_selection_section(self):
        """Build the Character/Source combo row pair.

        Mirrors Maya's own HumanIK Character Controls panel: "Character" is
        the characterized MMD model this window currently acts on; "Source"
        is "None" plus every other characterized scene MMD model -- selecting a model there is the
        retarget-connect trigger (see ``HumanIkPresenter``), selecting "None"
        disconnects. Character shows "(none)" when no scene MMD model has
        been characterized yet; imported-but-uncharacterized models are
        intentionally absent until explicit Setup succeeds.
        """
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(7)
        form.setVerticalSpacing(6)

        self.character_combo_label = QLabel(self.tr("humanik_character", "labels"))
        self.character_combo = QComboBox()
        _configure_compact_model_combo(self.character_combo)
        form.addRow(self.character_combo_label, self.character_combo)

        self.source_combo_label = QLabel(self.tr("humanik_source", "labels"))
        self.source_combo = QComboBox()
        _configure_compact_model_combo(self.source_combo)
        form.addRow(self.source_combo_label, self.source_combo)

        return form

    def _create_status_label(self):
        """Build the single-line status label (HUMANIK-FRONTEND-1 Phase B5).

        Replaces the previous four-row Mode/Source/Target/Control Rigs
        status display: SOURCE/TARGET are already visible via the Character/
        Source combos above, so the only information this label still needs
        to carry is the current mode plus, when at least one Control Rig
        exists, a compact count suffix (see ``_status_text``).
        """
        self.status_label = QLabel(self._status_text("neutral", 0))
        self.status_label.setWordWrap(True)
        return self.status_label

    def _build_actions(self, main_layout):
        """Lay out primary actions in one row and Bake in its own section.

        Setup, Create Control Rig, and Restore stay visible in one compact row.
        Bake owns the only collapsible section because its frame range, two
        destinations, and Execute action are useful as one hideable unit.
        """
        primary_actions = QHBoxLayout()
        primary_actions.setSpacing(4)
        self.primary_actions_layout = primary_actions
        self._add_action_row(
            primary_actions,
            "setup_characterize_btn",
            "humanik_setup_selected_model",
            compact=True,
        )
        self.setup_characterize_btn.setToolTip(
            self.tr("humanik_setup_selected_model_tooltip", "messages")
        )
        self._add_action_row(
            primary_actions,
            "create_control_rig_btn",
            "humanik_create_control_rig",
            compact=True,
        )
        self.create_control_rig_btn.setToolTip(
            self.tr("humanik_create_control_rig_tooltip", "messages")
        )
        self._add_action_row(
            primary_actions,
            "restore_btn",
            "humanik_restore",
            compact=True,
        )
        self.restore_btn.setToolTip(self.tr("humanik_restore_tooltip", "messages"))
        main_layout.addLayout(primary_actions)

        bake_section = QGroupBox()
        bake_section.setObjectName("HumanIkBakeSection")
        self.bake_section = bake_section
        bake_section_layout = QVBoxLayout(bake_section)
        bake_section_layout.setContentsMargins(8, 6, 8, 8)
        bake_section_layout.setSpacing(6)
        self.bake_toggle_btn = QPushButton()
        self.bake_toggle_btn.setCheckable(True)
        self.bake_toggle_btn.setChecked(True)
        self.bake_toggle_btn.setFlat(True)
        self.bake_toggle_btn.setStyleSheet("text-align: left; font-weight: bold;")
        bake_section_layout.addWidget(self.bake_toggle_btn)

        self.bake_content = QWidget()
        bake_content_layout = QVBoxLayout(self.bake_content)
        bake_content_layout.setContentsMargins(4, 2, 4, 4)
        bake_content_layout.setSpacing(6)
        bake_section_layout.addWidget(self.bake_content)

        bake_row = QHBoxLayout()
        self.bake_start_label = QLabel(self.tr("humanik_bake_start", "labels"))
        self.bake_start_spin = QSpinBox()
        self.bake_start_spin.setRange(-1_000_000, 1_000_000)
        self.bake_start_spin.setSuffix(" F")
        self.bake_start_spin.setMinimumWidth(40)
        self.bake_start_spin.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.bake_end_label = QLabel(self.tr("humanik_bake_end", "labels"))
        self.bake_end_spin = QSpinBox()
        self.bake_end_spin.setRange(-1_000_000, 1_000_000)
        self.bake_end_spin.setSuffix(" F")
        self.bake_end_spin.setMinimumWidth(40)
        self.bake_end_spin.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        bake_row.addWidget(self.bake_start_label)
        bake_row.addWidget(self.bake_start_spin)
        bake_row.addWidget(self.bake_end_label)
        bake_row.addWidget(self.bake_end_spin)
        bake_content_layout.addLayout(bake_row)

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
        # Maya's default 18px UI font makes the two labels overflow at the
        # editor's compact width. Match the denser native HumanIK option row.
        for radio in (self.bake_to_control_rig_radio, self.bake_to_mmd_rig_radio):
            radio.setStyleSheet("font-size: 13px;")
        self.bake_to_control_rig_radio.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.bake_to_mmd_rig_radio.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        destination_row.addWidget(self.bake_to_control_rig_radio)
        destination_row.addWidget(self.bake_to_mmd_rig_radio)
        bake_content_layout.addLayout(destination_row)
        self._add_action_row(bake_content_layout, "bake_btn", "humanik_bake_execute")
        # ``bake_btn`` remains the compatibility name used by older callers;
        # the explicit name makes the single Execute action discoverable.
        self.bake_execute_btn = self.bake_btn
        self.bake_toggle_btn.toggled.connect(self._set_bake_expanded)
        self._set_bake_expanded(True)
        main_layout.addWidget(bake_section)

    def _add_action_row(self, layout, attr, label_key, compact=False):
        """Add an action button, optionally ignoring its text size hint."""
        button = QPushButton(self.tr(label_key, "buttons"))
        # Qt normally suppresses hover events for disabled widgets.  Keep
        # action explanations discoverable even while a backend preflight
        # disables a button (Qt::WA_AlwaysShowToolTips is available in both
        # PySide2 and PySide6).
        always_show_tooltips = getattr(Qt, "WA_AlwaysShowToolTips", None)
        if always_show_tooltips is not None:
            button.setAttribute(always_show_tooltips, True)
        horizontal_policy = QSizePolicy.Ignored if compact else QSizePolicy.Expanding
        button.setSizePolicy(horizontal_policy, QSizePolicy.Fixed)
        layout.addWidget(button)
        setattr(self, attr, button)
        self._action_buttons[attr] = button
        if attr in _FRONTEND_ACTION_TO_BUTTON.values():
            # Until the first frontend-state snapshot arrives, fail closed.
            # The presenter will enable the button only after the backend
            # confirms the corresponding action is allowed.
            button.setEnabled(False)

    def _set_bake_expanded(self, expanded):
        """Show or hide Bake controls while keeping the section header visible."""
        self.bake_content.setVisible(bool(expanded))
        arrow = "▼" if expanded else "▶"
        self.bake_toggle_btn.setText(f"{arrow} {self.tr('humanik_bake_section', 'labels')}")

    # -- state rendering -------------------------------------------------

    def _mode_text(self, mode):
        key = MODE_TRANSLATION_KEYS.get(str(mode), "humanik_mode_neutral")
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
        """Render a ``describe_frontend_state()`` snapshot onto the view."""
        state = state or {}
        self._last_state = state
        mode = state.get("mode", "neutral")
        control_rigs = state.get("controlRigs") or []
        self._last_mode = mode
        self._last_control_rig_count = len(control_rigs)
        self.status_label.setText(self._status_text(mode, self._last_control_rig_count))
        action_states = state.get("actions") or {}
        self._last_action_states = action_states

        # Keep action guards in one place: the backend operation. The view
        # only renders the allowed bit from the frontend-state preflight and
        # never duplicates its conditions locally. Bake remains available as
        # a dispatch entry point for compatibility; the backend still owns
        # its validation and failure reporting.
        for action_name, button_attr in _FRONTEND_ACTION_TO_BUTTON.items():
            button = self._action_buttons.get(button_attr)
            if button is None:
                continue
            action_state = action_states.get(action_name)
            allowed = bool(action_state and action_state.get("allowed"))
            button.setEnabled(allowed)

            if action_name == "create_control_rig":
                button.setToolTip(self._create_control_rig_tooltip(action_state))

    def _create_control_rig_tooltip(self, action_state):
        """Return the Create Control Rig tooltip, including denial details."""
        base = self.tr("humanik_create_control_rig_tooltip", "messages")
        if not isinstance(action_state, dict) or action_state.get("allowed"):
            return base
        reason_text = str(action_state.get("reasonText") or "").strip()
        reason_code = str(action_state.get("reasonCode") or "").strip()
        details = []
        if reason_text:
            details.append(reason_text)
        if reason_code:
            details.append(f"[{reason_code}]")
        if not details:
            return base
        return f"{base}\n\n{' '.join(details)}"

    def _selected_bake_destination(self):
        """Return the stable destination identifier selected in the Bake UI."""
        radio = getattr(self, "bake_to_control_rig_radio", None)
        if radio is not None and radio.isChecked():
            return "control_rig"
        return "mmd_rig"

    def bake_destination(self):
        """Return the selected bake destination for presenter dispatch."""
        return self._selected_bake_destination()

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
        self.setup_characterize_btn.setToolTip(
            self.tr("humanik_setup_selected_model_tooltip", "messages")
        )
        self.restore_btn.setToolTip(self.tr("humanik_restore_tooltip", "messages"))
        create_button = self._action_buttons.get("create_control_rig_btn")
        if create_button is not None:
            create_button.setToolTip(
                self._create_control_rig_tooltip(
                    self._last_action_states.get("create_control_rig")
                )
            )
        self._set_bake_expanded(self.bake_toggle_btn.isChecked())
        self.status_label.setText(self._status_text(self._last_mode, self._last_control_rig_count))
