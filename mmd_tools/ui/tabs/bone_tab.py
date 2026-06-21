from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QGridLayout,
    QSplitter,
    Qt,
    QListWidget,
    QTableWidget,
    QScrollArea,
)
from ..base_tab import BaseTab


class BoneTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BoneTab")

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # スプリッターで左右を分割
        splitter = QSplitter(Qt.Horizontal)

        # 左側: ボーンツリー
        left_widget = self._create_bone_tree_section()
        splitter.addWidget(left_widget)

        # 右側: ボーン詳細設定
        right_widget = self._create_bone_details_section()
        splitter.addWidget(right_widget)

        # 初期のスプリッター比率
        splitter.setSizes([400, 600])

        main_layout.addWidget(splitter)

    def _create_bone_tree_section(self):
        """ボーンツリーセクションを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # ボーンリストグループ
        self.bone_tree_group = QGroupBox(self.tr("bone_list", "groups"))
        bone_tree_layout = QVBoxLayout()

        # ツールバー
        toolbar_layout = QHBoxLayout()
        self.refresh_btn = QPushButton(self.tr("refresh", "buttons"))
        self.refresh_btn.setMaximumWidth(60)

        toolbar_layout.addWidget(self.refresh_btn)
        toolbar_layout.addStretch()

        bone_tree_layout.addLayout(toolbar_layout)

        # ボーンリスト（単純なリスト表示）
        self.bone_list = QListWidget()
        self.bone_list.setAlternatingRowColors(True)
        # 複数選択を有効化
        self.bone_list.setSelectionMode(QListWidget.ExtendedSelection)
        bone_tree_layout.addWidget(self.bone_list)

        # ボーン検索
        search_layout = QHBoxLayout()
        self.search_label = QLabel(self.tr("search", "fields"))
        search_layout.addWidget(self.search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr("search_bone_name", "placeholders"))
        search_layout.addWidget(self.search_edit)
        bone_tree_layout.addLayout(search_layout)

        self.bone_tree_group.setLayout(bone_tree_layout)
        layout.addWidget(self.bone_tree_group)

        return widget

    def _create_bone_details_section(self):
        """ボーン詳細設定セクションを作成"""
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # スクロール可能なエリアを作成
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)

        # スクロールエリア内のコンテンツウィジェット
        self.bone_details_content = QWidget()
        layout = QVBoxLayout(self.bone_details_content)
        layout.setContentsMargins(5, 5, 5, 5)

        # 基本情報セクション
        self.basic_group = QGroupBox(self.tr("basic_information", "groups"))
        self.basic_group.setLayout(self._create_basic_info_layout())
        layout.addWidget(self.basic_group)

        # 変形制御セクション
        self.transform_group = QGroupBox(self.tr("transform_control", "groups"))
        self.transform_group.setLayout(self._create_transform_control_layout())
        layout.addWidget(self.transform_group)

        # IK設定セクション
        self.ik_group = QGroupBox(self.tr("ik_settings", "groups"))
        self.ik_group.setLayout(self._create_ik_settings_layout())
        layout.addWidget(self.ik_group)

        # 付与設定セクション
        self.grant_group = QGroupBox(self.tr("grant_settings", "groups"))
        self.grant_group.setLayout(self._create_grant_settings_layout())
        layout.addWidget(self.grant_group)

        # 軸制限セクション
        self.axis_group = QGroupBox(self.tr("axis_limit", "groups"))
        self.axis_group.setLayout(self._create_axis_limit_layout())
        layout.addWidget(self.axis_group)

        # ストレッチを追加して上に詰める
        layout.addStretch()

        # スクロールエリアに設定
        scroll_area.setWidget(self.bone_details_content)
        main_layout.addWidget(scroll_area)

        # ボタンバー
        button_layout = QHBoxLayout()
        self.apply_btn = QPushButton(self.tr("apply", "buttons"))
        self.reset_btn = QPushButton(self.tr("reset", "buttons"))
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.reset_btn)

        main_layout.addLayout(button_layout)

        return widget

    def _create_basic_info_layout(self):
        """基本情報レイアウトを作成"""
        layout = QFormLayout()

        # ボーン名
        self.bone_name_jp_edit = QLineEdit()
        self.bone_name_en_edit = QLineEdit()
        self.bone_name_jp_label = QLabel(self.tr("bone_name_jp", "fields"))
        self.bone_name_en_label = QLabel(self.tr("bone_name_en", "fields"))
        layout.addRow(self.bone_name_jp_label, self.bone_name_jp_edit)
        layout.addRow(self.bone_name_en_label, self.bone_name_en_edit)

        # 親ボーン
        self.parent_bone_edit = QLineEdit()
        self.parent_bone_edit.setReadOnly(True)
        self.parent_bone_label = QLabel(self.tr("parent_bone", "fields"))
        layout.addRow(self.parent_bone_label, self.parent_bone_edit)

        # 位置
        position_layout = QGridLayout()
        self.pos_x_spin = QDoubleSpinBox()
        self.pos_y_spin = QDoubleSpinBox()
        self.pos_z_spin = QDoubleSpinBox()
        for spin in [self.pos_x_spin, self.pos_y_spin, self.pos_z_spin]:
            spin.setRange(-9999.0, 9999.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.1)

        position_layout.addWidget(QLabel("X:"), 0, 0)
        position_layout.addWidget(self.pos_x_spin, 0, 1)
        position_layout.addWidget(QLabel("Y:"), 0, 2)
        position_layout.addWidget(self.pos_y_spin, 0, 3)
        position_layout.addWidget(QLabel("Z:"), 0, 4)
        position_layout.addWidget(self.pos_z_spin, 0, 5)
        self.position_label = QLabel(self.tr("position", "fields"))
        layout.addRow(self.position_label, position_layout)

        # 変形階層
        self.deform_layer_spin = QSpinBox()
        self.deform_layer_spin.setRange(0, 9999)
        self.deform_layer_label = QLabel(self.tr("deform_layer", "fields"))
        layout.addRow(self.deform_layer_label, self.deform_layer_spin)

        # 接続先
        connection_layout = QHBoxLayout()
        self.connection_type_combo = QComboBox()
        self.connection_type_combo.addItems(
            [self.tr("coordinate_offset", "bone_connection_types"), self.tr("bone", "bone_connection_types")]
        )
        self.connection_bone_edit = QLineEdit()
        self.connection_bone_edit.setReadOnly(True)

        connection_layout.addWidget(self.connection_type_combo)
        connection_layout.addWidget(self.connection_bone_edit)
        self.connection_label = QLabel(self.tr("connection", "fields"))
        layout.addRow(self.connection_label, connection_layout)

        # 接続先オフセット
        offset_layout = QGridLayout()
        self.offset_x_spin = QDoubleSpinBox()
        self.offset_y_spin = QDoubleSpinBox()
        self.offset_z_spin = QDoubleSpinBox()
        for spin in [self.offset_x_spin, self.offset_y_spin, self.offset_z_spin]:
            spin.setRange(-9999.0, 9999.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.1)

        offset_layout.addWidget(QLabel("X:"), 0, 0)
        offset_layout.addWidget(self.offset_x_spin, 0, 1)
        offset_layout.addWidget(QLabel("Y:"), 0, 2)
        offset_layout.addWidget(self.offset_y_spin, 0, 3)
        offset_layout.addWidget(QLabel("Z:"), 0, 4)
        offset_layout.addWidget(self.offset_z_spin, 0, 5)
        self.offset_label = QLabel(self.tr("offset", "fields"))
        layout.addRow(self.offset_label, offset_layout)

        return layout

    def _create_transform_control_layout(self):
        """変形制御レイアウトを作成"""
        layout = QVBoxLayout()

        # 基本フラグ
        self.flags_group = QGroupBox(self.tr("basic_settings", "groups"))
        flags_layout = QGridLayout()

        self.rotatable_check = QCheckBox(self.tr("rotatable", "bone_flags"))
        self.movable_check = QCheckBox(self.tr("movable", "bone_flags"))
        self.visible_check = QCheckBox(self.tr("visible", "bone_flags"))
        self.enabled_check = QCheckBox(self.tr("enabled", "bone_flags"))

        flags_layout.addWidget(self.rotatable_check, 0, 0)
        flags_layout.addWidget(self.movable_check, 0, 1)
        flags_layout.addWidget(self.visible_check, 1, 0)
        flags_layout.addWidget(self.enabled_check, 1, 1)

        self.flags_group.setLayout(flags_layout)
        layout.addWidget(self.flags_group)

        # 特殊フラグ
        self.special_group = QGroupBox(self.tr("special_settings", "groups"))
        special_layout = QGridLayout()

        self.after_physics_check = QCheckBox(self.tr("after_physics", "bone_flags"))
        self.external_parent_check = QCheckBox(self.tr("external_parent", "bone_flags"))

        special_layout.addWidget(self.after_physics_check, 0, 0)
        special_layout.addWidget(self.external_parent_check, 0, 1)

        # 外部親キー
        self.external_parent_key_label = QLabel(self.tr("external_parent_key", "fields"))
        self.external_parent_key_spin = QSpinBox()
        self.external_parent_key_spin.setRange(-1, 9999)
        special_layout.addWidget(self.external_parent_key_label, 1, 0)
        special_layout.addWidget(self.external_parent_key_spin, 1, 1)

        # 初期状態では非表示
        self.external_parent_key_label.setVisible(False)
        self.external_parent_key_spin.setVisible(False)

        self.special_group.setLayout(special_layout)
        layout.addWidget(self.special_group)

        layout.addStretch()

        return layout

    def _create_ik_settings_layout(self):
        """IK設定レイアウトを作成"""
        layout = QVBoxLayout()

        # IK有効化
        self.ik_enabled_check = QCheckBox(self.tr("enable_ik", "checkboxes"))
        layout.addWidget(self.ik_enabled_check)

        # IK設定グループ
        self.ik_settings_group = QGroupBox(self.tr("ik_settings", "groups"))
        ik_layout = QFormLayout()

        # IKターゲット
        target_layout = QHBoxLayout()
        self.ik_target_edit = QLineEdit()
        self.ik_target_edit.setReadOnly(True)
        self.select_ik_target_btn = QPushButton(self.tr("select", "buttons"))
        self.select_ik_target_btn.setMaximumWidth(60)
        target_layout.addWidget(self.ik_target_edit)
        target_layout.addWidget(self.select_ik_target_btn)
        self.ik_target_label = QLabel(self.tr("ik_target", "fields"))
        ik_layout.addRow(self.ik_target_label, target_layout)

        # IKループ回数
        self.ik_loop_spin = QSpinBox()
        self.ik_loop_spin.setRange(1, 255)
        self.ik_loop_spin.setValue(10)
        self.ik_loop_label = QLabel(self.tr("ik_loop_count", "fields"))
        ik_layout.addRow(self.ik_loop_label, self.ik_loop_spin)

        # 制限角度
        self.ik_limit_angle_spin = QDoubleSpinBox()
        self.ik_limit_angle_spin.setRange(0.0, 180.0)
        self.ik_limit_angle_spin.setValue(114.5916)  # PMDデフォルト値を4で割った値
        self.ik_limit_angle_spin.setSingleStep(1.0)
        self.ik_limit_angle_spin.setSuffix("°")
        self.ik_limit_angle_label = QLabel(self.tr("ik_limit_angle", "fields"))
        ik_layout.addRow(self.ik_limit_angle_label, self.ik_limit_angle_spin)

        self.ik_settings_group.setLayout(ik_layout)
        layout.addWidget(self.ik_settings_group)

        # 初期状態では非表示
        self.ik_settings_group.setVisible(False)

        # IKリンクリスト
        self.ik_links_group = QGroupBox(self.tr("ik_links", "groups"))
        links_layout = QVBoxLayout()

        # ツールバー
        links_toolbar = QHBoxLayout()
        self.add_ik_link_btn = QPushButton(self.tr("add", "buttons"))
        self.remove_ik_link_btn = QPushButton(self.tr("delete", "buttons"))
        self.move_up_btn = QPushButton("↑")
        self.move_down_btn = QPushButton("↓")

        links_toolbar.addWidget(self.add_ik_link_btn)
        links_toolbar.addWidget(self.remove_ik_link_btn)
        links_toolbar.addWidget(self.move_up_btn)
        links_toolbar.addWidget(self.move_down_btn)
        links_toolbar.addStretch()

        links_layout.addLayout(links_toolbar)

        # IKリンクテーブル
        self.ik_links_table = QTableWidget()
        self.ik_links_table.setColumnCount(8)
        self.ik_links_table.setHorizontalHeaderLabels(
            [
                self.tr("bone", "table_headers"),
                self.tr("angle_limit", "table_headers"),
                self.tr("lower_x", "table_headers"),
                self.tr("lower_y", "table_headers"),
                self.tr("lower_z", "table_headers"),
                self.tr("upper_x", "table_headers"),
                self.tr("upper_y", "table_headers"),
                self.tr("upper_z", "table_headers"),
            ]
        )
        self.ik_links_table.horizontalHeader().setStretchLastSection(True)
        links_layout.addWidget(self.ik_links_table)

        self.ik_links_group.setLayout(links_layout)
        layout.addWidget(self.ik_links_group)

        # 初期状態では非表示
        self.ik_links_group.setVisible(False)

        return layout

    def _create_grant_settings_layout(self):
        """付与設定レイアウトを作成"""
        layout = QVBoxLayout()

        # 回転付与
        self.rotation_grant_check = QCheckBox(self.tr("rotation_grant", "bone_flags"))
        layout.addWidget(self.rotation_grant_check)

        # 移動付与
        self.move_grant_check = QCheckBox(self.tr("move_grant", "bone_flags"))
        layout.addWidget(self.move_grant_check)

        # 付与設定グループ
        self.grant_settings_group = QGroupBox(self.tr("grant_settings", "groups"))
        grant_layout = QFormLayout()

        # 付与親
        parent_layout = QHBoxLayout()
        self.grant_parent_edit = QLineEdit()
        self.grant_parent_edit.setReadOnly(True)
        self.select_grant_parent_btn = QPushButton(self.tr("select", "buttons"))
        self.select_grant_parent_btn.setMaximumWidth(60)
        parent_layout.addWidget(self.grant_parent_edit)
        parent_layout.addWidget(self.select_grant_parent_btn)
        self.grant_parent_label = QLabel(self.tr("grant_parent", "fields"))
        grant_layout.addRow(self.grant_parent_label, parent_layout)

        # 付与率
        self.grant_rate_spin = QDoubleSpinBox()
        self.grant_rate_spin.setRange(-999.0, 999.0)
        self.grant_rate_spin.setDecimals(2)
        self.grant_rate_spin.setSingleStep(0.1)
        self.grant_rate_spin.setValue(1.0)
        self.grant_rate_label = QLabel(self.tr("grant_rate", "fields"))
        grant_layout.addRow(self.grant_rate_label, self.grant_rate_spin)

        # ローカル付与
        self.local_grant_check = QCheckBox(self.tr("local_grant", "bone_flags"))
        grant_layout.addRow("", self.local_grant_check)

        self.grant_settings_group.setLayout(grant_layout)
        layout.addWidget(self.grant_settings_group)

        # 初期状態では非表示
        self.grant_settings_group.setVisible(False)

        layout.addStretch()

        return layout

    def _create_axis_limit_layout(self):
        """軸制限レイアウトを作成"""
        layout = QVBoxLayout()

        # 軸固定
        self.fixed_axis_check = QCheckBox(self.tr("fixed_axis", "bone_flags"))
        layout.addWidget(self.fixed_axis_check)

        # 軸固定設定
        self.fixed_axis_group = QGroupBox(self.tr("fixed_axis", "groups"))
        axis_layout = QGridLayout()

        self.fixed_axis_x_spin = QDoubleSpinBox()
        self.fixed_axis_y_spin = QDoubleSpinBox()
        self.fixed_axis_z_spin = QDoubleSpinBox()

        for spin in [
            self.fixed_axis_x_spin,
            self.fixed_axis_y_spin,
            self.fixed_axis_z_spin,
        ]:
            spin.setRange(-1.0, 1.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.1)

        axis_layout.addWidget(QLabel("X:"), 0, 0)
        axis_layout.addWidget(self.fixed_axis_x_spin, 0, 1)
        axis_layout.addWidget(QLabel("Y:"), 0, 2)
        axis_layout.addWidget(self.fixed_axis_y_spin, 0, 3)
        axis_layout.addWidget(QLabel("Z:"), 0, 4)
        axis_layout.addWidget(self.fixed_axis_z_spin, 0, 5)

        self.fixed_axis_group.setLayout(axis_layout)
        layout.addWidget(self.fixed_axis_group)

        # 初期状態では非表示
        self.fixed_axis_group.setVisible(False)

        # ローカル軸
        self.local_axis_check = QCheckBox(self.tr("local_axis", "bone_flags"))
        layout.addWidget(self.local_axis_check)

        # ローカル軸設定
        self.local_axis_group = QGroupBox(self.tr("local_axis", "groups"))
        local_layout = QVBoxLayout()

        # X軸
        x_axis_layout = QGridLayout()
        self.local_x_axis_x_spin = QDoubleSpinBox()
        self.local_x_axis_y_spin = QDoubleSpinBox()
        self.local_x_axis_z_spin = QDoubleSpinBox()

        for spin in [
            self.local_x_axis_x_spin,
            self.local_x_axis_y_spin,
            self.local_x_axis_z_spin,
        ]:
            spin.setRange(-1.0, 1.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.1)

        self.x_axis_direction_label = QLabel(self.tr("x_axis_direction", "fields"))
        x_axis_layout.addWidget(self.x_axis_direction_label, 0, 0)
        x_axis_layout.addWidget(QLabel("X:"), 0, 1)
        x_axis_layout.addWidget(self.local_x_axis_x_spin, 0, 2)
        x_axis_layout.addWidget(QLabel("Y:"), 0, 3)
        x_axis_layout.addWidget(self.local_x_axis_y_spin, 0, 4)
        x_axis_layout.addWidget(QLabel("Z:"), 0, 5)
        x_axis_layout.addWidget(self.local_x_axis_z_spin, 0, 6)

        # Z軸
        z_axis_layout = QGridLayout()
        self.local_z_axis_x_spin = QDoubleSpinBox()
        self.local_z_axis_y_spin = QDoubleSpinBox()
        self.local_z_axis_z_spin = QDoubleSpinBox()

        for spin in [
            self.local_z_axis_x_spin,
            self.local_z_axis_y_spin,
            self.local_z_axis_z_spin,
        ]:
            spin.setRange(-1.0, 1.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.1)

        self.z_axis_direction_label = QLabel(self.tr("z_axis_direction", "fields"))
        z_axis_layout.addWidget(self.z_axis_direction_label, 0, 0)
        z_axis_layout.addWidget(QLabel("X:"), 0, 1)
        z_axis_layout.addWidget(self.local_z_axis_x_spin, 0, 2)
        z_axis_layout.addWidget(QLabel("Y:"), 0, 3)
        z_axis_layout.addWidget(self.local_z_axis_y_spin, 0, 4)
        z_axis_layout.addWidget(QLabel("Z:"), 0, 5)
        z_axis_layout.addWidget(self.local_z_axis_z_spin, 0, 6)

        local_layout.addLayout(x_axis_layout)
        local_layout.addLayout(z_axis_layout)

        self.local_axis_group.setLayout(local_layout)
        layout.addWidget(self.local_axis_group)

        # 初期状態では非表示
        self.local_axis_group.setVisible(False)

        layout.addStretch()

        return layout

    def set_bone_details_enabled(self, enabled):
        """ボーン詳細セクションの有効/無効を設定"""
        self.bone_details_content.setEnabled(enabled)
        self.apply_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)

    def retranslateUi(self):
        """言語切り替え時にUIを再翻訳"""
        # GroupBoxes
        if hasattr(self, "bone_tree_group"):
            self.bone_tree_group.setTitle(self.tr("bone_list", "groups"))
        if hasattr(self, "basic_group"):
            self.basic_group.setTitle(self.tr("basic_information", "groups"))
        if hasattr(self, "transform_group"):
            self.transform_group.setTitle(self.tr("transform_control", "groups"))
        if hasattr(self, "ik_group"):
            self.ik_group.setTitle(self.tr("ik_settings", "groups"))
        if hasattr(self, "grant_group"):
            self.grant_group.setTitle(self.tr("grant_settings", "groups"))
        if hasattr(self, "axis_group"):
            self.axis_group.setTitle(self.tr("axis_limit", "groups"))
        if hasattr(self, "flags_group"):
            self.flags_group.setTitle(self.tr("basic_settings", "groups"))
        if hasattr(self, "special_group"):
            self.special_group.setTitle(self.tr("special_settings", "groups"))
        if hasattr(self, "ik_settings_group"):
            self.ik_settings_group.setTitle(self.tr("ik_settings", "groups"))
        if hasattr(self, "ik_links_group"):
            self.ik_links_group.setTitle(self.tr("ik_links", "groups"))
        if hasattr(self, "grant_settings_group"):
            self.grant_settings_group.setTitle(self.tr("grant_settings", "groups"))
        if hasattr(self, "fixed_axis_group"):
            self.fixed_axis_group.setTitle(self.tr("fixed_axis", "groups"))
        if hasattr(self, "local_axis_group"):
            self.local_axis_group.setTitle(self.tr("local_axis", "groups"))

        # Buttons
        if hasattr(self, "refresh_btn"):
            self.refresh_btn.setText(self.tr("refresh", "buttons"))
        if hasattr(self, "apply_btn"):
            self.apply_btn.setText(self.tr("apply", "buttons"))
        if hasattr(self, "reset_btn"):
            self.reset_btn.setText(self.tr("reset", "buttons"))
        if hasattr(self, "select_ik_target_btn"):
            self.select_ik_target_btn.setText(self.tr("select", "buttons"))
        if hasattr(self, "add_ik_link_btn"):
            self.add_ik_link_btn.setText(self.tr("add", "buttons"))
        if hasattr(self, "remove_ik_link_btn"):
            self.remove_ik_link_btn.setText(self.tr("delete", "buttons"))
        if hasattr(self, "select_grant_parent_btn"):
            self.select_grant_parent_btn.setText(self.tr("select", "buttons"))

        # Labels
        if hasattr(self, "search_label"):
            self.search_label.setText(self.tr("search", "fields"))
        if hasattr(self, "bone_name_jp_label"):
            self.bone_name_jp_label.setText(self.tr("bone_name_jp", "fields"))
        if hasattr(self, "bone_name_en_label"):
            self.bone_name_en_label.setText(self.tr("bone_name_en", "fields"))
        if hasattr(self, "parent_bone_label"):
            self.parent_bone_label.setText(self.tr("parent_bone", "fields"))
        if hasattr(self, "position_label"):
            self.position_label.setText(self.tr("position", "fields"))
        if hasattr(self, "deform_layer_label"):
            self.deform_layer_label.setText(self.tr("deform_layer", "fields"))
        if hasattr(self, "connection_label"):
            self.connection_label.setText(self.tr("connection", "fields"))
        if hasattr(self, "offset_label"):
            self.offset_label.setText(self.tr("offset", "fields"))
        if hasattr(self, "external_parent_key_label"):
            self.external_parent_key_label.setText(self.tr("external_parent_key", "fields"))
        if hasattr(self, "ik_target_label"):
            self.ik_target_label.setText(self.tr("ik_target", "fields"))
        if hasattr(self, "ik_loop_label"):
            self.ik_loop_label.setText(self.tr("ik_loop_count", "fields"))
        if hasattr(self, "ik_limit_angle_label"):
            self.ik_limit_angle_label.setText(self.tr("ik_limit_angle", "fields"))
        if hasattr(self, "grant_parent_label"):
            self.grant_parent_label.setText(self.tr("grant_parent", "fields"))
        if hasattr(self, "grant_rate_label"):
            self.grant_rate_label.setText(self.tr("grant_rate", "fields"))
        if hasattr(self, "x_axis_direction_label"):
            self.x_axis_direction_label.setText(self.tr("x_axis_direction", "fields"))
        if hasattr(self, "z_axis_direction_label"):
            self.z_axis_direction_label.setText(self.tr("z_axis_direction", "fields"))

        # CheckBoxes
        if hasattr(self, "rotatable_check"):
            self.rotatable_check.setText(self.tr("rotatable", "bone_flags"))
        if hasattr(self, "movable_check"):
            self.movable_check.setText(self.tr("movable", "bone_flags"))
        if hasattr(self, "visible_check"):
            self.visible_check.setText(self.tr("visible", "bone_flags"))
        if hasattr(self, "enabled_check"):
            self.enabled_check.setText(self.tr("enabled", "bone_flags"))
        if hasattr(self, "after_physics_check"):
            self.after_physics_check.setText(self.tr("after_physics", "bone_flags"))
        if hasattr(self, "external_parent_check"):
            self.external_parent_check.setText(self.tr("external_parent", "bone_flags"))
        if hasattr(self, "ik_enabled_check"):
            self.ik_enabled_check.setText(self.tr("enable_ik", "checkboxes"))
        if hasattr(self, "rotation_grant_check"):
            self.rotation_grant_check.setText(self.tr("rotation_grant", "bone_flags"))
        if hasattr(self, "move_grant_check"):
            self.move_grant_check.setText(self.tr("move_grant", "bone_flags"))
        if hasattr(self, "local_grant_check"):
            self.local_grant_check.setText(self.tr("local_grant", "bone_flags"))
        if hasattr(self, "fixed_axis_check"):
            self.fixed_axis_check.setText(self.tr("fixed_axis", "bone_flags"))
        if hasattr(self, "local_axis_check"):
            self.local_axis_check.setText(self.tr("local_axis", "bone_flags"))

        # ComboBox items - Connection type
        if hasattr(self, "connection_type_combo"):
            self.connection_type_combo.clear()
            self.connection_type_combo.addItems(
                [self.tr("coordinate_offset", "bone_connection_types"), self.tr("bone", "bone_connection_types")]
            )

        # Table headers - IK Links
        if hasattr(self, "ik_links_table"):
            self.ik_links_table.setHorizontalHeaderLabels(
                [
                    self.tr("bone", "table_headers"),
                    self.tr("angle_limit", "table_headers"),
                    self.tr("lower_x", "table_headers"),
                    self.tr("lower_y", "table_headers"),
                    self.tr("lower_z", "table_headers"),
                    self.tr("upper_x", "table_headers"),
                    self.tr("upper_y", "table_headers"),
                    self.tr("upper_z", "table_headers"),
                ]
            )

        # Placeholders
        if hasattr(self, "search_edit"):
            self.search_edit.setPlaceholderText(self.tr("search_bone_name", "placeholders"))
