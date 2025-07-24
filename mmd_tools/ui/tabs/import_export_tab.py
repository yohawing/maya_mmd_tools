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
from ...settings import settings
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
        left_widget = QTabWidget()
        
        # Model Import Settings Tab
        model_settings_tab = QScrollArea()
        model_settings_tab.setWidgetResizable(True)
        model_settings_tab.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        model_settings_widget = QWidget()
        model_settings_layout = QVBoxLayout(model_settings_widget)
        
        # Scale factor
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel("Scale Factor:"))
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.001, 1000.0)
        self.scale_spin.setDecimals(3)
        self.scale_spin.setValue(settings.get("import.general.scale_factor", 1.0))
        self.scale_spin.valueChanged.connect(lambda v: settings.set("import.general.scale_factor", v))
        scale_layout.addWidget(self.scale_spin)
        scale_layout.addStretch()
        model_settings_layout.addLayout(scale_layout)
        
        # General Model Settings Group
        general_group = QGroupBox("General")
        general_layout = QVBoxLayout()
        
        # Root bone name
        root_bone_layout = QHBoxLayout()
        root_bone_layout.addWidget(QLabel("Root Bone Name:"))
        self.root_bone_name_edit = QLineEdit(settings.get("import.general.root_bone_name", "master"))
        self.root_bone_name_edit.textChanged.connect(lambda v: settings.set("import.general.root_bone_name", v))
        root_bone_layout.addWidget(self.root_bone_name_edit)
        general_layout.addLayout(root_bone_layout)
        
        self.use_namespace_check = QCheckBox("Use Namespace")
        self.use_namespace_check.setChecked(settings.get("import.general.use_namespace", False))
        self.use_namespace_check.toggled.connect(lambda v: settings.set("import.general.use_namespace", v))
        general_layout.addWidget(self.use_namespace_check)
        
        general_group.setLayout(general_layout)
        model_settings_layout.addWidget(general_group)
        
        # Model Settings Group
        model_group = QGroupBox("Model")
        model_layout = QVBoxLayout()
        
        self.import_models_check = QCheckBox("Import Models")
        self.import_models_check.setChecked(settings.get("import.model.import_models", True))
        self.import_models_check.toggled.connect(lambda v: settings.set("import.model.import_models", v))
        model_layout.addWidget(self.import_models_check)
        
        self.create_mmd_shaders_check = QCheckBox("Create MMD Shaders")
        self.create_mmd_shaders_check.setChecked(settings.get("import.model.create_mmd_shaders", True))
        self.create_mmd_shaders_check.toggled.connect(lambda v: settings.set("import.model.create_mmd_shaders", v))
        model_layout.addWidget(self.create_mmd_shaders_check)
        
        self.separate_meshes_check = QCheckBox("Separate Meshes by Material")
        self.separate_meshes_check.setChecked(settings.get("import.model.separate_meshes_by_material", False))
        self.separate_meshes_check.toggled.connect(lambda v: settings.set("import.model.separate_meshes_by_material", v))
        model_layout.addWidget(self.separate_meshes_check)
        
        self.hide_hidden_geometry_check = QCheckBox("Hide Hidden Geometry")
        self.hide_hidden_geometry_check.setChecked(settings.get("import.model.hide_hidden_geometry", True))
        self.hide_hidden_geometry_check.toggled.connect(lambda v: settings.set("import.model.hide_hidden_geometry", v))
        model_layout.addWidget(self.hide_hidden_geometry_check)
        
        self.joint_name_conversion_check = QCheckBox("Convert Joint Names to English")
        self.joint_name_conversion_check.setChecked(settings.get("import.model.joint_name_conversion_with_english", False))
        self.joint_name_conversion_check.toggled.connect(lambda v: settings.set("import.model.joint_name_conversion_with_english", v))
        model_layout.addWidget(self.joint_name_conversion_check)
        
        self.disable_backface_culling_check = QCheckBox("Disable Backface Culling")
        self.disable_backface_culling_check.setChecked(settings.get("import.model.disable_backface_culling", True))
        self.disable_backface_culling_check.toggled.connect(lambda v: settings.set("import.model.disable_backface_culling", v))
        model_layout.addWidget(self.disable_backface_culling_check)
        
        # Texture search path
        texture_layout = QHBoxLayout()
        texture_layout.addWidget(QLabel("Texture Search Path:"))
        self.texture_search_path_edit = QLineEdit(settings.get("import.model.texture_search_path", ""))
        self.texture_search_path_edit.textChanged.connect(lambda v: settings.set("import.model.texture_search_path", v))
        texture_layout.addWidget(self.texture_search_path_edit)
        model_layout.addLayout(texture_layout)
        
        # UV set name
        uv_layout = QHBoxLayout()
        uv_layout.addWidget(QLabel("UV Set Name:"))
        self.uv_set_name_edit = QLineEdit(settings.get("import.model.uv_set_name", "map#"))
        self.uv_set_name_edit.textChanged.connect(lambda v: settings.set("import.model.uv_set_name", v))
        uv_layout.addWidget(self.uv_set_name_edit)
        uv_layout.addStretch()
        model_layout.addLayout(uv_layout)
        
        model_group.setLayout(model_layout)
        model_settings_layout.addWidget(model_group)
        
        # Morph & Physics Group
        morph_physics_group = QGroupBox("Morph & Physics")
        morph_physics_layout = QVBoxLayout()
        
        self.import_morphs_check = QCheckBox("Import Morphs")
        self.import_morphs_check.setChecked(settings.get("import.morph.import_morphs", True))
        self.import_morphs_check.toggled.connect(lambda v: settings.set("import.morph.import_morphs", v))
        morph_physics_layout.addWidget(self.import_morphs_check)
        
        self.import_physics_check = QCheckBox("Import Physics")
        self.import_physics_check.setChecked(settings.get("import.physics.import_physics", False))
        self.import_physics_check.toggled.connect(lambda v: settings.set("import.physics.import_physics", v))
        morph_physics_layout.addWidget(self.import_physics_check)
        
        self.create_rigid_bodies_check = QCheckBox("Create Rigid Bodies")
        self.create_rigid_bodies_check.setChecked(settings.get("import.physics.create_rigid_bodies", True))
        self.create_rigid_bodies_check.toggled.connect(lambda v: settings.set("import.physics.create_rigid_bodies", v))
        morph_physics_layout.addWidget(self.create_rigid_bodies_check)
        
        self.create_physics_joints_check = QCheckBox("Create Physics Joints")
        self.create_physics_joints_check.setChecked(settings.get("import.physics.create_physics_joints", True))
        self.create_physics_joints_check.toggled.connect(lambda v: settings.set("import.physics.create_physics_joints", v))
        morph_physics_layout.addWidget(self.create_physics_joints_check)
        
        self.group_physics_objects_check = QCheckBox("Group Physics Objects")
        self.group_physics_objects_check.setChecked(settings.get("import.physics.group_physics_objects", True))
        self.group_physics_objects_check.toggled.connect(lambda v: settings.set("import.physics.group_physics_objects", v))
        morph_physics_layout.addWidget(self.group_physics_objects_check)
        
        morph_physics_group.setLayout(morph_physics_layout)
        model_settings_layout.addWidget(morph_physics_group)
        
        # Other Settings Group
        other_group = QGroupBox("Other")
        other_layout = QVBoxLayout()
        
        self.add_semi_standard_bones_check = QCheckBox("Add Semi-Standard Bones")
        self.add_semi_standard_bones_check.setChecked(settings.get("import.rig.add_semi_standard_bones", False))
        self.add_semi_standard_bones_check.toggled.connect(lambda v: settings.set("import.rig.add_semi_standard_bones", v))
        other_layout.addWidget(self.add_semi_standard_bones_check)
        
        self.translate_names_check = QCheckBox("Translate Names")
        self.translate_names_check.setChecked(settings.get("import.naming.translate_names", True))
        self.translate_names_check.toggled.connect(lambda v: settings.set("import.naming.translate_names", v))
        other_layout.addWidget(self.translate_names_check)
        
        other_group.setLayout(other_layout)
        model_settings_layout.addWidget(other_group)
        
        model_settings_layout.addStretch()
        model_settings_tab.setWidget(model_settings_widget)
        left_widget.addTab(model_settings_tab, "Model")
        
        # Animation Import Settings Tab
        anim_settings_tab = QScrollArea()
        anim_settings_tab.setWidgetResizable(True)
        anim_settings_tab.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        anim_settings_widget = QWidget()
        anim_settings_layout = QVBoxLayout(anim_settings_widget)
        
        # Start frame
        frame_layout = QHBoxLayout()
        frame_layout.addWidget(QLabel("Start Frame:"))
        self.animation_start_frame = QSpinBox()
        self.animation_start_frame.setRange(0, 10000)
        self.animation_start_frame.setValue(settings.get("import.animation.animation_start_frame", 1))
        self.animation_start_frame.valueChanged.connect(lambda v: settings.set("import.animation.animation_start_frame", v))
        frame_layout.addWidget(self.animation_start_frame)
        frame_layout.addStretch()
        anim_settings_layout.addLayout(frame_layout)
        
        # Animation type checkboxes
        self.import_bone_animation_check = QCheckBox("Import Bone Animation")
        self.import_bone_animation_check.setChecked(settings.get("import.animation.import_animations", True))
        self.import_bone_animation_check.toggled.connect(lambda v: settings.set("import.animation.import_animations", v))
        anim_settings_layout.addWidget(self.import_bone_animation_check)
        
        self.import_morph_animation_check = QCheckBox("Import Morph Animation")
        self.import_morph_animation_check.setChecked(settings.get("import.animation.import_morph_animation", True))
        self.import_morph_animation_check.toggled.connect(lambda v: settings.set("import.animation.import_morph_animation", v))
        anim_settings_layout.addWidget(self.import_morph_animation_check)
        
        self.import_camera_animation_check = QCheckBox("Import Camera Animation")
        self.import_camera_animation_check.setChecked(settings.get("import.animation.import_camera_animation", True))
        self.import_camera_animation_check.toggled.connect(lambda v: settings.set("import.animation.import_camera_animation", v))
        anim_settings_layout.addWidget(self.import_camera_animation_check)
        
        self.import_light_animation_check = QCheckBox("Import Light Animation")
        self.import_light_animation_check.setChecked(settings.get("import.animation.import_light_animation", True))
        self.import_light_animation_check.toggled.connect(lambda v: settings.set("import.animation.import_light_animation", v))
        anim_settings_layout.addWidget(self.import_light_animation_check)
        
        # Resample curves
        self.resample_curves_check = QCheckBox("Resample Animation Curves")
        self.resample_curves_check.setChecked(settings.get("import.animation.resample_curves", False))
        self.resample_curves_check.toggled.connect(lambda v: settings.set("import.animation.resample_curves", v))
        anim_settings_layout.addWidget(self.resample_curves_check)
        
        anim_settings_layout.addStretch()
        anim_settings_tab.setWidget(anim_settings_widget)
        left_widget.addTab(anim_settings_tab, "Animation")
        
        # Export Settings Tab
        export_settings_tab = QScrollArea()
        export_settings_tab.setWidgetResizable(True)
        export_settings_tab.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        export_settings_widget = QWidget()
        export_settings_layout = QVBoxLayout(export_settings_widget)
        
        # Export format
        format_layout = QHBoxLayout()
        format_layout.addWidget(QLabel("Format:"))
        self.export_format_combo = QComboBox()
        self.export_format_combo.addItems(["pmx", "pmd"])
        current_format = settings.get("export.general.export_format", "pmx")
        self.export_format_combo.setCurrentText(current_format)
        self.export_format_combo.currentTextChanged.connect(lambda v: settings.set("export.general.export_format", v))
        format_layout.addWidget(self.export_format_combo)
        format_layout.addStretch()
        export_settings_layout.addLayout(format_layout)
        
        # Apply scale checkbox
        self.apply_scale_check = QCheckBox("Apply Scale")
        self.apply_scale_check.setChecked(settings.get("export.general.apply_scale", True))
        self.apply_scale_check.toggled.connect(lambda v: settings.set("export.general.apply_scale", v))
        export_settings_layout.addWidget(self.apply_scale_check)
        
        export_settings_layout.addStretch()
        export_settings_tab.setWidget(export_settings_widget)
        left_widget.addTab(export_settings_tab, "Export")
        
        # 右側：インポート/エクスポートセクション
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        
        # Model Import Group (PMX/PMD)
        model_import_group = QGroupBox("Model Import (PMX/PMD)")
        model_import_layout = QFormLayout()
        
        # File path row
        self.import_path_edit = QLineEdit()
        saved_import_path = self.qt_settings.value("import_path", "")
        self.import_path_edit.setText(saved_import_path)
        self.import_path_button = QPushButton("...")
        import_path_layout = QHBoxLayout()
        import_path_layout.addWidget(self.import_path_edit)
        import_path_layout.addWidget(self.import_path_button)
        model_import_layout.addRow("File Path:", import_path_layout)
        
        # Connect signal to save path when changed
        self.import_path_edit.textChanged.connect(lambda text: self.qt_settings.setValue("import_path", text))

        # Import button with new file checkbox
        import_button_layout = QHBoxLayout()
        self.import_button = QPushButton("Import Model")
        self.new_file_check = QCheckBox("New File")
        import_button_layout.addWidget(self.import_button)
        import_button_layout.addWidget(self.new_file_check)
        import_button_layout.addStretch()
        model_import_layout.addRow(import_button_layout)

        model_import_group.setLayout(model_import_layout)
        right_layout.addWidget(model_import_group)
        
        # Animation Import Group (VMD)
        animation_group = QGroupBox("Animation Import (VMD)")
        animation_layout = QFormLayout()
        
        # VMD file path
        self.vmd_path_edit = QLineEdit()
        saved_vmd_path = self.qt_settings.value("vmd_path", "")
        self.vmd_path_edit.setText(saved_vmd_path)
        self.vmd_path_button = QPushButton("...")
        vmd_path_layout = QHBoxLayout()
        vmd_path_layout.addWidget(self.vmd_path_edit)
        vmd_path_layout.addWidget(self.vmd_path_button)
        animation_layout.addRow("VMD File:", vmd_path_layout)
        
        self.vmd_path_edit.textChanged.connect(lambda text: self.qt_settings.setValue("vmd_path", text))
        
        # Target model selection
        self.target_model_combo = QComboBox()
        self.refresh_model_list()
        animation_layout.addRow("Target Model:", self.target_model_combo)
        
        self.import_vmd_button = QPushButton("Import Animation")
        animation_layout.addRow(self.import_vmd_button)
        
        animation_group.setLayout(animation_layout)
        right_layout.addWidget(animation_group)

        # Export Group
        export_group = QGroupBox("Export")
        export_layout = QFormLayout()

        self.export_path_edit = QLineEdit()
        saved_export_path = self.qt_settings.value("export_path", "")
        self.export_path_edit.setText(saved_export_path)
        self.export_path_button = QPushButton("...")
        export_path_layout = QHBoxLayout()
        export_path_layout.addWidget(self.export_path_edit)
        export_path_layout.addWidget(self.export_path_button)
        export_layout.addRow("File Path:", export_path_layout)
        
        self.export_path_edit.textChanged.connect(lambda text: self.qt_settings.setValue("export_path", text))

        self.export_button = QPushButton("Export")
        export_layout.addRow(self.export_button)

        export_group.setLayout(export_layout)
        right_layout.addWidget(export_group)

        right_layout.addStretch()
        
        # スクロールエリアにウィジェットを設定
        right_scroll.setWidget(right_widget)
        
        # 左側のスクロールエリアにタブウィジェットを設定
        left_scroll.setWidget(left_widget)
        
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
        self.target_model_combo.addItem("<Auto Detect>")
        
        try:
            models = find_all_mmd_models()
            for model in models:
                display_name = get_mmd_model_display_name(model)
                self.target_model_combo.addItem(display_name, userData=model)
        except:
            pass