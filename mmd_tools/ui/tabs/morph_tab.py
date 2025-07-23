from ..qt_compat import QWidget, QVBoxLayout, QListWidget, QSlider, Qt, QHBoxLayout, QGroupBox, QFormLayout, QLineEdit
from ..base_tab import BaseTab

class MorphTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MorphTab")

        main_layout = QHBoxLayout(self)

        # Morph List
        morph_list_group = QGroupBox("Morphs")
        morph_list_layout = QVBoxLayout()
        self.morph_list = QListWidget()
        morph_list_layout.addWidget(self.morph_list)
        morph_list_group.setLayout(morph_list_layout)
        main_layout.addWidget(morph_list_group)

        # Morph Details
        morph_details_group = QGroupBox("Details")
        morph_details_layout = QFormLayout()

        self.morph_name_edit = QLineEdit()
        self.morph_slider = QSlider(Qt.Horizontal)

        morph_details_layout.addRow("Name:", self.morph_name_edit)
        morph_details_layout.addRow("Weight:", self.morph_slider)

        morph_details_group.setLayout(morph_details_layout)
        main_layout.addWidget(morph_details_group)
