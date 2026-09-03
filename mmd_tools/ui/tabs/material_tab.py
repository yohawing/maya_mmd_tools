from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QGroupBox,
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
    QSlider,
)
from ..base_tab import BaseTab
from ...core.name_display import original_pmx_fields_visible
from ..components.authoring_toolbar import AuthoringToolbar


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
        original_visible = original_pmx_fields_visible(self._translator.get_language())
        self.material_name_jp_label.setVisible(original_visible)
        self.material_jp_name_edit.setVisible(original_visible)

    def _create_material_list_section(self):
        """マテリアルリストセクションを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # マテリアルリストグループ
        self.material_list_group = QGroupBox(self.tr("material_list", "groups"))
        material_list_layout = QVBoxLayout()

        # ツールバー
        toolbar_layout = QHBoxLayout()
        self.authoring_toolbar = AuthoringToolbar(
            actions=("refresh", "create", "duplicate", "delete", "move_up", "move_down"),
            labels={
                "refresh": self.tr("refresh", "buttons"),
                "create": self.tr("create", "buttons"),
                "duplicate": self.tr("duplicate", "buttons"),
                "delete": self.tr("delete", "buttons"),
                "move_up": self.tr("up", "buttons"),
                "move_down": self.tr("down", "buttons"),
            },
            parent=self,
        )
        self.authoring_toolbar.setObjectName("materialAuthoringToolbar")
        self.refresh_btn = self.authoring_toolbar.button("refresh")
        self.create_btn = self.authoring_toolbar.button("create")
        self.duplicate_btn = self.authoring_toolbar.button("duplicate")
        self.delete_btn = self.authoring_toolbar.button("delete")
        self.reindex_up_btn = self.authoring_toolbar.button("move_up")
        self.reindex_down_btn = self.authoring_toolbar.button("move_down")
        self.refresh_btn.setObjectName("materialRefreshButton")
        self.create_btn.setObjectName("materialCreateButton")
        self.duplicate_btn.setObjectName("materialDuplicateButton")
        self.delete_btn.setObjectName("materialDeleteButton")
        self.reindex_up_btn.setObjectName("materialMoveUpButton")
        self.reindex_down_btn.setObjectName("materialMoveDownButton")
        toolbar_layout.addWidget(self.authoring_toolbar)

        # MaterialPresenter enables writes only after a semantic coordinator
        # has been injected for a valid model root.
        for action in ("create", "duplicate", "delete", "move_up", "move_down"):
            self.authoring_toolbar.set_action_enabled(
                action,
                False,
                self.tr("authoring_unavailable", "tooltips"),
                "authoring_unavailable",
            )

        material_list_layout.addLayout(toolbar_layout)

        # マテリアルリスト
        self.material_list = QListWidget()
        self.material_list.setObjectName("materialList")
        self.material_list.setAlternatingRowColors(True)
        # 複数選択を有効化
        self.material_list.setSelectionMode(QListWidget.ExtendedSelection)
        material_list_layout.addWidget(self.material_list)

        # マテリアル検索
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel(self.tr("search", "fields")))
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("materialSearchEdit")
        self.search_edit.setPlaceholderText(self.tr("search_material_name", "placeholders"))
        search_layout.addWidget(self.search_edit)
        material_list_layout.addLayout(search_layout)

        self.material_list_group.setLayout(material_list_layout)
        layout.addWidget(self.material_list_group)

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
        self.basic_group = QGroupBox(self.tr("basic_properties", "groups"))
        basic_layout = QGridLayout()

        # Japanese Name
        self.material_name_jp_label = QLabel(self.tr("material_name_jp", "fields"))
        basic_layout.addWidget(self.material_name_jp_label, 0, 0)
        self.material_jp_name_edit = QLineEdit()
        self.material_jp_name_edit.setObjectName("materialNameJpEdit")
        basic_layout.addWidget(self.material_jp_name_edit, 0, 1, 1, 2)

        # English Name
        self.material_name_en_label = QLabel(self.tr("material_name_en", "fields"))
        basic_layout.addWidget(self.material_name_en_label, 1, 0)
        self.material_en_name_edit = QLineEdit()
        self.material_en_name_edit.setObjectName("materialNameEnEdit")
        basic_layout.addWidget(self.material_en_name_edit, 1, 1, 1, 2)

        # Diffuse Color
        self.diffuse_color_label = QLabel(self.tr("diffuse_color", "fields"))
        basic_layout.addWidget(self.diffuse_color_label, 2, 0)
        self.diffuse_color_widget = self._create_color_widget()
        self.diffuse_color_widget.setObjectName("diffuseColorSwatch")
        basic_layout.addWidget(self.diffuse_color_widget, 2, 1, 1, 2)

        # Transparency (Alpha)
        self.transparency_label = QLabel(self.tr("transparency", "fields"))
        basic_layout.addWidget(self.transparency_label, 3, 0)
        transparency_layout = QHBoxLayout()
        self.transparency_spin = QDoubleSpinBox()
        self.transparency_spin.setObjectName("materialTransparencySpin")
        self.transparency_spin.setRange(0.0, 1.0)
        self.transparency_spin.setSingleStep(0.01)
        self.transparency_spin.setDecimals(2)
        self.transparency_slider = QSlider(Qt.Horizontal)
        self.transparency_slider.setRange(0, 100)
        self.transparency_slider.setValue(100)
        transparency_layout.addWidget(self.transparency_slider)
        transparency_layout.addWidget(self.transparency_spin)
        basic_layout.addLayout(transparency_layout, 3, 1, 1, 2)

        # Specular Color
        self.specular_color_label = QLabel(self.tr("specular_color", "fields"))
        basic_layout.addWidget(self.specular_color_label, 4, 0)
        self.specular_color_widget = self._create_color_widget()
        self.specular_color_widget.setObjectName("specularColorSwatch")
        basic_layout.addWidget(self.specular_color_widget, 4, 1, 1, 2)

        # Specular Coefficient
        self.specular_coefficient_label = QLabel(self.tr("specular_coefficient", "fields"))
        basic_layout.addWidget(self.specular_coefficient_label, 5, 0)
        specular_layout = QHBoxLayout()
        self.specular_coefficient_spin = QDoubleSpinBox()
        self.specular_coefficient_spin.setObjectName("materialSpecularCoefficientSpin")
        self.specular_coefficient_spin.setRange(0.0, 1.0)
        self.specular_coefficient_spin.setSingleStep(0.01)
        self.specular_coefficient_spin.setDecimals(2)
        self.specular_coefficient_slider = QSlider(Qt.Horizontal)
        self.specular_coefficient_slider.setRange(0, 100)
        self.specular_coefficient_slider.setValue(50)
        specular_layout.addWidget(self.specular_coefficient_slider)
        specular_layout.addWidget(self.specular_coefficient_spin)
        basic_layout.addLayout(specular_layout, 5, 1, 1, 2)

        # Ambient Color
        self.ambient_label = QLabel(self.tr("ambient_color", "fields"))
        basic_layout.addWidget(self.ambient_label, 6, 0)
        self.ambient_color_widget = self._create_color_widget()
        self.ambient_color_widget.setObjectName("ambientColorSwatch")
        basic_layout.addWidget(self.ambient_color_widget, 6, 1, 1, 2)

        self.basic_group.setLayout(basic_layout)
        layout.addWidget(self.basic_group)

        # Texture Properties
        self.texture_group = QGroupBox(self.tr("textures", "groups"))
        texture_layout = QGridLayout()

        # Main Texture
        self.texture_label = QLabel(self.tr("texture_path", "fields"))
        texture_layout.addWidget(self.texture_label, 0, 0)
        self.texture_path_edit = QLineEdit()
        self.texture_path_edit.setObjectName("materialTexturePathEdit")
        texture_layout.addWidget(self.texture_path_edit, 0, 1)
        self.texture_browse_btn = QPushButton(self.tr("browse", "buttons"))
        self.texture_browse_btn.setObjectName("materialTextureBrowseButton")
        texture_layout.addWidget(self.texture_browse_btn, 0, 2)

        # Sphere Map
        self.sphere_map_label = QLabel(self.tr("sphere_texture_path", "fields"))
        texture_layout.addWidget(self.sphere_map_label, 1, 0)
        self.sphere_map_path_edit = QLineEdit()
        self.sphere_map_path_edit.setObjectName("materialSphereMapPathEdit")
        texture_layout.addWidget(self.sphere_map_path_edit, 1, 1)
        self.sphere_map_browse_btn = QPushButton(self.tr("browse", "buttons"))
        self.sphere_map_browse_btn.setObjectName("materialSphereMapBrowseButton")
        texture_layout.addWidget(self.sphere_map_browse_btn, 1, 2)

        # Sphere Mode
        self.sphere_mode_label = QLabel(self.tr("sphere_mode", "fields"))
        texture_layout.addWidget(self.sphere_mode_label, 2, 0)
        self.sphere_mode_combo = QComboBox()
        self.sphere_mode_combo.setObjectName("materialSphereModeCombo")
        self.sphere_mode_combo.addItems(
            [
                self.tr("disabled", "sphere_modes"),
                self.tr("multiply", "sphere_modes"),
                self.tr("additive", "sphere_modes"),
                self.tr("subtexture", "sphere_modes"),
            ]
        )
        texture_layout.addWidget(self.sphere_mode_combo, 2, 1, 1, 2)

        # Toon Texture
        self.toon_sharing_check = QCheckBox(self.tr("toon_sharing", "fields"))
        self.toon_sharing_check.setObjectName("materialToonSharingCheck")
        self.toon_sharing_check.setChecked(True)
        texture_layout.addWidget(self.toon_sharing_check, 3, 0, 1, 3)

        self.toon_texture_label = QLabel(self.tr("toon_texture", "fields"))
        texture_layout.addWidget(self.toon_texture_label, 4, 0)
        self.toon_texture_combo = QComboBox()
        self.toon_texture_combo.setObjectName("materialToonTextureCombo")
        self.toon_texture_combo.addItems([f"toon{i:02d}.bmp" for i in range(1, 11)])
        texture_layout.addWidget(self.toon_texture_combo, 4, 1, 1, 2)

        self.toon_texture_path_label = QLabel(self.tr("toon_texture_path", "fields"))
        texture_layout.addWidget(self.toon_texture_path_label, 5, 0)
        self.toon_texture_path_edit = QLineEdit()
        self.toon_texture_path_edit.setObjectName("materialToonTexturePathEdit")
        texture_layout.addWidget(self.toon_texture_path_edit, 5, 1, 1, 2)

        self.toon_texture_index_label = QLabel(self.tr("toon_texture_index", "fields"))
        texture_layout.addWidget(self.toon_texture_index_label, 6, 0)
        self.toon_texture_index_spin = QSpinBox()
        self.toon_texture_index_spin.setObjectName("materialToonTextureIndexSpin")
        self.toon_texture_index_spin.setRange(-1, 2147483647)
        self.toon_texture_index_spin.setValue(-1)
        texture_layout.addWidget(self.toon_texture_index_spin, 6, 1, 1, 2)

        self.original_pmx_path_label = QLabel(self.tr("original_texture_path", "fields"))
        texture_layout.addWidget(self.original_pmx_path_label, 7, 0)
        self.original_pmx_path_edit = QLineEdit()
        self.original_pmx_path_edit.setObjectName("materialOriginalPmxPathEdit")
        self.original_pmx_path_edit.setReadOnly(True)
        texture_layout.addWidget(self.original_pmx_path_edit, 7, 1, 1, 2)

        self.texture_group.setLayout(texture_layout)
        layout.addWidget(self.texture_group)

        # Rendering Flags
        self.flags_group = QGroupBox(self.tr("rendering_flags", "groups"))
        flags_layout = QVBoxLayout()

        self.both_face_check = QCheckBox(self.tr("double_sided", "rendering_checkboxes"))
        self.both_face_check.setObjectName("materialDoubleSidedCheck")
        self.ground_shadow_check = QCheckBox(self.tr("ground_shadow", "rendering_checkboxes"))
        self.ground_shadow_check.setObjectName("materialGroundShadowCheck")
        self.self_shadow_map_check = QCheckBox(self.tr("self_shadow_map", "rendering_checkboxes"))
        self.self_shadow_map_check.setObjectName("materialSelfShadowMapCheck")
        self.self_shadow_check = QCheckBox(self.tr("self_shadow", "rendering_checkboxes"))
        self.self_shadow_check.setObjectName("materialSelfShadowCheck")
        self.edge_draw_check = QCheckBox(self.tr("edge_drawing", "rendering_checkboxes"))
        self.edge_draw_check.setObjectName("materialEdgeDrawCheck")
        self.vertex_color_check = QCheckBox(self.tr("vertex_color", "rendering_checkboxes"))
        self.vertex_color_check.setObjectName("materialVertexColorCheck")
        self.point_draw_check = QCheckBox(self.tr("point_drawing", "rendering_checkboxes"))
        self.point_draw_check.setObjectName("materialPointDrawCheck")
        self.line_draw_check = QCheckBox(self.tr("line_drawing", "rendering_checkboxes"))
        self.line_draw_check.setObjectName("materialLineDrawCheck")

        flags_layout.addWidget(self.both_face_check)
        flags_layout.addWidget(self.ground_shadow_check)
        flags_layout.addWidget(self.self_shadow_map_check)
        flags_layout.addWidget(self.self_shadow_check)
        flags_layout.addWidget(self.edge_draw_check)
        flags_layout.addWidget(self.vertex_color_check)
        flags_layout.addWidget(self.point_draw_check)
        flags_layout.addWidget(self.line_draw_check)

        self.flags_group.setLayout(flags_layout)
        layout.addWidget(self.flags_group)

        # Edge Properties
        self.edge_group = QGroupBox(self.tr("edge_properties", "groups"))
        edge_layout = QGridLayout()

        # Edge Color
        self.edge_color_label = QLabel(self.tr("edge_color", "fields"))
        edge_layout.addWidget(self.edge_color_label, 0, 0)
        self.edge_color_widget = self._create_color_widget()
        self.edge_color_widget.setObjectName("edgeColorSwatch")
        edge_layout.addWidget(self.edge_color_widget, 0, 1, 1, 2)

        # Edge Size
        self.edge_size_label = QLabel(self.tr("edge_size", "fields"))
        edge_layout.addWidget(self.edge_size_label, 1, 0)
        self.edge_size_spin = QDoubleSpinBox()
        self.edge_size_spin.setObjectName("materialEdgeSizeSpin")
        self.edge_size_spin.setRange(0.0, 2.0)
        self.edge_size_spin.setSingleStep(0.05)
        self.edge_size_spin.setDecimals(2)
        edge_layout.addWidget(self.edge_size_spin, 1, 1, 1, 2)

        # Maya viewport outline. This is intentionally separate from the PMX
        # Edge Drawing flag so imported semantics can be preserved while the
        # potentially visible outline pass remains opt-in.
        self.shader_outline_check = QCheckBox(self.tr("shader_outline", "rendering_checkboxes"))
        self.shader_outline_check.setObjectName("materialShaderOutlineCheck")
        self.shader_outline_check.setChecked(False)
        edge_layout.addWidget(self.shader_outline_check, 2, 0, 1, 3)

        self.edge_group.setLayout(edge_layout)
        layout.addWidget(self.edge_group)

        # ストレッチを追加して上に詰める
        layout.addStretch()

        # スクロールエリアに設定
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # ボタンバー
        button_layout = QHBoxLayout()
        self.apply_btn = QPushButton(self.tr("apply", "buttons"))
        self.reset_btn = QPushButton(self.tr("reset", "buttons"))
        self.apply_btn.setObjectName("materialApplyButton")
        self.reset_btn.setObjectName("materialResetButton")
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.reset_btn)

        main_layout.addLayout(button_layout)

        return widget

    def _create_color_widget(self):
        """Create a clickable color display widget"""
        widget = QWidget()
        widget.setFixedSize(50, 20)
        widget.setStyleSheet("background-color: rgb(128, 128, 128); border: 1px solid black;")
        widget.setCursor(Qt.PointingHandCursor)
        return widget

    def _set_details_enabled(self, enabled):
        """Enable/disable all detail widgets"""
        widgets = [
            self.material_jp_name_edit,
            self.material_en_name_edit,
            self.diffuse_color_widget,
            self.transparency_spin,
            self.transparency_slider,
            self.specular_color_widget,
            self.specular_coefficient_spin,
            self.specular_coefficient_slider,
            self.ambient_color_widget,
            self.texture_path_edit,
            self.texture_browse_btn,
            self.sphere_map_path_edit,
            self.sphere_map_browse_btn,
            self.sphere_mode_combo,
            self.toon_sharing_check,
            self.toon_texture_combo,
            self.toon_texture_path_edit,
            self.toon_texture_index_spin,
            self.original_pmx_path_edit,
            self.both_face_check,
            self.ground_shadow_check,
            self.self_shadow_map_check,
            self.self_shadow_check,
            self.edge_draw_check,
            self.vertex_color_check,
            self.point_draw_check,
            self.line_draw_check,
            self.edge_color_widget,
            self.edge_size_spin,
            self.shader_outline_check,
            self.apply_btn,
            self.reset_btn,
        ]
        for widget in widgets:
            widget.setEnabled(enabled)

    def _show_placeholder(self):
        """マテリアルリストが空の場合のプレースホルダーを表示"""
        if self.material_list.count() == 0:
            placeholder_item = QListWidgetItem(self.tr("no_materials", "placeholders"))
            placeholder_item.setFlags(placeholder_item.flags() & ~Qt.ItemIsSelectable)
            self.material_list.addItem(placeholder_item)

    def retranslateUi(self):
        """言語切り替え時にUIを再翻訳"""
        # GroupBoxes
        if hasattr(self, "material_list_group"):
            self.material_list_group.setTitle(self.tr("material_list", "groups"))
        if hasattr(self, "basic_group"):
            self.basic_group.setTitle(self.tr("basic_properties", "groups"))
        if hasattr(self, "texture_group"):
            self.texture_group.setTitle(self.tr("textures", "groups"))
        if hasattr(self, "flags_group"):
            self.flags_group.setTitle(self.tr("rendering_flags", "groups"))
        if hasattr(self, "edge_group"):
            self.edge_group.setTitle(self.tr("edge_properties", "groups"))

        # Buttons
        if hasattr(self, "authoring_toolbar"):
            self.authoring_toolbar.retranslate(
                {
                    "refresh": self.tr("refresh", "buttons"),
                    "create": self.tr("create", "buttons"),
                    "duplicate": self.tr("duplicate", "buttons"),
                    "delete": self.tr("delete", "buttons"),
                    "move_up": self.tr("up", "buttons"),
                    "move_down": self.tr("down", "buttons"),
                },
                reason_resolver=lambda key: self.tr(key, "tooltips"),
            )
        if hasattr(self, "import_path_button"):
            self.import_path_button.setText(self.tr("browse", "buttons"))
        if hasattr(self, "texture_browse_btn"):
            self.texture_browse_btn.setText(self.tr("browse", "buttons"))
        if hasattr(self, "sphere_map_browse_btn"):
            self.sphere_map_browse_btn.setText(self.tr("browse", "buttons"))
        if hasattr(self, "apply_btn"):
            self.apply_btn.setText(self.tr("apply", "buttons"))
        if hasattr(self, "reset_btn"):
            self.reset_btn.setText(self.tr("reset", "buttons"))

        # Labels
        if hasattr(self, "material_name_jp_label"):
            self.material_name_jp_label.setText(self.tr("material_name_jp", "fields"))
            original_visible = original_pmx_fields_visible(self._translator.get_language())
            self.material_name_jp_label.setVisible(original_visible)
            self.material_jp_name_edit.setVisible(original_visible)
        if hasattr(self, "material_name_en_label"):
            self.material_name_en_label.setText(self.tr("material_name_en", "fields"))
        if hasattr(self, "diffuse_color_label"):
            self.diffuse_color_label.setText(self.tr("diffuse_color", "fields"))
        if hasattr(self, "transparency_label"):
            self.transparency_label.setText(self.tr("transparency", "fields"))
        if hasattr(self, "specular_color_label"):
            self.specular_color_label.setText(self.tr("specular_color", "fields"))
        if hasattr(self, "specular_coefficient_label"):
            self.specular_coefficient_label.setText(self.tr("specular_coefficient", "fields"))
        if hasattr(self, "ambient_label"):
            self.ambient_label.setText(self.tr("ambient_color", "fields"))
        if hasattr(self, "texture_label"):
            self.texture_label.setText(self.tr("texture_path", "fields"))
        if hasattr(self, "sphere_map_label"):
            self.sphere_map_label.setText(self.tr("sphere_texture_path", "fields"))
        if hasattr(self, "sphere_mode_label"):
            self.sphere_mode_label.setText(self.tr("sphere_mode", "fields"))
        if hasattr(self, "toon_texture_label"):
            self.toon_texture_label.setText(self.tr("toon_texture", "fields"))
        if hasattr(self, "toon_sharing_check"):
            self.toon_sharing_check.setText(self.tr("toon_sharing", "fields"))
        if hasattr(self, "toon_texture_path_label"):
            self.toon_texture_path_label.setText(self.tr("toon_texture_path", "fields"))
        if hasattr(self, "toon_texture_index_label"):
            self.toon_texture_index_label.setText(self.tr("toon_texture_index", "fields"))
        if hasattr(self, "original_pmx_path_label"):
            self.original_pmx_path_label.setText(self.tr("original_texture_path", "fields"))
        if hasattr(self, "edge_color_label"):
            self.edge_color_label.setText(self.tr("edge_color", "fields"))
        if hasattr(self, "edge_size_label"):
            self.edge_size_label.setText(self.tr("edge_size", "fields"))
        if hasattr(self, "shader_outline_check"):
            self.shader_outline_check.setText(self.tr("shader_outline", "rendering_checkboxes"))
        # CheckBoxes
        if hasattr(self, "both_face_check"):
            self.both_face_check.setText(self.tr("double_sided", "rendering_checkboxes"))
        if hasattr(self, "ground_shadow_check"):
            self.ground_shadow_check.setText(self.tr("ground_shadow", "rendering_checkboxes"))
        if hasattr(self, "self_shadow_map_check"):
            self.self_shadow_map_check.setText(self.tr("self_shadow_map", "rendering_checkboxes"))
        if hasattr(self, "self_shadow_check"):
            self.self_shadow_check.setText(self.tr("self_shadow", "rendering_checkboxes"))
        if hasattr(self, "edge_draw_check"):
            self.edge_draw_check.setText(self.tr("edge_drawing", "rendering_checkboxes"))
        if hasattr(self, "vertex_color_check"):
            self.vertex_color_check.setText(self.tr("vertex_color", "rendering_checkboxes"))
        if hasattr(self, "point_draw_check"):
            self.point_draw_check.setText(self.tr("point_drawing", "rendering_checkboxes"))
        if hasattr(self, "line_draw_check"):
            self.line_draw_check.setText(self.tr("line_drawing", "rendering_checkboxes"))
        # ComboBox items - Sphere modes
        if hasattr(self, "sphere_mode_combo"):
            self.sphere_mode_combo.clear()
            self.sphere_mode_combo.addItems(
                [
                    self.tr("disabled", "sphere_modes"),
                    self.tr("multiply", "sphere_modes"),
                    self.tr("additive", "sphere_modes"),
                    self.tr("subtexture", "sphere_modes"),
                ]
            )

        # Placeholders
        if hasattr(self, "search_edit"):
            self.search_edit.setPlaceholderText(self.tr("search_material_name", "placeholders"))

        # Update material list placeholder if empty
        if hasattr(self, "material_list") and self.material_list.count() == 1:
            item = self.material_list.item(0)
            if item and item.flags() & ~Qt.ItemIsSelectable:
                item.setText(self.tr("no_materials", "placeholders"))
