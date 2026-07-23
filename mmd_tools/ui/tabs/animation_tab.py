"""Animator Toolset tab — dev-mode gated, 4-tab picker + tools."""

from ..qt_compat import (
    QCheckBox,
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


class AnimationTab(BaseTab):
    """Anim Picker tab with Body / Finger / Morph / Display sub-tabs."""

    TAB_BODY = 0
    TAB_FINGER = 1
    TAB_MORPH = 2
    TAB_OTHER = 3

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
        self.refresh_btn = QPushButton("Refresh")
        selector_layout.addWidget(self.refresh_btn)
        main_layout.addLayout(selector_layout)

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
        morph_scroll.setWidgetResizable(True)
        morph_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        self.clear_btn = QPushButton("Clear")
        status_layout.addWidget(self.clear_btn)
        main_layout.addLayout(status_layout)

        # --- Collapsible visibility toggles ---
        self.visibility_toggle = QPushButton("Visibility  ▸")
        self.visibility_toggle.setCheckable(True)
        self.visibility_toggle.setChecked(False)
        self.visibility_toggle.setStyleSheet(
            "QPushButton { text-align: left; padding: 5px 8px; background: #3f3f3f; "
            "color: #d0d0d0; border: 1px solid #2c2c2c; } "
            "QPushButton:hover { background: #484848; }"
        )
        main_layout.addWidget(self.visibility_toggle)

        self.visibility_content = QWidget()
        vis_layout = QHBoxLayout()
        vis_layout.setContentsMargins(4, 4, 4, 4)
        vis_layout.setSpacing(8)

        self.vis_checkboxes: dict[str, QCheckBox] = {}
        for key, label in [
            ("mesh", "Mesh"),
            ("joints", "Joints"),
            ("colliders", "Colliders"),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(True)
            vis_layout.addWidget(cb)
            self.vis_checkboxes[key] = cb

        self.visibility_content.setLayout(vis_layout)
        self.visibility_content.setVisible(False)
        main_layout.addWidget(self.visibility_content)
        self.visibility_toggle.toggled.connect(self._set_visibility_expanded)

        # --- Tools section ---
        self.tools_group = QGroupBox("Tools")
        tools_layout = QGridLayout()
        tools_layout.setContentsMargins(4, 4, 4, 4)
        tools_layout.setSpacing(4)

        self.tool_buttons: dict[str, QPushButton] = {}
        for i, (key, label) in enumerate(
            [
                ("copy", "Copy"),
                ("paste", "Paste"),
                ("mirror", "Mirror"),
                ("reset", "Reset"),
                ("clean", "Clean"),
                ("bake", "Bake"),
            ]
        ):
            btn = QPushButton(label)
            btn.setMinimumHeight(28)
            row, col = divmod(i, 5)
            tools_layout.addWidget(btn, row, col)
            self.tool_buttons[key] = btn

        self.tools_group.setLayout(tools_layout)
        main_layout.addWidget(self.tools_group)

    def _set_visibility_expanded(self, expanded: bool) -> None:
        self.visibility_content.setVisible(expanded)
        self.visibility_toggle.setText("Visibility  ▾" if expanded else "Visibility  ▸")
