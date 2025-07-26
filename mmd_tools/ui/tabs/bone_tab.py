from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QTreeView,
    QGroupBox,
    QTreeWidget,
    QTreeWidgetItem,
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
    QTabWidget,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
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
        bone_tree_group = QGroupBox("ボーンリスト")
        bone_tree_layout = QVBoxLayout()

        # ツールバー
        toolbar_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("更新")
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
        search_layout.addWidget(QLabel("検索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("ボーン名を検索...")
        search_layout.addWidget(self.search_edit)
        bone_tree_layout.addLayout(search_layout)

        bone_tree_group.setLayout(bone_tree_layout)
        layout.addWidget(bone_tree_group)

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
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 基本情報セクション
        basic_group = QGroupBox("基本情報")
        basic_group.setLayout(self._create_basic_info_layout())
        layout.addWidget(basic_group)
        
        # 変形制御セクション
        transform_group = QGroupBox("変形制御")
        transform_group.setLayout(self._create_transform_control_layout())
        layout.addWidget(transform_group)
        
        # IK設定セクション
        ik_group = QGroupBox("IK設定")
        ik_group.setLayout(self._create_ik_settings_layout())
        layout.addWidget(ik_group)
        
        # 付与設定セクション
        grant_group = QGroupBox("付与設定")
        grant_group.setLayout(self._create_grant_settings_layout())
        layout.addWidget(grant_group)
        
        # 軸制限セクション
        axis_group = QGroupBox("軸制限")
        axis_group.setLayout(self._create_axis_limit_layout())
        layout.addWidget(axis_group)
        
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

        layout.addLayout(button_layout)

        return widget

    def _create_basic_info_layout(self):
        """基本情報レイアウトを作成"""
        layout = QFormLayout()

        # ボーン名
        self.bone_name_jp_edit = QLineEdit()
        self.bone_name_en_edit = QLineEdit()
        layout.addRow("日本語名:", self.bone_name_jp_edit)
        layout.addRow("英語名:", self.bone_name_en_edit)

        # 親ボーン
        parent_layout = QHBoxLayout()
        self.parent_bone_edit = QLineEdit()
        self.parent_bone_edit.setReadOnly(True)
        self.select_parent_btn = QPushButton("選択")
        self.select_parent_btn.setMaximumWidth(60)
        parent_layout.addWidget(self.parent_bone_edit)
        parent_layout.addWidget(self.select_parent_btn)
        layout.addRow("親ボーン:", parent_layout)

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
        layout.addRow("位置:", position_layout)

        # 変形階層
        self.deform_layer_spin = QSpinBox()
        self.deform_layer_spin.setRange(0, 9999)
        layout.addRow("変形階層:", self.deform_layer_spin)

        # 接続先
        connection_layout = QHBoxLayout()
        self.connection_type_combo = QComboBox()
        self.connection_type_combo.addItems(["座標オフセット", "ボーン"])
        self.connection_bone_edit = QLineEdit()
        self.connection_bone_edit.setReadOnly(True)
        self.select_connection_btn = QPushButton("選択")
        self.select_connection_btn.setMaximumWidth(60)

        connection_layout.addWidget(self.connection_type_combo)
        connection_layout.addWidget(self.connection_bone_edit)
        connection_layout.addWidget(self.select_connection_btn)
        layout.addRow("接続先:", connection_layout)

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
        layout.addRow("オフセット:", offset_layout)

        return layout

    def _create_transform_control_layout(self):
        """変形制御レイアウトを作成"""
        layout = QVBoxLayout()

        # 基本フラグ
        flags_group = QGroupBox("基本設定")
        flags_layout = QGridLayout()

        self.rotatable_check = QCheckBox("回転可能")
        self.movable_check = QCheckBox("移動可能")
        self.visible_check = QCheckBox("表示")
        self.enabled_check = QCheckBox("操作可")

        flags_layout.addWidget(self.rotatable_check, 0, 0)
        flags_layout.addWidget(self.movable_check, 0, 1)
        flags_layout.addWidget(self.visible_check, 1, 0)
        flags_layout.addWidget(self.enabled_check, 1, 1)

        flags_group.setLayout(flags_layout)
        layout.addWidget(flags_group)

        # 特殊フラグ
        special_group = QGroupBox("特殊設定")
        special_layout = QGridLayout()

        self.after_physics_check = QCheckBox("物理後変形")
        self.external_parent_check = QCheckBox("外部親変形")

        special_layout.addWidget(self.after_physics_check, 0, 0)
        special_layout.addWidget(self.external_parent_check, 0, 1)

        # 外部親キー
        self.external_parent_key_label = QLabel("外部親キー:")
        self.external_parent_key_spin = QSpinBox()
        self.external_parent_key_spin.setRange(-1, 9999)
        special_layout.addWidget(self.external_parent_key_label, 1, 0)
        special_layout.addWidget(self.external_parent_key_spin, 1, 1)
        
        # 初期状態では非表示
        self.external_parent_key_label.setVisible(False)
        self.external_parent_key_spin.setVisible(False)

        special_group.setLayout(special_layout)
        layout.addWidget(special_group)

        layout.addStretch()

        return layout

    def _create_ik_settings_layout(self):
        """IK設定レイアウトを作成"""
        layout = QVBoxLayout()

        # IK有効化
        self.ik_enabled_check = QCheckBox("IKを有効にする")
        layout.addWidget(self.ik_enabled_check)

        # IK設定グループ
        self.ik_settings_group = QGroupBox("IK設定")
        ik_layout = QFormLayout()

        # IKターゲット
        target_layout = QHBoxLayout()
        self.ik_target_edit = QLineEdit()
        self.ik_target_edit.setReadOnly(True)
        self.select_ik_target_btn = QPushButton("選択")
        self.select_ik_target_btn.setMaximumWidth(60)
        target_layout.addWidget(self.ik_target_edit)
        target_layout.addWidget(self.select_ik_target_btn)
        ik_layout.addRow("IKターゲット:", target_layout)

        # IKループ回数
        self.ik_loop_spin = QSpinBox()
        self.ik_loop_spin.setRange(1, 255)
        self.ik_loop_spin.setValue(10)
        ik_layout.addRow("ループ回数:", self.ik_loop_spin)

        # 制限角度
        self.ik_limit_angle_spin = QDoubleSpinBox()
        self.ik_limit_angle_spin.setRange(0.0, 180.0)
        self.ik_limit_angle_spin.setValue(114.5916)  # PMDデフォルト値を4で割った値
        self.ik_limit_angle_spin.setSingleStep(1.0)
        self.ik_limit_angle_spin.setSuffix("°")
        ik_layout.addRow("制限角度:", self.ik_limit_angle_spin)

        self.ik_settings_group.setLayout(ik_layout)
        layout.addWidget(self.ik_settings_group)
        
        # 初期状態では非表示
        self.ik_settings_group.setVisible(False)

        # IKリンクリスト
        self.ik_links_group = QGroupBox("IKリンク")
        links_layout = QVBoxLayout()

        # ツールバー
        links_toolbar = QHBoxLayout()
        self.add_ik_link_btn = QPushButton("追加")
        self.remove_ik_link_btn = QPushButton("削除")
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
            ["ボーン", "角度制限", "下限X", "下限Y", "下限Z", "上限X", "上限Y", "上限Z"]
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
        self.rotation_grant_check = QCheckBox("回転付与")
        layout.addWidget(self.rotation_grant_check)

        # 移動付与
        self.move_grant_check = QCheckBox("移動付与")
        layout.addWidget(self.move_grant_check)

        # 付与設定グループ
        self.grant_settings_group = QGroupBox("付与設定")
        grant_layout = QFormLayout()

        # 付与親
        parent_layout = QHBoxLayout()
        self.grant_parent_edit = QLineEdit()
        self.grant_parent_edit.setReadOnly(True)
        self.select_grant_parent_btn = QPushButton("選択")
        self.select_grant_parent_btn.setMaximumWidth(60)
        parent_layout.addWidget(self.grant_parent_edit)
        parent_layout.addWidget(self.select_grant_parent_btn)
        grant_layout.addRow("付与親:", parent_layout)

        # 付与率
        self.grant_rate_spin = QDoubleSpinBox()
        self.grant_rate_spin.setRange(-999.0, 999.0)
        self.grant_rate_spin.setDecimals(2)
        self.grant_rate_spin.setSingleStep(0.1)
        self.grant_rate_spin.setValue(1.0)
        grant_layout.addRow("付与率:", self.grant_rate_spin)

        # ローカル付与
        self.local_grant_check = QCheckBox("ローカル付与")
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
        self.fixed_axis_check = QCheckBox("軸固定")
        layout.addWidget(self.fixed_axis_check)

        # 軸固定設定
        self.fixed_axis_group = QGroupBox("固定軸")
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
        self.local_axis_check = QCheckBox("ローカル軸")
        layout.addWidget(self.local_axis_check)

        # ローカル軸設定
        self.local_axis_group = QGroupBox("ローカル軸")
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

        x_axis_layout.addWidget(QLabel("X軸方向:"), 0, 0)
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

        z_axis_layout.addWidget(QLabel("Z軸方向:"), 0, 0)
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
        # スクロールエリア内の各グループを有効/無効化
        # Note: 現在の実装ではQScrollArea内のウィジェットへの参照を直接保持していないため
        # このメソッドは後で更新が必要
        self.apply_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)
