from ..qt_compat import (
    QVBoxLayout,
    QGroupBox,
    QFormLayout,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QTabWidget,
    QWidget,
    QLabel,
    QSpinBox,
    QDoubleSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QGridLayout,
    QTextEdit,
    QScrollArea,
)
from ..base_tab import BaseTab


class SettingsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsTab")
        
        # デバッグ用
        from ...core.logger import get_logger
        logger = get_logger(__name__)
        logger.debug("SettingsTab initialization started")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # スクロールエリアを作成
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        
        # スクロール内のウィジェット
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)

        # タブウィジェット
        self.settings_tabs = QTabWidget()
        
        # 各設定タブを追加
        self.settings_tabs.addTab(self._create_general_tab(), self.tr("general_settings", "tabs"))
        self.settings_tabs.addTab(self._create_import_tab(), self.tr("import_settings", "tabs"))
        self.settings_tabs.addTab(self._create_export_tab(), self.tr("export_settings", "tabs"))
        
        scroll_layout.addWidget(self.settings_tabs)
        
        # ボタンバー
        button_layout = QHBoxLayout()
        self.save_settings_btn = QPushButton(self.tr("save_settings", "actions"))
        self.reset_settings_btn = QPushButton(self.tr("reset_to_default", "actions"))
        self.export_settings_btn = QPushButton(self.tr("export_settings", "actions"))
        self.import_settings_btn = QPushButton(self.tr("import_settings", "actions"))
        
        button_layout.addStretch()
        button_layout.addWidget(self.save_settings_btn)
        button_layout.addWidget(self.reset_settings_btn)
        button_layout.addWidget(self.export_settings_btn)
        button_layout.addWidget(self.import_settings_btn)
        
        scroll_layout.addLayout(button_layout)
        
        scroll_area.setWidget(scroll_widget)
        main_layout.addWidget(scroll_area)
        
        # デバッグ：初期化完了
        logger.debug("SettingsTab initialization completed")
        logger.debug(f"SettingsTab attributes: {list(self.__dict__.keys())}")

    def _create_general_tab(self):
        """全般設定タブを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # UI設定
        self.ui_group = QGroupBox(self.tr("ui_settings", "groups"))
        ui_layout = QFormLayout()
        
        self.show_advanced_options_check = QCheckBox(self.tr("show_advanced_options", "checkboxes"))
        ui_layout.addRow("", self.show_advanced_options_check)
        
        self.ui_log_level_combo = QComboBox()
        self.ui_log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.ui_log_level_label = QLabel(self.tr("ui_log_level", "fields"))
        ui_layout.addRow(self.ui_log_level_label, self.ui_log_level_combo)
        
        # 言語選択
        self.language_combo = QComboBox()
        # UITranslatorから言語リストを取得
        from ...ui.translations import UITranslator
        translator = UITranslator.instance()
        languages = translator.get_supported_languages()
        for code, name in languages.items():
            self.language_combo.addItem(name, code)
        self.language_label = QLabel(self.tr("language", "fields"))
        ui_layout.addRow(self.language_label, self.language_combo)
        
        self.ui_group.setLayout(ui_layout)
        layout.addWidget(self.ui_group)
        
        # ログ設定
        self.log_group = QGroupBox(self.tr("log_settings", "groups"))
        log_layout = QFormLayout()
        
        self.logging_enabled_check = QCheckBox(self.tr("enable_logging", "checkboxes"))
        log_layout.addRow("", self.logging_enabled_check)
        
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.log_level_label = QLabel(self.tr("log_level", "fields"))
        log_layout.addRow(self.log_level_label, self.log_level_combo)
        
        log_file_layout = QHBoxLayout()
        self.log_file_path_edit = QLineEdit()
        self.log_file_browse_btn = QPushButton(self.tr("browse", "buttons"))
        self.log_file_browse_btn.setMaximumWidth(60)
        log_file_layout.addWidget(self.log_file_path_edit)
        log_file_layout.addWidget(self.log_file_browse_btn)
        self.log_file_label = QLabel(self.tr("log_file", "fields"))
        log_layout.addRow(self.log_file_label, log_file_layout)
        
        self.log_group.setLayout(log_layout)
        layout.addWidget(self.log_group)
        
        layout.addStretch()
        return widget

    def _create_import_tab(self):
        """インポート設定タブを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 基本設定
        self.import_general_group = QGroupBox(self.tr("basic_settings", "groups"))
        general_layout = QFormLayout()
        
        self.scale_factor_spin = QDoubleSpinBox()
        self.scale_factor_spin.setRange(0.001, 1000.0)
        self.scale_factor_spin.setValue(1.0)
        self.scale_factor_spin.setDecimals(3)
        self.scale_factor_label = QLabel(self.tr("scale_factor", "fields"))
        general_layout.addRow(self.scale_factor_label, self.scale_factor_spin)
        
        self.use_namespace_check = QCheckBox(self.tr("use_namespace", "checkboxes"))
        general_layout.addRow("", self.use_namespace_check)
        
        self.root_bone_name_edit = QLineEdit("master")
        self.root_bone_name_label = QLabel(self.tr("root_bone_name", "fields"))
        general_layout.addRow(self.root_bone_name_label, self.root_bone_name_edit)
        
        self.import_general_group.setLayout(general_layout)
        layout.addWidget(self.import_general_group)
        
        # モデル設定
        self.model_group = QGroupBox(self.tr("model_settings", "groups"))
        model_layout = QVBoxLayout()
        
        self.import_models_check = QCheckBox(self.tr("import_models", "checkboxes"))
        self.separate_meshes_check = QCheckBox(self.tr("separate_meshes", "checkboxes"))
        self.create_mmd_shaders_check = QCheckBox(self.tr("create_mmd_shaders", "checkboxes"))
        self.hide_hidden_geometry_check = QCheckBox(self.tr("hide_hidden_geometry", "checkboxes"))
        self.joint_name_conversion_check = QCheckBox(self.tr("joint_name_conversion", "checkboxes"))
        self.disable_backface_culling_check = QCheckBox(self.tr("disable_backface_culling", "checkboxes"))
        
        model_layout.addWidget(self.import_models_check)
        model_layout.addWidget(self.separate_meshes_check)
        model_layout.addWidget(self.create_mmd_shaders_check)
        model_layout.addWidget(self.hide_hidden_geometry_check)
        model_layout.addWidget(self.joint_name_conversion_check)
        model_layout.addWidget(self.disable_backface_culling_check)
        
        texture_layout = QHBoxLayout()
        self.texture_search_path_label = QLabel(self.tr("texture_search_path", "fields"))
        texture_layout.addWidget(self.texture_search_path_label)
        self.texture_search_path_edit = QLineEdit()
        self.texture_path_browse_btn = QPushButton(self.tr("browse", "buttons"))
        self.texture_path_browse_btn.setMaximumWidth(60)
        texture_layout.addWidget(self.texture_search_path_edit)
        texture_layout.addWidget(self.texture_path_browse_btn)
        model_layout.addLayout(texture_layout)
        
        uv_layout = QHBoxLayout()
        self.uv_set_name_label = QLabel(self.tr("uv_set_name", "fields"))
        uv_layout.addWidget(self.uv_set_name_label)
        self.uv_set_name_edit = QLineEdit("map#")
        uv_layout.addWidget(self.uv_set_name_edit)
        uv_layout.addStretch()
        model_layout.addLayout(uv_layout)
        
        self.model_group.setLayout(model_layout)
        layout.addWidget(self.model_group)
        
        # 物理設定
        self.physics_group = QGroupBox(self.tr("physics_settings", "groups"))
        physics_layout = QVBoxLayout()
        
        self.import_physics_check = QCheckBox(self.tr("import_physics", "checkboxes"))
        self.create_rigid_bodies_check = QCheckBox(self.tr("create_rigid_bodies", "checkboxes"))
        self.create_physics_joints_check = QCheckBox(self.tr("create_physics_joints", "checkboxes"))
        self.group_physics_objects_check = QCheckBox(self.tr("group_physics_objects", "checkboxes"))
        
        physics_layout.addWidget(self.import_physics_check)
        physics_layout.addWidget(self.create_rigid_bodies_check)
        physics_layout.addWidget(self.create_physics_joints_check)
        physics_layout.addWidget(self.group_physics_objects_check)
        
        self.physics_group.setLayout(physics_layout)
        layout.addWidget(self.physics_group)
        
        # その他の設定
        self.other_group = QGroupBox(self.tr("other_settings", "groups"))
        other_layout = QVBoxLayout()
        
        self.import_morphs_check = QCheckBox(self.tr("import_morphs", "checkboxes"))
        self.add_semi_standard_bones_check = QCheckBox(self.tr("add_semi_standard_bones", "checkboxes"))
        self.translate_names_check = QCheckBox(self.tr("translate_names", "checkboxes"))
        
        other_layout.addWidget(self.import_morphs_check)
        other_layout.addWidget(self.add_semi_standard_bones_check)
        other_layout.addWidget(self.translate_names_check)
        
        self.other_group.setLayout(other_layout)
        layout.addWidget(self.other_group)
        
        layout.addStretch()
        return widget

    def _create_export_tab(self):
        """エクスポート設定タブを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 基本設定
        self.export_general_group = QGroupBox(self.tr("basic_settings", "groups"))
        general_layout = QFormLayout()
        
        self.export_format_combo = QComboBox()
        self.export_format_combo.addItems(["pmx", "pmd"])
        self.export_format_label = QLabel(self.tr("export_format", "fields"))
        general_layout.addRow(self.export_format_label, self.export_format_combo)
        
        self.apply_scale_check = QCheckBox(self.tr("apply_scale", "checkboxes"))
        general_layout.addRow("", self.apply_scale_check)
        
        self.export_general_group.setLayout(general_layout)
        layout.addWidget(self.export_general_group)
        
        layout.addStretch()
        return widget
    
    def retranslateUi(self):
        """UIテキストを再翻訳"""
        # タブテキスト
        if hasattr(self, 'settings_tabs'):
            if self.settings_tabs.count() >= 3:
                self.settings_tabs.setTabText(0, self.tr("general_settings", "tabs"))
                self.settings_tabs.setTabText(1, self.tr("import_settings", "tabs"))
                self.settings_tabs.setTabText(2, self.tr("export_settings", "tabs"))
        
        # ボタン
        if hasattr(self, 'save_settings_btn'):
            self.save_settings_btn.setText(self.tr("save_settings", "actions"))
        if hasattr(self, 'reset_settings_btn'):
            self.reset_settings_btn.setText(self.tr("reset_to_default", "actions"))
        if hasattr(self, 'export_settings_btn'):
            self.export_settings_btn.setText(self.tr("export_settings", "actions"))
        if hasattr(self, 'import_settings_btn'):
            self.import_settings_btn.setText(self.tr("import_settings", "actions"))
        if hasattr(self, 'log_file_browse_btn'):
            self.log_file_browse_btn.setText(self.tr("browse", "buttons"))
        if hasattr(self, 'texture_path_browse_btn'):
            self.texture_path_browse_btn.setText(self.tr("browse", "buttons"))
        
        # GroupBoxes
        if hasattr(self, 'ui_group'):
            self.ui_group.setTitle(self.tr("ui_settings", "groups"))
        if hasattr(self, 'log_group'):
            self.log_group.setTitle(self.tr("log_settings", "groups"))
        if hasattr(self, 'import_general_group'):
            self.import_general_group.setTitle(self.tr("basic_settings", "groups"))
        if hasattr(self, 'model_group'):
            self.model_group.setTitle(self.tr("model_settings", "groups"))
        if hasattr(self, 'physics_group'):
            self.physics_group.setTitle(self.tr("physics_settings", "groups"))
        if hasattr(self, 'other_group'):
            self.other_group.setTitle(self.tr("other_settings", "groups"))
        if hasattr(self, 'export_general_group'):
            self.export_general_group.setTitle(self.tr("basic_settings", "groups"))
        
        # Labels
        if hasattr(self, 'ui_log_level_label'):
            self.ui_log_level_label.setText(self.tr("ui_log_level", "fields"))
        if hasattr(self, 'language_label'):
            self.language_label.setText(self.tr("language", "fields"))
        if hasattr(self, 'log_level_label'):
            self.log_level_label.setText(self.tr("log_level", "fields"))
        if hasattr(self, 'log_file_label'):
            self.log_file_label.setText(self.tr("log_file", "fields"))
        if hasattr(self, 'scale_factor_label'):
            self.scale_factor_label.setText(self.tr("scale_factor", "fields"))
        if hasattr(self, 'root_bone_name_label'):
            self.root_bone_name_label.setText(self.tr("root_bone_name", "fields"))
        if hasattr(self, 'texture_search_path_label'):
            self.texture_search_path_label.setText(self.tr("texture_search_path", "fields"))
        if hasattr(self, 'uv_set_name_label'):
            self.uv_set_name_label.setText(self.tr("uv_set_name", "fields"))
        if hasattr(self, 'export_format_label'):
            self.export_format_label.setText(self.tr("export_format", "fields"))
        
        # CheckBoxes
        if hasattr(self, 'show_advanced_options_check'):
            self.show_advanced_options_check.setText(self.tr("show_advanced_options", "checkboxes"))
        if hasattr(self, 'logging_enabled_check'):
            self.logging_enabled_check.setText(self.tr("enable_logging", "checkboxes"))
        if hasattr(self, 'use_namespace_check'):
            self.use_namespace_check.setText(self.tr("use_namespace", "checkboxes"))
        if hasattr(self, 'import_models_check'):
            self.import_models_check.setText(self.tr("import_models", "checkboxes"))
        if hasattr(self, 'separate_meshes_check'):
            self.separate_meshes_check.setText(self.tr("separate_meshes", "checkboxes"))
        if hasattr(self, 'create_mmd_shaders_check'):
            self.create_mmd_shaders_check.setText(self.tr("create_mmd_shaders", "checkboxes"))
        if hasattr(self, 'hide_hidden_geometry_check'):
            self.hide_hidden_geometry_check.setText(self.tr("hide_hidden_geometry", "checkboxes"))
        if hasattr(self, 'joint_name_conversion_check'):
            self.joint_name_conversion_check.setText(self.tr("joint_name_conversion", "checkboxes"))
        if hasattr(self, 'disable_backface_culling_check'):
            self.disable_backface_culling_check.setText(self.tr("disable_backface_culling", "checkboxes"))
        if hasattr(self, 'import_physics_check'):
            self.import_physics_check.setText(self.tr("import_physics", "checkboxes"))
        if hasattr(self, 'create_rigid_bodies_check'):
            self.create_rigid_bodies_check.setText(self.tr("create_rigid_bodies", "checkboxes"))
        if hasattr(self, 'create_physics_joints_check'):
            self.create_physics_joints_check.setText(self.tr("create_physics_joints", "checkboxes"))
        if hasattr(self, 'group_physics_objects_check'):
            self.group_physics_objects_check.setText(self.tr("group_physics_objects", "checkboxes"))
        if hasattr(self, 'import_morphs_check'):
            self.import_morphs_check.setText(self.tr("import_morphs", "checkboxes"))
        if hasattr(self, 'add_semi_standard_bones_check'):
            self.add_semi_standard_bones_check.setText(self.tr("add_semi_standard_bones", "checkboxes"))
        if hasattr(self, 'translate_names_check'):
            self.translate_names_check.setText(self.tr("translate_names", "checkboxes"))
        if hasattr(self, 'apply_scale_check'):
            self.apply_scale_check.setText(self.tr("apply_scale", "checkboxes"))


