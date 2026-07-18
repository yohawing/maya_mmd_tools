"""Physics tab UI shell for inspecting rigid bodies and joints."""

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
    QDoubleSpinBox,
    QAbstractSpinBox,
    QSlider,
    QGridLayout,
    Signal,
    QTabWidget,
    QSplitter,
    QScrollArea,
    Qt,
)
from ..base_tab import BaseTab
from .translation_registry import apply_translation_registry


class Vec3Editor(QWidget):
    """Compact, axis-labelled vector editor with a line-edit compatible API."""

    valueChanged = Signal()

    def __init__(self, minimum=-1_000_000.0, maximum=1_000_000.0, decimals=4, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.spins = []
        no_buttons = getattr(
            getattr(QAbstractSpinBox, "ButtonSymbols", QAbstractSpinBox),
            "NoButtons",
        )
        for _axis in "XYZ":
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setDecimals(decimals)
            spin.setSingleStep(0.1)
            spin.setKeyboardTracking(False)
            spin.setButtonSymbols(no_buttons)
            spin.setMinimumWidth(52)
            spin.valueChanged.connect(lambda _value: self.valueChanged.emit())
            layout.addWidget(spin, 1)
            self.spins.append(spin)

    def values(self):
        return tuple(spin.value() for spin in self.spins)

    def setValues(self, values):
        previous = [spin.blockSignals(True) for spin in self.spins]
        try:
            for spin, value in zip(self.spins, values):
                spin.setValue(float(value))
        finally:
            for spin, blocked in zip(self.spins, previous):
                spin.blockSignals(blocked)

    def text(self):
        return ", ".join(str(value) for value in self.values())

    def setText(self, text):
        parts = [part.strip() for part in str(text).split(",")]
        if len(parts) == 3:
            try:
                self.setValues(parts)
            except ValueError:
                pass

    def setValue(self, value):
        self.setText(value)

    def setComponentCount(self, count):
        """Show only the leading components used by the current PMX shape."""
        for index, spin in enumerate(self.spins):
            spin.setVisible(index < int(count))


class ScalarSliderEditor(QWidget):
    """A precise spin box paired with a slider for fast physical tuning."""

    valueChanged = Signal(float)

    def __init__(self, slider_minimum, slider_maximum, single_step=0.01, parent=None):
        super().__init__(parent)
        self._slider_minimum = float(slider_minimum)
        self._slider_maximum = float(slider_maximum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(-1_000_000.0, 1_000_000.0)
        self.spin.setDecimals(4)
        self.spin.setSingleStep(single_step)
        self.spin.setKeyboardTracking(False)
        self.spin.setMinimumWidth(88)
        self.slider.valueChanged.connect(self._set_from_slider)
        self.spin.valueChanged.connect(self._set_from_spin)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)

    def _set_from_slider(self, position):
        value = self._slider_minimum + (self._slider_maximum - self._slider_minimum) * position / 1000.0
        previous = self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(previous)
        self.valueChanged.emit(self.spin.value())

    def _set_from_spin(self, value):
        span = self._slider_maximum - self._slider_minimum
        position = round((float(value) - self._slider_minimum) / span * 1000.0) if span else 0
        previous = self.slider.blockSignals(True)
        self.slider.setValue(max(0, min(1000, position)))
        self.slider.blockSignals(previous)
        self.valueChanged.emit(float(value))

    def value(self):
        return self.spin.value()

    def setValue(self, value):
        self.spin.setValue(float(value))

    def text(self):
        return str(self.value())

    def setText(self, text):
        try:
            self.setValue(text)
        except (TypeError, ValueError):
            pass


class CollisionGroupsEditor(QWidget):
    """Sixteen direct-access PMX collision group buttons."""

    valueChanged = Signal(int)

    def __init__(self, multiple=False, parent=None):
        super().__init__(parent)
        self._multiple = multiple
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(3)
        layout.setVerticalSpacing(3)
        self.buttons = []
        for group in range(16):
            button = QPushButton(str(group))
            button.setCheckable(True)
            button.setFixedWidth(28)
            button.clicked.connect(lambda checked, index=group: self._clicked(index, checked))
            layout.addWidget(button, group // 8, group % 8)
            self.buttons.append(button)

    def _clicked(self, index, checked):
        if not self._multiple:
            for group, button in enumerate(self.buttons):
                button.setChecked(group == index)
        self.valueChanged.emit(self.value())

    def value(self):
        if self._multiple:
            return sum(1 << index for index, button in enumerate(self.buttons) if button.isChecked())
        return next((index for index, button in enumerate(self.buttons) if button.isChecked()), 0)

    def setValue(self, value):
        value = int(value, 0) if isinstance(value, str) else int(value)
        previous = [button.blockSignals(True) for button in self.buttons]
        try:
            for index, button in enumerate(self.buttons):
                button.setChecked(bool(value & (1 << index)) if self._multiple else index == value)
        finally:
            for button, blocked in zip(self.buttons, previous):
                button.blockSignals(blocked)

    def text(self):
        return f"0x{self.value():04X}" if self._multiple else str(self.value())

    def setText(self, text):
        try:
            self.setValue(int(str(text), 0))
        except ValueError:
            pass


class PhysicsTab(BaseTab):
    AUTHORING_ENABLED = False

    _TRANSLATION_REGISTRY = (
        ("physics_objects_group", "setTitle", "physics_objects", "groups"),
        ("refresh_btn", "setText", "refresh", "buttons"),
        ("create_btn", "setText", "create", "buttons"),
        ("duplicate_btn", "setText", "duplicate", "buttons"),
        ("delete_btn", "setText", "delete", "buttons"),
        ("collider_visible_check", "setText", "show_colliders", "checkboxes"),
        ("physics_enable_check", "setText", "enable_physics", "checkboxes"),
        ("rigid_body_search_edit", "setPlaceholderText", "search_rigid_bodies", "placeholders"),
        ("joint_search_edit", "setPlaceholderText", "search_joints", "placeholders"),
        ("apply_btn", "setText", "apply", "buttons"),
        ("reset_btn", "setText", "reset", "buttons"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PhysicsTab")
        self._form_labels = {}
        self._physics_editors = {}
        self._combo_options = {}
        self._binding_editor_keys = set()

        main_layout = QVBoxLayout(self)
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
        self.create_btn = QPushButton(self.tr("create", "buttons"))
        self.create_btn.setMaximumWidth(60)
        self.create_btn.setEnabled(False)
        self.duplicate_btn = QPushButton(self.tr("duplicate", "buttons"))
        self.duplicate_btn.setMaximumWidth(70)
        self.duplicate_btn.setEnabled(False)
        self.delete_btn = QPushButton(self.tr("delete", "buttons"))
        self.delete_btn.setMaximumWidth(60)
        self.delete_btn.setEnabled(False)
        self.collider_visible_check = QCheckBox(self.tr("show_colliders", "checkboxes"))
        self.collider_visible_check.setChecked(False)
        self.physics_enable_check = QCheckBox(self.tr("enable_physics", "checkboxes"))
        self.physics_enable_check.setChecked(False)
        self.physics_enable_check.setEnabled(False)
        toolbar_layout.addWidget(self.refresh_btn)
        toolbar_layout.addWidget(self.create_btn)
        toolbar_layout.addWidget(self.duplicate_btn)
        toolbar_layout.addWidget(self.delete_btn)
        for button in (self.create_btn, self.duplicate_btn, self.delete_btn):
            button.hide()
        toolbar_layout.addWidget(self.collider_visible_check)
        toolbar_layout.addWidget(self.physics_enable_check)
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
        """Right pane: scrollable physics property forms with Apply/Reset."""
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.details_scroll_area = QScrollArea()
        self.details_scroll_area.setWidgetResizable(True)

        self.physics_details_content = QWidget()
        content_layout = QVBoxLayout(self.physics_details_content)
        content_layout.setContentsMargins(5, 5, 5, 5)

        self.rigid_body_form_group = self._create_rigid_body_form()
        self.joint_form_group = self._create_joint_form()
        content_layout.addWidget(self.rigid_body_form_group)
        content_layout.addWidget(self.joint_form_group)
        self.rigid_body_form_group.hide()
        self.joint_form_group.hide()

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.apply_btn = QPushButton(self.tr("apply", "buttons"))
        self.reset_btn = QPushButton(self.tr("reset", "buttons"))
        self.apply_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.reset_btn)
        content_layout.addLayout(button_layout)
        content_layout.addStretch()

        self.details_scroll_area.setWidget(self.physics_details_content)
        main_layout.addWidget(self.details_scroll_area)

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
        self.rigid_related_bone_combo = self._binding_editor(
            "rigid_related_bone", "related_bone", "rigidRelatedBoneCombo"
        )
        self._add_editor_row(
            layout, "related_bone", "rigid_related_bone", self.rigid_related_bone_combo
        )
        self.rigid_shape_size_edit = self._vec3_editor("rigid_shape_size", "shape_size", minimum=0.0)
        self.rigid_shape_combo.currentIndexChanged.connect(self._update_rigid_shape_size_editor)
        self.rigid_position_edit = self._vec3_editor("rigid_position", "pmx_position")
        self.rigid_rotation_edit = self._vec3_editor("rigid_rotation", "pmx_rotation_degrees", decimals=2)
        self.rigid_collision_group_spin = self._int_editor(
            "rigid_collision_group", "collision_group", 0, 15
        )
        self.rigid_collision_mask_spin = self._collision_groups_editor(
            "rigid_collision_mask", "collision_mask", multiple=True
        )
        self.rigid_mass_edit = self._slider_editor("rigid_mass", "mass", 0.0, 10.0, 0.1)
        self.rigid_linear_damping_edit = self._slider_editor(
            "rigid_linear_damping", "linear_damping", 0.0, 1.0
        )
        self.rigid_angular_damping_edit = self._slider_editor(
            "rigid_angular_damping", "angular_damping", 0.0, 1.0
        )
        self.rigid_restitution_edit = self._slider_editor(
            "rigid_restitution", "restitution", 0.0, 1.0
        )
        self.rigid_friction_edit = self._slider_editor("rigid_friction", "friction", 0.0, 1.0)
        for key, label_key in (
            ("rigid_shape_size", "shape_size"),
            ("rigid_position", "pmx_position"),
            ("rigid_rotation", "pmx_rotation_degrees"),
            ("rigid_collision_group", "collision_group"),
            ("rigid_collision_mask", "non_collision_groups"),
            ("rigid_mass", "mass"),
            ("rigid_linear_damping", "linear_damping"),
            ("rigid_angular_damping", "angular_damping"),
            ("rigid_restitution", "restitution"),
            ("rigid_friction", "friction"),
        ):
            self._add_editor_row(layout, label_key, key, self._physics_editors[key][1])
        self._update_rigid_shape_size_editor(self.rigid_shape_combo.currentIndex())
        group.setLayout(layout)
        return group

    def _update_rigid_shape_size_editor(self, shape_type):
        shape_type = int(shape_type)
        component_count = (1, 3, 2)[max(0, min(2, shape_type))]
        field_key = ("radius", "shape_size", "radius_height")[max(0, min(2, shape_type))]
        self.rigid_shape_size_edit.setComponentCount(component_count)
        if "rigid_shape_size" in self._form_labels:
            label = self._form_labels["rigid_shape_size"][1]
            self._form_labels["rigid_shape_size"] = (field_key, label)
            label.setText(self.tr(field_key, "fields"))

    def _create_joint_form(self):
        group = QGroupBox(self.tr("joint_values", "groups"))
        layout = QFormLayout()
        self.joint_name_edit = self._line_editor("joint_name", "name")
        self.joint_name_english_edit = self._line_editor("joint_name_english", "name_english")
        self.joint_type_spin = self._line_editor("joint_type", "joint_type")
        self.joint_body_a_combo = self._binding_editor(
            "joint_body_a", "rigid_body_a", "jointRigidBodyACombo"
        )
        self.joint_body_b_combo = self._binding_editor(
            "joint_body_b", "rigid_body_b", "jointRigidBodyBCombo"
        )
        self.joint_position_edit = self._vec3_editor("joint_position", "pmx_position")
        self.joint_rotation_edit = self._vec3_editor(
            "joint_rotation", "pmx_rotation_degrees", decimals=2
        )
        for key, field_key in (
            ("joint_translation_min", "translation_limit_min"),
            ("joint_translation_max", "translation_limit_max"),
            ("joint_rotation_min", "rotation_limit_min_degrees"),
            ("joint_rotation_max", "rotation_limit_max_degrees"),
            ("joint_spring_translation", "spring_translation"),
            ("joint_spring_rotation", "spring_rotation"),
        ):
            decimals = 2 if "rotation" in key else 4
            setattr(self, f"{key}_edit", self._vec3_editor(key, field_key, decimals=decimals))
        for key in (
            "joint_name",
            "joint_name_english",
            "joint_type",
            "joint_body_a",
            "joint_body_b",
            "joint_position",
            "joint_rotation",
            "joint_translation_min",
            "joint_translation_max",
            "joint_rotation_min",
            "joint_rotation_max",
            "joint_spring_translation",
            "joint_spring_rotation",
        ):
            self._add_editor_row(layout, self._physics_editors[key][0], key, self._physics_editors[key][1])
        group.setLayout(layout)
        return group

    def _line_editor(self, key, field_key):
        editor = QLineEdit()
        self._physics_editors[key] = (field_key, editor)
        return editor

    def _int_editor(self, key, field_key, minimum, maximum):
        editor = QSpinBox()
        editor.setRange(minimum, maximum)
        self._physics_editors[key] = (field_key, editor)
        return editor

    def _vec3_editor(self, key, field_key, minimum=-1_000_000.0, maximum=1_000_000.0, decimals=4):
        editor = Vec3Editor(minimum, maximum, decimals)
        self._physics_editors[key] = (field_key, editor)
        return editor

    def _slider_editor(self, key, field_key, minimum, maximum, single_step=0.01):
        editor = ScalarSliderEditor(minimum, maximum, single_step)
        self._physics_editors[key] = (field_key, editor)
        return editor

    def _collision_groups_editor(self, key, field_key, multiple=False):
        editor = CollisionGroupsEditor(multiple)
        self._physics_editors[key] = (field_key, editor)
        return editor

    def _combo_editor(self, key, field_key, option_keys):
        editor = QComboBox()
        editor.addItems([self.tr(option_key, "options") for option_key in option_keys])
        self._physics_editors[key] = (field_key, editor)
        self._combo_options[key] = tuple(option_keys)
        return editor

    def _binding_editor(self, key, field_key, object_name):
        editor = QComboBox()
        editor.setObjectName(object_name)
        editor.addItem(self.tr("none", "options"), ("", -1))
        self._physics_editors[key] = (field_key, editor)
        self._binding_editor_keys.add(key)
        return editor

    def set_binding_options(self, editor_key, candidates):
        """Replace one root-scoped binding list without emitting edit signals."""
        editor = self._physics_editors[editor_key][1]
        previous = editor.blockSignals(True)
        try:
            editor.clear()
            editor.addItem(self.tr("none", "options"), ("", -1))
            for display, node, index in candidates:
                editor.addItem(display, (node, int(index)))
        finally:
            editor.blockSignals(previous)

    def _set_binding_selection(self, editor_key, value):
        editor = self._physics_editors[editor_key][1]
        node, fallback_index = value
        selected = 0
        if node:
            for index in range(1, editor.count()):
                if editor.itemData(index)[0] == node:
                    selected = index
                    break
        elif fallback_index >= 0:
            for index in range(1, editor.count()):
                if editor.itemData(index)[1] == fallback_index:
                    selected = index
                    break
        editor.setCurrentIndex(selected)

    def binding_selection(self, editor_key):
        """Return the selected long node path and fallback PMX index."""
        data = self._physics_editors[editor_key][1].currentData()
        return tuple(data) if data else ("", -1)

    def _retranslate_binding_none_items(self):
        for editor_key in self._binding_editor_keys:
            editor = self._physics_editors[editor_key][1]
            if editor.count():
                editor.setItemText(0, self.tr("none", "options"))

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
                    if editor_key in self._binding_editor_keys:
                        self._set_binding_selection(editor_key, value)
                    else:
                        editor.setCurrentIndex(int(value))
                else:
                    editor.setValue(value)
            finally:
                editor.blockSignals(previous)
        if kind == "rigid" and "shape" in values:
            self._update_rigid_shape_size_editor(values["shape"])

    def set_physics_details_enabled(self, enabled):
        """Expose physics details without enabling unsupported authoring."""
        self.physics_details_content.setEnabled(bool(enabled and self.AUTHORING_ENABLED))

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
        self._retranslate_binding_none_items()
        if self.list_tabs.count() >= 2:
            self.list_tabs.setTabText(0, self.tr("rigid_bodies", "tabs"))
            self.list_tabs.setTabText(1, self.tr("joints", "tabs"))
