from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QSettings,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QLabel,
    QScrollArea,
    Qt,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QColor,
)
from ..base_tab import BaseTab
from ...core.settings import settings
import os
import json


def _format_target_model_label(model_root, display_name):
    """VMD import target combo に表示するモデル名を返す。"""
    display_name = display_name or model_root
    if ":" not in model_root:
        return display_name

    namespace = model_root.rsplit(":", 1)[0]
    if "|" in namespace:
        namespace = namespace.split("|")[-1]
    root_name = model_root.rsplit(":", 1)[-1]
    return f"{display_name} [{namespace}:{root_name}]"


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

        # 左側：設定セクション（フラット）
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.left_widget = QWidget()
        model_settings_layout = QVBoxLayout(self.left_widget)

        # Scale factor
        scale_layout = QHBoxLayout()
        self.scale_label = QLabel(self.tr("scale_factor", "fields"))
        scale_layout.addWidget(self.scale_label)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.001, 1000.0)
        self.scale_spin.setDecimals(3)
        self.scale_spin.setValue(settings.get("import.general.scale_factor", 1.0))
        self.scale_spin.valueChanged.connect(lambda v: settings.set("import.general.scale_factor", v))
        self.scale_spin.setToolTip(self.tr("scale_factor", "tooltips"))
        scale_layout.addWidget(self.scale_spin)
        scale_layout.addStretch()
        model_settings_layout.addLayout(scale_layout)

        # General Model Settings Group
        self.general_group = QGroupBox(self.tr("general", "groups"))
        general_layout = QVBoxLayout()

        self.use_namespace_check = QCheckBox(self.tr("use_namespace", "checkboxes"))
        self.use_namespace_check.setChecked(settings.get("import.general.use_namespace", False))
        self.use_namespace_check.toggled.connect(lambda v: settings.set("import.general.use_namespace", v))
        self.use_namespace_check.setToolTip(self.tr("use_namespace", "tooltips"))
        general_layout.addWidget(self.use_namespace_check)

        # Namespace名を手動で指定するオプション
        namespace_layout = QHBoxLayout()
        namespace_layout.setContentsMargins(20, 0, 0, 0)  # インデント

        self.custom_namespace_check = QCheckBox(self.tr("custom_namespace", "checkboxes"))
        # カスタムnamespaceチェックボックスの状態を読み込み
        saved_custom_namespace = self.qt_settings.value("custom_namespace_check", "false")
        self.custom_namespace_check.setChecked(saved_custom_namespace.lower() == "true")
        self.custom_namespace_check.setEnabled(self.use_namespace_check.isChecked())
        # 状態が変更されたら保存
        self.custom_namespace_check.toggled.connect(
            lambda checked: self.qt_settings.setValue("custom_namespace_check", str(checked))
        )
        namespace_layout.addWidget(self.custom_namespace_check)

        self.namespace_edit = QLineEdit()
        self.namespace_edit.setPlaceholderText(self.tr("namespace_placeholder", "labels"))
        # namespace名を読み込み
        saved_namespace = self.qt_settings.value("custom_namespace_name", "")
        self.namespace_edit.setText(saved_namespace)
        self.namespace_edit.setEnabled(self.custom_namespace_check.isChecked())
        # テキストが変更されたら保存
        self.namespace_edit.textChanged.connect(lambda text: self.qt_settings.setValue("custom_namespace_name", text))
        namespace_layout.addWidget(self.namespace_edit)

        # シグナル接続
        self.use_namespace_check.toggled.connect(self.custom_namespace_check.setEnabled)
        self.custom_namespace_check.toggled.connect(self.namespace_edit.setEnabled)

        general_layout.addLayout(namespace_layout)

        self.general_group.setLayout(general_layout)
        model_settings_layout.addWidget(self.general_group)

        # Model Settings Group
        self.model_group = QGroupBox(self.tr("model", "groups"))
        model_layout = QVBoxLayout()

        self.import_models_check = QCheckBox(self.tr("import_models", "checkboxes"))
        self.import_models_check.setChecked(settings.get("import.model.import_models", True))
        self.import_models_check.toggled.connect(lambda v: settings.set("import.model.import_models", v))
        model_layout.addWidget(self.import_models_check)

        self.create_mmd_shaders_check = QCheckBox(self.tr("create_mmd_shaders", "checkboxes"))
        self.create_mmd_shaders_check.setChecked(settings.get("import.model.create_mmd_shaders", True))
        self.create_mmd_shaders_check.toggled.connect(lambda v: settings.set("import.model.create_mmd_shaders", v))
        self.create_mmd_shaders_check.setToolTip(self.tr("create_mmd_shaders", "tooltips"))
        model_layout.addWidget(self.create_mmd_shaders_check)

        self.separate_meshes_check = QCheckBox(self.tr("separate_meshes", "checkboxes"))
        self.separate_meshes_check.setChecked(settings.get("import.model.separate_meshes_by_material", False))
        self.separate_meshes_check.toggled.connect(lambda v: settings.set("import.model.separate_meshes_by_material", v))
        self.separate_meshes_check.setToolTip(self.tr("separate_meshes", "tooltips"))
        model_layout.addWidget(self.separate_meshes_check)

        self.split_by_morph_groups_check = QCheckBox(self.tr("split_meshes_by_morph_groups", "checkboxes"))
        self.split_by_morph_groups_check.setChecked(settings.get("import.model.split_meshes_by_morph_groups", False))
        self.split_by_morph_groups_check.toggled.connect(lambda v: settings.set("import.model.split_meshes_by_morph_groups", v))
        self.split_by_morph_groups_check.setToolTip(self.tr("split_by_morph_groups", "tooltips"))
        model_layout.addWidget(self.split_by_morph_groups_check)

        # Auto-classify transparency (opt-in): scan each material's used-UV texture
        # alpha to assign cutout/blend. Off by default -> materials import opaque
        # and the user assigns blend manually in the Material tab.
        self.auto_classify_transparency_check = QCheckBox(self.tr("auto_classify_transparency", "checkboxes"))
        self.auto_classify_transparency_check.setChecked(
            settings.get("import.model.auto_classify_transparency", False)
        )
        self.auto_classify_transparency_check.toggled.connect(
            lambda v: settings.set("import.model.auto_classify_transparency", v)
        )
        self.auto_classify_transparency_check.setToolTip(self.tr("auto_classify_transparency", "tooltips"))
        model_layout.addWidget(self.auto_classify_transparency_check)

        self.auto_resolve_textures_check = QCheckBox(self.tr("auto_resolve_textures", "checkboxes"))
        self.auto_resolve_textures_check.setChecked(settings.get("import.model.auto_resolve_textures", True))
        self.auto_resolve_textures_check.toggled.connect(
            lambda v: settings.set("import.model.auto_resolve_textures", v)
        )
        self.auto_resolve_textures_check.setToolTip(self.tr("auto_resolve_textures", "tooltips"))
        model_layout.addWidget(self.auto_resolve_textures_check)

        self.transparency_threshold_row = QWidget()
        transparency_threshold_layout = QHBoxLayout(self.transparency_threshold_row)
        transparency_threshold_layout.setContentsMargins(0, 0, 0, 0)
        self.transparency_threshold_label = QLabel(self.tr("transparency_opaque_threshold", "fields"))
        self.transparency_threshold_spin = QSpinBox()
        self.transparency_threshold_spin.setRange(0, 255)
        self.transparency_threshold_spin.setValue(
            int(settings.get("import.model.transparency_opaque_threshold", 255))
        )
        self.transparency_threshold_spin.valueChanged.connect(
            lambda v: settings.set("import.model.transparency_opaque_threshold", int(v))
        )
        transparency_threshold_layout.addWidget(self.transparency_threshold_label)
        transparency_threshold_layout.addWidget(self.transparency_threshold_spin)
        transparency_threshold_layout.addStretch()
        model_layout.addWidget(self.transparency_threshold_row)

        self.hide_hidden_geometry_check = QCheckBox(self.tr("hide_hidden_geometry", "checkboxes"))
        self.hide_hidden_geometry_check.setChecked(settings.get("import.model.hide_hidden_geometry", True))
        self.hide_hidden_geometry_check.toggled.connect(lambda v: settings.set("import.model.hide_hidden_geometry", v))
        self.hide_hidden_geometry_check.setToolTip(self.tr("hide_hidden_geometry", "tooltips"))
        model_layout.addWidget(self.hide_hidden_geometry_check)

        self.disable_backface_culling_check = QCheckBox(self.tr("disable_backface_culling", "checkboxes"))
        self.disable_backface_culling_check.setChecked(settings.get("import.model.disable_backface_culling", True))
        self.disable_backface_culling_check.toggled.connect(lambda v: settings.set("import.model.disable_backface_culling", v))
        self.disable_backface_culling_check.setToolTip(self.tr("disable_backface_culling", "tooltips"))
        model_layout.addWidget(self.disable_backface_culling_check)

        # Texture search path
        self.texture_row = QWidget()
        texture_layout = QHBoxLayout(self.texture_row)
        texture_layout.setContentsMargins(0, 0, 0, 0)
        self.texture_search_label = QLabel(self.tr("texture_search_path", "fields"))
        texture_layout.addWidget(self.texture_search_label)
        self.texture_search_path_edit = QLineEdit(settings.get("import.model.texture_search_path", ""))
        self.texture_search_path_edit.textChanged.connect(lambda v: settings.set("import.model.texture_search_path", v))
        texture_layout.addWidget(self.texture_search_path_edit)
        model_layout.addWidget(self.texture_row)

        # UV set name
        self.uv_row = QWidget()
        uv_layout = QHBoxLayout(self.uv_row)
        uv_layout.setContentsMargins(0, 0, 0, 0)
        self.uv_set_label = QLabel(self.tr("uv_set_name", "fields"))
        uv_layout.addWidget(self.uv_set_label)
        self.uv_set_name_edit = QLineEdit(settings.get("import.model.uv_set_name", "map#"))
        self.uv_set_name_edit.textChanged.connect(lambda v: settings.set("import.model.uv_set_name", v))
        uv_layout.addWidget(self.uv_set_name_edit)
        uv_layout.addStretch()
        model_layout.addWidget(self.uv_row)

        self.model_group.setLayout(model_layout)
        model_settings_layout.addWidget(self.model_group)

        # Morph & Physics Group
        self.morph_physics_group = QGroupBox(self.tr("morph_physics", "groups"))
        morph_physics_layout = QVBoxLayout()

        self.import_morphs_check = QCheckBox(self.tr("import_morphs", "checkboxes"))
        self.import_morphs_check.setChecked(settings.get("import.morph.import_morphs", True))
        self.import_morphs_check.toggled.connect(lambda v: settings.set("import.morph.import_morphs", v))
        morph_physics_layout.addWidget(self.import_morphs_check)

        self.import_physics_check = QCheckBox(self.tr("import_physics", "checkboxes"))
        self.import_physics_check.setChecked(settings.get("import.physics.import_physics", False))
        self.import_physics_check.toggled.connect(lambda v: settings.set("import.physics.import_physics", v))
        self.import_physics_check.setToolTip(self.tr("import_physics", "tooltips"))
        morph_physics_layout.addWidget(self.import_physics_check)

        self.create_rigid_bodies_check = QCheckBox(self.tr("create_rigid_bodies", "checkboxes"))
        self.create_rigid_bodies_check.setChecked(settings.get("import.physics.create_rigid_bodies", True))
        self.create_rigid_bodies_check.toggled.connect(lambda v: settings.set("import.physics.create_rigid_bodies", v))
        morph_physics_layout.addWidget(self.create_rigid_bodies_check)

        self.create_physics_joints_check = QCheckBox(self.tr("create_physics_joints", "checkboxes"))
        self.create_physics_joints_check.setChecked(settings.get("import.physics.create_physics_joints", True))
        self.create_physics_joints_check.toggled.connect(lambda v: settings.set("import.physics.create_physics_joints", v))
        morph_physics_layout.addWidget(self.create_physics_joints_check)

        self.group_physics_objects_check = QCheckBox(self.tr("group_physics_objects", "checkboxes"))
        self.group_physics_objects_check.setChecked(settings.get("import.physics.group_physics_objects", True))
        self.group_physics_objects_check.toggled.connect(lambda v: settings.set("import.physics.group_physics_objects", v))
        morph_physics_layout.addWidget(self.group_physics_objects_check)

        self.morph_physics_group.setLayout(morph_physics_layout)
        model_settings_layout.addWidget(self.morph_physics_group)

        # Other Settings Group
        self.other_group = QGroupBox(self.tr("other", "groups"))
        other_layout = QVBoxLayout()

        self.add_semi_standard_bones_check = QCheckBox(self.tr("add_semi_standard_bones", "checkboxes"))
        self.add_semi_standard_bones_check.setChecked(settings.get("import.rig.add_semi_standard_bones", False))
        self.add_semi_standard_bones_check.toggled.connect(lambda v: settings.set("import.rig.add_semi_standard_bones", v))
        self.add_semi_standard_bones_check.setToolTip(self.tr("add_semi_standard_bones", "tooltips"))
        other_layout.addWidget(self.add_semi_standard_bones_check)

        self.translate_names_check = QCheckBox(self.tr("translate_names", "checkboxes"))
        self.translate_names_check.setChecked(settings.get("import.naming.translate_names", True))
        self.translate_names_check.toggled.connect(lambda v: settings.set("import.naming.translate_names", v))
        self.translate_names_check.setToolTip(self.tr("translate_names", "tooltips"))
        other_layout.addWidget(self.translate_names_check)

        self.use_cpp_rig_nodes_check = QCheckBox(self.tr("use_cpp_rig_nodes", "checkboxes"))
        self.use_cpp_rig_nodes_check.setChecked(settings.get("import.native.use_cpp_rig_nodes", False))
        self.use_cpp_rig_nodes_check.toggled.connect(
            lambda v: settings.set("import.native.use_cpp_rig_nodes", v)
        )
        self.use_cpp_rig_nodes_check.setToolTip(self.tr("use_cpp_rig_nodes", "tooltips"))
        other_layout.addWidget(self.use_cpp_rig_nodes_check)

        self.other_group.setLayout(other_layout)

        # Animation Import Settings
        self.animation_settings_group = QGroupBox(self.tr("animation", "tabs"))
        anim_settings_layout = QVBoxLayout()

        # Start frame
        frame_layout = QHBoxLayout()
        self.start_frame_label = QLabel(self.tr("start_frame", "fields"))
        frame_layout.addWidget(self.start_frame_label)
        self.animation_start_frame = QSpinBox()
        self.animation_start_frame.setRange(0, 10000)
        self.animation_start_frame.setValue(settings.get("import.animation.animation_start_frame", 1))
        self.animation_start_frame.valueChanged.connect(lambda v: settings.set("import.animation.animation_start_frame", v))
        self.animation_start_frame.setToolTip(self.tr("start_frame", "tooltips"))
        frame_layout.addWidget(self.animation_start_frame)
        frame_layout.addStretch()
        anim_settings_layout.addLayout(frame_layout)

        # VMD FPS (Maya scene time unit for VMD import; VMD has no FPS metadata)
        fps_layout = QHBoxLayout()
        self.vmd_fps_label = QLabel(self.tr("vmd_fps", "fields"))
        fps_layout.addWidget(self.vmd_fps_label)
        self.vmd_fps_combo = QComboBox()
        self.vmd_fps_combo.addItems(["30", "60"])
        vmd_fps_val = settings.get("import.animation.vmd_fps", 30)
        try:
            vmd_fps_int = int(vmd_fps_val)
        except (TypeError, ValueError):
            vmd_fps_int = 30
        if vmd_fps_int not in (30, 60):
            vmd_fps_int = 30
            settings.set("import.animation.vmd_fps", 30)
        self.vmd_fps_combo.setCurrentText(str(vmd_fps_int))
        self.vmd_fps_combo.currentTextChanged.connect(
            lambda v: settings.set("import.animation.vmd_fps", int(v))
        )
        self.vmd_fps_combo.setToolTip(self.tr("vmd_fps", "tooltips"))
        fps_layout.addWidget(self.vmd_fps_combo)
        fps_layout.addStretch()
        anim_settings_layout.addLayout(fps_layout)

        # Motion scale
        motion_scale_layout = QHBoxLayout()
        self.motion_scale_label = QLabel(self.tr("motion_scale", "fields"))
        motion_scale_layout.addWidget(self.motion_scale_label)
        self.motion_scale_spin = QDoubleSpinBox()
        self.motion_scale_spin.setRange(0.001, 1000.0)
        self.motion_scale_spin.setDecimals(3)
        self.motion_scale_spin.setSingleStep(0.1)
        self.motion_scale_spin.setValue(settings.get("import.animation.motion_scale", 1.0))
        self.motion_scale_spin.valueChanged.connect(lambda v: settings.set("import.animation.motion_scale", v))
        self.motion_scale_spin.setToolTip(self.tr("motion_scale", "tooltips"))
        motion_scale_layout.addWidget(self.motion_scale_spin)
        motion_scale_layout.addStretch()
        anim_settings_layout.addLayout(motion_scale_layout)

        self.bake_mode_check = QCheckBox(self.tr("bake_mode", "checkboxes"))
        self.bake_mode_check.setChecked(settings.get("import.rig.bake_mode", False))
        self.bake_mode_check.toggled.connect(
            lambda v: settings.set("import.rig.bake_mode", v)
        )
        self.bake_mode_check.setToolTip(self.tr("bake_mode", "tooltips"))
        anim_settings_layout.addWidget(self.bake_mode_check)

        self.clear_existing_motion_check = QCheckBox(self.tr("clear_existing_motion", "checkboxes"))
        self.clear_existing_motion_check.setChecked(settings.get("import.animation.clear_existing_motion", False))
        self.clear_existing_motion_check.toggled.connect(
            lambda v: settings.set("import.animation.clear_existing_motion", v)
        )
        self.clear_existing_motion_check.setToolTip(self.tr("clear_existing_motion", "tooltips"))
        anim_settings_layout.addWidget(self.clear_existing_motion_check)

        # Animation type checkboxes
        self.import_bone_animation_check = QCheckBox(self.tr("import_bone_animation", "checkboxes"))
        self.import_bone_animation_check.setChecked(settings.get("import.animation.import_animations", True))
        self.import_bone_animation_check.toggled.connect(lambda v: settings.set("import.animation.import_animations", v))
        self.import_bone_animation_check.setToolTip(self.tr("import_bone_animation", "tooltips"))
        anim_settings_layout.addWidget(self.import_bone_animation_check)

        self.import_morph_animation_check = QCheckBox(self.tr("import_morph_animation", "checkboxes"))
        self.import_morph_animation_check.setChecked(settings.get("import.animation.import_morph_animation", True))
        self.import_morph_animation_check.toggled.connect(lambda v: settings.set("import.animation.import_morph_animation", v))
        self.import_morph_animation_check.setToolTip(self.tr("import_morph_animation", "tooltips"))
        anim_settings_layout.addWidget(self.import_morph_animation_check)

        self.import_camera_animation_check = QCheckBox(self.tr("import_camera_animation", "checkboxes"))
        self.import_camera_animation_check.setChecked(settings.get("import.animation.import_camera_animation", True))
        self.import_camera_animation_check.toggled.connect(
            lambda v: settings.set("import.animation.import_camera_animation", v)
        )
        self.import_camera_animation_check.setToolTip(self.tr("import_camera_animation", "tooltips"))
        anim_settings_layout.addWidget(self.import_camera_animation_check)

        self.import_light_animation_check = QCheckBox(self.tr("import_light_animation", "checkboxes"))
        self.import_light_animation_check.setChecked(settings.get("import.animation.import_light_animation", True))
        self.import_light_animation_check.toggled.connect(lambda v: settings.set("import.animation.import_light_animation", v))
        self.import_light_animation_check.setToolTip(self.tr("import_light_animation", "tooltips"))
        anim_settings_layout.addWidget(self.import_light_animation_check)

        # Resample curves
        self.resample_curves_check = QCheckBox(self.tr("resample_curves", "checkboxes"))
        self.resample_curves_check.setChecked(settings.get("import.animation.resample_curves", False))
        self.resample_curves_check.toggled.connect(lambda v: settings.set("import.animation.resample_curves", v))
        self.resample_curves_check.setToolTip(self.tr("resample_curves", "tooltips"))
        anim_settings_layout.addWidget(self.resample_curves_check)

        self.animation_settings_group.setLayout(anim_settings_layout)
        model_settings_layout.addWidget(self.animation_settings_group)

        # Other group at the bottom (dev-only)
        model_settings_layout.addWidget(self.other_group)

        model_settings_layout.addStretch()

        # Export Settings
        self._export_settings_tab = QScrollArea()
        self._export_settings_tab.setWidgetResizable(True)
        self._export_settings_tab.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        export_settings_widget = QWidget()
        export_settings_layout = QVBoxLayout(export_settings_widget)

        # Export format
        format_layout = QHBoxLayout()
        self.format_label = QLabel(self.tr("format", "fields"))
        format_layout.addWidget(self.format_label)
        self.export_format_combo = QComboBox()
        # PMD エクスポートは PmdExporter が未実装のため選択肢に出さない。
        self.export_format_combo.addItems(["pmx", "vmd"])
        current_format = settings.get("export.general.export_format", "pmx")
        self.export_format_combo.setCurrentText(current_format)
        self.export_format_combo.currentTextChanged.connect(self._on_export_format_changed)
        format_layout.addWidget(self.export_format_combo)
        format_layout.addStretch()
        export_settings_layout.addLayout(format_layout)

        # Apply scale checkbox
        self.apply_scale_check = QCheckBox(self.tr("apply_scale", "checkboxes"))
        self.apply_scale_check.setChecked(settings.get("export.general.apply_scale", True))
        self.apply_scale_check.toggled.connect(lambda v: settings.set("export.general.apply_scale", v))
        export_settings_layout.addWidget(self.apply_scale_check)

        export_settings_layout.addStretch()
        self._export_settings_tab.setWidget(export_settings_widget)
        model_settings_layout.addWidget(self._export_settings_tab)

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
        self.import_path_edit.textChanged.connect(lambda text: self.qt_settings.setValue("import_path", text))

        # Import button with new file checkbox
        import_button_layout = QHBoxLayout()
        self.import_button = QPushButton(self.tr("import_model", "actions"))
        self.new_file_check = QCheckBox(self.tr("new_file", "checkboxes"))
        # NewFileチェックボックスの状態を読み込み
        saved_new_file = self.qt_settings.value("new_file_check", "false")
        self.new_file_check.setChecked(saved_new_file.lower() == "true")
        # 状態が変更されたら保存
        self.new_file_check.toggled.connect(lambda checked: self.qt_settings.setValue("new_file_check", str(checked)))
        import_button_layout.addWidget(self.import_button)
        self.fix_texture_path_button = QPushButton(self.tr("fix_texture_path", "texture_issues"))
        import_button_layout.addWidget(self.fix_texture_path_button)
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

        self.vmd_path_edit.textChanged.connect(lambda text: self.qt_settings.setValue("vmd_path", text))

        # Target model selection
        self.target_model_combo = QComboBox()
        # モデルリストを更新してから保存された選択を復元
        self.refresh_model_list(restore_selection=True)
        # 選択が変更されたら保存
        self.target_model_combo.currentIndexChanged.connect(
            lambda index: self.qt_settings.setValue("target_model_index", str(index))
        )
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

        self.export_path_edit.textChanged.connect(lambda text: self.qt_settings.setValue("export_path", text))

        self.export_button = QPushButton(self.tr("export", "buttons"))
        export_layout.addRow(self.export_button)

        self.export_group.setLayout(export_layout)
        right_layout.addWidget(self.export_group)

        # 統合履歴表示エリア
        self._setup_unified_history_area(right_layout)

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

        self._apply_export_visibility()
        # import_models is always ON in behavior; checkbox removed from UI.
        self.import_models_check.setVisible(False)

        # Dev-only controls: shown only when development_mode=True.
        self._dev_only_widgets = [
            self.separate_meshes_check,
            self.split_by_morph_groups_check,
            self.auto_classify_transparency_check,
            self.transparency_threshold_row,
            self.hide_hidden_geometry_check,
            self.disable_backface_culling_check,
            self.texture_row,
            self.uv_row,
            self.import_physics_check,
            self.create_rigid_bodies_check,
            self.create_physics_joints_check,
            self.group_physics_objects_check,
            self.morph_physics_group,
            self.other_group,
            self.use_cpp_rig_nodes_check,
            self.resample_curves_check,
            self.import_bone_animation_check,
            self.import_morph_animation_check,
            self.import_camera_animation_check,
            self.import_light_animation_check,
        ]
        self._apply_dev_mode_visibility()

    def _apply_dev_mode_visibility(self):
        """dev-only UI controls の表示/非表示を development_mode 設定に合わせる。"""
        is_dev = settings.get("ui.general.development_mode", False)
        for widget in self._dev_only_widgets:
            widget.setVisible(is_dev)

    def _on_export_format_changed(self, export_format):
        """エクスポート形式を保存し、利用可能な export UI だけを表示する。"""
        settings.set("export.general.export_format", export_format)
        self._apply_export_visibility()

    def _apply_export_visibility(self):
        """VMD export 実装済みのときだけ export 操作群を表示する。"""
        if hasattr(self, "export_group"):
            self.export_group.setVisible(settings.get("export.general.export_format", "pmx") == "vmd")

    def set_target_model_items(self, model_items, restore_selection=False):
        """Presenter から渡されたモデル候補で target combo を更新する。"""
        # 現在の選択を保持
        current_index = self.target_model_combo.currentIndex() if not restore_selection else -1
        saved_index = None
        if restore_selection:
            saved_target_model = self.qt_settings.value("target_model_index", 0)
            try:
                saved_index = int(saved_target_model)
            except Exception:
                saved_index = None

        previous_signal_state = None
        if hasattr(self.target_model_combo, "blockSignals"):
            previous_signal_state = self.target_model_combo.blockSignals(True)
        try:
            self.target_model_combo.clear()
            self.target_model_combo.addItem(self.tr("auto_detect", "actions"))

            for model_root, display_name in model_items:
                self.target_model_combo.addItem(_format_target_model_label(model_root, display_name), userData=model_root)

            # 保存された選択または現在の選択を復元
            if restore_selection:
                if saved_index is not None and 0 <= saved_index < self.target_model_combo.count():
                    self.target_model_combo.setCurrentIndex(saved_index)
            elif 0 <= current_index < self.target_model_combo.count():
                self.target_model_combo.setCurrentIndex(current_index)
        finally:
            if previous_signal_state is not None:
                self.target_model_combo.blockSignals(previous_signal_state)

    def refresh_model_list(self, restore_selection=False):
        """シーン内のMMDモデルリストを更新"""
        presenter = getattr(self, "presenter", None)
        if presenter is not None:
            presenter.refresh_model_list(restore_selection=restore_selection)
            return
        self.set_target_model_items([], restore_selection=restore_selection)

    def get_custom_namespace(self):
        """カスタムnamespace名を取得"""
        if (
            self.use_namespace_check.isChecked()
            and self.custom_namespace_check.isChecked()
            and self.namespace_edit.text().strip()
        ):
            return self.namespace_edit.text().strip()
        return None

    def retranslateUi(self):
        """言語切り替え時にUIを再翻訳"""
        # Labels
        if hasattr(self, "scale_label"):
            self.scale_label.setText(self.tr("scale_factor", "fields"))
        if hasattr(self, "texture_search_label"):
            self.texture_search_label.setText(self.tr("texture_search_path", "fields"))
        if hasattr(self, "uv_set_label"):
            self.uv_set_label.setText(self.tr("uv_set_name", "fields"))
        if hasattr(self, "start_frame_label"):
            self.start_frame_label.setText(self.tr("start_frame", "fields"))
        if hasattr(self, "vmd_fps_label"):
            self.vmd_fps_label.setText(self.tr("vmd_fps", "fields"))
        if hasattr(self, "motion_scale_label"):
            self.motion_scale_label.setText(self.tr("motion_scale", "fields"))
        if hasattr(self, "transparency_threshold_label"):
            self.transparency_threshold_label.setText(self.tr("transparency_opaque_threshold", "fields"))
        if hasattr(self, "format_label"):
            self.format_label.setText(self.tr("format", "fields"))
        if hasattr(self, "import_path_label"):
            self.import_path_label.setText(self.tr("file_path", "labels"))
        if hasattr(self, "vmd_file_label"):
            self.vmd_file_label.setText(self.tr("vmd_file", "fields"))
        if hasattr(self, "target_model_label"):
            self.target_model_label.setText(self.tr("target_model", "fields"))
        if hasattr(self, "export_path_label"):
            self.export_path_label.setText(self.tr("file_path", "labels"))


        # GroupBoxes
        if hasattr(self, "general_group"):
            self.general_group.setTitle(self.tr("general", "groups"))
        if hasattr(self, "model_group"):
            self.model_group.setTitle(self.tr("model", "groups"))
        if hasattr(self, "morph_physics_group"):
            self.morph_physics_group.setTitle(self.tr("morph_physics", "groups"))
        if hasattr(self, "other_group"):
            self.other_group.setTitle(self.tr("other", "groups"))
        if hasattr(self, "model_import_group"):
            self.model_import_group.setTitle(self.tr("model_import", "groups"))
        if hasattr(self, "animation_group"):
            self.animation_group.setTitle(self.tr("animation_import", "groups"))
        if hasattr(self, "export_group"):
            self.export_group.setTitle(self.tr("export", "buttons"))
        if hasattr(self, "history_group"):
            self.history_group.setTitle(self.tr("file_history", "groups"))
        if hasattr(self, "clear_history_button"):
            self.clear_history_button.setText(self.tr("clear_history", "buttons"))

        # CheckBoxes
        self.use_namespace_check.setText(self.tr("use_namespace", "checkboxes"))
        self.custom_namespace_check.setText(self.tr("custom_namespace", "checkboxes"))
        self.namespace_edit.setPlaceholderText(self.tr("namespace_placeholder", "labels"))
        self.import_models_check.setText(self.tr("import_models", "checkboxes"))
        self.create_mmd_shaders_check.setText(self.tr("create_mmd_shaders", "checkboxes"))
        self.separate_meshes_check.setText(self.tr("separate_meshes", "checkboxes"))
        self.split_by_morph_groups_check.setText(self.tr("split_meshes_by_morph_groups", "checkboxes"))
        if hasattr(self, "auto_classify_transparency_check"):
            self.auto_classify_transparency_check.setText(self.tr("auto_classify_transparency", "checkboxes"))
        if hasattr(self, "auto_resolve_textures_check"):
            self.auto_resolve_textures_check.setText(self.tr("auto_resolve_textures", "checkboxes"))
        self.hide_hidden_geometry_check.setText(self.tr("hide_hidden_geometry", "checkboxes"))
        self.disable_backface_culling_check.setText(self.tr("disable_backface_culling", "checkboxes"))
        self.import_morphs_check.setText(self.tr("import_morphs", "checkboxes"))
        self.import_physics_check.setText(self.tr("import_physics", "checkboxes"))
        self.create_rigid_bodies_check.setText(self.tr("create_rigid_bodies", "checkboxes"))
        self.create_physics_joints_check.setText(self.tr("create_physics_joints", "checkboxes"))
        self.group_physics_objects_check.setText(self.tr("group_physics_objects", "checkboxes"))
        self.add_semi_standard_bones_check.setText(self.tr("add_semi_standard_bones", "checkboxes"))
        self.bake_mode_check.setText(self.tr("bake_mode", "checkboxes"))
        self.clear_existing_motion_check.setText(self.tr("clear_existing_motion", "checkboxes"))
        self.use_cpp_rig_nodes_check.setText(self.tr("use_cpp_rig_nodes", "checkboxes"))
        self.translate_names_check.setText(self.tr("translate_names", "checkboxes"))
        self.import_bone_animation_check.setText(self.tr("import_bone_animation", "checkboxes"))
        self.import_morph_animation_check.setText(self.tr("import_morph_animation", "checkboxes"))
        self.import_camera_animation_check.setText(self.tr("import_camera_animation", "checkboxes"))
        self.import_light_animation_check.setText(self.tr("import_light_animation", "checkboxes"))
        self.resample_curves_check.setText(self.tr("resample_curves", "checkboxes"))
        self.apply_scale_check.setText(self.tr("apply_scale", "checkboxes"))
        self.new_file_check.setText(self.tr("new_file", "checkboxes"))

        # Tooltips
        self.scale_spin.setToolTip(self.tr("scale_factor", "tooltips"))
        self.use_namespace_check.setToolTip(self.tr("use_namespace", "tooltips"))
        self.create_mmd_shaders_check.setToolTip(self.tr("create_mmd_shaders", "tooltips"))
        self.separate_meshes_check.setToolTip(self.tr("separate_meshes", "tooltips"))
        self.split_by_morph_groups_check.setToolTip(self.tr("split_by_morph_groups", "tooltips"))
        self.auto_classify_transparency_check.setToolTip(self.tr("auto_classify_transparency", "tooltips"))
        self.auto_resolve_textures_check.setToolTip(self.tr("auto_resolve_textures", "tooltips"))
        self.hide_hidden_geometry_check.setToolTip(self.tr("hide_hidden_geometry", "tooltips"))
        self.disable_backface_culling_check.setToolTip(self.tr("disable_backface_culling", "tooltips"))
        self.import_physics_check.setToolTip(self.tr("import_physics", "tooltips"))
        self.add_semi_standard_bones_check.setToolTip(self.tr("add_semi_standard_bones", "tooltips"))
        self.bake_mode_check.setToolTip(self.tr("bake_mode", "tooltips"))
        self.clear_existing_motion_check.setToolTip(self.tr("clear_existing_motion", "tooltips"))
        self.use_cpp_rig_nodes_check.setToolTip(self.tr("use_cpp_rig_nodes", "tooltips"))
        self.translate_names_check.setToolTip(self.tr("translate_names", "tooltips"))
        self.animation_start_frame.setToolTip(self.tr("start_frame", "tooltips"))
        self.vmd_fps_combo.setToolTip(self.tr("vmd_fps", "tooltips"))
        self.motion_scale_spin.setToolTip(self.tr("motion_scale", "tooltips"))
        self.import_bone_animation_check.setToolTip(self.tr("import_bone_animation", "tooltips"))
        self.import_morph_animation_check.setToolTip(self.tr("import_morph_animation", "tooltips"))
        self.import_camera_animation_check.setToolTip(self.tr("import_camera_animation", "tooltips"))
        self.import_light_animation_check.setToolTip(self.tr("import_light_animation", "tooltips"))
        self.resample_curves_check.setToolTip(self.tr("resample_curves", "tooltips"))

        # Buttons
        self.import_path_button.setText(self.tr("browse", "buttons"))
        self.vmd_path_button.setText(self.tr("browse", "buttons"))
        self.export_path_button.setText(self.tr("browse", "buttons"))
        self.import_button.setText(self.tr("import_model", "actions"))
        if hasattr(self, "fix_texture_path_button"):
            self.fix_texture_path_button.setText(self.tr("fix_texture_path", "texture_issues"))
        self.import_vmd_button.setText(self.tr("import_animation", "actions"))
        self.export_button.setText(self.tr("export", "buttons"))

        # Tab widget texts
        if hasattr(self, "animation_settings_group"):
            self.animation_settings_group.setTitle(self.tr("animation", "tabs"))

        # Refresh model list to update auto detect text
        self.refresh_model_list()

    def _load_history(self, key, max_items=10):
        """履歴を読み込み"""
        history_json = self.qt_settings.value(key, "[]")
        try:
            history = json.loads(history_json)
            # 存在するファイルのみフィルタリング
            valid_history = []
            for path in history:
                if os.path.exists(path):
                    valid_history.append(path)
            return valid_history[:max_items]
        except Exception:
            return []

    def _save_history(self, key, new_path, max_items=10):
        """履歴を保存"""
        if not new_path or not os.path.exists(new_path):
            return

        history = self._load_history(key, max_items)

        # 既存のパスを削除
        if new_path in history:
            history.remove(new_path)

        # 先頭に追加
        history.insert(0, new_path)

        # 最大数を制限
        history = history[:max_items]

        # JSONとして保存
        self.qt_settings.setValue(key, json.dumps(history))

    def _setup_unified_history_area(self, layout):
        """統合履歴表示エリアを設定"""
        self.history_group = QGroupBox(self.tr("file_history", "groups"))
        history_layout = QVBoxLayout()

        # 統合履歴リスト
        self.unified_history_list = QListWidget()
        self.unified_history_list.setMaximumHeight(250)

        # ダブルクリックでファイルタイプに応じて適切なUIに設定
        self.unified_history_list.itemDoubleClicked.connect(self._on_history_item_double_clicked)

        history_layout.addWidget(self.unified_history_list)

        # 履歴クリアボタン
        self.clear_history_button = QPushButton(self.tr("clear_history", "buttons"))
        self.clear_history_button.clicked.connect(self._clear_all_history)
        history_layout.addWidget(self.clear_history_button)

        self.history_group.setLayout(history_layout)
        layout.addWidget(self.history_group)

        # 初期化時に履歴を読み込み
        self.refresh_unified_history()

    def _on_history_item_double_clicked(self, item):
        """履歴アイテムがダブルクリックされた時の処理"""
        file_path = item.data(Qt.UserRole)
        file_type = item.data(Qt.UserRole + 1)

        if file_type == "import":
            self.import_path_edit.setText(file_path)
        elif file_type == "vmd":
            self.vmd_path_edit.setText(file_path)
        elif file_type == "export":
            self.export_path_edit.setText(file_path)

    def _clear_all_history(self):
        """すべての履歴をクリア"""
        self.qt_settings.setValue("import_path_history", "[]")
        self.qt_settings.setValue("vmd_path_history", "[]")
        self.qt_settings.setValue("export_path_history", "[]")
        self.refresh_unified_history()

    def refresh_unified_history(self):
        """統合履歴リストを更新"""
        self.unified_history_list.clear()

        # すべての履歴を統合して表示
        all_items = []

        # インポート履歴
        import_history = self._load_history("import_path_history")
        for path in import_history:
            ext = os.path.splitext(path)[1].lower()
            if ext in [".pmd", ".pmx"]:
                item_data = {"path": path, "type": "import", "display": f"[Model] {os.path.basename(path)}"}
                all_items.append(item_data)

        # VMD履歴
        vmd_history = self._load_history("vmd_path_history")
        for path in vmd_history:
            item_data = {"path": path, "type": "vmd", "display": f"[Animation] {os.path.basename(path)}"}
            all_items.append(item_data)

        # エクスポート履歴
        export_history = self._load_history("export_path_history")
        for path in export_history:
            item_data = {"path": path, "type": "export", "display": f"[Export] {os.path.basename(path)}"}
            all_items.append(item_data)

        # リストに追加（最新のものから表示）
        for item_data in all_items:
            item = QListWidgetItem(item_data["display"])
            item.setData(Qt.UserRole, item_data["path"])
            item.setData(Qt.UserRole + 1, item_data["type"])
            item.setToolTip(item_data["path"])

            # タイプによって色分け
            if item_data["type"] == "import":
                item.setForeground(QColor(100, 200, 255))  # 水色
            elif item_data["type"] == "vmd":
                item.setForeground(QColor(255, 200, 100))  # オレンジ
            elif item_data["type"] == "export":
                item.setForeground(QColor(100, 255, 100))  # 緑

            self.unified_history_list.addItem(item)

    def add_import_path_to_history(self, path):
        """インポートパスを履歴に追加"""
        self._save_history("import_path_history", path)
        # 履歴リストを更新
        self.refresh_unified_history()

    def add_vmd_path_to_history(self, path):
        """アニメーションパスを履歴に追加"""
        self._save_history("vmd_path_history", path)
        # 履歴リストを更新
        self.refresh_unified_history()

    def add_export_path_to_history(self, path):
        """エクスポートパスを履歴に追加"""
        self._save_history("export_path_history", path)
        # 履歴リストを更新
        self.refresh_unified_history()
