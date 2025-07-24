from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QTreeView,
    QGroupBox,
    QTreeWidget,
    QTreeWidgetItem,
    QFormLayout,
    QLineEdit,
    QHBoxLayout
)
from ..base_tab import BaseTab

class BoneTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BoneTab")

        main_layout = QHBoxLayout(self)

        # Bone Tree
        bone_tree_group = QGroupBox("Bones")
        bone_tree_layout = QVBoxLayout()
        self.bone_tree = QTreeWidget()
        self.bone_tree.setHeaderLabels(["Name", "Parent"])
        bone_tree_layout.addWidget(self.bone_tree)
        bone_tree_group.setLayout(bone_tree_layout)
        main_layout.addWidget(bone_tree_group)

        # Bone Details
        bone_details_group = QGroupBox("Details")
        bone_details_layout = QFormLayout()

        self.bone_name_edit = QLineEdit()
        self.parent_bone_edit = QLineEdit()

        bone_details_layout.addRow("Name:", self.bone_name_edit)
        bone_details_layout.addRow("Parent:", self.parent_bone_edit)

        bone_details_group.setLayout(bone_details_layout)
        main_layout.addWidget(bone_details_group)
