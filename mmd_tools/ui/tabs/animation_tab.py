"""Animator Toolset tab — dev-mode gated, 4-tab picker + tools."""

from ..qt_compat import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)
from ..base_tab import BaseTab


class AnimationTab(BaseTab):
    """Anim Picker tab with Body / Finger / Morph / Other sub-tabs."""

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
        self.body_placeholder = QLabel("Body picker (Phase 2)")
        self.body_placeholder.setAlignment(0x0084)  # AlignCenter
        body_layout.addWidget(self.body_placeholder)
        self.picker_tabs.addTab(self.body_page, "Body")

        self.finger_page = QWidget()
        finger_layout = QVBoxLayout(self.finger_page)
        self.finger_placeholder = QLabel("Finger picker (Phase 3)")
        self.finger_placeholder.setAlignment(0x0084)
        finger_layout.addWidget(self.finger_placeholder)
        self.picker_tabs.addTab(self.finger_page, "Finger")

        self.morph_page = QWidget()
        morph_layout = QVBoxLayout(self.morph_page)
        self.morph_placeholder = QLabel("Morph tab (Phase 3)")
        self.morph_placeholder.setAlignment(0x0084)
        morph_layout.addWidget(self.morph_placeholder)
        self.picker_tabs.addTab(self.morph_page, "Morph")

        self.other_page = QWidget()
        other_layout = QVBoxLayout(self.other_page)
        self.display_frame_tree = QTreeWidget()
        self.display_frame_tree.setHeaderHidden(True)
        other_layout.addWidget(self.display_frame_tree)
        self.picker_tabs.addTab(self.other_page, "Other")

        main_layout.addWidget(self.picker_tabs, 1)

        # --- Status bar ---
        status_layout = QHBoxLayout()
        self.status_label = QLabel("")
        status_layout.addWidget(self.status_label, 1)
        self.clear_btn = QPushButton("Clear")
        status_layout.addWidget(self.clear_btn)
        main_layout.addLayout(status_layout)
