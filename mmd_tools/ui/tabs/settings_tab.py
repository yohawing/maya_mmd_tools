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
        self.settings_tabs.addTab(self._create_general_tab(), "全般設定")
        self.settings_tabs.addTab(self._create_import_tab(), "インポート設定")
        self.settings_tabs.addTab(self._create_export_tab(), "エクスポート設定")
        
        scroll_layout.addWidget(self.settings_tabs)
        
        # ボタンバー
        button_layout = QHBoxLayout()
        self.save_settings_btn = QPushButton("設定を保存")
        self.reset_settings_btn = QPushButton("デフォルトに戻す")
        self.export_settings_btn = QPushButton("設定をエクスポート")
        self.import_settings_btn = QPushButton("設定をインポート")
        
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
        ui_group = QGroupBox("UI設定")
        ui_layout = QFormLayout()
        
        self.show_advanced_options_check = QCheckBox("高度なオプションを表示")
        ui_layout.addRow("", self.show_advanced_options_check)
        
        self.ui_log_level_combo = QComboBox()
        self.ui_log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        ui_layout.addRow("UIログレベル:", self.ui_log_level_combo)
        
        # 言語選択
        self.language_combo = QComboBox()
        # UITranslatorから言語リストを取得
        from ...ui.translations import UITranslator
        translator = UITranslator.instance()
        languages = translator.get_supported_languages()
        for code, name in languages.items():
            self.language_combo.addItem(name, code)
        ui_layout.addRow("言語:", self.language_combo)
        
        ui_group.setLayout(ui_layout)
        layout.addWidget(ui_group)
        
        # ログ設定
        log_group = QGroupBox("ログ設定")
        log_layout = QFormLayout()
        
        self.logging_enabled_check = QCheckBox("ログを有効にする")
        log_layout.addRow("", self.logging_enabled_check)
        
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        log_layout.addRow("ログレベル:", self.log_level_combo)
        
        log_file_layout = QHBoxLayout()
        self.log_file_path_edit = QLineEdit()
        self.log_file_browse_btn = QPushButton("参照")
        self.log_file_browse_btn.setMaximumWidth(60)
        log_file_layout.addWidget(self.log_file_path_edit)
        log_file_layout.addWidget(self.log_file_browse_btn)
        log_layout.addRow("ログファイル:", log_file_layout)
        
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)
        
        layout.addStretch()
        return widget

    def _create_import_tab(self):
        """インポート設定タブを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 基本設定
        general_group = QGroupBox("基本設定")
        general_layout = QFormLayout()
        
        self.scale_factor_spin = QDoubleSpinBox()
        self.scale_factor_spin.setRange(0.001, 1000.0)
        self.scale_factor_spin.setValue(1.0)
        self.scale_factor_spin.setDecimals(3)
        general_layout.addRow("スケール係数:", self.scale_factor_spin)
        
        self.use_namespace_check = QCheckBox("ネームスペースを使用")
        general_layout.addRow("", self.use_namespace_check)
        
        self.root_bone_name_edit = QLineEdit("master")
        general_layout.addRow("ルートボーン名:", self.root_bone_name_edit)
        
        general_group.setLayout(general_layout)
        layout.addWidget(general_group)
        
        # モデル設定
        model_group = QGroupBox("モデル設定")
        model_layout = QVBoxLayout()
        
        self.import_models_check = QCheckBox("モデルをインポート")
        self.separate_meshes_check = QCheckBox("マテリアルごとにメッシュを分割")
        self.create_mmd_shaders_check = QCheckBox("MMDシェーダーを作成")
        self.hide_hidden_geometry_check = QCheckBox("非表示ジオメトリを隠す")
        self.joint_name_conversion_check = QCheckBox("英語名でジョイント名を変換")
        self.disable_backface_culling_check = QCheckBox("バックフェースカリングを無効化")
        
        model_layout.addWidget(self.import_models_check)
        model_layout.addWidget(self.separate_meshes_check)
        model_layout.addWidget(self.create_mmd_shaders_check)
        model_layout.addWidget(self.hide_hidden_geometry_check)
        model_layout.addWidget(self.joint_name_conversion_check)
        model_layout.addWidget(self.disable_backface_culling_check)
        
        texture_layout = QHBoxLayout()
        texture_layout.addWidget(QLabel("テクスチャ検索パス:"))
        self.texture_search_path_edit = QLineEdit()
        self.texture_path_browse_btn = QPushButton("参照")
        self.texture_path_browse_btn.setMaximumWidth(60)
        texture_layout.addWidget(self.texture_search_path_edit)
        texture_layout.addWidget(self.texture_path_browse_btn)
        model_layout.addLayout(texture_layout)
        
        uv_layout = QHBoxLayout()
        uv_layout.addWidget(QLabel("UVセット名:"))
        self.uv_set_name_edit = QLineEdit("map#")
        uv_layout.addWidget(self.uv_set_name_edit)
        uv_layout.addStretch()
        model_layout.addLayout(uv_layout)
        
        model_group.setLayout(model_layout)
        layout.addWidget(model_group)
        
        # 物理設定
        physics_group = QGroupBox("物理設定")
        physics_layout = QVBoxLayout()
        
        self.import_physics_check = QCheckBox("物理をインポート")
        self.create_rigid_bodies_check = QCheckBox("剛体を作成")
        self.create_physics_joints_check = QCheckBox("物理ジョイントを作成")
        self.group_physics_objects_check = QCheckBox("物理オブジェクトをグループ化")
        
        physics_layout.addWidget(self.import_physics_check)
        physics_layout.addWidget(self.create_rigid_bodies_check)
        physics_layout.addWidget(self.create_physics_joints_check)
        physics_layout.addWidget(self.group_physics_objects_check)
        
        physics_group.setLayout(physics_layout)
        layout.addWidget(physics_group)
        
        # その他の設定
        other_group = QGroupBox("その他")
        other_layout = QVBoxLayout()
        
        self.import_morphs_check = QCheckBox("モーフをインポート")
        self.add_semi_standard_bones_check = QCheckBox("準標準ボーンを追加")
        self.translate_names_check = QCheckBox("名前を翻訳")
        
        other_layout.addWidget(self.import_morphs_check)
        other_layout.addWidget(self.add_semi_standard_bones_check)
        other_layout.addWidget(self.translate_names_check)
        
        other_group.setLayout(other_layout)
        layout.addWidget(other_group)
        
        layout.addStretch()
        return widget

    def _create_export_tab(self):
        """エクスポート設定タブを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 基本設定
        general_group = QGroupBox("基本設定")
        general_layout = QFormLayout()
        
        self.export_format_combo = QComboBox()
        self.export_format_combo.addItems(["pmx", "pmd"])
        general_layout.addRow("エクスポート形式:", self.export_format_combo)
        
        self.apply_scale_check = QCheckBox("スケールを適用")
        general_layout.addRow("", self.apply_scale_check)
        
        general_group.setLayout(general_layout)
        layout.addWidget(general_group)
        
        layout.addStretch()
        return widget
    
    def retranslateUi(self):
        """UIテキストを再翻訳"""
        # タブテキスト
        self.settings_tabs.setTabText(0, self.tr("settings.general"))
        self.settings_tabs.setTabText(1, self.tr("settings.import"))
        self.settings_tabs.setTabText(2, self.tr("settings.export"))
        
        # ボタン
        self.save_settings_btn.setText(self.tr("save", "buttons"))
        self.reset_settings_btn.setText(self.tr("reset", "buttons"))
        self.export_settings_btn.setText(self.tr("export", "buttons"))
        self.import_settings_btn.setText(self.tr("import", "buttons"))
        
        # その他のUIテキストも必要に応じて追加


