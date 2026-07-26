"""Animator Toolset tab with a public picker and development-only pose tools."""

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
from ..components.symbol_tool_button import MaterialSymbolToolButton
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
        self.refresh_btn = MaterialSymbolToolButton("refresh", "Refresh")
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
            button = MaterialSymbolToolButton(symbol, key, checkable=True)
            button.setChecked(True)
            if key == "control_rig":
                button._control_rig_available = False
                button.setEnabled(False)
            visibility_layout.addWidget(button)
            self.vis_checkboxes[key] = button
        visibility_layout.addStretch(1)
        main_layout.addLayout(visibility_layout)

        self.control_rig_group = QGroupBox("MMD Control Rig")
        control_rig_layout = QGridLayout()
        self.control_rig_buttons: dict[str, QPushButton] = {}
        for index, (key, label) in enumerate(
            (
                ("create", "Create"),
                ("edit", "Attach / Edit"),
                ("bake_mmd", "Bake to MMD"),
                ("restore", "Restore"),
                ("delete", "Delete Rig"),
                ("diagnostics", "Diagnostics"),
            )
        ):
            button = QPushButton(label)
            button.setToolTip(label)
            row, column = divmod(index, 3)
            control_rig_layout.addWidget(button, row, column)
            self.control_rig_buttons[key] = button
        self.control_rig_group.setLayout(control_rig_layout)
        main_layout.addWidget(self.control_rig_group)

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

        # --- Status bar ---
        status_layout = QHBoxLayout()
        self.status_label = QLabel("")
        status_layout.addWidget(self.status_label, 1)
        # Kept as hidden compatibility endpoints; the visible actions live in
        # the two blue Body picker buttons.
        self.select_all_btn = QPushButton("Select All", self)
        self.select_all_btn.setToolTip("現在のMMDモデルの全ボーンを選択")
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
                ("mirror", "Mirror", "flip"),
                ("reset", "Reset Pose", "restart_alt"),
                ("clean", "Clean", "cleaning_services"),
                ("bake", "Bake", "animation"),
            ]
        ):
            btn = MaterialSymbolToolButton(symbol, label)
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
        """Show unfinished pose tools only in Development Mode."""

        development_mode = SettingsService().is_development_mode()
        picker_tab = self.picker_tabs.currentIndex() in (self.TAB_BODY, self.TAB_FINGER)
        self.tools_group.setVisible(picker_tab and development_mode)
        # The MMD Control Rig is unsupported outside Development Mode, so the
        # group is disabled as well as hidden: hiding alone still leaves the
        # buttons clickable through a re-parented or scripted view.
        self.control_rig_group.setVisible(development_mode)
        self.control_rig_group.setEnabled(development_mode)
        control_rig_visibility = self.vis_checkboxes.get("control_rig")
        if control_rig_visibility is not None:
            control_rig_visibility.setVisible(development_mode)
            control_rig_visibility.setEnabled(
                development_mode
                and bool(
                    getattr(control_rig_visibility, "_control_rig_available", False)
                )
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
                "mirror_sel": tr("mirror_selection"),
            },
            tooltips={
                "select_all": tr("select_all_tooltip"),
                "clear_selection": tr("clear_tooltip"),
                "reset_pose": tr("reset_pose_tooltip"),
                "mirror_sel": tr("mirror_selection_tooltip"),
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
            self.vis_checkboxes[key].setText(tr(key))
        self.tools_group.setTitle(tr("tools"))
        for key, button in self.tool_buttons.items():
            button.setText(tr(key))
