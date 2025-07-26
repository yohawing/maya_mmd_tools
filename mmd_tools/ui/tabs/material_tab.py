from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QGridLayout,
    QScrollArea,
    QListWidgetItem,
    Qt,
    QSplitter,
)
from ..base_tab import BaseTab


class MaterialTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MaterialTab")

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # スプリッターで左右を分割
        splitter = QSplitter(Qt.Horizontal)

        # 左側: マテリアルリスト
        left_widget = self._create_material_list_section()
        splitter.addWidget(left_widget)

        # 右側: マテリアル詳細設定
        right_widget = self._create_material_details_section()
        splitter.addWidget(right_widget)

        # 初期のスプリッター比率
        splitter.setSizes([400, 600])

        main_layout.addWidget(splitter)
        
        # Set initial state
        self._set_details_enabled(False)
        self._show_placeholder()

    def _create_material_list_section(self):
        """マテリアルリストセクションを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # マテリアルリストグループ
        material_list_group = QGroupBox("マテリアルリスト")
        material_list_layout = QVBoxLayout()

        # ツールバー
        toolbar_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("更新")
        self.refresh_btn.setMaximumWidth(60)
        
        toolbar_layout.addWidget(self.refresh_btn)
        toolbar_layout.addStretch()

        material_list_layout.addLayout(toolbar_layout)

        # マテリアルリスト
        self.material_list = QListWidget()
        self.material_list.setAlternatingRowColors(True)
        # 複数選択を有効化
        self.material_list.setSelectionMode(QListWidget.ExtendedSelection)
        material_list_layout.addWidget(self.material_list)

        # マテリアル検索
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("検索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("マテリアル名を検索...")
        search_layout.addWidget(self.search_edit)
        material_list_layout.addLayout(search_layout)

        material_list_group.setLayout(material_list_layout)
        layout.addWidget(material_list_group)

        return widget

    def _create_material_details_section(self):
        """マテリアル詳細設定セクションを作成"""
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # スクロール可能なエリアを作成
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        
        # スクロールエリア内のコンテンツウィジェット
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Basic Properties
        basic_group = QGroupBox("基本プロパティ")
        basic_layout = QGridLayout()
        
        # Japanese Name
        basic_layout.addWidget(QLabel("日本語名:"), 0, 0)
        self.material_jp_name_edit = QLineEdit()
        basic_layout.addWidget(self.material_jp_name_edit, 0, 1, 1, 2)
        
        # English Name
        basic_layout.addWidget(QLabel("英語名:"), 1, 0)
        self.material_en_name_edit = QLineEdit()
        basic_layout.addWidget(self.material_en_name_edit, 1, 1, 1, 2)
        
        # Material Name
        basic_layout.addWidget(QLabel("マテリアル名:"), 2, 0)
        self.material_name_edit = QLineEdit()
        self.material_name_edit.setReadOnly(True)
        basic_layout.addWidget(self.material_name_edit, 2, 1, 1, 2)
        
        # Diffuse Color
        basic_layout.addWidget(QLabel("Diffuse色:"), 3, 0)
        self.diffuse_color_widget = self._create_color_widget()
        basic_layout.addWidget(self.diffuse_color_widget, 3, 1)
        self.diffuse_color_btn = QPushButton("選択")
        basic_layout.addWidget(self.diffuse_color_btn, 3, 2)
        
        # Transparency (Alpha)
        basic_layout.addWidget(QLabel("透明度:"), 4, 0)
        self.transparency_spin = QDoubleSpinBox()
        self.transparency_spin.setRange(0.0, 1.0)
        self.transparency_spin.setSingleStep(0.1)
        self.transparency_spin.setDecimals(2)
        basic_layout.addWidget(self.transparency_spin, 4, 1, 1, 2)
        
        # Specular Color
        basic_layout.addWidget(QLabel("Specular色:"), 5, 0)
        self.specular_color_widget = self._create_color_widget()
        basic_layout.addWidget(self.specular_color_widget, 5, 1)
        self.specular_color_btn = QPushButton("選択")
        basic_layout.addWidget(self.specular_color_btn, 5, 2)
        
        # Specular Coefficient
        basic_layout.addWidget(QLabel("スペキュラ係数:"), 6, 0)
        self.specular_coefficient_spin = QDoubleSpinBox()
        self.specular_coefficient_spin.setRange(0.0, 100.0)
        self.specular_coefficient_spin.setSingleStep(1.0)
        self.specular_coefficient_spin.setDecimals(1)
        basic_layout.addWidget(self.specular_coefficient_spin, 6, 1, 1, 2)
        
        # Ambient Color
        basic_layout.addWidget(QLabel("Ambient色:"), 7, 0)
        self.ambient_color_widget = self._create_color_widget()
        basic_layout.addWidget(self.ambient_color_widget, 7, 1)
        self.ambient_color_btn = QPushButton("選択")
        basic_layout.addWidget(self.ambient_color_btn, 7, 2)
        
        basic_group.setLayout(basic_layout)
        layout.addWidget(basic_group)
        
        # Texture Properties
        texture_group = QGroupBox("テクスチャ設定")
        texture_layout = QGridLayout()
        
        # Main Texture
        texture_layout.addWidget(QLabel("テクスチャ:"), 0, 0)
        self.texture_path_edit = QLineEdit()
        texture_layout.addWidget(self.texture_path_edit, 0, 1)
        self.texture_browse_btn = QPushButton("参照")
        texture_layout.addWidget(self.texture_browse_btn, 0, 2)
        
        # Sphere Map
        texture_layout.addWidget(QLabel("スフィアマップ:"), 1, 0)
        self.sphere_map_path_edit = QLineEdit()
        texture_layout.addWidget(self.sphere_map_path_edit, 1, 1)
        self.sphere_map_browse_btn = QPushButton("参照")
        texture_layout.addWidget(self.sphere_map_browse_btn, 1, 2)
        
        # Sphere Mode
        texture_layout.addWidget(QLabel("スフィアモード:"), 2, 0)
        self.sphere_mode_combo = QComboBox()
        self.sphere_mode_combo.addItems(["無効", "乗算", "加算", "サブテクスチャ"])
        texture_layout.addWidget(self.sphere_mode_combo, 2, 1, 1, 2)
        
        # Toon Texture
        texture_layout.addWidget(QLabel("トゥーンテクスチャ:"), 3, 0)
        self.toon_texture_combo = QComboBox()
        self.toon_texture_combo.addItems([f"toon{i:02d}.bmp" for i in range(1, 11)])
        texture_layout.addWidget(self.toon_texture_combo, 3, 1, 1, 2)
        
        texture_group.setLayout(texture_layout)
        layout.addWidget(texture_group)
        
        # Rendering Flags
        flags_group = QGroupBox("描画フラグ")
        flags_layout = QVBoxLayout()
        
        self.both_face_check = QCheckBox("両面描画")
        self.ground_shadow_check = QCheckBox("地面影")
        self.self_shadow_map_check = QCheckBox("セルフシャドウマップへの描画")
        self.self_shadow_check = QCheckBox("セルフシャドウの描画")
        self.edge_draw_check = QCheckBox("エッジ描画")
        self.vertex_color_check = QCheckBox("頂点色")
        self.point_draw_check = QCheckBox("ポイント描画")
        self.line_draw_check = QCheckBox("ライン描画")
        
        flags_layout.addWidget(self.both_face_check)
        flags_layout.addWidget(self.ground_shadow_check)
        flags_layout.addWidget(self.self_shadow_map_check)
        flags_layout.addWidget(self.self_shadow_check)
        flags_layout.addWidget(self.edge_draw_check)
        flags_layout.addWidget(self.vertex_color_check)
        flags_layout.addWidget(self.point_draw_check)
        flags_layout.addWidget(self.line_draw_check)
        
        flags_group.setLayout(flags_layout)
        layout.addWidget(flags_group)
        
        # Edge Properties
        edge_group = QGroupBox("エッジ設定")
        edge_layout = QGridLayout()
        
        # Edge Color
        edge_layout.addWidget(QLabel("エッジ色:"), 0, 0)
        self.edge_color_widget = self._create_color_widget()
        edge_layout.addWidget(self.edge_color_widget, 0, 1)
        self.edge_color_btn = QPushButton("選択")
        edge_layout.addWidget(self.edge_color_btn, 0, 2)
        
        # Edge Size
        edge_layout.addWidget(QLabel("エッジサイズ:"), 1, 0)
        self.edge_size_spin = QDoubleSpinBox()
        self.edge_size_spin.setRange(0.0, 10.0)
        self.edge_size_spin.setSingleStep(0.1)
        self.edge_size_spin.setDecimals(2)
        edge_layout.addWidget(self.edge_size_spin, 1, 1, 1, 2)
        
        edge_group.setLayout(edge_layout)
        layout.addWidget(edge_group)
        
        # ストレッチを追加して上に詰める
        layout.addStretch()
        
        # スクロールエリアに設定
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # ボタンバー
        button_layout = QHBoxLayout()
        self.apply_btn = QPushButton("適用")
        self.reset_btn = QPushButton("リセット")
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.reset_btn)

        main_layout.addLayout(button_layout)

        return widget
    
    def _create_color_widget(self):
        """Create a color display widget"""
        widget = QWidget()
        widget.setFixedSize(50, 20)
        widget.setStyleSheet("background-color: rgb(128, 128, 128); border: 1px solid black;")
        return widget
    
    def _set_details_enabled(self, enabled):
        """Enable/disable all detail widgets"""
        widgets = [
            self.material_jp_name_edit,
            self.material_en_name_edit,
            self.material_name_edit,
            self.diffuse_color_btn,
            self.transparency_spin,
            self.specular_color_btn,
            self.specular_coefficient_spin,
            self.ambient_color_btn,
            self.texture_path_edit,
            self.texture_browse_btn,
            self.sphere_map_path_edit,
            self.sphere_map_browse_btn,
            self.sphere_mode_combo,
            self.toon_texture_combo,
            self.both_face_check,
            self.ground_shadow_check,
            self.self_shadow_map_check,
            self.self_shadow_check,
            self.edge_draw_check,
            self.vertex_color_check,
            self.point_draw_check,
            self.line_draw_check,
            self.edge_color_btn,
            self.edge_size_spin,
            self.apply_btn,
            self.reset_btn
        ]
        for widget in widgets:
            widget.setEnabled(enabled)
    
    def _show_placeholder(self):
        """マテリアルリストが空の場合のプレースホルダーを表示"""
        if self.material_list.count() == 0:
            placeholder_item = QListWidgetItem("-- マテリアルがありません --")
            placeholder_item.setFlags(placeholder_item.flags() & ~Qt.ItemIsSelectable)
            self.material_list.addItem(placeholder_item)
