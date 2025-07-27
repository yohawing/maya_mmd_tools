from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QDoubleValidator,
    QSettings,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QLabel,
    QScrollArea,
    Qt,
    QSplitter,
    QTabWidget,
)
from ..base_tab import BaseTab
from ...core.settings import settings
from ...core.maya_utils import find_all_mmd_models, get_mmd_model_display_name


class ImportExportTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ImportExportTab")

        # Initialize settings for file paths only
        self.qt_settings = QSettings("maya_mmd_tools", "ImportExportTab")

        # メインレイアウト
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # スプリッターを作成（横方向）
        splitter = QSplitter(Qt.Horizontal)

        # 左側：設定セクション（タブ化）
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.left_widget = QTabWidget()

        # Model Import Settings Tab
        model_settings_tab = QScrollArea()
        model_settings_tab.setWidgetResizable(True)
        model_settings_tab.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        model_settings_widget = QWidget()
        model_settings_layout = QVBoxLayout(model_settings_widget)

        # Scale factor
        scale_layout = QHBoxLayout()
        self.scale_label = QLabel(self.tr("scale_factor", "fields"))
        scale_layout.addWidget(self.scale_label)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.001, 1000.0)
        self.scale_spin.setDecimals(3)
        self.scale_spin.setValue(settings.get("import.general.scale_factor", 1.0))
        self.scale_spin.valueChanged.connect(
            lambda v: settings.set("import.general.scale_factor", v)
        )
        scale_layout.addWidget(self.scale_spin)
        scale_layout.addStretch()
        model_settings_layout.addLayout(scale_layout)

        # General Model Settings Group
        self.general_group = QGroupBox(self.tr("general", "groups"))
        general_layout = QVBoxLayout()

        # Root bone name
        root_bone_layout = QHBoxLayout()
        self.root_bone_label = QLabel(self.tr("root_bone_name", "fields"))
        root_bone_layout.addWidget(self.root_bone_label)
        self.root_bone_name_edit = QLineEdit(
            settings.get("import.general.root_bone_name", "master")
        )
        self.root_bone_name_edit.textChanged.connect(
            lambda v: settings.set("import.general.root_bone_name", v)
        )
        root_bone_layout.addWidget(self.root_bone_name_edit)
        general_layout.addLayout(root_bone_layout)

        self.use_namespace_check = QCheckBox(self.tr("use_namespace", "checkboxes"))
        self.use_namespace_check.setChecked(
            settings.get("import.general.use_namespace", False)
        )
        self.use_namespace_check.toggled.connect(
            lambda v: settings.set("import.general.use_namespace", v)
        )
        general_layout.addWidget(self.use_namespace_check)

        self.general_group.setLayout(general_layout)
        model_settings_layout.addWidget(self.general_group)

        # Model Settings Group
        self.model_group = QGroupBox(self.tr("model", "groups"))
        model_layout = QVBoxLayout()

        self.import_models_check = QCheckBox(self.tr("import_models", "checkboxes"))
        self.import_models_check.setChecked(
            settings.get("import.model.import_models", True)
        )
        self.import_models_check.toggled.connect(
            lambda v: settings.set("import.model.import_models", v)
        )
        model_layout.addWidget(self.import_models_check)

        self.create_mmd_shaders_check = QCheckBox(self.tr("create_mmd_shaders", "checkboxes"))
        self.create_mmd_shaders_check.setChecked(
            settings.get("import.model.create_mmd_shaders", True)
        )
        self.create_mmd_shaders_check.toggled.connect(
            lambda v: settings.set("import.model.create_mmd_shaders", v)
        )
        model_layout.addWidget(self.create_mmd_shaders_check)

        self.separate_meshes_check = QCheckBox(self.tr("separate_meshes", "checkboxes"))
        self.separate_meshes_check.setChecked(
            settings.get("import.model.separate_meshes_by_material", False)
        )
        self.separate_meshes_check.toggled.connect(
            lambda v: settings.set("import.model.separate_meshes_by_material", v)
        )
        model_layout.addWidget(self.separate_meshes_check)

        self.hide_hidden_geometry_check = QCheckBox(self.tr("hide_hidden_geometry", "checkboxes"))
        self.hide_hidden_geometry_check.setChecked(
            settings.get("import.model.hide_hidden_geometry", True)
        )
        self.hide_hidden_geometry_check.toggled.connect(
            lambda v: settings.set("import.model.hide_hidden_geometry", v)
        )
        model_layout.addWidget(self.hide_hidden_geometry_check)

        self.joint_name_conversion_check = QCheckBox(self.tr("joint_name_conversion", "checkboxes"))
        self.joint_name_conversion_check.setChecked(
            settings.get("import.model.joint_name_conversion_with_english", False)
        )
        self.joint_name_conversion_check.toggled.connect(
            lambda v: settings.set("import.model.joint_name_conversion_with_english", v)
        )
        model_layout.addWidget(self.joint_name_conversion_check)

        self.disable_backface_culling_check = QCheckBox(self.tr("disable_backface_culling", "checkboxes"))
        self.disable_backface_culling_check.setChecked(
            settings.get("import.model.disable_backface_culling", True)
        )
        self.disable_backface_culling_check.toggled.connect(
            lambda v: settings.set("import.model.disable_backface_culling", v)
        )
        model_layout.addWidget(self.disable_backface_culling_check)

        # Texture search path
        texture_layout = QHBoxLayout()
        self.texture_search_label = QLabel(self.tr("texture_search_path", "fields"))
        texture_layout.addWidget(self.texture_search_label)
        self.texture_search_path_edit = QLineEdit(
            settings.get("import.model.texture_search_path", "")
        )
        self.texture_search_path_edit.textChanged.connect(
            lambda v: settings.set("import.model.texture_search_path", v)
        )
        texture_layout.addWidget(self.texture_search_path_edit)
        model_layout.addLayout(texture_layout)

        # UV set name
        uv_layout = QHBoxLayout()
        self.uv_set_label = QLabel(self.tr("uv_set_name", "fields"))
        uv_layout.addWidget(self.uv_set_label)
        self.uv_set_name_edit = QLineEdit(
            settings.get("import.model.uv_set_name", "map#")
        )
        self.uv_set_name_edit.textChanged.connect(
            lambda v: settings.set("import.model.uv_set_name", v)
        )
        uv_layout.addWidget(self.uv_set_name_edit)
        uv_layout.addStretch()
        model_layout.addLayout(uv_layout)

        self.model_group.setLayout(model_layout)
        model_settings_layout.addWidget(self.model_group)

        # Morph & Physics Group
        self.morph_physics_group = QGroupBox(self.tr("morph_physics", "groups"))
        morph_physics_layout = QVBoxLayout()

        self.import_morphs_check = QCheckBox(self.tr("import_morphs", "checkboxes"))
        self.import_morphs_check.setChecked(
            settings.get("import.morph.import_morphs", True)
        )
        self.import_morphs_check.toggled.connect(
            lambda v: settings.set("import.morph.import_morphs", v)
        )
        morph_physics_layout.addWidget(self.import_morphs_check)

        self.import_physics_check = QCheckBox(self.tr("import_physics", "checkboxes"))
        self.import_physics_check.setChecked(
            settings.get("import.physics.import_physics", False)
        )
        self.import_physics_check.toggled.connect(
            lambda v: settings.set("import.physics.import_physics", v)
        )
        morph_physics_layout.addWidget(self.import_physics_check)

        self.create_rigid_bodies_check = QCheckBox(self.tr("create_rigid_bodies", "checkboxes"))
        self.create_rigid_bodies_check.setChecked(
            settings.get("import.physics.create_rigid_bodies", True)
        )
        self.create_rigid_bodies_check.toggled.connect(
            lambda v: settings.set("import.physics.create_rigid_bodies", v)
        )
        morph_physics_layout.addWidget(self.create_rigid_bodies_check)

        self.create_physics_joints_check = QCheckBox(self.tr("create_physics_joints", "checkboxes"))
        self.create_physics_joints_check.setChecked(
            settings.get("import.physics.create_physics_joints", True)
        )
        self.create_physics_joints_check.toggled.connect(
            lambda v: settings.set("import.physics.create_physics_joints", v)
        )
        morph_physics_layout.addWidget(self.create_physics_joints_check)

        self.group_physics_objects_check = QCheckBox(self.tr("group_physics_objects", "checkboxes"))
        self.group_physics_objects_check.setChecked(
            settings.get("import.physics.group_physics_objects", True)
        )
        self.group_physics_objects_check.toggled.connect(
            lambda v: settings.set("import.physics.group_physics_objects", v)
        )
        morph_physics_layout.addWidget(self.group_physics_objects_check)

        self.morph_physics_group.setLayout(morph_physics_layout)
        model_settings_layout.addWidget(self.morph_physics_group)

        # Other Settings Group
        self.other_group = QGroupBox(self.tr("other", "groups"))
        other_layout = QVBoxLayout()

        self.add_semi_standard_bones_check = QCheckBox(self.tr("add_semi_standard_bones", "checkboxes"))
        self.add_semi_standard_bones_check.setChecked(
            settings.get("import.rig.add_semi_standard_bones", False)
        )
        self.add_semi_standard_bones_check.toggled.connect(
            lambda v: settings.set("import.rig.add_semi_standard_bones", v)
        )
        other_layout.addWidget(self.add_semi_standard_bones_check)

        self.translate_names_check = QCheckBox(self.tr("translate_names", "checkboxes"))
        self.translate_names_check.setChecked(
            settings.get("import.naming.translate_names", True)
        )
        self.translate_names_check.toggled.connect(
            lambda v: settings.set("import.naming.translate_names", v)
        )
        other_layout.addWidget(self.translate_names_check)

        self.other_group.setLayout(other_layout)
        model_settings_layout.addWidget(self.other_group)

        model_settings_layout.addStretch()
        model_settings_tab.setWidget(model_settings_widget)
        self.left_widget.addTab(model_settings_tab, self.tr("model", "groups"))

        # Animation Import Settings Tab
        anim_settings_tab = QScrollArea()
        anim_settings_tab.setWidgetResizable(True)
        anim_settings_tab.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        anim_settings_widget = QWidget()
        anim_settings_layout = QVBoxLayout(anim_settings_widget)

        # Start frame
        frame_layout = QHBoxLayout()
        self.start_frame_label = QLabel(self.tr("start_frame", "fields"))
        frame_layout.addWidget(self.start_frame_label)
        self.animation_start_frame = QSpinBox()
        self.animation_start_frame.setRange(0, 10000)
        self.animation_start_frame.setValue(
            settings.get("import.animation.animation_start_frame", 1)
        )
        self.animation_start_frame.valueChanged.connect(
            lambda v: settings.set("import.animation.animation_start_frame", v)
        )
        frame_layout.addWidget(self.animation_start_frame)
        frame_layout.addStretch()
        anim_settings_layout.addLayout(frame_layout)

        # Animation type checkboxes
        self.import_bone_animation_check = QCheckBox(self.tr("import_bone_animation", "checkboxes"))
        self.import_bone_animation_check.setChecked(
            settings.get("import.animation.import_animations", True)
        )
        self.import_bone_animation_check.toggled.connect(
            lambda v: settings.set("import.animation.import_animations", v)
        )
        anim_settings_layout.addWidget(self.import_bone_animation_check)

        self.import_morph_animation_check = QCheckBox(self.tr("import_morph_animation", "checkboxes"))
        self.import_morph_animation_check.setChecked(
            settings.get("import.animation.import_morph_animation", True)
        )
        self.import_morph_animation_check.toggled.connect(
            lambda v: settings.set("import.animation.import_morph_animation", v)
        )
        anim_settings_layout.addWidget(self.import_morph_animation_check)

        self.import_camera_animation_check = QCheckBox(self.tr("import_camera_animation", "checkboxes"))
        self.import_camera_animation_check.setChecked(
            settings.get("import.animation.import_camera_animation", True)
        )
        self.import_camera_animation_check.toggled.connect(
            lambda v: settings.set("import.animation.import_camera_animation", v)
        )
        anim_settings_layout.addWidget(self.import_camera_animation_check)

        self.import_light_animation_check = QCheckBox(self.tr("import_light_animation", "checkboxes"))
        self.import_light_animation_check.setChecked(
            settings.get("import.animation.import_light_animation", True)
        )
        self.import_light_animation_check.toggled.connect(
            lambda v: settings.set("import.animation.import_light_animation", v)
        )
        anim_settings_layout.addWidget(self.import_light_animation_check)

        # Resample curves
        self.resample_curves_check = QCheckBox(self.tr("resample_curves", "checkboxes"))
        self.resample_curves_check.setChecked(
            settings.get("import.animation.resample_curves", False)
        )
        self.resample_curves_check.toggled.connect(
            lambda v: settings.set("import.animation.resample_curves", v)
        )
        anim_settings_layout.addWidget(self.resample_curves_check)

        anim_settings_layout.addStretch()
        anim_settings_tab.setWidget(anim_settings_widget)
        self.left_widget.addTab(anim_settings_tab, self.tr("animation", "tabs"))

        # Export Settings Tab
        export_settings_tab = QScrollArea()
        export_settings_tab.setWidgetResizable(True)
        export_settings_tab.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        export_settings_widget = QWidget()
        export_settings_layout = QVBoxLayout(export_settings_widget)

        # Export format
        format_layout = QHBoxLayout()
        self.format_label = QLabel(self.tr("format", "fields"))
        format_layout.addWidget(self.format_label)
        self.export_format_combo = QComboBox()
        self.export_format_combo.addItems(["pmx", "pmd"])
        current_format = settings.get("export.general.export_format", "pmx")
        self.export_format_combo.setCurrentText(current_format)
        self.export_format_combo.currentTextChanged.connect(
            lambda v: settings.set("export.general.export_format", v)
        )
        format_layout.addWidget(self.export_format_combo)
        format_layout.addStretch()
        export_settings_layout.addLayout(format_layout)

        # Apply scale checkbox
        self.apply_scale_check = QCheckBox(self.tr("apply_scale", "checkboxes"))
        self.apply_scale_check.setChecked(
            settings.get("export.general.apply_scale", True)
        )
        self.apply_scale_check.toggled.connect(
            lambda v: settings.set("export.general.apply_scale", v)
        )
        export_settings_layout.addWidget(self.apply_scale_check)

        export_settings_layout.addStretch()
        export_settings_tab.setWidget(export_settings_widget)
        self.left_widget.addTab(export_settings_tab, self.tr("export", "buttons"))

        # 右側：インポート/エクスポートセクション
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Model Import Group (PMX/PMD)
        self.model_import_group = QGroupBox(self.tr("model_import", "groups"))
        model_import_layout = QFormLayout()

        # File path row
        self.import_path_edit = QLineEdit()
        saved_import_path = self.qt_settings.value("import_path", "")
        self.import_path_edit.setText(saved_import_path)
        self.import_path_button = QPushButton(self.tr("browse", "buttons"))
        import_path_layout = QHBoxLayout()
        import_path_layout.addWidget(self.import_path_edit)
        import_path_layout.addWidget(self.import_path_button)
        self.import_path_label = QLabel(self.tr("file_path", "labels"))
        model_import_layout.addRow(self.import_path_label, import_path_layout)

        # Connect signal to save path when changed
        self.import_path_edit.textChanged.connect(
            lambda text: self.qt_settings.setValue("import_path", text)
        )

        # Import button with new file checkbox
        import_button_layout = QHBoxLayout()
        self.import_button = QPushButton(self.tr("import_model", "actions"))
        self.new_file_check = QCheckBox(self.tr("new_file", "checkboxes"))
        import_button_layout.addWidget(self.import_button)
        import_button_layout.addWidget(self.new_file_check)
        import_button_layout.addStretch()
        model_import_layout.addRow(import_button_layout)

        self.model_import_group.setLayout(model_import_layout)
        right_layout.addWidget(self.model_import_group)

        # Animation Import Group (VMD)
        self.animation_group = QGroupBox(self.tr("animation_import", "groups"))
        animation_layout = QFormLayout()

        # VMD file path
        self.vmd_path_edit = QLineEdit()
        saved_vmd_path = self.qt_settings.value("vmd_path", "")
        self.vmd_path_edit.setText(saved_vmd_path)
        self.vmd_path_button = QPushButton(self.tr("browse", "buttons"))
        vmd_path_layout = QHBoxLayout()
        vmd_path_layout.addWidget(self.vmd_path_edit)
        vmd_path_layout.addWidget(self.vmd_path_button)
        self.vmd_file_label = QLabel(self.tr("vmd_file", "fields"))
        animation_layout.addRow(self.vmd_file_label, vmd_path_layout)

        self.vmd_path_edit.textChanged.connect(
            lambda text: self.qt_settings.setValue("vmd_path", text)
        )

        # Target model selection
        self.target_model_combo = QComboBox()
        self.refresh_model_list()
        self.target_model_label = QLabel(self.tr("target_model", "fields"))
        animation_layout.addRow(self.target_model_label, self.target_model_combo)

        self.import_vmd_button = QPushButton(self.tr("import_animation", "actions"))
        animation_layout.addRow(self.import_vmd_button)

        self.animation_group.setLayout(animation_layout)
        right_layout.addWidget(self.animation_group)

        # Export Group
        self.export_group = QGroupBox(self.tr("export", "buttons"))
        export_layout = QFormLayout()

        self.export_path_edit = QLineEdit()
        saved_export_path = self.qt_settings.value("export_path", "")
        self.export_path_edit.setText(saved_export_path)
        self.export_path_button = QPushButton(self.tr("browse", "buttons"))
        export_path_layout = QHBoxLayout()
        export_path_layout.addWidget(self.export_path_edit)
        export_path_layout.addWidget(self.export_path_button)
        self.export_path_label = QLabel(self.tr("file_path", "labels"))
        export_layout.addRow(self.export_path_label, export_path_layout)

        self.export_path_edit.textChanged.connect(
            lambda text: self.qt_settings.setValue("export_path", text)
        )

        self.export_button = QPushButton(self.tr("export", "buttons"))
        export_layout.addRow(self.export_button)

        self.export_group.setLayout(export_layout)
        right_layout.addWidget(self.export_group)

        right_layout.addStretch()

        # スクロールエリアにウィジェットを設定
        right_scroll.setWidget(right_widget)

        # 左側のスクロールエリアにタブウィジェットを設定
        left_scroll.setWidget(self.left_widget)

        # スプリッターに左右のウィジェットを追加
        splitter.addWidget(left_scroll)
        splitter.addWidget(right_scroll)

        # 初期のスプリッター比率を設定（左:右 = 1:2）
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(splitter)

    def refresh_model_list(self):
        """シーン内のMMDモデルリストを更新"""
        self.target_model_combo.clear()
        self.target_model_combo.addItem(self.tr("auto_detect", "actions"))

        try:
            models = find_all_mmd_models()
            for model in models:
                display_name = get_mmd_model_display_name(model)
                self.target_model_combo.addItem(display_name, userData=model)
        except:
            pass
    
    def retranslateUi(self):
        """言語切り替え時にUIを再翻訳"""
        # Labels
        if hasattr(self, 'scale_label'):
            self.scale_label.setText(self.tr("scale_factor", "fields"))
        if hasattr(self, 'root_bone_label'):
            self.root_bone_label.setText(self.tr("root_bone_name", "fields"))
        if hasattr(self, 'texture_search_label'):
            self.texture_search_label.setText(self.tr("texture_search_path", "fields"))
        if hasattr(self, 'uv_set_label'):
            self.uv_set_label.setText(self.tr("uv_set_name", "fields"))
        if hasattr(self, 'start_frame_label'):
            self.start_frame_label.setText(self.tr("start_frame", "fields"))
        if hasattr(self, 'format_label'):
            self.format_label.setText(self.tr("format", "fields"))
        if hasattr(self, 'import_path_label'):
            self.import_path_label.setText(self.tr("file_path", "labels"))
        if hasattr(self, 'vmd_file_label'):
            self.vmd_file_label.setText(self.tr("vmd_file", "fields"))
        if hasattr(self, 'target_model_label'):
            self.target_model_label.setText(self.tr("target_model", "fields"))
        if hasattr(self, 'export_path_label'):
            self.export_path_label.setText(self.tr("file_path", "labels"))
        
        # GroupBoxes
        if hasattr(self, 'general_group'):
            self.general_group.setTitle(self.tr("general", "groups"))
        if hasattr(self, 'model_group'):
            self.model_group.setTitle(self.tr("model", "groups"))
        if hasattr(self, 'morph_physics_group'):
            self.morph_physics_group.setTitle(self.tr("morph_physics", "groups"))
        if hasattr(self, 'other_group'):
            self.other_group.setTitle(self.tr("other", "groups"))
        if hasattr(self, 'model_import_group'):
            self.model_import_group.setTitle(self.tr("model_import", "groups"))
        if hasattr(self, 'animation_group'):
            self.animation_group.setTitle(self.tr("animation_import", "groups"))
        if hasattr(self, 'export_group'):
            self.export_group.setTitle(self.tr("export", "buttons"))
        
        # CheckBoxes
        self.use_namespace_check.setText(self.tr("use_namespace", "checkboxes"))
        self.import_models_check.setText(self.tr("import_models", "checkboxes"))
        self.create_mmd_shaders_check.setText(self.tr("create_mmd_shaders", "checkboxes"))
        self.separate_meshes_check.setText(self.tr("separate_meshes", "checkboxes"))
        self.hide_hidden_geometry_check.setText(self.tr("hide_hidden_geometry", "checkboxes"))
        self.joint_name_conversion_check.setText(self.tr("joint_name_conversion", "checkboxes"))
        self.disable_backface_culling_check.setText(self.tr("disable_backface_culling", "checkboxes"))
        self.import_morphs_check.setText(self.tr("import_morphs", "checkboxes"))
        self.import_physics_check.setText(self.tr("import_physics", "checkboxes"))
        self.create_rigid_bodies_check.setText(self.tr("create_rigid_bodies", "checkboxes"))
        self.create_physics_joints_check.setText(self.tr("create_physics_joints", "checkboxes"))
        self.group_physics_objects_check.setText(self.tr("group_physics_objects", "checkboxes"))
        self.add_semi_standard_bones_check.setText(self.tr("add_semi_standard_bones", "checkboxes"))
        self.translate_names_check.setText(self.tr("translate_names", "checkboxes"))
        self.import_bone_animation_check.setText(self.tr("import_bone_animation", "checkboxes"))
        self.import_morph_animation_check.setText(self.tr("import_morph_animation", "checkboxes"))
        self.import_camera_animation_check.setText(self.tr("import_camera_animation", "checkboxes"))
        self.import_light_animation_check.setText(self.tr("import_light_animation", "checkboxes"))
        self.resample_curves_check.setText(self.tr("resample_curves", "checkboxes"))
        self.apply_scale_check.setText(self.tr("apply_scale", "checkboxes"))
        self.new_file_check.setText(self.tr("new_file", "checkboxes"))
        
        # Buttons
        self.import_path_button.setText(self.tr("browse", "buttons"))
        self.vmd_path_button.setText(self.tr("browse", "buttons"))
        self.export_path_button.setText(self.tr("browse", "buttons"))
        self.import_button.setText(self.tr("import_model", "actions"))
        self.import_vmd_button.setText(self.tr("import_animation", "actions"))
        self.export_button.setText(self.tr("export", "buttons"))
        
        # Tab widget texts
        if hasattr(self, 'left_widget') and self.left_widget.count() >= 3:
            self.left_widget.setTabText(0, self.tr("model", "groups"))
            self.left_widget.setTabText(1, self.tr("animation", "tabs"))
            self.left_widget.setTabText(2, self.tr("export", "buttons"))
        
        # Refresh model list to update auto detect text
        self.refresh_model_list()
