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
)
from ..base_tab import BaseTab


class MaterialTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MaterialTab")

        main_layout = QHBoxLayout(self)

        # Left panel - Material List
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        material_list_group = QGroupBox("マテリアル一覧")
        material_list_layout = QVBoxLayout()
        
        # Material count label
        self.material_count_label = QLabel("マテリアル数: 0")
        material_list_layout.addWidget(self.material_count_label)
        
        self.material_list = QListWidget()
        material_list_layout.addWidget(self.material_list)
        
        # Refresh button
        self.refresh_btn = QPushButton("リフレッシュ")
        material_list_layout.addWidget(self.refresh_btn)
        
        material_list_group.setLayout(material_list_layout)
        left_layout.addWidget(material_list_group)
        
        main_layout.addWidget(left_panel, 1)

        # Right panel - Material Details
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # Create scrollable area for details
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        
        details_widget = QWidget()
        details_layout = QVBoxLayout(details_widget)
        
        # Basic Properties
        basic_group = QGroupBox("基本プロパティ")
        basic_layout = QGridLayout()
        
        # Japanese Name
        basic_layout.addWidget(QLabel("日本語名:"), 0, 0)
        self.material_jp_name_edit = QLineEdit()
        self.material_jp_name_edit.setReadOnly(True)
        basic_layout.addWidget(self.material_jp_name_edit, 0, 1, 1, 2)
        
        # Material Name
        basic_layout.addWidget(QLabel("マテリアル名:"), 1, 0)
        self.material_name_edit = QLineEdit()
        self.material_name_edit.setReadOnly(True)
        basic_layout.addWidget(self.material_name_edit, 1, 1, 1, 2)
        
        # Diffuse Color
        basic_layout.addWidget(QLabel("Diffuse色:"), 2, 0)
        self.diffuse_color_widget = self._create_color_widget()
        basic_layout.addWidget(self.diffuse_color_widget, 2, 1)
        self.diffuse_color_btn = QPushButton("選択")
        basic_layout.addWidget(self.diffuse_color_btn, 2, 2)
        
        # Transparency (Alpha)
        basic_layout.addWidget(QLabel("透明度:"), 3, 0)
        self.transparency_spin = QDoubleSpinBox()
        self.transparency_spin.setRange(0.0, 1.0)
        self.transparency_spin.setSingleStep(0.1)
        self.transparency_spin.setDecimals(2)
        basic_layout.addWidget(self.transparency_spin, 3, 1, 1, 2)
        
        # Specular Color
        basic_layout.addWidget(QLabel("Specular色:"), 4, 0)
        self.specular_color_widget = self._create_color_widget()
        basic_layout.addWidget(self.specular_color_widget, 4, 1)
        self.specular_color_btn = QPushButton("選択")
        basic_layout.addWidget(self.specular_color_btn, 4, 2)
        
        # Specular Coefficient
        basic_layout.addWidget(QLabel("スペキュラ係数:"), 5, 0)
        self.specular_coefficient_spin = QDoubleSpinBox()
        self.specular_coefficient_spin.setRange(0.0, 100.0)
        self.specular_coefficient_spin.setSingleStep(1.0)
        self.specular_coefficient_spin.setDecimals(1)
        basic_layout.addWidget(self.specular_coefficient_spin, 5, 1, 1, 2)
        
        # Ambient Color
        basic_layout.addWidget(QLabel("Ambient色:"), 6, 0)
        self.ambient_color_widget = self._create_color_widget()
        basic_layout.addWidget(self.ambient_color_widget, 6, 1)
        self.ambient_color_btn = QPushButton("選択")
        basic_layout.addWidget(self.ambient_color_btn, 6, 2)
        
        basic_group.setLayout(basic_layout)
        details_layout.addWidget(basic_group)
        
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
        details_layout.addWidget(texture_group)
        
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
        details_layout.addWidget(flags_group)
        
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
        details_layout.addWidget(edge_group)
        
        # Apply/Reset buttons
        button_layout = QHBoxLayout()
        self.apply_btn = QPushButton("変更を適用")
        self.reset_btn = QPushButton("リセット")
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.reset_btn)
        button_layout.addStretch()
        details_layout.addLayout(button_layout)
        
        # Add stretch at bottom
        details_layout.addStretch()
        
        scroll_area.setWidget(details_widget)
        right_layout.addWidget(scroll_area)
        
        main_layout.addWidget(right_panel, 2)
        
        # Set initial state
        self._set_details_enabled(False)
        self._show_placeholder()
    
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
