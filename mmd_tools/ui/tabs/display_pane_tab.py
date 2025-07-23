from PySide6.QtWidgets import (
    QVBoxLayout,
    QGroupBox,
    QListWidget,
    QHBoxLayout
)
from ..base_tab import BaseTab

class DisplayPaneTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DisplayPaneTab")

        main_layout = QHBoxLayout(self)

        # Display Pane List
        display_pane_list_group = QGroupBox("Display Panes")
        display_pane_list_layout = QVBoxLayout()
        self.display_pane_list = QListWidget()
        display_pane_list_layout.addWidget(self.display_pane_list)
        display_pane_list_group.setLayout(display_pane_list_layout)
        main_layout.addWidget(display_pane_list_group)

        # Contained Bones/Morphs
        contained_items_group = QGroupBox("Contained Items")
        contained_items_layout = QVBoxLayout()
        self.contained_items_list = QListWidget()
        contained_items_layout.addWidget(self.contained_items_list)
        contained_items_group.setLayout(contained_items_layout)
        main_layout.addWidget(contained_items_group)
