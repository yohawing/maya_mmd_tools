import math
from mmd_tools.adapters import MayaCmdsAdapter
from ...core.logger import get_logger
from ...core.maya_attribute_utils import (
    set_custom_attributes,
    get_attribute,
)
from ...core.maya_scene_utils import object_exists
from ...core.humanik_builder import create_humanik_definition_from_scene
from ...core.constants import (
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_IK_TARGET,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_GRANT_PARENT,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    ATTR_MMD_BONE_INDEX,
)
from ...core.pmx_data.bone import PmxBoneFlag
from ..qt_compat import (
    QListWidgetItem,
    Qt,
    QCheckBox,
    QTableWidgetItem,
    QTimer,
)
from .list_presenter_helpers import (
    apply_list_filter,
    reload_for_current_model_change,
    select_existing_user_role_nodes,
    tr_message,
    tr_message_format,
)

logger = get_logger(__name__)


class BonePresenter:
    def __init__(self, view, app_state, maya_adapter=None, humanik_builder=None):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.humanik_builder = humanik_builder or create_humanik_definition_from_scene
        self.current_bone = None
        self.bone_data = {}  # Store original bone data for reset
        self.bone_list_items = {}  # Map bone name to list item
        self.all_bones = []  # All bones list
        self.is_updating = False  # Prevent feedback loops

        self.connect_signals()

        # 既に選択されているモデルがある場合はロード
        if self.app_state.current_model_root:
            # Qt のイベントループが安定してから実行
            QTimer.singleShot(100, self.load_bones)

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)

        # リストビューのシグナル
        self.view.bone_list.currentItemChanged.connect(self.on_bone_selected)
        self.view.bone_list.itemSelectionChanged.connect(self.on_selection_changed_maya)
        self.view.refresh_btn.clicked.connect(self.load_bones)
        self.view.search_edit.textChanged.connect(self.filter_bones)
        humanik_button = getattr(self.view, "create_humanik_rig_btn", None)
        if humanik_button is not None:
            humanik_button.clicked.connect(self.create_humanik_rig)

        # ボーン選択ボタン
        self.view.select_ik_target_btn.clicked.connect(lambda: self.select_bone_dialog("ik_target"))
        self.view.select_grant_parent_btn.clicked.connect(lambda: self.select_bone_dialog("grant_parent"))

        # フラグチェックボックス
        self.view.ik_enabled_check.toggled.connect(self.on_ik_enabled_toggled)
        self.view.rotation_grant_check.toggled.connect(self.on_grant_toggled)
        self.view.move_grant_check.toggled.connect(self.on_grant_toggled)
        self.view.fixed_axis_check.toggled.connect(self.on_axis_toggled)
        self.view.local_axis_check.toggled.connect(self.on_axis_toggled)
        self.view.external_parent_check.toggled.connect(self.on_external_parent_toggled)
        self.view.connection_type_combo.currentIndexChanged.connect(self.on_connection_type_changed)

        # IKリンクテーブル
        self.view.add_ik_link_btn.clicked.connect(self.add_ik_link)
        self.view.remove_ik_link_btn.clicked.connect(self.remove_ik_link)
        self.view.move_up_btn.clicked.connect(lambda: self.move_ik_link(-1))
        self.view.move_down_btn.clicked.connect(lambda: self.move_ik_link(1))

        # 適用/リセットボタン
        self.view.apply_btn.clicked.connect(self.apply_changes)
        self.view.reset_btn.clicked.connect(self.reset_changes)

    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        self.current_bone = None
        reload_for_current_model_change(logger, "BonePresenter", model_root, self.load_bones)

    def load_bones(self):
        """ボーンリストをロード"""
        self.view.bone_list.clear()
        self.bone_list_items.clear()
        self.all_bones.clear()
        self.view.set_bone_details_enabled(False)

        current_model_root = self.app_state.current_model_root
        logger.debug(f"Current model root: {current_model_root}")

        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            logger.warning(f"Model root does not exist: {current_model_root}")
            return

        # ジョイントを検索する複数の方法を試す
        joints = self.maya_adapter.list_relatives(current_model_root, allDescendents=True, type="joint") or []
        logger.debug(f"Found {len(joints)} joints using listRelatives")

        # もしジョイントが見つからない場合、別の方法を試す
        if not joints:
            # ルートノードの子を確認
            children = self.maya_adapter.list_relatives(current_model_root, children=True) or []
            logger.debug(f"Direct children of root: {children}")

            # 全ての子孫を取得してジョイントをフィルタ
            all_descendants = self.maya_adapter.list_relatives(current_model_root, allDescendents=True) or []
            joints = [node for node in all_descendants if self.maya_adapter.node_type(node) == "joint"]
            logger.debug(f"Found {len(joints)} joints using nodeType filter from {len(all_descendants)} descendants")

        if not joints:
            logger.info("No bones found in the model")
            return

        # mmd_bone_indexでソート
        joints_with_index = []
        for joint in joints:
            bone_index = get_attribute(joint, ATTR_MMD_BONE_INDEX)
            joints_with_index.append((joint, bone_index))

        # インデックスでソート（インデックスがない場合は最後に）
        joints_with_index.sort(key=lambda x: x[1] if x[1] is not None and x[1] >= 0 else float("inf"))

        # ソートされたジョイントリストを作成
        sorted_joints = [joint for joint, _ in joints_with_index]

        # ボーンをリストに追加
        self.all_bones = sorted_joints
        for idx, joint in enumerate(sorted_joints):
            # ボーン情報を取得
            name_jp = get_attribute(joint, ATTR_MMD_BONE_NAME)
            name_en = get_attribute(joint, ATTR_MMD_BONE_NAME_EN)
            bone_index = get_attribute(joint, ATTR_MMD_BONE_INDEX)

            # リストアイテムの表示形式: "インデックス:日本語名（Maya名）"
            if bone_index is not None and bone_index >= 0:
                display_text = f"{bone_index}:{name_jp}（{joint}）"
            else:
                display_text = f"-:{name_jp}（{joint}）"

            if name_en:
                display_text += f" [{name_en}]"

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, joint)  # 実際のジョイント名を保存
            self.view.bone_list.addItem(item)
            self.bone_list_items[joint] = item

        logger.info(f"Loaded {len(joints)} bones for model: {current_model_root}")

    def create_humanik_rig(self):
        """Create a HumanIK definition and control rig for the current model."""
        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            self.app_state.emit_status(tr_message("humanik_no_model"))
            return

        try:
            character = self.humanik_builder(current_model_root, create_control_rig=True)
            self.app_state.emit_status(tr_message_format("humanik_rig_created", character=character))
        except Exception as e:
            logger.error(f"Failed to create HumanIK rig: {e}", exc_info=True)
            self.app_state.emit_status(tr_message_format("humanik_rig_failed", error=str(e)))

    def _get_bone_type(self, joint):
        """ボーンのタイプを判定"""
        flags = get_attribute(joint, ATTR_MMD_BONE_FLAGS)

        if flags & PmxBoneFlag.IK:
            return "IK"
        elif flags & PmxBoneFlag.GRANT_PARENT_ROTATE or flags & PmxBoneFlag.GRANT_PARENT_MOVE:
            return "付与"
        elif flags & PmxBoneFlag.DEFORM_AFTER_PHYSICS:
            return "物理後"
        else:
            return "通常"

    def filter_bones(self, text):
        """ボーンを検索フィルタリング"""
        apply_list_filter(
            self.bone_list_items.values(),
            text,
            self._bone_filter_terms,
        )

    def _bone_filter_terms(self, item):
        """Return searchable terms for a bone list item."""
        joint = item.data(Qt.UserRole)
        return (
            item.text(),
            joint,
            get_attribute(joint, ATTR_MMD_BONE_NAME) if joint else "",
            get_attribute(joint, ATTR_MMD_BONE_NAME_EN) if joint else "",
        )

    def on_bone_selected(self, current, previous):
        """ボーンが選択されたときの処理"""
        if not current:
            self.view.set_bone_details_enabled(False)
            return

        self.current_bone = current.data(Qt.UserRole)
        if not self.current_bone or not object_exists(self.current_bone):
            self.view.set_bone_details_enabled(False)
            return

        logger.debug(f"Selected bone: {self.current_bone}")
        self.view.set_bone_details_enabled(True)
        self.load_bone_properties()

    def on_selection_changed_maya(self):
        """リスト選択が変更されたときにMayaでも選択する"""
        if self.is_updating:
            return

        select_existing_user_role_nodes(
            self.view.bone_list,
            self.maya_adapter,
            Qt.UserRole,
            exists=object_exists,
            logger=logger,
            label="joints",
        )

    def load_bone_properties(self):
        """選択されたボーンのプロパティをロード"""
        if not self.current_bone:
            return

        self.is_updating = True
        try:
            # 基本情報
            self.view.bone_name_jp_edit.setText(get_attribute(self.current_bone, ATTR_MMD_BONE_NAME))
            self.view.bone_name_en_edit.setText(get_attribute(self.current_bone, ATTR_MMD_BONE_NAME_EN))

            # 親ボーン
            parent = self.maya_adapter.list_relatives(self.current_bone, parent=True, type="joint")
            self.view.parent_bone_edit.setText(parent[0] if parent else "")

            # 位置
            pos = self.maya_adapter.xform(self.current_bone, query=True, translation=True, worldSpace=True)
            self.view.pos_x_spin.setValue(pos[0])
            self.view.pos_y_spin.setValue(pos[1])
            self.view.pos_z_spin.setValue(pos[2])

            # 変形階層
            self.view.deform_layer_spin.setValue(get_attribute(self.current_bone, ATTR_MMD_DEFORM_LAYER))

            # ボーンフラグ
            flags = get_attribute(self.current_bone, ATTR_MMD_BONE_FLAGS)

            # 基本フラグ
            self.view.rotatable_check.setChecked(bool(flags & PmxBoneFlag.ROTATABLE))
            self.view.movable_check.setChecked(bool(flags & PmxBoneFlag.MOVABLE))
            self.view.visible_check.setChecked(bool(flags & PmxBoneFlag.DISPLAY))
            self.view.enabled_check.setChecked(bool(flags & PmxBoneFlag.OPERATABLE))

            # 特殊フラグ
            self.view.after_physics_check.setChecked(bool(flags & PmxBoneFlag.DEFORM_AFTER_PHYSICS))
            self.view.external_parent_check.setChecked(bool(flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM))

            # 接続先
            connection_type = 0 if (flags & PmxBoneFlag.CONNECT_BONE) == 0 else 1
            self.view.connection_type_combo.setCurrentIndex(connection_type)

            if connection_type == 0:
                # 座標オフセット
                offset = get_attribute(self.current_bone, ATTR_MMD_BONE_OFFSET)
                if isinstance(offset, (list, tuple)) and len(offset) >= 3:
                    self.view.offset_x_spin.setValue(float(offset[0]))
                    self.view.offset_y_spin.setValue(float(offset[1]))
                    self.view.offset_z_spin.setValue(float(offset[2]))
                else:
                    self.view.offset_x_spin.setValue(0.0)
                    self.view.offset_y_spin.setValue(-1.0)
                    self.view.offset_z_spin.setValue(0.0)
                self.view.connection_bone_edit.clear()
            else:
                # ボーン接続
                connection_bone = get_attribute(self.current_bone, ATTR_MMD_CONNECTION_BONE)
                # 接続先ボーンの表示名を作成
                display_name = self._get_bone_display_name(connection_bone)
                self.view.connection_bone_edit.setText(display_name)

            # IK設定
            self.view.ik_enabled_check.setChecked(bool(flags & PmxBoneFlag.IK))
            self._load_ik_settings()

            # 付与設定
            self.view.rotation_grant_check.setChecked(bool(flags & PmxBoneFlag.GRANT_PARENT_ROTATE))
            self.view.move_grant_check.setChecked(bool(flags & PmxBoneFlag.GRANT_PARENT_MOVE))
            self._load_grant_settings()

            # 軸制限
            self.view.fixed_axis_check.setChecked(bool(flags & PmxBoneFlag.AXIS_FIXED))
            self.view.local_axis_check.setChecked(bool(flags & PmxBoneFlag.LOCAL_AXIS))
            self._load_axis_settings()

            # 外部親
            if flags & PmxBoneFlag.EXTERNAL_PARENT_DEFORM:
                key = get_attribute(self.current_bone, ATTR_MMD_EXTERNAL_PARENT_KEY)
                self.view.external_parent_key_spin.setValue(key)

            # 初期状態の表示/非表示を設定
            self.on_grant_toggled()
            self.on_axis_toggled()
            self.on_external_parent_toggled(self.view.external_parent_check.isChecked())
            self.on_connection_type_changed(self.view.connection_type_combo.currentIndex())

            # データを保存（リセット用）
            self._store_bone_data()

        finally:
            self.is_updating = False

    def _load_ik_settings(self):
        """IK設定をロード"""
        if not self.view.ik_enabled_check.isChecked():
            self.view.ik_settings_group.setVisible(False)
            self.view.ik_links_group.setVisible(False)
            return

        self.view.ik_settings_group.setVisible(True)
        self.view.ik_links_group.setVisible(True)

        # IKターゲット
        ik_target_index = get_attribute(self.current_bone, ATTR_MMD_IK_TARGET_INDEX)
        if isinstance(ik_target_index, int) and 0 <= ik_target_index < len(self.all_bones):
            ik_target = self.all_bones[ik_target_index]
            display_name = self._get_bone_display_name(ik_target)
        else:
            display_name = ""
        self.view.ik_target_edit.setText(display_name)

        # IKループ回数
        ik_loop = get_attribute(self.current_bone, ATTR_MMD_IK_LOOP)
        self.view.ik_loop_spin.setValue(ik_loop)

        # 制限角度（ラジアンから度に変換）
        ik_limit_rad = get_attribute(self.current_bone, ATTR_MMD_IK_LIMIT_ANGLE)
        ik_limit_deg = math.degrees(ik_limit_rad)
        self.view.ik_limit_angle_spin.setValue(ik_limit_deg)

        # IKリンクをロード
        self._load_ik_links()

    def _load_ik_links(self):
        """IKリンクをロード"""
        self.view.ik_links_table.setRowCount(0)

        # IKリンクデータを取得
        ik_links = self._get_attr_safe(self.current_bone, ATTR_MMD_IK_LINKS, [])
        logger.debug(f"Loading IK links for {self.current_bone}: {ik_links}")

        for link_data in ik_links:
            if isinstance(link_data, dict):
                bone_index = link_data.get("bone", "")
                logger.debug(f"Processing IK link with bone index: {bone_index}")

                # ボーンインデックスから実際のボーン名を取得
                if isinstance(bone_index, int) and bone_index < len(self.all_bones):
                    bone_name = self.all_bones[bone_index]
                else:
                    bone_name = str(bone_index)

                display_name = self._get_bone_display_name(bone_name)
                self._add_ik_link_row(
                    display_name,
                    link_data.get("limit_enabled", False),
                    link_data.get("lower_limit", [0.0, 0.0, 0.0]),
                    link_data.get("upper_limit", [0.0, 0.0, 0.0]),
                )

    def _add_ik_link_row(self, bone_name, limit_enabled, lower_limit, upper_limit):
        """IKリンクテーブルに行を追加"""
        row = self.view.ik_links_table.rowCount()
        self.view.ik_links_table.insertRow(row)

        # ボーン名
        self.view.ik_links_table.setItem(row, 0, QTableWidgetItem(bone_name))

        # 角度制限チェックボックス
        limit_check = QCheckBox()
        limit_check.setChecked(limit_enabled)
        self.view.ik_links_table.setCellWidget(row, 1, limit_check)

        # 下限
        for i, value in enumerate(lower_limit[:3]):
            item = QTableWidgetItem(f"{math.degrees(value):.1f}")
            self.view.ik_links_table.setItem(row, 2 + i, item)

        # 上限
        for i, value in enumerate(upper_limit[:3]):
            item = QTableWidgetItem(f"{math.degrees(value):.1f}")
            self.view.ik_links_table.setItem(row, 5 + i, item)

    def _load_grant_settings(self):
        """付与設定をロード"""
        grant_enabled = self.view.rotation_grant_check.isChecked() or self.view.move_grant_check.isChecked()
        self.view.grant_settings_group.setVisible(grant_enabled)

        if not grant_enabled:
            return

        # 付与親
        grant_parent = get_attribute(self.current_bone, ATTR_MMD_GRANT_PARENT)
        display_name = self._get_bone_display_name(grant_parent)
        self.view.grant_parent_edit.setText(display_name)

        # 付与率
        grant_rate = get_attribute(self.current_bone, ATTR_MMD_GRANT_RATE)
        self.view.grant_rate_spin.setValue(grant_rate)

        # ローカル付与
        flags = get_attribute(self.current_bone, ATTR_MMD_BONE_FLAGS)
        self.view.local_grant_check.setChecked(bool(flags & 0x0080))

    def _load_axis_settings(self):
        """軸制限設定をロード"""
        # 軸固定
        self.view.fixed_axis_group.setVisible(self.view.fixed_axis_check.isChecked())
        if self.view.fixed_axis_check.isChecked():
            fixed_axis = self._get_attr_safe(self.current_bone, ATTR_MMD_FIXED_AXIS, [0.0, 0.0, 1.0])
            # リストまたはタプルであることを確認
            if isinstance(fixed_axis, (list, tuple)) and len(fixed_axis) >= 3:
                self.view.fixed_axis_x_spin.setValue(float(fixed_axis[0]))
                self.view.fixed_axis_y_spin.setValue(float(fixed_axis[1]))
                self.view.fixed_axis_z_spin.setValue(float(fixed_axis[2]))
            else:
                # デフォルト値を使用
                self.view.fixed_axis_x_spin.setValue(0.0)
                self.view.fixed_axis_y_spin.setValue(0.0)
                self.view.fixed_axis_z_spin.setValue(1.0)

        # ローカル軸
        self.view.local_axis_group.setVisible(self.view.local_axis_check.isChecked())
        if self.view.local_axis_check.isChecked():
            local_x = get_attribute(self.current_bone, ATTR_MMD_LOCAL_X_AXIS)
            if local_x is None:
                local_x = [1.0, 0.0, 0.0]

            local_z = get_attribute(self.current_bone, ATTR_MMD_LOCAL_Z_AXIS)
            if local_z is None:
                local_z = [0.0, 0.0, 1.0]

            # リストまたはタプルであることを確認し、リストに変換
            if isinstance(local_x, (list, tuple)):
                local_x = list(local_x)
            else:
                logger.warning(f"local_x is not a list or tuple: {type(local_x)}")
                local_x = [1.0, 0.0, 0.0]

            if isinstance(local_z, (list, tuple)):
                local_z = list(local_z)
            else:
                logger.warning(f"local_z is not a list or tuple: {type(local_z)}")
                local_z = [0.0, 0.0, 1.0]

            # 各要素を個別に取得して型を確認
            x_val = float(local_x[0]) if len(local_x) > 0 else 1.0
            y_val = float(local_x[1]) if len(local_x) > 1 else 0.0
            z_val = float(local_x[2]) if len(local_x) > 2 else 0.0

            self.view.local_x_axis_x_spin.setValue(x_val)
            self.view.local_x_axis_y_spin.setValue(y_val)
            self.view.local_x_axis_z_spin.setValue(z_val)

            x_val = float(local_z[0]) if len(local_z) > 0 else 0.0
            y_val = float(local_z[1]) if len(local_z) > 1 else 0.0
            z_val = float(local_z[2]) if len(local_z) > 2 else 1.0

            self.view.local_z_axis_x_spin.setValue(x_val)
            self.view.local_z_axis_y_spin.setValue(y_val)
            self.view.local_z_axis_z_spin.setValue(z_val)

    def _store_bone_data(self):
        """現在のボーンデータを保存（リセット用）"""
        if not self.current_bone:
            return

        self.bone_data = {
            "name_jp": self.view.bone_name_jp_edit.text(),
            "name_en": self.view.bone_name_en_edit.text(),
            "position": [
                self.view.pos_x_spin.value(),
                self.view.pos_y_spin.value(),
                self.view.pos_z_spin.value(),
            ],
            "deform_layer": self.view.deform_layer_spin.value(),
            "flags": self._calculate_bone_flags(),
            "all_settings": self._gather_all_settings(),
        }

    def select_bone_dialog(self, target_type):
        """ボーン選択ダイアログを表示"""
        # 簡易的な実装：現在のMaya選択を使用
        selected = self.maya_adapter.ls(selection=True, type="joint")
        if not selected:
            self.app_state.emit_status(tr_message("select_joint"))
            return

        bone = selected[0]
        display_name = self._get_bone_display_name(bone)

        if target_type == "ik_target":
            self.view.ik_target_edit.setText(display_name)
        elif target_type == "grant_parent":
            self.view.grant_parent_edit.setText(display_name)

    def _is_descendant(self, parent, child):
        """childがparentの子孫かどうかをチェック"""
        if not parent or not child:
            return False

        descendants = self.maya_adapter.list_relatives(parent, allDescendents=True, type="joint") or []
        return child in descendants

    def on_ik_enabled_toggled(self, checked):
        """IK有効化トグル時の処理"""
        self.view.ik_settings_group.setVisible(checked)
        self.view.ik_links_group.setVisible(checked)

    def on_grant_toggled(self):
        """付与設定トグル時の処理"""
        enabled = self.view.rotation_grant_check.isChecked() or self.view.move_grant_check.isChecked()
        self.view.grant_settings_group.setVisible(enabled)

    def on_axis_toggled(self):
        """軸制限トグル時の処理"""
        self.view.fixed_axis_group.setVisible(self.view.fixed_axis_check.isChecked())
        self.view.local_axis_group.setVisible(self.view.local_axis_check.isChecked())

    def on_external_parent_toggled(self, checked):
        """外部親変形トグル時の処理"""
        self.view.external_parent_key_label.setVisible(checked)
        self.view.external_parent_key_spin.setVisible(checked)

    def on_connection_type_changed(self, index):
        """接続タイプ変更時の処理"""
        if index == 0:  # 座標オフセット
            self.view.offset_x_spin.setEnabled(True)
            self.view.offset_y_spin.setEnabled(True)
            self.view.offset_z_spin.setEnabled(True)
            self.view.connection_bone_edit.setEnabled(False)
        else:  # ボーン
            self.view.offset_x_spin.setEnabled(False)
            self.view.offset_y_spin.setEnabled(False)
            self.view.offset_z_spin.setEnabled(False)
            self.view.connection_bone_edit.setEnabled(True)

    def add_ik_link(self):
        """IKリンクを追加"""
        selected = self.maya_adapter.ls(selection=True, type="joint")
        if not selected:
            self.app_state.emit_status(tr_message("select_joint_for_ik_link"))
            return

        bone = selected[0]
        display_name = self._get_bone_display_name(bone)
        self._add_ik_link_row(display_name, False, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0])

    def remove_ik_link(self):
        """選択されたIKリンクを削除"""
        current_row = self.view.ik_links_table.currentRow()
        if current_row >= 0:
            self.view.ik_links_table.removeRow(current_row)

    def move_ik_link(self, direction):
        """IKリンクの順序を変更"""
        current_row = self.view.ik_links_table.currentRow()
        if current_row < 0:
            return

        new_row = current_row + direction
        if new_row < 0 or new_row >= self.view.ik_links_table.rowCount():
            return

        # 行データを保存
        row_data = []
        for col in range(self.view.ik_links_table.columnCount()):
            if col == 1:  # チェックボックス
                widget = self.view.ik_links_table.cellWidget(current_row, col)
                row_data.append(widget.isChecked() if widget else False)
            else:
                item = self.view.ik_links_table.item(current_row, col)
                row_data.append(item.text() if item else "")

        # 行を削除して新しい位置に挿入
        self.view.ik_links_table.removeRow(current_row)
        self.view.ik_links_table.insertRow(new_row)

        # データを復元
        for col, data in enumerate(row_data):
            if col == 1:  # チェックボックス
                check = QCheckBox()
                check.setChecked(data)
                self.view.ik_links_table.setCellWidget(new_row, col, check)
            else:
                self.view.ik_links_table.setItem(new_row, col, QTableWidgetItem(str(data)))

        # 選択を維持
        self.view.ik_links_table.setCurrentCell(new_row, 0)

    def apply_changes(self):
        """変更を適用"""
        if not self.current_bone or not self.maya_adapter.object_exists(self.current_bone):
            return

        try:
            # 基本情報
            attributes = {
                ATTR_MMD_BONE_NAME: self.view.bone_name_jp_edit.text(),
                ATTR_MMD_BONE_NAME_EN: self.view.bone_name_en_edit.text(),
                ATTR_MMD_DEFORM_LAYER: self.view.deform_layer_spin.value(),
                ATTR_MMD_BONE_FLAGS: self._calculate_bone_flags(),
            }

            # 位置（ワールド座標で設定）
            pos = [
                self.view.pos_x_spin.value(),
                self.view.pos_y_spin.value(),
                self.view.pos_z_spin.value(),
            ]
            self.maya_adapter.xform(self.current_bone, translation=pos, worldSpace=True)

            # 接続先設定
            if self.view.connection_type_combo.currentIndex() == 0:
                # 座標オフセット
                offset = [
                    self.view.offset_x_spin.value(),
                    self.view.offset_y_spin.value(),
                    self.view.offset_z_spin.value(),
                ]
                attributes[ATTR_MMD_BONE_OFFSET] = offset
            else:
                # ボーン接続（表示名から実際のボーン名を抽出）
                display_name = self.view.connection_bone_edit.text()
                actual_bone = self._extract_bone_name(display_name)
                attributes[ATTR_MMD_CONNECTION_BONE] = actual_bone

            # IK設定
            if self.view.ik_enabled_check.isChecked():
                # IKターゲット（表示名から実際のボーン名を抽出）
                display_name = self.view.ik_target_edit.text()
                actual_bone = self._extract_bone_name(display_name)
                attributes[ATTR_MMD_IK_LOOP] = self.view.ik_loop_spin.value()
                attributes[ATTR_MMD_IK_LIMIT_ANGLE] = math.radians(self.view.ik_limit_angle_spin.value())

                # IKリンク
                ik_links = []
                for row in range(self.view.ik_links_table.rowCount()):
                    bone_item = self.view.ik_links_table.item(row, 0)
                    limit_widget = self.view.ik_links_table.cellWidget(row, 1)

                    if bone_item:
                        # 表示名から実際のボーン名を抽出
                        display_name = bone_item.text()
                        actual_bone = self._extract_bone_name(display_name)
                        link_data = {
                            "bone": actual_bone,
                            "limit_enabled": limit_widget.isChecked() if limit_widget else False,
                            "lower_limit": [
                                math.radians(float(self.view.ik_links_table.item(row, 2).text())),
                                math.radians(float(self.view.ik_links_table.item(row, 3).text())),
                                math.radians(float(self.view.ik_links_table.item(row, 4).text())),
                            ],
                            "upper_limit": [
                                math.radians(float(self.view.ik_links_table.item(row, 5).text())),
                                math.radians(float(self.view.ik_links_table.item(row, 6).text())),
                                math.radians(float(self.view.ik_links_table.item(row, 7).text())),
                            ],
                        }
                        ik_links.append(link_data)

                # IKリンクをJSON文字列として保存
                import json

                attributes[ATTR_MMD_IK_LINKS] = json.dumps(ik_links)

            # 付与設定
            if self.view.rotation_grant_check.isChecked() or self.view.move_grant_check.isChecked():
                # 付与親（表示名から実際のボーン名を抽出）
                display_name = self.view.grant_parent_edit.text()
                actual_bone = self._extract_bone_name(display_name)
                attributes[ATTR_MMD_GRANT_PARENT] = actual_bone
                attributes[ATTR_MMD_GRANT_RATE] = self.view.grant_rate_spin.value()

            # 軸制限設定
            if self.view.fixed_axis_check.isChecked():
                fixed_axis = [
                    self.view.fixed_axis_x_spin.value(),
                    self.view.fixed_axis_y_spin.value(),
                    self.view.fixed_axis_z_spin.value(),
                ]
                attributes[ATTR_MMD_FIXED_AXIS] = fixed_axis

            if self.view.local_axis_check.isChecked():
                local_x = [
                    self.view.local_x_axis_x_spin.value(),
                    self.view.local_x_axis_y_spin.value(),
                    self.view.local_x_axis_z_spin.value(),
                ]
                local_z = [
                    self.view.local_z_axis_x_spin.value(),
                    self.view.local_z_axis_y_spin.value(),
                    self.view.local_z_axis_z_spin.value(),
                ]
                attributes[ATTR_MMD_LOCAL_X_AXIS] = local_x
                attributes[ATTR_MMD_LOCAL_Z_AXIS] = local_z

            # 外部親設定
            if self.view.external_parent_check.isChecked():
                attributes[ATTR_MMD_EXTERNAL_PARENT_KEY] = self.view.external_parent_key_spin.value()

            # 属性を設定
            self._ensure_mmd_attributes(self.current_bone)
            set_custom_attributes(self.current_bone, attributes)

            # リストビューの表示を更新
            if self.current_bone in self.bone_list_items:
                item = self.bone_list_items[self.current_bone]
                bone_index = get_attribute(self.current_bone, ATTR_MMD_BONE_INDEX)
                name_jp = self.view.bone_name_jp_edit.text()
                name_en = self.view.bone_name_en_edit.text()

                # 表示フォーマット更新
                if bone_index >= 0:
                    display_text = f"{bone_index}:{name_jp}（{self.current_bone}）"
                else:
                    display_text = f"-:{name_jp}（{self.current_bone}）"

                if name_en:
                    display_text += f" [{name_en}]"
                item.setText(display_text)

            logger.info(f"Applied changes to bone '{self.current_bone}'")
            self.app_state.emit_status(tr_message_format("bone_changes_applied", bone=self.current_bone))

        except Exception as e:
            logger.error(f"Failed to apply bone changes: {e}", exc_info=True)
            self.app_state.emit_status(tr_message_format("bone_changes_failed", error=str(e)))

    def reset_changes(self):
        """変更をリセット"""
        if self.current_bone and self.bone_data:
            self.load_bone_properties()
            self.app_state.emit_status(tr_message("changes_reset"))

    def _calculate_bone_flags(self):
        """UIの状態からボーンフラグを計算"""
        flags = 0

        # 接続先
        if self.view.connection_type_combo.currentIndex() == 1:
            flags |= PmxBoneFlag.CONNECT_BONE

        # 基本フラグ
        if self.view.rotatable_check.isChecked():
            flags |= PmxBoneFlag.ROTATABLE
        if self.view.movable_check.isChecked():
            flags |= PmxBoneFlag.MOVABLE
        if self.view.visible_check.isChecked():
            flags |= PmxBoneFlag.DISPLAY
        if self.view.enabled_check.isChecked():
            flags |= PmxBoneFlag.OPERATABLE

        # IK
        if self.view.ik_enabled_check.isChecked():
            flags |= PmxBoneFlag.IK

        # 付与
        if self.view.local_grant_check.isChecked():
            flags |= PmxBoneFlag.LOCAL
        if self.view.rotation_grant_check.isChecked():
            flags |= PmxBoneFlag.GRANT_PARENT_ROTATE
        if self.view.move_grant_check.isChecked():
            flags |= PmxBoneFlag.GRANT_PARENT_MOVE

        # 軸制限
        if self.view.fixed_axis_check.isChecked():
            flags |= PmxBoneFlag.AXIS_FIXED
        if self.view.local_axis_check.isChecked():
            flags |= PmxBoneFlag.LOCAL_AXIS

        # 特殊
        if self.view.after_physics_check.isChecked():
            flags |= PmxBoneFlag.DEFORM_AFTER_PHYSICS
        if self.view.external_parent_check.isChecked():
            flags |= PmxBoneFlag.EXTERNAL_PARENT_DEFORM

        return flags

    def _gather_all_settings(self):
        """全ての設定を収集"""
        # 現在のUI状態を辞書として返す
        return {
            # ここに全ての設定を追加（必要に応じて）
        }

    def _get_bone_display_name(self, bone_name):
        """ボーンの表示名を取得（Maya名:日本語名）"""
        if not bone_name or not object_exists(bone_name):
            return bone_name or ""

        # MMD日本語名を取得
        name_jp = get_attribute(bone_name, ATTR_MMD_BONE_NAME)
        if name_jp and name_jp != bone_name:
            return f"{bone_name}:{name_jp}"
        return bone_name

    def _extract_bone_name(self, display_name):
        """表示名から実際のボーン名を抽出"""
        if not display_name:
            return ""

        # "Maya名:日本語名"形式の場合、Maya名を抽出
        if ":" in display_name:
            return display_name.split(":")[0]
        return display_name

    def _get_attr_safe(self, node, attr, default):
        """属性を安全に取得"""
        try:
            if self.maya_adapter.attribute_exists(attr, node):
                value = get_attribute(node, attr)
                # IKリンクの場合、JSON文字列をパース
                if attr == ATTR_MMD_IK_LINKS and isinstance(value, str):
                    import json

                    try:
                        parsed_value = json.loads(value)
                        logger.debug(f"Successfully parsed JSON for {attr}: {parsed_value}")
                        return parsed_value
                    except Exception as e:
                        logger.warning(f"Failed to parse JSON for {attr}: {e}, value: {value}")
                        return default
                return value if value is not None else default
        except Exception as e:
            logger.debug(f"Failed to get attribute {attr} from {node}: {e}")
            pass
        return default

    def _ensure_mmd_attributes(self, joint):
        """MMD用カスタム属性が存在することを確認"""
        attrs = [
            (ATTR_MMD_BONE_NAME, "string", ""),
            (ATTR_MMD_BONE_NAME_EN, "string", ""),
            (
                ATTR_MMD_BONE_FLAGS,
                "long",
                int(PmxBoneFlag.ROTATABLE | PmxBoneFlag.DISPLAY),
            ),
            (ATTR_MMD_DEFORM_LAYER, "long", 0),
            (ATTR_MMD_BONE_OFFSET, "double3", None),
            (ATTR_MMD_CONNECTION_BONE, "string", ""),
            (ATTR_MMD_IK_TARGET, "string", ""),
            (ATTR_MMD_IK_LOOP, "long", 10),
            (ATTR_MMD_IK_LIMIT_ANGLE, "double", 2.0),
            (ATTR_MMD_IK_LINKS, "string", "[]"),  # JSON文字列として保存
            (ATTR_MMD_GRANT_PARENT, "string", ""),
            (ATTR_MMD_GRANT_RATE, "double", 1.0),
            (ATTR_MMD_FIXED_AXIS, "double3", None),
            (ATTR_MMD_LOCAL_X_AXIS, "double3", None),
            (ATTR_MMD_LOCAL_Z_AXIS, "double3", None),
            (ATTR_MMD_EXTERNAL_PARENT_KEY, "long", -1),
        ]

        for attr_name, attr_type, default in attrs:
            if not self.maya_adapter.attribute_exists(attr_name, joint):
                if attr_type == "double3":
                    # double3アトリビュートを作成
                    defaults = {
                        ATTR_MMD_BONE_OFFSET: [0.0, -1.0, 0.0],
                        ATTR_MMD_FIXED_AXIS: [0.0, 0.0, 1.0],
                        ATTR_MMD_LOCAL_X_AXIS: [1.0, 0.0, 0.0],
                        ATTR_MMD_LOCAL_Z_AXIS: [0.0, 0.0, 1.0],
                    }
                    default_value = defaults.get(attr_name, [0.0, 0.0, 0.0])
                    set_custom_attributes(joint, {attr_name: default_value})
                else:
                    # その他のアトリビュートを作成
                    if default is not None:
                        set_custom_attributes(joint, {attr_name: default})
                    else:
                        # デフォルト値がない場合はタイプに応じて初期値を設定
                        if attr_type == "string":
                            set_custom_attributes(joint, {attr_name: ""})
                        elif attr_type == "double":
                            set_custom_attributes(joint, {attr_name: 0.0})
                        elif attr_type == "long":
                            set_custom_attributes(joint, {attr_name: 0})
                        elif attr_type == "bool":
                            set_custom_attributes(joint, {attr_name: False})
