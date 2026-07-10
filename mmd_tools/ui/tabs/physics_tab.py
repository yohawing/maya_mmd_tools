"""Physics tab UI shell and cached physics value editor.

Layout mirrors Bone/Morph: left list with Rigid Body / Joint switch and search,
right scrollable details with editable cached values and Apply/Reset. Scene
writes land in a later slice; presenter-facing attributes stay compatible.
This D1 form deliberately accepts round-trip text without parsing or physical
range checks. D2 must validate every field before any explicit Apply write.
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
    QComboBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTabWidget,
    QSplitter,
    QScrollArea,
    Signal,
    Qt,
)
from ..base_tab import BaseTab
from .translation_registry import apply_translation_registry


class PhysicsTab(BaseTab):
    physics_form_changed = Signal()

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
        self._form_labels = {}
        self._physics_editors = {}
        self._combo_options = {}

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
        self.rigid_body_form_group = self._create_rigid_body_form()
        self.joint_form_group = self._create_joint_form()
        content_layout.addWidget(self.rigid_body_form_group)
        content_layout.addWidget(self.joint_form_group)
        self.rigid_body_form_group.hide()
        self.joint_form_group.hide()
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

    def _create_rigid_body_form(self):
        group = QGroupBox(self.tr("rigid_body_values", "groups"))
        layout = QFormLayout()
        self.rigid_name_edit = self._line_editor("rigid_name", "name")
        self.rigid_name_english_edit = self._line_editor("rigid_name_english", "name_english")
        self._add_editor_row(layout, "name", "rigid_name", self.rigid_name_edit)
        self._add_editor_row(layout, "name_english", "rigid_name_english", self.rigid_name_english_edit)
        self.rigid_shape_combo = self._combo_editor(
            "rigid_shape",
            "shape",
            ["physics_shape_sphere", "physics_shape_box", "physics_shape_capsule"],
        )
        self._add_editor_row(layout, "shape", "rigid_shape", self.rigid_shape_combo)
        self.rigid_physics_mode_combo = self._combo_editor(
            "rigid_physics_mode",
            "physics_mode",
            ["physics_mode_bone", "physics_mode_physics", "physics_mode_physics_bone"],
        )
        self._add_editor_row(layout, "physics_mode", "rigid_physics_mode", self.rigid_physics_mode_combo)
        self.rigid_related_bone_spin = self._int_editor("rigid_related_bone", "related_bone", -1, 999999)
        self.rigid_collision_group_spin = self._int_editor("rigid_collision_group", "collision_group", 0, 15)
        self.rigid_collision_mask_spin = self._int_editor("rigid_collision_mask", "collision_mask", 0, 0xFFFF)
        self.rigid_mass_edit = self._line_editor("rigid_mass", "mass")
        self.rigid_linear_damping_edit = self._line_editor("rigid_linear_damping", "linear_damping")
        self.rigid_angular_damping_edit = self._line_editor("rigid_angular_damping", "angular_damping")
        self.rigid_restitution_edit = self._line_editor("rigid_restitution", "restitution")
        self.rigid_friction_edit = self._line_editor("rigid_friction", "friction")
        for key in (
            "rigid_related_bone",
            "rigid_collision_group",
            "rigid_collision_mask",
            "rigid_mass",
            "rigid_linear_damping",
            "rigid_angular_damping",
            "rigid_restitution",
            "rigid_friction",
        ):
            self._add_editor_row(layout, self._physics_editors[key][0], key, self._physics_editors[key][1])
        group.setLayout(layout)
        return group

    def _create_joint_form(self):
        group = QGroupBox(self.tr("joint_values", "groups"))
        layout = QFormLayout()
        self.joint_name_edit = self._line_editor("joint_name", "name")
        self.joint_name_english_edit = self._line_editor("joint_name_english", "name_english")
        self.joint_type_spin = self._int_editor("joint_type", "joint_type", 0, 6)
        self.joint_body_a_spin = self._int_editor("joint_body_a", "rigid_body_a", -1, 999999)
        self.joint_body_b_spin = self._int_editor("joint_body_b", "rigid_body_b", -1, 999999)
        for key, field_key in (
            ("joint_linear_states", "linear_constraint_states"),
            ("joint_angular_states", "angular_constraint_states"),
            ("joint_translation_min", "translation_limit_min"),
            ("joint_translation_max", "translation_limit_max"),
            ("joint_rotation_min", "rotation_limit_min_degrees"),
            ("joint_rotation_max", "rotation_limit_max_degrees"),
            ("joint_spring_translation", "spring_translation"),
            ("joint_spring_rotation", "spring_rotation"),
            ("joint_spring_translation_enabled", "spring_translation_enabled"),
            ("joint_spring_rotation_enabled", "spring_rotation_enabled"),
        ):
            setattr(self, f"{key}_edit", self._line_editor(key, field_key))
        for key in (
            "joint_name",
            "joint_name_english",
            "joint_type",
            "joint_body_a",
            "joint_body_b",
            "joint_linear_states",
            "joint_angular_states",
            "joint_translation_min",
            "joint_translation_max",
            "joint_rotation_min",
            "joint_rotation_max",
            "joint_spring_translation",
            "joint_spring_rotation",
            "joint_spring_translation_enabled",
            "joint_spring_rotation_enabled",
        ):
            self._add_editor_row(layout, self._physics_editors[key][0], key, self._physics_editors[key][1])
        group.setLayout(layout)
        return group

    def _line_editor(self, key, field_key):
        editor = QLineEdit()
        self._physics_editors[key] = (field_key, editor)
        editor.textChanged.connect(lambda *_args: self.physics_form_changed.emit())
        return editor

    def _int_editor(self, key, field_key, minimum, maximum):
        editor = QSpinBox()
        editor.setRange(minimum, maximum)
        self._physics_editors[key] = (field_key, editor)
        editor.valueChanged.connect(lambda *_args: self.physics_form_changed.emit())
        return editor

    def _combo_editor(self, key, field_key, option_keys):
        editor = QComboBox()
        editor.addItems([self.tr(option_key, "options") for option_key in option_keys])
        self._physics_editors[key] = (field_key, editor)
        self._combo_options[key] = tuple(option_keys)
        editor.currentIndexChanged.connect(lambda *_args: self.physics_form_changed.emit())
        return editor

    def _add_editor_row(self, layout, field_key, editor_key, editor):
        label = QLabel(self.tr(field_key, "fields"))
        self._form_labels[editor_key] = (field_key, label)
        layout.addRow(label, editor)

    def set_physics_form(self, kind, values):
        """Populate one cached edit form without reporting user edits."""
        self.rigid_body_form_group.setVisible(kind == "rigid")
        self.joint_form_group.setVisible(kind == "joint")
        prefix = f"{kind}_" if kind else ""
        for editor_key, (field_key, editor) in self._physics_editors.items():
            if not prefix or not editor_key.startswith(prefix) or field_key not in values:
                continue
            previous = editor.blockSignals(True)
            try:
                value = values[field_key]
                if isinstance(editor, QLineEdit):
                    editor.setText(str(value))
                elif isinstance(editor, QComboBox):
                    editor.setCurrentIndex(int(value))
                else:
                    editor.setValue(value)
            finally:
                editor.blockSignals(previous)
        self.set_physics_dirty(False)

    def set_physics_dirty(self, dirty):
        enabled = bool(dirty) and self.physics_details_content.isEnabled()
        # D1 keeps Apply disabled until D2 connects validated scene writes.
        self.apply_btn.setEnabled(False)
        self.reset_btn.setEnabled(enabled)

    def set_physics_details_enabled(self, enabled):
        """Enable or disable the details content and Apply/Reset buttons."""
        self.physics_details_content.setEnabled(enabled)
        if not enabled:
            self.set_physics_dirty(False)

    def retranslateUi(self):
        """Re-apply translation registry and tab titles on language change."""
        apply_translation_registry(self, self._TRANSLATION_REGISTRY)
        self.rigid_body_form_group.setTitle(self.tr("rigid_body_values", "groups"))
        self.joint_form_group.setTitle(self.tr("joint_values", "groups"))
        for field_key, label in self._form_labels.values():
            label.setText(self.tr(field_key, "fields"))
        for editor_key, option_keys in self._combo_options.items():
            editor = self._physics_editors[editor_key][1]
            for index, option_key in enumerate(option_keys):
                editor.setItemText(index, self.tr(option_key, "options"))
        if self.list_tabs.count() >= 2:
            self.list_tabs.setTabText(0, self.tr("rigid_bodies", "tabs"))
            self.list_tabs.setTabText(1, self.tr("joints", "tabs"))
