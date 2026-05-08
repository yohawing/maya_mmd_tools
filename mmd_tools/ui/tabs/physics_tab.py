from ..qt_compat import (
    QVBoxLayout,
    QTabWidget,
    QGroupBox,
    QHBoxLayout,
    QListWidget,
)
from ..base_tab import BaseTab


class PhysicsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PhysicsTab")

        main_layout = QVBoxLayout(self)

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
