"""Animator Toolset tab with picker tools and the MMD Control Rig manager."""

from ..qt_compat import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
    Qt,
)
from ..base_tab import BaseTab
from ..widgets.body_picker_widget import BodyPickerWidget
from ..widgets.finger_picker_widget import FingerPickerWidget
from ..components.symbol_tool_button import SymbolToolButton
from ...services.settings_service import SettingsService


class AnimationTab(BaseTab):
    """Anim Picker tab with Body / Finger / Morph / Display sub-tabs."""

    TAB_BODY = 0
    TAB_FINGER = 1
    TAB_MORPH = 2
    TAB_DISPLAY = 3
    TAB_OTHER = TAB_DISPLAY  # Compatibility for existing presenter tests/extensions.

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AnimationTab")
        self.setMinimumWidth(150)

        main_layout = QVBoxLayout(self)
        main_layout.setSizeConstraint(QVBoxLayout.SetNoConstraint)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # --- Model selector ---
        selector_layout = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        selector_layout.addWidget(self.model_combo, 1)
        self.refresh_btn = SymbolToolButton("refresh", "Refresh")
        selector_layout.addWidget(self.refresh_btn)
        main_layout.addLayout(selector_layout)

        visibility_layout = QHBoxLayout()
        visibility_layout.setContentsMargins(0, 0, 0, 0)
        self.visibility_label = QLabel("Visibility:")
        visibility_layout.addWidget(self.visibility_label)
        self.vis_checkboxes = {}
        for key, symbol in (
            ("mesh", "view_in_ar"),
            ("joints", "bone"),
            ("colliders", "capsule"),
            ("control_rig", "controlrig"),
        ):
            button = SymbolToolButton(symbol, key, tri_state=True)
            button.setVisibilityState("visible")
            if key == "control_rig":
                button._control_rig_available = False
                button.setVisibilityAvailable(False)
            visibility_layout.addWidget(button)
            self.vis_checkboxes[key] = button
        visibility_layout.addStretch(1)
        main_layout.addLayout(visibility_layout)

        # --- Picker sub-tabs ---
        self.picker_tabs = QTabWidget()
        self.picker_tabs.setObjectName("PickerTabs")

        self.body_page = QWidget()
        body_layout = QVBoxLayout(self.body_page)
        body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_picker = BodyPickerWidget()
        body_layout.addWidget(self.body_picker, 0, Qt.AlignTop)
        body_layout.addStretch(1)
        self.picker_tabs.addTab(self.body_page, "Body")

        self.finger_page = QWidget()
        finger_layout = QVBoxLayout(self.finger_page)
        finger_layout.setContentsMargins(0, 0, 0, 0)
        self.finger_picker = FingerPickerWidget()
        finger_layout.addWidget(self.finger_picker, 0, Qt.AlignTop)
        finger_layout.addStretch(1)
        self.picker_tabs.addTab(self.finger_page, "Finger")

        self.morph_page = QWidget()
        morph_outer = QVBoxLayout(self.morph_page)
        morph_outer.setContentsMargins(0, 0, 0, 0)
        morph_scroll = QScrollArea()
        morph_scroll.setObjectName("MorphPickerScroll")
        morph_scroll.setWidgetResizable(True)
        morph_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        morph_scroll.setStyleSheet(
            "QScrollArea#MorphPickerScroll { border: none; background: #303030; }"
        )
        self.morph_scroll_content = QWidget()
        self.morph_groups_layout = QVBoxLayout(self.morph_scroll_content)
        self.morph_groups_layout.setContentsMargins(4, 4, 4, 4)
        self.morph_groups_layout.setSpacing(2)
        self.morph_groups_layout.addStretch()
        morph_scroll.setWidget(self.morph_scroll_content)
        morph_outer.addWidget(morph_scroll)
        self.picker_tabs.addTab(self.morph_page, "Morph")

        self.other_page = QWidget()
        other_layout = QVBoxLayout(self.other_page)
        self.display_frame_tree = QTreeWidget()
        self.display_frame_tree.setHeaderHidden(True)
        other_layout.addWidget(self.display_frame_tree)
        self.picker_tabs.addTab(self.other_page, "Display")

        main_layout.addWidget(self.picker_tabs, 1)

        # --- Common Body/Finger actions ---------------------------------
        # Keep one action-bar instance outside both picker pages.  The
        # presenter connects these buttons once, so switching Body/Finger
        # cannot duplicate callbacks or transaction state.
        self.common_action_bar = QWidget(self)
        self.common_action_bar.setObjectName("CommonPickerActionBar")
        common_layout = QHBoxLayout(self.common_action_bar)
        common_layout.setContentsMargins(0, 2, 0, 2)
        common_layout.setSpacing(4)
        self.common_action_buttons: dict[str, QPushButton] = {}
        for key, symbol in (
            ("reset", "restart_alt"),
            ("mirror", "flip"),
        ):
            button = SymbolToolButton(symbol, key)
            button.setObjectName(f"CommonPickerAction_{key}")
            common_layout.addWidget(button)
            self.common_action_buttons[key] = button
        common_layout.addStretch(1)
        main_layout.addWidget(self.common_action_bar)

        # --- Status bar ---
        status_layout = QHBoxLayout()
        self.status_label = QLabel("")
        status_layout.addWidget(self.status_label, 1)
        self.control_rig_manager_btn = QPushButton()
        self.control_rig_manager_btn.setObjectName("ControlRigManagerButton")
        status_layout.addWidget(self.control_rig_manager_btn)
        # Kept as hidden compatibility endpoints; the visible actions live in
        # the two blue Body picker buttons.
        self.select_all_btn = QPushButton("Select All", self)
        self.select_all_btn.hide()
        self.clear_btn = QPushButton("Clear", self)
        self.clear_btn.hide()
        main_layout.addLayout(status_layout)

        # --- Tools section ---
        self.tools_group = QGroupBox("Tools")
        tools_layout = QGridLayout()
        tools_layout.setContentsMargins(4, 4, 4, 4)
        tools_layout.setSpacing(4)

        self.tool_buttons: dict[str, QPushButton] = {}
        for i, (key, label, symbol) in enumerate(
            [
                ("copy", "Copy", "content_copy"),
                ("paste", "Paste", "content_paste"),
                ("clean", "Clean", "cleaning_services"),
                ("bake", "Bake", "animation"),
            ]
        ):
            btn = SymbolToolButton(symbol, label)
            row, col = divmod(i, 6)
            tools_layout.addWidget(btn, row, col)
            self.tool_buttons[key] = btn

        self.tools_group.setLayout(tools_layout)
        self.picker_tabs.currentChanged.connect(self._update_tools_placement)
        self._update_tools_placement(self.picker_tabs.currentIndex())
        self.retranslateUi()

    def _update_tools_placement(self, tab_index: int) -> None:
        """Keep Tools inside Body/Finger pages and absent from data editors."""

        target_page = {
            self.TAB_BODY: self.body_page,
            self.TAB_FINGER: self.finger_page,
        }.get(tab_index)
        if target_page is not None:
            for page in (self.body_page, self.finger_page):
                page.layout().removeWidget(self.tools_group)
            self.tools_group.setParent(target_page)
            target_layout = target_page.layout()
            target_layout.insertWidget(max(0, target_layout.count() - 1), self.tools_group)
        self.refresh_development_mode_visibility()

    def refresh_development_mode_visibility(self):
        """Refresh visibility for legacy tools and the public rig controls.

        Pose authoring helpers are still Development Mode-only.  The MMD
        Control Rig itself graduated to the normal Animator Toolset in 0.6.1,
        so it must not disappear when Development Mode is disabled.
        """

        development_mode = SettingsService().is_development_mode()
        picker_tab = self.picker_tabs.currentIndex() in (self.TAB_BODY, self.TAB_FINGER)
        self.tools_group.setVisible(picker_tab and development_mode)
        common_action_bar = getattr(self, "common_action_bar", None)
        if common_action_bar is not None:
            common_action_bar.setVisible(picker_tab)
        # Third-party/headless views from pre-manager releases may still
        # expose ``control_rig_group``.  Keep their visibility contract
        # harmlessly alive while the production view uses the footer launcher.
        legacy_group = getattr(self, "control_rig_group", None)
        if legacy_group is not None:
            legacy_group.setVisible(True)
            legacy_group.setEnabled(True)
        control_rig_visibility = self.vis_checkboxes.get("control_rig")
        if control_rig_visibility is not None:
            control_rig_visibility.setVisible(True)
            control_rig_visibility.setEnabled(
                bool(getattr(control_rig_visibility, "_control_rig_available", False))
            )

    def current_language(self) -> str:
        """Return the active UI locale for presenter-owned dynamic text."""

        return self._translator.get_language()

    def retranslateUi(self):
        """Update static Animator Toolset text without rebuilding picker state."""
        def tr(key):
            return self.tr(key, "animation_toolset")

        self.refresh_btn.setText(tr("refresh"))
        for index, key in enumerate(("body", "finger", "morph", "display")):
            self.picker_tabs.setTabText(index, tr(key))
        self.select_all_btn.setText(tr("select_all"))
        self.select_all_btn.setToolTip(tr("select_all_tooltip"))
        self.clear_btn.setText(tr("clear"))
        self.visibility_label.setText(f"{tr('visibility')}：")
        self.body_picker.update_region_texts(
            labels={
                "select_all": "ALL",
                "clear_selection": tr("clear"),
                "reset_pose": tr("reset"),
            },
            tooltips={
                "select_all": tr("select_all_tooltip"),
                "clear_selection": tr("clear_tooltip"),
                "reset_pose": tr("reset_pose_tooltip"),
                "fingers_left": tr("finger_picker_tooltip"),
                "fingers_right": tr("finger_picker_tooltip"),
                "ik_enable_left": self.tr("ik_enable_side_tooltip"),
                "ik_enable_right": self.tr("ik_enable_side_tooltip"),
            },
        )
        self.finger_picker.update_region_texts(
            labels={"back_to_body": f"‹  {tr('back_to_body')}"},
            tooltips={"back_to_body": tr("back_to_body_tooltip")},
        )
        for key in ("mesh", "joints", "colliders", "control_rig"):
            button = self.vis_checkboxes[key]
            button.setText(tr(key))
            if hasattr(button, "setVisibilityLabels"):
                button.setVisibilityLabels(
                    {
                        "visible": tr("visibility_state_visible"),
                        "reference": tr("visibility_state_reference"),
                        "hidden": tr("visibility_state_hidden"),
                    },
                    tr("visibility_unavailable"),
                )
        self.tools_group.setTitle(tr("tools"))
        common_action_buttons = getattr(self, "common_action_buttons", {})
        if common_action_buttons:
            common_labels = {
                "reset": tr("reset"),
                "mirror": tr("mirror"),
            }
            common_tooltips = {
                "reset": tr("reset_pose_tooltip"),
                "mirror": tr("mirror_pose_tooltip"),
            }
            for key, button in common_action_buttons.items():
                button.setText(common_labels[key])
                button.setToolTip(common_tooltips[key])
        manager_button = getattr(self, "control_rig_manager_btn", None)
        if manager_button is not None:
            manager_button.setText(tr("control_rig_manager"))
            manager_button.setToolTip(tr("control_rig_manager_tooltip"))
        legacy_group = getattr(self, "control_rig_group", None)
        if legacy_group is not None:
            legacy_group.setTitle(tr("control_rig_group_title"))
            for key, button in getattr(self, "control_rig_buttons", {}).items():
                translation_key = f"control_rig_{key}"
                button.setText(tr(translation_key))
                button.setToolTip(tr(f"{translation_key}_tooltip"))
        for key, button in self.tool_buttons.items():
            button.setText(tr(key))
