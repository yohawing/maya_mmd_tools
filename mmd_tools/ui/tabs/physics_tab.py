"""Physics tab UI shell (dev-mode).

Layout mirrors Bone/Morph: left list with Rigid Body / Joint switch and search,
right scrollable read-only details with Apply/Reset. Scene writes land in later
slices; presenter-facing attributes stay compatible with PhysicsPresenter.
"""

from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QListWidget,
    QPushButton,
    QCheckBox,
    QLabel,
    QLineEdit,
    QTabWidget,
    QSplitter,
    QScrollArea,
    Qt,
)
from ..base_tab import BaseTab
from .translation_registry import apply_translation_registry


class PhysicsTab(BaseTab):
    _TRANSLATION_REGISTRY = (
        ("physics_objects_group", "setTitle", "physics_objects", "groups"),
        ("details_group", "setTitle", "details", "groups"),
        ("refresh_btn", "setText", "refresh", "buttons"),
        ("apply_btn", "setText", "apply", "buttons"),
        ("reset_btn", "setText", "reset", "buttons"),
        ("collider_visible_check", "setText", "show_colliders", "checkboxes"),
        ("detail_name_label", "setText", "name", "fields"),
        ("detail_type_label", "setText", "type", "fields"),
        ("detail_shape_label", "setText", "shape", "fields"),
        ("detail_bodies_label", "setText", "bodies", "fields"),
        ("detail_node_label", "setText", "node", "fields"),
        ("rigid_body_search_edit", "setPlaceholderText", "search_rigid_bodies", "placeholders"),
        ("joint_search_edit", "setPlaceholderText", "search_joints", "placeholders"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PhysicsTab")

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        self.splitter = QSplitter(Qt.Horizontal)

        left_widget = self._create_list_section()
        self.splitter.addWidget(left_widget)

        right_widget = self._create_details_section()
        self.splitter.addWidget(right_widget)

        self.splitter.setSizes([400, 600])
        main_layout.addWidget(self.splitter)

        self.set_physics_details_enabled(False)

    def _create_list_section(self):
        """Left pane: toolbar, Rigid Bodies / Joints tabs, per-tab search."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.physics_objects_group = QGroupBox(self.tr("physics_objects", "groups"))
        group_layout = QVBoxLayout()

        toolbar_layout = QHBoxLayout()
        self.refresh_btn = QPushButton(self.tr("refresh", "buttons"))
        self.refresh_btn.setMaximumWidth(60)
        self.collider_visible_check = QCheckBox(self.tr("show_colliders", "checkboxes"))
        self.collider_visible_check.setChecked(False)
        toolbar_layout.addWidget(self.refresh_btn)
        toolbar_layout.addWidget(self.collider_visible_check)
        toolbar_layout.addStretch()
        group_layout.addLayout(toolbar_layout)

        self.list_tabs = QTabWidget()

        rigid_tab = QWidget()
        rigid_layout = QVBoxLayout(rigid_tab)
        rigid_layout.setContentsMargins(0, 0, 0, 0)
        self.rigid_body_list = QListWidget()
        self.rigid_body_list.setAlternatingRowColors(True)
        rigid_layout.addWidget(self.rigid_body_list)
        self.rigid_body_search_edit = QLineEdit()
        self.rigid_body_search_edit.setPlaceholderText(self.tr("search_rigid_bodies", "placeholders"))
        rigid_layout.addWidget(self.rigid_body_search_edit)
        self.list_tabs.addTab(rigid_tab, self.tr("rigid_bodies", "tabs"))

        joint_tab = QWidget()
        joint_layout = QVBoxLayout(joint_tab)
        joint_layout.setContentsMargins(0, 0, 0, 0)
        self.joint_list = QListWidget()
        self.joint_list.setAlternatingRowColors(True)
        joint_layout.addWidget(self.joint_list)
        self.joint_search_edit = QLineEdit()
        self.joint_search_edit.setPlaceholderText(self.tr("search_joints", "placeholders"))
        joint_layout.addWidget(self.joint_search_edit)
        self.list_tabs.addTab(joint_tab, self.tr("joints", "tabs"))

        group_layout.addWidget(self.list_tabs)
        self.physics_objects_group.setLayout(group_layout)
        layout.addWidget(self.physics_objects_group)
        return widget

    def _create_details_section(self):
        """Right pane: scrollable read-only details and Apply/Reset bar."""
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.details_scroll_area = QScrollArea()
        self.details_scroll_area.setWidgetResizable(True)

        self.physics_details_content = QWidget()
        content_layout = QVBoxLayout(self.physics_details_content)
        content_layout.setContentsMargins(5, 5, 5, 5)

        self.details_group = QGroupBox(self.tr("details", "groups"))
        details_layout = QFormLayout()

        self.detail_name_label = QLabel(self.tr("name", "fields"))
        self.detail_type_label = QLabel(self.tr("type", "fields"))
        self.detail_shape_label = QLabel(self.tr("shape", "fields"))
        self.detail_bodies_label = QLabel(self.tr("bodies", "fields"))
        self.detail_node_label = QLabel(self.tr("node", "fields"))

        self.detail_name_value = QLabel("")
        self.detail_type_value = QLabel("")
        self.detail_shape_value = QLabel("")
        self.detail_bodies_value = QLabel("")
        self.detail_node_value = QLabel("")

        details_layout.addRow(self.detail_name_label, self.detail_name_value)
        details_layout.addRow(self.detail_type_label, self.detail_type_value)
        details_layout.addRow(self.detail_shape_label, self.detail_shape_value)
        details_layout.addRow(self.detail_bodies_label, self.detail_bodies_value)
        details_layout.addRow(self.detail_node_label, self.detail_node_value)

        self.details_group.setLayout(details_layout)
        content_layout.addWidget(self.details_group)
        content_layout.addStretch()

        self.details_scroll_area.setWidget(self.physics_details_content)
        main_layout.addWidget(self.details_scroll_area)

        button_layout = QHBoxLayout()
        self.apply_btn = QPushButton(self.tr("apply", "buttons"))
        self.reset_btn = QPushButton(self.tr("reset", "buttons"))
        self.apply_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.reset_btn)
        main_layout.addLayout(button_layout)

        return widget

    def set_physics_details_enabled(self, enabled):
        """Enable or disable the details content and Apply/Reset buttons."""
        self.physics_details_content.setEnabled(enabled)
        self.apply_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)

    def retranslateUi(self):
        """Re-apply translation registry and tab titles on language change."""
        apply_translation_registry(self, self._TRANSLATION_REGISTRY)
        if self.list_tabs.count() >= 2:
            self.list_tabs.setTabText(0, self.tr("rigid_bodies", "tabs"))
            self.list_tabs.setTabText(1, self.tr("joints", "tabs"))
