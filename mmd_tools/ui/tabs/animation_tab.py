"""Animator Toolset tab — dev-mode gated, 4-tab picker + tools."""

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

        main_layout = QVBoxLayout(self)
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
            ("joints", "account_tree"),
            ("colliders", "shield"),
        ):
            button = MaterialSymbolToolButton(symbol, key, checkable=True)
            button.setChecked(True)
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
        finger_nav_layout = QHBoxLayout()
        finger_nav_layout.setContentsMargins(0, 0, 0, 0)
        self.finger_body_btn = QPushButton("‹  Bodyへ戻る")
        self.finger_body_btn.setToolTip("全身Pickerへ戻る")
        finger_nav_layout.addWidget(self.finger_body_btn)
        finger_nav_layout.addStretch(1)
        finger_layout.addLayout(finger_nav_layout)
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
                ("reset", "Rest Pose", "restart_alt"),
                ("clean", "Clean", "cleaning_services"),
                ("bake", "Bake", "animation"),
            ]
        ):
            btn = MaterialSymbolToolButton(symbol, label)
            row, col = divmod(i, 6)
            tools_layout.addWidget(btn, row, col)
            self.tool_buttons[key] = btn

        self.tools_group.setLayout(tools_layout)
        main_layout.addWidget(self.tools_group)
        self.refresh_development_mode_visibility()
        self.retranslateUi()

    def refresh_development_mode_visibility(self):
        """Show unfinished pose tools only in Development Mode."""

        self.tools_group.setVisible(SettingsService().is_development_mode())

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
        self.finger_body_btn.setText(f"‹  {tr('back_to_body')}")
        self.finger_body_btn.setToolTip(tr("back_to_body_tooltip"))
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
                "reset_pose": tr("picker_rest_pose_tooltip"),
                "mirror_sel": tr("mirror_selection_tooltip"),
                "fingers_left": tr("finger_picker_tooltip"),
                "fingers_right": tr("finger_picker_tooltip"),
            },
        )
        for key in ("mesh", "joints", "colliders"):
            self.vis_checkboxes[key].setText(tr(key))
        self.tools_group.setTitle(tr("tools"))
        for key, button in self.tool_buttons.items():
            button.setText(tr(key))
