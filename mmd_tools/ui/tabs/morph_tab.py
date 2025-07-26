from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QListWidget,
    QSlider,
    Qt,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QListWidgetItem,
    QSplitter,
    QCheckBox,
)
from ..base_tab import BaseTab


class MorphTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MorphTab")

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # スプリッターで左中右を分割
        splitter = QSplitter(Qt.Horizontal)

        # 左側: モーフグループリスト
        left_widget = self._create_group_section()
        splitter.addWidget(left_widget)

        # 中央: モーフリスト
        center_widget = self._create_morph_list_section()
        splitter.addWidget(center_widget)

        # 右側: モーフ詳細
        right_widget = self._create_morph_details_section()
        splitter.addWidget(right_widget)

        # 初期のスプリッター比率
        splitter.setSizes([200, 300, 500])

        main_layout.addWidget(splitter)

    def _create_group_section(self):
        """モーフグループセクションを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # グループリスト
        group_box = QGroupBox("モーフグループ")
        group_layout = QVBoxLayout()

        # ツールバー
        toolbar_layout = QHBoxLayout()
        self.add_group_btn = QPushButton("追加")
        self.remove_group_btn = QPushButton("削除")
        self.add_group_btn.setMaximumWidth(60)
        self.remove_group_btn.setMaximumWidth(60)
        toolbar_layout.addWidget(self.add_group_btn)
        toolbar_layout.addWidget(self.remove_group_btn)
        toolbar_layout.addStretch()
        group_layout.addLayout(toolbar_layout)

        # グループリスト
        self.group_list = QListWidget()
        self.group_list.setAlternatingRowColors(True)
        
        # デフォルトグループを追加
        self.group_list.addItem("全て表示")
        default_groups = ["眉", "目", "口", "その他"]
        for group in default_groups:
            self.group_list.addItem(group)
        
        group_layout.addWidget(self.group_list)

        group_box.setLayout(group_layout)
        layout.addWidget(group_box)

        return widget

    def _create_morph_list_section(self):
        """モーフリストセクションを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # モーフリスト
        morph_list_group = QGroupBox("モーフ一覧")
        morph_list_layout = QVBoxLayout()

        # ツールバー
        toolbar_layout = QHBoxLayout()
        self.refresh_morphs_btn = QPushButton("更新")
        self.refresh_morphs_btn.setMaximumWidth(60)
        self.select_in_maya_btn = QPushButton("Mayaで選択")
        self.select_in_maya_btn.setMaximumWidth(100)
        
        toolbar_layout.addWidget(self.refresh_morphs_btn)
        toolbar_layout.addWidget(self.select_in_maya_btn)
        toolbar_layout.addStretch()
        morph_list_layout.addLayout(toolbar_layout)

        # モーフリスト
        self.morph_list = QListWidget()
        self.morph_list.setAlternatingRowColors(True)
        morph_list_layout.addWidget(self.morph_list)

        # 検索
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("検索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("モーフ名を検索...")
        search_layout.addWidget(self.search_edit)
        morph_list_layout.addLayout(search_layout)

        morph_list_group.setLayout(morph_list_layout)
        layout.addWidget(morph_list_group)

        return widget

    def _create_morph_details_section(self):
        """モーフ詳細セクションを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # タブウィジェット
        self.detail_tabs = QTabWidget()

        # 基本情報タブ
        self.detail_tabs.addTab(self._create_basic_info_tab(), "基本情報")

        # オフセット情報タブ
        self.detail_tabs.addTab(self._create_offset_info_tab(), "オフセット情報")

        # Maya連携タブ
        self.detail_tabs.addTab(self._create_maya_connection_tab(), "Maya連携")

        layout.addWidget(self.detail_tabs)

        # プレビューセクション
        preview_group = QGroupBox("プレビュー")
        preview_layout = QVBoxLayout()

        # スライダー
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(QLabel("適用率:"))
        self.morph_slider = QSlider(Qt.Horizontal)
        self.morph_slider.setRange(0, 100)
        self.morph_slider.setTickPosition(QSlider.TicksBelow)
        self.morph_slider.setTickInterval(10)
        slider_layout.addWidget(self.morph_slider)

        self.morph_value_label = QLabel("0%")
        self.morph_value_label.setMinimumWidth(40)
        slider_layout.addWidget(self.morph_value_label)

        preview_layout.addLayout(slider_layout)

        # リセットボタン
        reset_layout = QHBoxLayout()
        self.reset_slider_btn = QPushButton("リセット")
        self.reset_all_btn = QPushButton("全てリセット")
        reset_layout.addStretch()
        reset_layout.addWidget(self.reset_slider_btn)
        reset_layout.addWidget(self.reset_all_btn)
        preview_layout.addLayout(reset_layout)
        
        # プリセット機能
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("プリセット:"))
        self.preset_combo = QComboBox()
        self.preset_combo.setEditable(True)
        self.preset_combo.addItems(["なし", "笑顔", "ウィンク", "驚き", "悲しみ"])
        preset_layout.addWidget(self.preset_combo)
        self.save_preset_btn = QPushButton("保存")
        self.load_preset_btn = QPushButton("読込")
        self.delete_preset_btn = QPushButton("削除")
        preset_layout.addWidget(self.save_preset_btn)
        preset_layout.addWidget(self.load_preset_btn)
        preset_layout.addWidget(self.delete_preset_btn)
        preview_layout.addLayout(preset_layout)

        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group)

        # ボタンバー
        button_layout = QHBoxLayout()
        self.apply_btn = QPushButton("適用")
        self.reset_btn = QPushButton("リセット")
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.reset_btn)
        layout.addLayout(button_layout)

        return widget

    def _create_basic_info_tab(self):
        """基本情報タブを作成"""
        widget = QWidget()
        layout = QFormLayout(widget)

        # モーフ名
        self.morph_name_jp_edit = QLineEdit()
        self.morph_name_en_edit = QLineEdit()
        layout.addRow("日本語名:", self.morph_name_jp_edit)
        layout.addRow("英語名:", self.morph_name_en_edit)

        # パネル
        panel_layout = QHBoxLayout()
        self.panel_combo = QComboBox()
        self.panel_combo.addItems(["なし", "眉(左下)", "目(左上)", "口(右上)", "その他(右下)"])
        panel_layout.addWidget(self.panel_combo)
        layout.addRow("パネル:", panel_layout)

        # モーフタイプ
        self.morph_type_combo = QComboBox()
        self.morph_type_combo.addItems([
            "頂点", "UV", "UV1", "UV2", "UV3", "UV4",
            "追加UV1", "追加UV2", "追加UV3", "追加UV4",
            "ボーン", "材質", "グループ", "フリップ", "インパルス"
        ])
        layout.addRow("タイプ:", self.morph_type_combo)

        # グループ設定
        group_layout = QHBoxLayout()
        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.addItems(["眉", "目", "口", "その他"])
        group_layout.addWidget(self.group_combo)
        layout.addRow("グループ:", group_layout)

        return widget

    def _create_offset_info_tab(self):
        """オフセット情報タブを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # オフセット数表示
        info_layout = QHBoxLayout()
        self.offset_count_label = QLabel("オフセット数: 0")
        info_layout.addWidget(self.offset_count_label)
        info_layout.addStretch()
        layout.addLayout(info_layout)

        # オフセットテーブル
        self.offset_table = QTableWidget()
        self.offset_table.setColumnCount(5)
        self.offset_table.setHorizontalHeaderLabels(["インデックス", "タイプ", "要素", "値", "詳細"])
        self.offset_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.offset_table)

        # ツールバー
        toolbar_layout = QHBoxLayout()
        self.add_offset_btn = QPushButton("追加")
        self.remove_offset_btn = QPushButton("削除")
        self.clear_offsets_btn = QPushButton("全クリア")
        toolbar_layout.addWidget(self.add_offset_btn)
        toolbar_layout.addWidget(self.remove_offset_btn)
        toolbar_layout.addWidget(self.clear_offsets_btn)
        toolbar_layout.addStretch()
        layout.addLayout(toolbar_layout)

        return widget

    def _create_maya_connection_tab(self):
        """Maya連携タブを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # ブレンドシェイプ設定
        blend_group = QGroupBox("ブレンドシェイプ連携")
        blend_layout = QFormLayout()

        # 連携状態
        self.connection_status_label = QLabel("未連携")
        self.connection_status_label.setStyleSheet("color: red;")
        blend_layout.addRow("状態:", self.connection_status_label)

        # ブレンドシェイプノード
        node_layout = QHBoxLayout()
        self.blend_shape_edit = QLineEdit()
        self.blend_shape_edit.setReadOnly(True)
        self.select_blend_shape_btn = QPushButton("選択")
        self.select_blend_shape_btn.setMaximumWidth(60)
        node_layout.addWidget(self.blend_shape_edit)
        node_layout.addWidget(self.select_blend_shape_btn)
        blend_layout.addRow("ノード:", node_layout)

        # ターゲット名
        self.target_name_edit = QLineEdit()
        blend_layout.addRow("ターゲット名:", self.target_name_edit)

        # 連携ボタン
        connection_layout = QHBoxLayout()
        self.connect_btn = QPushButton("連携")
        self.disconnect_btn = QPushButton("解除")
        self.auto_connect_btn = QPushButton("自動連携")
        connection_layout.addWidget(self.connect_btn)
        connection_layout.addWidget(self.disconnect_btn)
        connection_layout.addWidget(self.auto_connect_btn)
        connection_layout.addStretch()
        blend_layout.addRow("", connection_layout)

        blend_group.setLayout(blend_layout)
        layout.addWidget(blend_group)

        # 詳細設定
        advanced_group = QGroupBox("詳細設定")
        advanced_layout = QFormLayout()

        # 反転
        self.invert_check = QCheckBox("値を反転")
        advanced_layout.addRow("", self.invert_check)

        # 乗数
        self.multiplier_spin = QDoubleSpinBox()
        self.multiplier_spin.setRange(-10.0, 10.0)
        self.multiplier_spin.setValue(1.0)
        self.multiplier_spin.setSingleStep(0.1)
        advanced_layout.addRow("乗数:", self.multiplier_spin)

        advanced_group.setLayout(advanced_layout)
        layout.addWidget(advanced_group)

        layout.addStretch()

        return widget

    def set_morph_details_enabled(self, enabled):
        """モーフ詳細セクションの有効/無効を設定"""
        self.detail_tabs.setEnabled(enabled)
        self.morph_slider.setEnabled(enabled)
        self.reset_slider_btn.setEnabled(enabled)
        self.apply_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)
