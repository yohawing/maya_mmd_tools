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
    QDoubleSpinBox,
    QTabWidget,
    QTableWidget,
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
        self.group_box = QGroupBox(self.tr("morph_groups", "groups"))
        group_layout = QVBoxLayout()

        # ツールバー
        toolbar_layout = QHBoxLayout()
        self.add_group_btn = QPushButton(self.tr("add", "buttons"))
        self.remove_group_btn = QPushButton(self.tr("delete", "buttons"))
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
        self.group_list.addItem(self.tr("show_all", "morph_groups"))
        default_groups = [
            self.tr("eyebrows", "morph_groups"),
            self.tr("eyes", "morph_groups"),
            self.tr("mouth", "morph_groups"),
            self.tr("other", "morph_groups"),
        ]
        for group in default_groups:
            self.group_list.addItem(group)

        group_layout.addWidget(self.group_list)

        self.group_box.setLayout(group_layout)
        layout.addWidget(self.group_box)

        return widget

    def _create_morph_list_section(self):
        """モーフリストセクションを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # モーフリスト
        self.morph_list_group = QGroupBox(self.tr("morph_list", "groups"))
        morph_list_layout = QVBoxLayout()

        # ツールバー
        toolbar_layout = QHBoxLayout()
        self.refresh_morphs_btn = QPushButton(self.tr("refresh", "buttons"))
        self.refresh_morphs_btn.setMaximumWidth(60)
        self.select_in_maya_btn = QPushButton(self.tr("select_in_maya", "actions"))
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
        self.search_label = QLabel(self.tr("search", "fields"))
        search_layout.addWidget(self.search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr("search_morph_name", "placeholders"))
        search_layout.addWidget(self.search_edit)
        morph_list_layout.addLayout(search_layout)

        self.morph_list_group.setLayout(morph_list_layout)
        layout.addWidget(self.morph_list_group)

        return widget

    def _create_morph_details_section(self):
        """モーフ詳細セクションを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # タブウィジェット
        self.detail_tabs = QTabWidget()

        # 基本情報タブ
        self.detail_tabs.addTab(self._create_basic_info_tab(), self.tr("basic_information", "tabs"))

        # オフセット情報タブ
        self.detail_tabs.addTab(self._create_offset_info_tab(), self.tr("offset_information", "tabs"))

        # Maya連携タブ
        self.detail_tabs.addTab(self._create_maya_connection_tab(), self.tr("maya_connection", "tabs"))

        layout.addWidget(self.detail_tabs)

        # プレビューセクション
        self.preview_group = QGroupBox(self.tr("preview", "groups"))
        preview_layout = QVBoxLayout()

        # スライダー
        slider_layout = QHBoxLayout()
        self.apply_rate_label = QLabel(self.tr("apply_rate", "fields"))
        slider_layout.addWidget(self.apply_rate_label)
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
        self.reset_slider_btn = QPushButton(self.tr("reset", "buttons"))
        self.reset_all_btn = QPushButton(self.tr("reset_all", "actions"))
        reset_layout.addStretch()
        reset_layout.addWidget(self.reset_slider_btn)
        reset_layout.addWidget(self.reset_all_btn)
        preview_layout.addLayout(reset_layout)

        # プリセット機能
        preset_layout = QHBoxLayout()
        self.preset_label = QLabel(self.tr("preset", "fields"))
        preset_layout.addWidget(self.preset_label)
        self.preset_combo = QComboBox()
        self.preset_combo.setEditable(True)
        self.preset_combo.addItems(
            [
                self.tr("none", "presets"),
                self.tr("smile", "presets"),
                self.tr("wink", "presets"),
                self.tr("surprise", "presets"),
                self.tr("sadness", "presets"),
            ]
        )
        preset_layout.addWidget(self.preset_combo)
        self.save_preset_btn = QPushButton(self.tr("save", "buttons"))
        self.load_preset_btn = QPushButton(self.tr("load", "buttons"))
        self.delete_preset_btn = QPushButton(self.tr("delete", "buttons"))
        preset_layout.addWidget(self.save_preset_btn)
        preset_layout.addWidget(self.load_preset_btn)
        preset_layout.addWidget(self.delete_preset_btn)
        preview_layout.addLayout(preset_layout)

        self.preview_group.setLayout(preview_layout)
        layout.addWidget(self.preview_group)

        # ボタンバー
        button_layout = QHBoxLayout()
        self.apply_btn = QPushButton(self.tr("apply", "buttons"))
        self.reset_btn = QPushButton(self.tr("reset", "buttons"))
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
        self.morph_name_jp_label = QLabel(self.tr("morph_name_jp", "fields"))
        self.morph_name_en_label = QLabel(self.tr("morph_name_en", "fields"))
        layout.addRow(self.morph_name_jp_label, self.morph_name_jp_edit)
        layout.addRow(self.morph_name_en_label, self.morph_name_en_edit)

        # パネル
        panel_layout = QHBoxLayout()
        self.panel_combo = QComboBox()
        self.panel_combo.addItems(
            [
                self.tr("none", "morph_panels"),
                self.tr("eyebrows_lower_left", "morph_panels"),
                self.tr("eyes_upper_left", "morph_panels"),
                self.tr("mouth_upper_right", "morph_panels"),
                self.tr("other_lower_right", "morph_panels"),
            ]
        )
        panel_layout.addWidget(self.panel_combo)
        self.panel_label = QLabel(self.tr("panel", "fields"))
        layout.addRow(self.panel_label, panel_layout)

        # モーフタイプ
        self.morph_type_combo = QComboBox()
        self.morph_type_combo.addItems(
            [
                self.tr("vertex", "morph_types"),
                "UV",
                "UV1",
                "UV2",
                "UV3",
                "UV4",
                self.tr("additional_uv1", "morph_types"),
                self.tr("additional_uv2", "morph_types"),
                self.tr("additional_uv3", "morph_types"),
                self.tr("additional_uv4", "morph_types"),
                self.tr("bone", "morph_types"),
                self.tr("material", "morph_types"),
                self.tr("group", "morph_types"),
                self.tr("flip", "morph_types"),
                self.tr("impulse", "morph_types"),
            ]
        )
        self.morph_type_label = QLabel(self.tr("type", "fields"))
        layout.addRow(self.morph_type_label, self.morph_type_combo)

        # グループ設定
        group_layout = QHBoxLayout()
        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.addItems(
            [
                self.tr("eyebrows", "morph_groups"),
                self.tr("eyes", "morph_groups"),
                self.tr("mouth", "morph_groups"),
                self.tr("other", "morph_groups"),
            ]
        )
        group_layout.addWidget(self.group_combo)
        self.group_label = QLabel(self.tr("group", "fields"))
        layout.addRow(self.group_label, group_layout)

        return widget

    def _create_offset_info_tab(self):
        """オフセット情報タブを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # オフセット数表示（オフセットデータ表示は未対応のため未対応ラベルを表示）
        info_layout = QHBoxLayout()
        self.offset_count_label = QLabel(self.tr("offset_not_supported", "labels"))
        info_layout.addWidget(self.offset_count_label)
        info_layout.addStretch()
        layout.addLayout(info_layout)

        # オフセットテーブル
        self.offset_table = QTableWidget()
        self.offset_table.setColumnCount(5)
        self.offset_table.setHorizontalHeaderLabels(
            [
                self.tr("index", "table_headers"),
                self.tr("type", "table_headers"),
                self.tr("element", "table_headers"),
                self.tr("value", "table_headers"),
                self.tr("details", "table_headers"),
            ]
        )
        self.offset_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.offset_table)

        return widget

    def _create_maya_connection_tab(self):
        """Maya連携タブを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # ブレンドシェイプ設定
        self.blend_group = QGroupBox(self.tr("blendshape_connection", "groups"))
        blend_layout = QFormLayout()

        # 連携状態
        self.connection_status_label = QLabel(self.tr("not_connected", "status"))
        self.connection_status_label.setStyleSheet("color: red;")
        self.status_label = QLabel(self.tr("status", "fields"))
        blend_layout.addRow(self.status_label, self.connection_status_label)

        # ブレンドシェイプノード
        node_layout = QHBoxLayout()
        self.blend_shape_edit = QLineEdit()
        self.blend_shape_edit.setReadOnly(True)
        self.select_blend_shape_btn = QPushButton(self.tr("select", "buttons"))
        self.select_blend_shape_btn.setMaximumWidth(60)
        node_layout.addWidget(self.blend_shape_edit)
        node_layout.addWidget(self.select_blend_shape_btn)
        self.node_label = QLabel(self.tr("node", "fields"))
        blend_layout.addRow(self.node_label, node_layout)

        # ターゲット名
        self.target_name_edit = QLineEdit()
        self.target_name_label = QLabel(self.tr("target_name", "fields"))
        blend_layout.addRow(self.target_name_label, self.target_name_edit)

        # 連携ボタン
        connection_layout = QHBoxLayout()
        self.connect_btn = QPushButton(self.tr("connect", "actions"))
        self.disconnect_btn = QPushButton(self.tr("disconnect", "actions"))
        self.auto_connect_btn = QPushButton(self.tr("auto_connect", "actions"))
        connection_layout.addWidget(self.connect_btn)
        connection_layout.addWidget(self.disconnect_btn)
        connection_layout.addWidget(self.auto_connect_btn)
        connection_layout.addStretch()
        blend_layout.addRow("", connection_layout)

        self.blend_group.setLayout(blend_layout)
        layout.addWidget(self.blend_group)

        # 詳細設定
        self.advanced_group = QGroupBox(self.tr("advanced_settings", "groups"))
        advanced_layout = QFormLayout()

        # 反転
        self.invert_check = QCheckBox(self.tr("invert_value", "checkboxes"))
        advanced_layout.addRow("", self.invert_check)

        # 乗数
        self.multiplier_spin = QDoubleSpinBox()
        self.multiplier_spin.setRange(-10.0, 10.0)
        self.multiplier_spin.setValue(1.0)
        self.multiplier_spin.setSingleStep(0.1)
        self.multiplier_label = QLabel(self.tr("multiplier", "fields"))
        advanced_layout.addRow(self.multiplier_label, self.multiplier_spin)

        self.advanced_group.setLayout(advanced_layout)
        layout.addWidget(self.advanced_group)

        layout.addStretch()

        return widget

    def set_morph_details_enabled(self, enabled):
        """モーフ詳細セクションの有効/無効を設定"""
        self.detail_tabs.setEnabled(enabled)
        self.morph_slider.setEnabled(enabled)
        self.reset_slider_btn.setEnabled(enabled)
        self.apply_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)

    def retranslateUi(self):
        """言語切り替え時にUIを再翻訳"""
        # GroupBoxes
        if hasattr(self, "group_box"):
            self.group_box.setTitle(self.tr("morph_groups", "groups"))
        if hasattr(self, "morph_list_group"):
            self.morph_list_group.setTitle(self.tr("morph_list", "groups"))
        if hasattr(self, "preview_group"):
            self.preview_group.setTitle(self.tr("preview", "groups"))
        if hasattr(self, "blend_group"):
            self.blend_group.setTitle(self.tr("blendshape_connection", "groups"))
        if hasattr(self, "advanced_group"):
            self.advanced_group.setTitle(self.tr("advanced_settings", "groups"))

        # Buttons
        if hasattr(self, "add_group_btn"):
            self.add_group_btn.setText(self.tr("add", "buttons"))
        if hasattr(self, "remove_group_btn"):
            self.remove_group_btn.setText(self.tr("delete", "buttons"))
        if hasattr(self, "refresh_morphs_btn"):
            self.refresh_morphs_btn.setText(self.tr("refresh", "buttons"))
        if hasattr(self, "select_in_maya_btn"):
            self.select_in_maya_btn.setText(self.tr("select_in_maya", "actions"))
        if hasattr(self, "reset_slider_btn"):
            self.reset_slider_btn.setText(self.tr("reset", "buttons"))
        if hasattr(self, "reset_all_btn"):
            self.reset_all_btn.setText(self.tr("reset_all", "actions"))
        if hasattr(self, "save_preset_btn"):
            self.save_preset_btn.setText(self.tr("save", "buttons"))
        if hasattr(self, "load_preset_btn"):
            self.load_preset_btn.setText(self.tr("load", "buttons"))
        if hasattr(self, "delete_preset_btn"):
            self.delete_preset_btn.setText(self.tr("delete", "buttons"))
        if hasattr(self, "apply_btn"):
            self.apply_btn.setText(self.tr("apply", "buttons"))
        if hasattr(self, "reset_btn"):
            self.reset_btn.setText(self.tr("reset", "buttons"))
        if hasattr(self, "select_blend_shape_btn"):
            self.select_blend_shape_btn.setText(self.tr("select", "buttons"))
        if hasattr(self, "connect_btn"):
            self.connect_btn.setText(self.tr("connect", "actions"))
        if hasattr(self, "disconnect_btn"):
            self.disconnect_btn.setText(self.tr("disconnect", "actions"))
        if hasattr(self, "auto_connect_btn"):
            self.auto_connect_btn.setText(self.tr("auto_connect", "actions"))

        # Labels
        if hasattr(self, "search_label"):
            self.search_label.setText(self.tr("search", "fields"))
        if hasattr(self, "apply_rate_label"):
            self.apply_rate_label.setText(self.tr("apply_rate", "fields"))
        if hasattr(self, "preset_label"):
            self.preset_label.setText(self.tr("preset", "fields"))
        if hasattr(self, "morph_name_jp_label"):
            self.morph_name_jp_label.setText(self.tr("morph_name_jp", "fields"))
        if hasattr(self, "morph_name_en_label"):
            self.morph_name_en_label.setText(self.tr("morph_name_en", "fields"))
        if hasattr(self, "panel_label"):
            self.panel_label.setText(self.tr("panel", "fields"))
        if hasattr(self, "morph_type_label"):
            self.morph_type_label.setText(self.tr("type", "fields"))
        if hasattr(self, "group_label"):
            self.group_label.setText(self.tr("group", "fields"))
        if hasattr(self, "status_label"):
            self.status_label.setText(self.tr("status", "fields"))
        if hasattr(self, "node_label"):
            self.node_label.setText(self.tr("node", "fields"))
        if hasattr(self, "target_name_label"):
            self.target_name_label.setText(self.tr("target_name", "fields"))
        if hasattr(self, "multiplier_label"):
            self.multiplier_label.setText(self.tr("multiplier", "fields"))

        # オフセット表示は未対応のため、件数ではなく未対応ラベルを表示する
        if hasattr(self, "offset_count_label"):
            self.offset_count_label.setText(self.tr("offset_not_supported", "labels"))

        # CheckBoxes
        if hasattr(self, "invert_check"):
            self.invert_check.setText(self.tr("invert_value", "checkboxes"))

        # Tab widget texts
        if hasattr(self, "detail_tabs"):
            if self.detail_tabs.count() >= 3:
                self.detail_tabs.setTabText(0, self.tr("basic_information", "tabs"))
                self.detail_tabs.setTabText(1, self.tr("offset_information", "tabs"))
                self.detail_tabs.setTabText(2, self.tr("maya_connection", "tabs"))

        # Group list items
        if hasattr(self, "group_list"):
            if self.group_list.count() >= 5:
                self.group_list.item(0).setText(self.tr("show_all", "morph_groups"))
                self.group_list.item(1).setText(self.tr("eyebrows", "morph_groups"))
                self.group_list.item(2).setText(self.tr("eyes", "morph_groups"))
                self.group_list.item(3).setText(self.tr("mouth", "morph_groups"))
                self.group_list.item(4).setText(self.tr("other", "morph_groups"))

        # ComboBox items - Panel
        if hasattr(self, "panel_combo"):
            self.panel_combo.clear()
            self.panel_combo.addItems(
                [
                    self.tr("none", "morph_panels"),
                    self.tr("eyebrows_lower_left", "morph_panels"),
                    self.tr("eyes_upper_left", "morph_panels"),
                    self.tr("mouth_upper_right", "morph_panels"),
                    self.tr("other_lower_right", "morph_panels"),
                ]
            )

        # ComboBox items - Morph Type
        if hasattr(self, "morph_type_combo"):
            self.morph_type_combo.clear()
            self.morph_type_combo.addItems(
                [
                    self.tr("vertex", "morph_types"),
                    "UV",
                    "UV1",
                    "UV2",
                    "UV3",
                    "UV4",
                    self.tr("additional_uv1", "morph_types"),
                    self.tr("additional_uv2", "morph_types"),
                    self.tr("additional_uv3", "morph_types"),
                    self.tr("additional_uv4", "morph_types"),
                    self.tr("bone", "morph_types"),
                    self.tr("material", "morph_types"),
                    self.tr("group", "morph_types"),
                    self.tr("flip", "morph_types"),
                    self.tr("impulse", "morph_types"),
                ]
            )

        # ComboBox items - Preset
        if hasattr(self, "preset_combo"):
            current_text = self.preset_combo.currentText()
            self.preset_combo.clear()
            self.preset_combo.addItems(
                [
                    self.tr("none", "presets"),
                    self.tr("smile", "presets"),
                    self.tr("wink", "presets"),
                    self.tr("surprise", "presets"),
                    self.tr("sadness", "presets"),
                ]
            )
            # Try to restore selection if it was a preset
            if current_text in ["なし", "笑顔", "ウィンク", "驚き", "悲しみ", "None", "Smile", "Wink", "Surprise", "Sadness"]:
                index = self.preset_combo.findText(current_text)
                if index >= 0:
                    self.preset_combo.setCurrentIndex(index)

        # Table headers - Offset table
        if hasattr(self, "offset_table"):
            self.offset_table.setHorizontalHeaderLabels(
                [
                    self.tr("index", "table_headers"),
                    self.tr("type", "table_headers"),
                    self.tr("element", "table_headers"),
                    self.tr("value", "table_headers"),
                    self.tr("details", "table_headers"),
                ]
            )

        # Status label text
        if hasattr(self, "connection_status_label"):
            if self.connection_status_label.styleSheet() == "color: red;":
                self.connection_status_label.setText(self.tr("not_connected", "status"))
            else:
                self.connection_status_label.setText(self.tr("connected", "status"))

        # Placeholders
        if hasattr(self, "search_edit"):
            self.search_edit.setPlaceholderText(self.tr("search_morph_name", "placeholders"))
