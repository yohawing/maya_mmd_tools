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
    QGridLayout,
    QSplitter,
    Qt,
    QListWidget,
    QTableWidget,
    QScrollArea,
)
from ..base_tab import BaseTab
from .translation_registry import apply_translation_registry
from ..components.authoring_toolbar import AuthoringToolbar


class BoneTab(BaseTab):
    _TRANSLATION_REGISTRY = (
        ("bone_tree_group", "setTitle", "bone_list", "groups"),
        ("basic_group", "setTitle", "basic_information", "groups"),
        ("transform_group", "setTitle", "transform_control", "groups"),
        ("ik_group", "setTitle", "ik_settings", "groups"),
        ("grant_group", "setTitle", "grant_settings", "groups"),
        ("axis_group", "setTitle", "axis_limit", "groups"),
        ("flags_group", "setTitle", "basic_settings", "groups"),
        ("special_group", "setTitle", "special_settings", "groups"),
        ("ik_settings_group", "setTitle", "ik_settings", "groups"),
        ("ik_links_group", "setTitle", "ik_links", "groups"),
        ("grant_settings_group", "setTitle", "grant_settings", "groups"),
        ("fixed_axis_group", "setTitle", "fixed_axis", "groups"),
        ("local_axis_group", "setTitle", "local_axis", "groups"),
        ("sync_btn", "setText", "sync", "buttons"),
        ("apply_btn", "setText", "apply", "buttons"),
        ("reset_btn", "setText", "reset", "buttons"),
        ("select_ik_target_btn", "setText", "select", "buttons"),
        ("add_ik_link_btn", "setText", "add", "buttons"),
        ("remove_ik_link_btn", "setText", "delete", "buttons"),
        ("select_grant_parent_btn", "setText", "select", "buttons"),
        ("search_label", "setText", "search", "fields"),
        ("bone_name_jp_label", "setText", "bone_name_jp", "fields"),
        ("bone_name_en_label", "setText", "bone_name_en", "fields"),
        ("parent_bone_label", "setText", "parent_bone", "fields"),
        ("deform_layer_label", "setText", "deform_layer", "fields"),
        ("external_parent_key_label", "setText", "external_parent_key", "fields"),
        ("ik_target_label", "setText", "ik_target", "fields"),
        ("ik_loop_label", "setText", "ik_loop_count", "fields"),
        ("ik_limit_angle_label", "setText", "ik_limit_angle", "fields"),
        ("grant_parent_label", "setText", "grant_parent", "fields"),
        ("grant_rate_label", "setText", "grant_rate", "fields"),
        ("x_axis_direction_label", "setText", "x_axis_direction", "fields"),
        ("z_axis_direction_label", "setText", "z_axis_direction", "fields"),
        ("rotatable_check", "setText", "rotatable", "bone_flags"),
        ("movable_check", "setText", "movable", "bone_flags"),
        ("visible_check", "setText", "visible", "bone_flags"),
        ("enabled_check", "setText", "enabled", "bone_flags"),
        ("after_physics_check", "setText", "after_physics", "bone_flags"),
        ("external_parent_check", "setText", "external_parent", "bone_flags"),
        ("ik_enabled_check", "setText", "enable_ik", "checkboxes"),
        ("rotation_grant_check", "setText", "rotation_grant", "bone_flags"),
        ("move_grant_check", "setText", "move_grant", "bone_flags"),
        ("local_grant_check", "setText", "local_grant", "bone_flags"),
        ("fixed_axis_check", "setText", "fixed_axis", "bone_flags"),
        ("local_axis_check", "setText", "local_axis", "bone_flags"),
        ("search_edit", "setPlaceholderText", "search_bone_name", "placeholders"),
    )

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
        self.bone_authoring_toolbar = AuthoringToolbar(
            actions=("sync", "move_up", "move_down"),
            labels={
                "sync": self.tr("sync", "buttons"),
                "move_up": self.tr("up", "buttons"),
                "move_down": self.tr("down", "buttons"),
            },
            parent=self,
        )
        self.bone_authoring_toolbar.setObjectName("boneAuthoringToolbar")
        self.sync_btn = self.bone_authoring_toolbar.button("sync")
        self.reindex_up_btn = self.bone_authoring_toolbar.button("move_up")
        self.reindex_down_btn = self.bone_authoring_toolbar.button("move_down")
        self.sync_btn.setObjectName("boneSyncButton")
        self.reindex_up_btn.setObjectName("boneMoveUpButton")
        self.reindex_down_btn.setObjectName("boneMoveDownButton")
        # Compatibility aliases keep integrations that only read the old
        # attributes working; both names point to the one visible action.
        self.refresh_btn = self.sync_btn
        self.reset_authoring_btn = self.sync_btn
        toolbar_layout.addWidget(self.bone_authoring_toolbar)
        bone_tree_layout.addLayout(toolbar_layout)
        for action in ("move_up", "move_down"):
            self.bone_authoring_toolbar.set_action_enabled(
                action,
                False,
                self.tr("authoring_selection_required", "tooltips"),
                "authoring_selection_required",
            )
        self.sync_btn.setEnabled(False)

        self.animation_warning_label = QLabel()
        self.animation_warning_label.setWordWrap(True)
        self.animation_warning_label.setVisible(False)
        bone_tree_layout.addWidget(self.animation_warning_label)

        # ボーンリスト（単純なリスト表示）
        self.bone_list = QListWidget()
        self.bone_list.setObjectName("boneList")
        self.bone_list.setAlternatingRowColors(True)
        # 複数選択を有効化
        self.bone_list.setSelectionMode(QListWidget.ExtendedSelection)
        bone_tree_layout.addWidget(self.bone_list)

        # ボーン検索
        search_layout = QHBoxLayout()
        self.search_label = QLabel(self.tr("search", "fields"))
        search_layout.addWidget(self.search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("boneSearchEdit")
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
        self.apply_btn.setObjectName("boneApplyButton")
        self.reset_btn.setObjectName("boneResetButton")
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
        self.bone_name_jp_edit.setObjectName("boneNameJpEdit")
        self.bone_name_en_edit = QLineEdit()
        self.bone_name_en_edit.setObjectName("boneNameEnEdit")
        self.bone_name_jp_label = QLabel(self.tr("bone_name_jp", "fields"))
        self.bone_name_en_label = QLabel(self.tr("bone_name_en", "fields"))
        layout.addRow(self.bone_name_jp_label, self.bone_name_jp_edit)
        layout.addRow(self.bone_name_en_label, self.bone_name_en_edit)

        # 親ボーン
        self.parent_bone_edit = QLineEdit()
        self.parent_bone_edit.setObjectName("boneParentEdit")
        self.parent_bone_edit.setReadOnly(True)
        self.parent_bone_label = QLabel(self.tr("parent_bone", "fields"))
        layout.addRow(self.parent_bone_label, self.parent_bone_edit)

        # 変形階層
        self.deform_layer_spin = QSpinBox()
        self.deform_layer_spin.setObjectName("boneDeformLayerSpin")
        self.deform_layer_spin.setRange(0, 9999)
        self.deform_layer_label = QLabel(self.tr("deform_layer", "fields"))
        layout.addRow(self.deform_layer_label, self.deform_layer_spin)

        return layout

    def _create_transform_control_layout(self):
        """変形制御レイアウトを作成"""
        layout = QVBoxLayout()

        # 基本フラグ
        self.flags_group = QGroupBox(self.tr("basic_settings", "groups"))
        flags_layout = QGridLayout()

        self.rotatable_check = QCheckBox(self.tr("rotatable", "bone_flags"))
        self.rotatable_check.setObjectName("boneRotatableCheck")
        self.movable_check = QCheckBox(self.tr("movable", "bone_flags"))
        self.movable_check.setObjectName("boneMovableCheck")
        self.visible_check = QCheckBox(self.tr("visible", "bone_flags"))
        self.visible_check.setObjectName("boneVisibleCheck")
        self.enabled_check = QCheckBox(self.tr("enabled", "bone_flags"))
        self.enabled_check.setObjectName("boneEnabledCheck")

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
        self.after_physics_check.setObjectName("boneAfterPhysicsCheck")
        self.external_parent_check = QCheckBox(self.tr("external_parent", "bone_flags"))
        self.external_parent_check.setObjectName("boneExternalParentCheck")

        special_layout.addWidget(self.after_physics_check, 0, 0)
        special_layout.addWidget(self.external_parent_check, 0, 1)

        # 外部親キー
        self.external_parent_key_label = QLabel(self.tr("external_parent_key", "fields"))
        self.external_parent_key_spin = QSpinBox()
        self.external_parent_key_spin.setObjectName("boneExternalParentKeySpin")
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
        self.ik_enabled_check.setObjectName("boneIkEnabledCheck")
        layout.addWidget(self.ik_enabled_check)

        # IK設定グループ
        self.ik_settings_group = QGroupBox(self.tr("ik_settings", "groups"))
        ik_layout = QFormLayout()

        # IKターゲット
        target_layout = QHBoxLayout()
        self.ik_target_edit = QLineEdit()
        self.ik_target_edit.setObjectName("boneIkTargetEdit")
        self.ik_target_edit.setReadOnly(True)
        self.select_ik_target_btn = QPushButton(self.tr("select", "buttons"))
        self.select_ik_target_btn.setObjectName("boneSelectIkTargetButton")
        self.select_ik_target_btn.setMaximumWidth(60)
        target_layout.addWidget(self.ik_target_edit)
        target_layout.addWidget(self.select_ik_target_btn)
        self.ik_target_label = QLabel(self.tr("ik_target", "fields"))
        ik_layout.addRow(self.ik_target_label, target_layout)

        # IKループ回数
        self.ik_loop_spin = QSpinBox()
        self.ik_loop_spin.setObjectName("boneIkLoopSpin")
        self.ik_loop_spin.setRange(1, 255)
        self.ik_loop_spin.setValue(10)
        self.ik_loop_label = QLabel(self.tr("ik_loop_count", "fields"))
        ik_layout.addRow(self.ik_loop_label, self.ik_loop_spin)

        # 制限角度
        self.ik_limit_angle_spin = QDoubleSpinBox()
        self.ik_limit_angle_spin.setObjectName("boneIkLimitAngleSpin")
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
        self.ik_authoring_toolbar = AuthoringToolbar(
            actions=("create", "delete", "move_up", "move_down"),
            labels={
                "create": self.tr("add", "buttons"),
                "delete": self.tr("delete", "buttons"),
                "move_up": self.tr("up", "buttons"),
                "move_down": self.tr("down", "buttons"),
            },
            parent=self,
        )
        self.ik_authoring_toolbar.setObjectName("boneIkAuthoringToolbar")
        self.add_ik_link_btn = self.ik_authoring_toolbar.button("create")
        self.remove_ik_link_btn = self.ik_authoring_toolbar.button("delete")
        self.move_up_btn = self.ik_authoring_toolbar.button("move_up")
        self.move_down_btn = self.ik_authoring_toolbar.button("move_down")
        self.add_ik_link_btn.setObjectName("boneAddIkLinkButton")
        self.remove_ik_link_btn.setObjectName("boneRemoveIkLinkButton")
        self.move_up_btn.setObjectName("boneMoveIkLinkUpButton")
        self.move_down_btn.setObjectName("boneMoveIkLinkDownButton")
        links_toolbar.addWidget(self.ik_authoring_toolbar)

        links_layout.addLayout(links_toolbar)

        # IKリンクテーブル
        self.ik_links_table = QTableWidget()
        self.ik_links_table.setObjectName("boneIkLinksTable")
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
        self.rotation_grant_check.setObjectName("boneRotationGrantCheck")
        layout.addWidget(self.rotation_grant_check)

        # 移動付与
        self.move_grant_check = QCheckBox(self.tr("move_grant", "bone_flags"))
        self.move_grant_check.setObjectName("boneMoveGrantCheck")
        layout.addWidget(self.move_grant_check)

        # 付与設定グループ
        self.grant_settings_group = QGroupBox(self.tr("grant_settings", "groups"))
        grant_layout = QFormLayout()

        # 付与親
        parent_layout = QHBoxLayout()
        self.grant_parent_edit = QLineEdit()
        self.grant_parent_edit.setObjectName("boneGrantParentEdit")
        self.grant_parent_edit.setReadOnly(True)
        self.select_grant_parent_btn = QPushButton(self.tr("select", "buttons"))
        self.select_grant_parent_btn.setObjectName("boneSelectGrantParentButton")
        self.select_grant_parent_btn.setMaximumWidth(60)
        parent_layout.addWidget(self.grant_parent_edit)
        parent_layout.addWidget(self.select_grant_parent_btn)
        self.grant_parent_label = QLabel(self.tr("grant_parent", "fields"))
        grant_layout.addRow(self.grant_parent_label, parent_layout)

        # 付与率
        self.grant_rate_spin = QDoubleSpinBox()
        self.grant_rate_spin.setObjectName("boneGrantRateSpin")
        self.grant_rate_spin.setRange(-999.0, 999.0)
        self.grant_rate_spin.setDecimals(2)
        self.grant_rate_spin.setSingleStep(0.1)
        self.grant_rate_spin.setValue(1.0)
        self.grant_rate_label = QLabel(self.tr("grant_rate", "fields"))
        grant_layout.addRow(self.grant_rate_label, self.grant_rate_spin)

        # ローカル付与
        self.local_grant_check = QCheckBox(self.tr("local_grant", "bone_flags"))
        self.local_grant_check.setObjectName("boneLocalGrantCheck")
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
        self.fixed_axis_check.setObjectName("boneFixedAxisCheck")
        layout.addWidget(self.fixed_axis_check)

        # 軸固定設定
        self.fixed_axis_group = QGroupBox(self.tr("fixed_axis", "groups"))
        axis_layout = QGridLayout()

        self.fixed_axis_x_spin = QDoubleSpinBox()
        self.fixed_axis_y_spin = QDoubleSpinBox()
        self.fixed_axis_z_spin = QDoubleSpinBox()
        self.fixed_axis_x_spin.setObjectName("boneFixedAxisXSpin")
        self.fixed_axis_y_spin.setObjectName("boneFixedAxisYSpin")
        self.fixed_axis_z_spin.setObjectName("boneFixedAxisZSpin")

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
        self.local_axis_check.setObjectName("boneLocalAxisCheck")
        layout.addWidget(self.local_axis_check)

        # ローカル軸設定
        self.local_axis_group = QGroupBox(self.tr("local_axis", "groups"))
        local_layout = QVBoxLayout()

        # X軸
        x_axis_layout = QGridLayout()
        self.local_x_axis_x_spin = QDoubleSpinBox()
        self.local_x_axis_y_spin = QDoubleSpinBox()
        self.local_x_axis_z_spin = QDoubleSpinBox()
        self.local_x_axis_x_spin.setObjectName("boneLocalXAxisXSpin")
        self.local_x_axis_y_spin.setObjectName("boneLocalXAxisYSpin")
        self.local_x_axis_z_spin.setObjectName("boneLocalXAxisZSpin")

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
        self.local_z_axis_x_spin.setObjectName("boneLocalZAxisXSpin")
        self.local_z_axis_y_spin.setObjectName("boneLocalZAxisYSpin")
        self.local_z_axis_z_spin.setObjectName("boneLocalZAxisZSpin")

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
        apply_translation_registry(self, self._TRANSLATION_REGISTRY)
        self.animation_warning_label.setText("")
        self.bone_authoring_toolbar.retranslate(
            {
                "sync": self.tr("sync", "buttons"),
                "move_up": self.tr("up", "buttons"),
                "move_down": self.tr("down", "buttons"),
            },
            reason_resolver=lambda key: self.tr(key, "tooltips"),
        )
        self.ik_authoring_toolbar.retranslate(
            {
                "create": self.tr("add", "buttons"),
                "delete": self.tr("delete", "buttons"),
                "move_up": self.tr("up", "buttons"),
                "move_down": self.tr("down", "buttons"),
            },
            reason_resolver=lambda key: self.tr(key, "tooltips"),
        )

        # Table headers - IK Links
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
