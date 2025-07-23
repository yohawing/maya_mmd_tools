from PySide6.QtWidgets import (
    QVBoxLayout,
    QGroupBox,
    QFormLayout,
    QLineEdit
)
from ..base_tab import BaseTab

class InfoTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoTab")

        main_layout = QVBoxLayout(self)

        info_group = QGroupBox("Model Information")
        info_layout = QFormLayout()

        self.model_name_jp_edit = QLineEdit()
        self.model_name_en_edit = QLineEdit()
        self.comment_jp_edit = QLineEdit()
        self.comment_en_edit = QLineEdit()

        info_layout.addRow("Model Name (JP):", self.model_name_jp_edit)
        info_layout.addRow("Model Name (EN):", self.model_name_en_edit)
        info_layout.addRow("Comment (JP):", self.comment_jp_edit)
        info_layout.addRow("Comment (EN):", self.comment_en_edit)

        info_group.setLayout(info_layout)
        main_layout.addWidget(info_group)

        main_layout.addStretch()
