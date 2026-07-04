from ..qt_compat import (
    QVBoxLayout,
    QTabWidget,
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QCheckBox,
    QLabel,
    QFormLayout,
)
from ..base_tab import BaseTab


class PhysicsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PhysicsTab")

        main_layout = QVBoxLayout(self)
        controls_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        controls_layout.addWidget(self.refresh_btn)
        self.collider_visible_check = QCheckBox("Show Colliders")
        self.collider_visible_check.setChecked(True)
        controls_layout.addWidget(self.collider_visible_check)
        main_layout.addLayout(controls_layout)

        tab_widget = QTabWidget()
        main_layout.addWidget(tab_widget)

        # Rigid Bodies
        rigid_body_group = QGroupBox("Rigid Bodies")
        rigid_body_layout = QHBoxLayout()
        self.rigid_body_list = QListWidget()
        rigid_body_layout.addWidget(self.rigid_body_list)
        rigid_body_group.setLayout(rigid_body_layout)
        tab_widget.addTab(rigid_body_group, "Rigid Bodies")

        # Joints
        joint_group = QGroupBox("Joints")
        joint_layout = QHBoxLayout()
        self.joint_list = QListWidget()
        joint_layout.addWidget(self.joint_list)
        joint_group.setLayout(joint_layout)
        tab_widget.addTab(joint_group, "Joints")

        details_group = QGroupBox("Details")
        details_layout = QFormLayout()
        self.detail_name_value = QLabel("None")
        self.detail_type_value = QLabel("")
        self.detail_shape_value = QLabel("")
        self.detail_bodies_value = QLabel("")
        self.detail_node_value = QLabel("")
        details_layout.addRow("Name", self.detail_name_value)
        details_layout.addRow("Type", self.detail_type_value)
        details_layout.addRow("Shape", self.detail_shape_value)
        details_layout.addRow("Bodies", self.detail_bodies_value)
        details_layout.addRow("Node", self.detail_node_value)
        details_group.setLayout(details_layout)
        main_layout.addWidget(details_group)
