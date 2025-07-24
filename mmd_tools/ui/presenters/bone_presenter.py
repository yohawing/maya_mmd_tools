from maya import cmds
import math
from ...core.logger import get_logger
from ...core.maya_utils import (
    get_parent_mmd_root,
    set_custom_attributes,
)
from ..qt_compat import QTreeWidgetItem, Qt, QCheckBox, QTableWidgetItem, QTimer

logger = get_logger(__name__)

class BonePresenter:
    def __init__(self, view, app_state):
        self.view = view
        self.app_state = app_state
        self.current_bone = None
        self.bone_data = {}  # Store original bone data for reset
        self.bone_tree_items = {}  # Map bone name to tree item
        self.is_updating = False  # Prevent feedback loops
        
        self.connect_signals()
        
        # 既に選択されているモデルがある場合はロード
        if self.app_state.current_model_root:
            # Qt のイベントループが安定してから実行
            QTimer.singleShot(100, self.load_bones)

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        
        # ツリービューのシグナル
        self.view.bone_tree.currentItemChanged.connect(self.on_bone_selected)
        self.view.refresh_btn.clicked.connect(self.load_bones)
        self.view.expand_all_btn.clicked.connect(self.view.bone_tree.expandAll)
        self.view.collapse_all_btn.clicked.connect(self.view.bone_tree.collapseAll)
        self.view.select_in_maya_btn.clicked.connect(self.select_bone_in_maya)
        self.view.search_edit.textChanged.connect(self.filter_bones)
        
        # ボーン選択ボタン
        self.view.select_parent_btn.clicked.connect(lambda: self.select_bone_dialog("parent"))
        self.view.select_connection_btn.clicked.connect(lambda: self.select_bone_dialog("connection"))
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
        logger.info(f"BonePresenter: Current model changed to {model_root}")
        self.current_bone = None
        self.load_bones()

    def load_bones(self):
        """ボーンツリーをロード"""
        logger.debug("Loading bones...")
        self.view.bone_tree.clear()
        self.bone_tree_items.clear()
        self.view.set_bone_details_enabled(False)
        
        current_model_root = self.app_state.current_model_root
        logger.debug(f"Current model root: {current_model_root}")
        
        if not current_model_root or not cmds.objExists(current_model_root):
            logger.warning(f"Model root does not exist: {current_model_root}")
            return

        # ジョイントを検索する複数の方法を試す
        joints = cmds.listRelatives(current_model_root, allDescendents=True, type="joint") or []
        logger.debug(f"Found {len(joints)} joints using listRelatives")
        
        # もしジョイントが見つからない場合、別の方法を試す
        if not joints:
            # ルートノードの子を確認
            children = cmds.listRelatives(current_model_root, children=True) or []
            logger.debug(f"Direct children of root: {children}")
            
            # 全ての子孫を取得してジョイントをフィルタ
            all_descendants = cmds.listRelatives(current_model_root, allDescendents=True) or []
            joints = [node for node in all_descendants if cmds.nodeType(node) == "joint"]
            logger.debug(f"Found {len(joints)} joints using nodeType filter from {len(all_descendants)} descendants")
        
        if not joints:
            logger.info("No bones found in the model")
            return

        # Create a dictionary to store tree items for quick lookup
        for joint in joints:
            # ボーン情報を取得
            name_jp = self._get_attr_safe(joint, "mmd_bone_name_jp", joint)
            
            # ツリーアイテムを作成（ボーン名のみ）
            item = QTreeWidgetItem([name_jp])
            item.setData(0, Qt.UserRole, joint)  # 実際のジョイント名を保存
            self.bone_tree_items[joint] = item

        # 階層構造を構築
        for joint in joints:
            parent = cmds.listRelatives(joint, parent=True, type="joint")
            parent_joint = parent[0] if parent else None
            
            item = self.bone_tree_items[joint]
            
            # 親がジョイントでない場合も考慮
            if not parent_joint and parent:
                # 親がジョイントでない場合、階層を上って最初のジョイントを探す
                all_parents = cmds.listRelatives(joint, allParents=True) or []
                for p in all_parents:
                    if cmds.nodeType(p) == "joint" and p in self.bone_tree_items:
                        parent_joint = p
                        break
            
            if parent_joint and parent_joint in self.bone_tree_items:
                self.bone_tree_items[parent_joint].addChild(item)
            else:
                self.view.bone_tree.addTopLevelItem(item)
        
        # ツリーを展開
        self.view.bone_tree.expandAll()
        
        # ツリーの最初のアイテムがある場合は、それを確認
        if self.view.bone_tree.topLevelItemCount() > 0:
            logger.debug(f"Top level items: {self.view.bone_tree.topLevelItemCount()}")
        else:
            logger.warning("No top level items in bone tree!")
        
        logger.info(f"Loaded {len(joints)} bones for model: {current_model_root}")

    def _get_bone_type(self, joint):
        """ボーンのタイプを判定"""
        flags = self._get_attr_safe(joint, "mmd_bone_flags", 0)
        
        if flags & 0x0020:  # IK
            return "IK"
        elif flags & 0x0100 or flags & 0x0200:  # 付与
            return "付与"
        elif flags & 0x1000:  # 物理後
            return "物理後"
        else:
            return "通常"

    def filter_bones(self, text):
        """ボーンを検索フィルタリング"""
        if not text:
            # 全て表示
            for item in self.bone_tree_items.values():
                item.setHidden(False)
                # 親も表示
                parent = item.parent()
                while parent:
                    parent.setHidden(False)
                    parent = parent.parent()
        else:
            # フィルタリング
            text_lower = text.lower()
            for joint, item in self.bone_tree_items.items():
                name_jp = item.text(0).lower()
                name_en = item.text(1).lower()
                
                if text_lower in name_jp or text_lower in name_en or text_lower in joint.lower():
                    item.setHidden(False)
                    # 親も表示
                    parent = item.parent()
                    while parent:
                        parent.setHidden(False)
                        parent = parent.parent()
                else:
                    item.setHidden(True)

    def on_bone_selected(self, current, previous):
        """ボーンが選択されたときの処理"""
        if not current:
            self.view.set_bone_details_enabled(False)
            return
        
        self.current_bone = current.data(0, Qt.UserRole)
        if not self.current_bone or not cmds.objExists(self.current_bone):
            self.view.set_bone_details_enabled(False)
            return
        
        logger.info(f"Selected bone: {self.current_bone}")
        self.view.set_bone_details_enabled(True)
        self.load_bone_properties()

    def load_bone_properties(self):
        """選択されたボーンのプロパティをロード"""
        if not self.current_bone:
            return
        
        self.is_updating = True
        try:
            # 基本情報
            self.view.bone_name_jp_edit.setText(
                self._get_attr_safe(self.current_bone, "mmd_bone_name_jp", self.current_bone)
            )
            self.view.bone_name_en_edit.setText(
                self._get_attr_safe(self.current_bone, "mmd_bone_name_en", "")
            )
            
            # 親ボーン
            parent = cmds.listRelatives(self.current_bone, parent=True, type="joint")
            self.view.parent_bone_edit.setText(parent[0] if parent else "")
            
            # 位置
            pos = cmds.xform(self.current_bone, query=True, translation=True, worldSpace=True)
            self.view.pos_x_spin.setValue(pos[0])
            self.view.pos_y_spin.setValue(pos[1])
            self.view.pos_z_spin.setValue(pos[2])
            
            # 変形階層
            self.view.deform_layer_spin.setValue(
                self._get_attr_safe(self.current_bone, "mmd_deform_layer", 0)
            )
            
            # ボーンフラグ
            flags = self._get_attr_safe(self.current_bone, "mmd_bone_flags", 0x0005)  # デフォルト: 回転可能+表示
            
            # 基本フラグ
            self.view.rotatable_check.setChecked(bool(flags & 0x0002))
            self.view.movable_check.setChecked(bool(flags & 0x0004))
            self.view.visible_check.setChecked(bool(flags & 0x0008))
            self.view.enabled_check.setChecked(bool(flags & 0x0010))
            
            # 特殊フラグ
            self.view.after_physics_check.setChecked(bool(flags & 0x1000))
            self.view.external_parent_check.setChecked(bool(flags & 0x2000))
            
            # 接続先
            connection_type = 0 if (flags & 0x0001) == 0 else 1
            self.view.connection_type_combo.setCurrentIndex(connection_type)
            
            if connection_type == 0:
                # 座標オフセット
                offset = self._get_attr_safe(self.current_bone, "mmd_bone_offset", [0.0, -1.0, 0.0])
                self.view.offset_x_spin.setValue(offset[0])
                self.view.offset_y_spin.setValue(offset[1])
                self.view.offset_z_spin.setValue(offset[2])
                self.view.connection_bone_edit.clear()
            else:
                # ボーン接続
                connection_bone = self._get_attr_safe(self.current_bone, "mmd_connection_bone", "")
                self.view.connection_bone_edit.setText(connection_bone)
            
            # IK設定
            self.view.ik_enabled_check.setChecked(bool(flags & 0x0020))
            self._load_ik_settings()
            
            # 付与設定
            self.view.rotation_grant_check.setChecked(bool(flags & 0x0100))
            self.view.move_grant_check.setChecked(bool(flags & 0x0200))
            self._load_grant_settings()
            
            # 軸制限
            self.view.fixed_axis_check.setChecked(bool(flags & 0x0400))
            self.view.local_axis_check.setChecked(bool(flags & 0x0800))
            self._load_axis_settings()
            
            # 外部親
            if flags & 0x2000:
                key = self._get_attr_safe(self.current_bone, "mmd_external_parent_key", -1)
                self.view.external_parent_key_spin.setValue(key)
            
            # データを保存（リセット用）
            self._store_bone_data()
            
        finally:
            self.is_updating = False

    def _load_ik_settings(self):
        """IK設定をロード"""
        if not self.view.ik_enabled_check.isChecked():
            self.view.ik_settings_group.setEnabled(False)
            self.view.ik_links_group.setEnabled(False)
            return
        
        self.view.ik_settings_group.setEnabled(True)
        self.view.ik_links_group.setEnabled(True)
        
        # IKターゲット
        ik_target = self._get_attr_safe(self.current_bone, "mmd_ik_target", "")
        self.view.ik_target_edit.setText(ik_target)
        
        # IKループ回数
        ik_loop = self._get_attr_safe(self.current_bone, "mmd_ik_loop", 10)
        self.view.ik_loop_spin.setValue(ik_loop)
        
        # 制限角度（ラジアンから度に変換）
        ik_limit_rad = self._get_attr_safe(self.current_bone, "mmd_ik_limit_angle", 2.0)
        ik_limit_deg = math.degrees(ik_limit_rad)
        self.view.ik_limit_angle_spin.setValue(ik_limit_deg)
        
        # IKリンクをロード
        self._load_ik_links()

    def _load_ik_links(self):
        """IKリンクをロード"""
        self.view.ik_links_table.setRowCount(0)
        
        # IKリンクデータを取得
        ik_links = self._get_attr_safe(self.current_bone, "mmd_ik_links", [])
        
        for link_data in ik_links:
            if isinstance(link_data, dict):
                self._add_ik_link_row(
                    link_data.get("bone", ""),
                    link_data.get("limit_enabled", False),
                    link_data.get("lower_limit", [0.0, 0.0, 0.0]),
                    link_data.get("upper_limit", [0.0, 0.0, 0.0])
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
        self.view.grant_settings_group.setEnabled(grant_enabled)
        
        if not grant_enabled:
            return
        
        # 付与親
        grant_parent = self._get_attr_safe(self.current_bone, "mmd_grant_parent", "")
        self.view.grant_parent_edit.setText(grant_parent)
        
        # 付与率
        grant_rate = self._get_attr_safe(self.current_bone, "mmd_grant_rate", 1.0)
        self.view.grant_rate_spin.setValue(grant_rate)
        
        # ローカル付与
        flags = self._get_attr_safe(self.current_bone, "mmd_bone_flags", 0)
        self.view.local_grant_check.setChecked(bool(flags & 0x0080))

    def _load_axis_settings(self):
        """軸制限設定をロード"""
        # 軸固定
        if self.view.fixed_axis_check.isChecked():
            self.view.fixed_axis_group.setEnabled(True)
            fixed_axis = self._get_attr_safe(self.current_bone, "mmd_fixed_axis", [0.0, 0.0, 1.0])
            self.view.fixed_axis_x_spin.setValue(fixed_axis[0])
            self.view.fixed_axis_y_spin.setValue(fixed_axis[1])
            self.view.fixed_axis_z_spin.setValue(fixed_axis[2])
        else:
            self.view.fixed_axis_group.setEnabled(False)
        
        # ローカル軸
        if self.view.local_axis_check.isChecked():
            self.view.local_axis_group.setEnabled(True)
            local_x = self._get_attr_safe(self.current_bone, "mmd_local_x_axis", [1.0, 0.0, 0.0])
            local_z = self._get_attr_safe(self.current_bone, "mmd_local_z_axis", [0.0, 0.0, 1.0])
            
            self.view.local_x_axis_x_spin.setValue(local_x[0])
            self.view.local_x_axis_y_spin.setValue(local_x[1])
            self.view.local_x_axis_z_spin.setValue(local_x[2])
            
            self.view.local_z_axis_x_spin.setValue(local_z[0])
            self.view.local_z_axis_y_spin.setValue(local_z[1])
            self.view.local_z_axis_z_spin.setValue(local_z[2])
        else:
            self.view.local_axis_group.setEnabled(False)

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
                self.view.pos_z_spin.value()
            ],
            "deform_layer": self.view.deform_layer_spin.value(),
            "flags": self._calculate_bone_flags(),
            "all_settings": self._gather_all_settings()
        }

    def select_bone_in_maya(self):
        """Mayaビューポートでボーンを選択"""
        if self.current_bone and cmds.objExists(self.current_bone):
            cmds.select(self.current_bone, replace=True)
            logger.info(f"Selected bone in Maya: {self.current_bone}")
            self.app_state.emit_status(f"ボーンを選択しました: {self.current_bone}")

    def select_bone_dialog(self, target_type):
        """ボーン選択ダイアログを表示"""
        # 簡易的な実装：現在のMaya選択を使用
        selected = cmds.ls(selection=True, type="joint")
        if not selected:
            self.app_state.emit_status("ジョイントを選択してください")
            return
        
        bone = selected[0]
        
        if target_type == "parent":
            # 現在のボーンの子供でないことを確認
            if self._is_descendant(self.current_bone, bone):
                self.app_state.emit_status("子ボーンを親として設定することはできません")
                return
            self.view.parent_bone_edit.setText(bone)
        elif target_type == "connection":
            self.view.connection_bone_edit.setText(bone)
        elif target_type == "ik_target":
            self.view.ik_target_edit.setText(bone)
        elif target_type == "grant_parent":
            self.view.grant_parent_edit.setText(bone)

    def _is_descendant(self, parent, child):
        """childがparentの子孫かどうかをチェック"""
        if not parent or not child:
            return False
        
        descendants = cmds.listRelatives(parent, allDescendents=True, type="joint") or []
        return child in descendants

    def on_ik_enabled_toggled(self, checked):
        """IK有効化トグル時の処理"""
        self.view.ik_settings_group.setEnabled(checked)
        self.view.ik_links_group.setEnabled(checked)

    def on_grant_toggled(self):
        """付与設定トグル時の処理"""
        enabled = self.view.rotation_grant_check.isChecked() or self.view.move_grant_check.isChecked()
        self.view.grant_settings_group.setEnabled(enabled)

    def on_axis_toggled(self):
        """軸制限トグル時の処理"""
        self.view.fixed_axis_group.setEnabled(self.view.fixed_axis_check.isChecked())
        self.view.local_axis_group.setEnabled(self.view.local_axis_check.isChecked())

    def on_external_parent_toggled(self, checked):
        """外部親変形トグル時の処理"""
        self.view.external_parent_key_spin.setEnabled(checked)

    def on_connection_type_changed(self, index):
        """接続タイプ変更時の処理"""
        if index == 0:  # 座標オフセット
            self.view.offset_x_spin.setEnabled(True)
            self.view.offset_y_spin.setEnabled(True)
            self.view.offset_z_spin.setEnabled(True)
            self.view.connection_bone_edit.setEnabled(False)
            self.view.select_connection_btn.setEnabled(False)
        else:  # ボーン
            self.view.offset_x_spin.setEnabled(False)
            self.view.offset_y_spin.setEnabled(False)
            self.view.offset_z_spin.setEnabled(False)
            self.view.connection_bone_edit.setEnabled(True)
            self.view.select_connection_btn.setEnabled(True)

    def add_ik_link(self):
        """IKリンクを追加"""
        selected = cmds.ls(selection=True, type="joint")
        if not selected:
            self.app_state.emit_status("IKリンクとして追加するジョイントを選択してください")
            return
        
        bone = selected[0]
        self._add_ik_link_row(bone, False, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0])

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
        if not self.current_bone or not cmds.objExists(self.current_bone):
            return
        
        try:
            # 基本情報
            attributes = {
                "mmd_bone_name_jp": self.view.bone_name_jp_edit.text(),
                "mmd_bone_name_en": self.view.bone_name_en_edit.text(),
                "mmd_deform_layer": self.view.deform_layer_spin.value(),
                "mmd_bone_flags": self._calculate_bone_flags(),
            }
            
            # 位置（ワールド座標で設定）
            pos = [
                self.view.pos_x_spin.value(),
                self.view.pos_y_spin.value(),
                self.view.pos_z_spin.value()
            ]
            cmds.xform(self.current_bone, translation=pos, worldSpace=True)
            
            # 親ボーンの変更
            new_parent = self.view.parent_bone_edit.text()
            if new_parent and cmds.objExists(new_parent):
                current_parent = cmds.listRelatives(self.current_bone, parent=True)
                if not current_parent or current_parent[0] != new_parent:
                    cmds.parent(self.current_bone, new_parent)
            
            # 接続先設定
            if self.view.connection_type_combo.currentIndex() == 0:
                # 座標オフセット
                offset = [
                    self.view.offset_x_spin.value(),
                    self.view.offset_y_spin.value(),
                    self.view.offset_z_spin.value()
                ]
                attributes["mmd_bone_offset"] = offset
            else:
                # ボーン接続
                attributes["mmd_connection_bone"] = self.view.connection_bone_edit.text()
            
            # IK設定
            if self.view.ik_enabled_check.isChecked():
                attributes["mmd_ik_target"] = self.view.ik_target_edit.text()
                attributes["mmd_ik_loop"] = self.view.ik_loop_spin.value()
                attributes["mmd_ik_limit_angle"] = math.radians(self.view.ik_limit_angle_spin.value())
                
                # IKリンク
                ik_links = []
                for row in range(self.view.ik_links_table.rowCount()):
                    bone_item = self.view.ik_links_table.item(row, 0)
                    limit_widget = self.view.ik_links_table.cellWidget(row, 1)
                    
                    if bone_item:
                        link_data = {
                            "bone": bone_item.text(),
                            "limit_enabled": limit_widget.isChecked() if limit_widget else False,
                            "lower_limit": [
                                math.radians(float(self.view.ik_links_table.item(row, 2).text())),
                                math.radians(float(self.view.ik_links_table.item(row, 3).text())),
                                math.radians(float(self.view.ik_links_table.item(row, 4).text()))
                            ],
                            "upper_limit": [
                                math.radians(float(self.view.ik_links_table.item(row, 5).text())),
                                math.radians(float(self.view.ik_links_table.item(row, 6).text())),
                                math.radians(float(self.view.ik_links_table.item(row, 7).text()))
                            ]
                        }
                        ik_links.append(link_data)
                
                # IKリンクをJSON文字列として保存
                import json
                attributes["mmd_ik_links"] = json.dumps(ik_links)
            
            # 付与設定
            if self.view.rotation_grant_check.isChecked() or self.view.move_grant_check.isChecked():
                attributes["mmd_grant_parent"] = self.view.grant_parent_edit.text()
                attributes["mmd_grant_rate"] = self.view.grant_rate_spin.value()
            
            # 軸制限設定
            if self.view.fixed_axis_check.isChecked():
                fixed_axis = [
                    self.view.fixed_axis_x_spin.value(),
                    self.view.fixed_axis_y_spin.value(),
                    self.view.fixed_axis_z_spin.value()
                ]
                attributes["mmd_fixed_axis"] = fixed_axis
            
            if self.view.local_axis_check.isChecked():
                local_x = [
                    self.view.local_x_axis_x_spin.value(),
                    self.view.local_x_axis_y_spin.value(),
                    self.view.local_x_axis_z_spin.value()
                ]
                local_z = [
                    self.view.local_z_axis_x_spin.value(),
                    self.view.local_z_axis_y_spin.value(),
                    self.view.local_z_axis_z_spin.value()
                ]
                attributes["mmd_local_x_axis"] = local_x
                attributes["mmd_local_z_axis"] = local_z
            
            # 外部親設定
            if self.view.external_parent_check.isChecked():
                attributes["mmd_external_parent_key"] = self.view.external_parent_key_spin.value()
            
            # 属性を設定
            self._ensure_mmd_attributes(self.current_bone)
            set_custom_attributes(self.current_bone, attributes)
            
            # ツリービューの表示を更新
            if self.current_bone in self.bone_tree_items:
                item = self.bone_tree_items[self.current_bone]
                item.setText(0, self.view.bone_name_jp_edit.text())
            
            logger.info(f"ボーン '{self.current_bone}' の変更を適用しました")
            self.app_state.emit_status(f"ボーンの変更を適用しました: {self.current_bone}")
            
        except Exception as e:
            logger.error(f"Failed to apply bone changes: {e}", exc_info=True)
            self.app_state.emit_status(f"ボーンの変更に失敗しました: {str(e)}")

    def reset_changes(self):
        """変更をリセット"""
        if self.current_bone and self.bone_data:
            self.load_bone_properties()
            self.app_state.emit_status("変更をリセットしました")

    def _calculate_bone_flags(self):
        """UIの状態からボーンフラグを計算"""
        flags = 0
        
        # 接続先
        if self.view.connection_type_combo.currentIndex() == 1:
            flags |= 0x0001
        
        # 基本フラグ
        if self.view.rotatable_check.isChecked():
            flags |= 0x0002
        if self.view.movable_check.isChecked():
            flags |= 0x0004
        if self.view.visible_check.isChecked():
            flags |= 0x0008
        if self.view.enabled_check.isChecked():
            flags |= 0x0010
        
        # IK
        if self.view.ik_enabled_check.isChecked():
            flags |= 0x0020
        
        # 付与
        if self.view.local_grant_check.isChecked():
            flags |= 0x0080
        if self.view.rotation_grant_check.isChecked():
            flags |= 0x0100
        if self.view.move_grant_check.isChecked():
            flags |= 0x0200
        
        # 軸制限
        if self.view.fixed_axis_check.isChecked():
            flags |= 0x0400
        if self.view.local_axis_check.isChecked():
            flags |= 0x0800
        
        # 特殊
        if self.view.after_physics_check.isChecked():
            flags |= 0x1000
        if self.view.external_parent_check.isChecked():
            flags |= 0x2000
        
        return flags

    def _gather_all_settings(self):
        """全ての設定を収集"""
        # 現在のUI状態を辞書として返す
        return {
            # ここに全ての設定を追加（必要に応じて）
        }

    def _get_attr_safe(self, node, attr, default):
        """属性を安全に取得"""
        try:
            if cmds.attributeQuery(attr, node=node, exists=True):
                value = cmds.getAttr(f"{node}.{attr}")
                # IKリンクの場合、JSON文字列をパース
                if attr == "mmd_ik_links" and isinstance(value, str):
                    import json
                    try:
                        return json.loads(value)
                    except:
                        return default
                return value if value is not None else default
        except:
            pass
        return default

    def _ensure_mmd_attributes(self, joint):
        """MMD用カスタム属性が存在することを確認"""
        attrs = [
            ("mmd_bone_name_jp", "string", ""),
            ("mmd_bone_name_en", "string", ""),
            ("mmd_bone_flags", "long", 0x0005),
            ("mmd_deform_layer", "long", 0),
            ("mmd_bone_offset", "double3", None),
            ("mmd_connection_bone", "string", ""),
            ("mmd_ik_target", "string", ""),
            ("mmd_ik_loop", "long", 10),
            ("mmd_ik_limit_angle", "double", 2.0),
            ("mmd_ik_links", "string", "[]"),  # JSON文字列として保存
            ("mmd_grant_parent", "string", ""),
            ("mmd_grant_rate", "double", 1.0),
            ("mmd_fixed_axis", "double3", None),
            ("mmd_local_x_axis", "double3", None),
            ("mmd_local_z_axis", "double3", None),
            ("mmd_external_parent_key", "long", -1),
        ]
        
        for attr_name, attr_type, default in attrs:
            if not cmds.attributeQuery(attr_name, node=joint, exists=True):
                if attr_type == "double3":
                    cmds.addAttr(joint, longName=attr_name, attributeType="double3")
                    cmds.addAttr(joint, longName=f"{attr_name}X", attributeType="double", parent=attr_name)
                    cmds.addAttr(joint, longName=f"{attr_name}Y", attributeType="double", parent=attr_name)
                    cmds.addAttr(joint, longName=f"{attr_name}Z", attributeType="double", parent=attr_name)
                    if attr_name == "mmd_bone_offset":
                        cmds.setAttr(f"{joint}.{attr_name}", 0.0, -1.0, 0.0, type="double3")
                    elif attr_name == "mmd_fixed_axis":
                        cmds.setAttr(f"{joint}.{attr_name}", 0.0, 0.0, 1.0, type="double3")
                    elif attr_name == "mmd_local_x_axis":
                        cmds.setAttr(f"{joint}.{attr_name}", 1.0, 0.0, 0.0, type="double3")
                    elif attr_name == "mmd_local_z_axis":
                        cmds.setAttr(f"{joint}.{attr_name}", 0.0, 0.0, 1.0, type="double3")
                else:
                    cmds.addAttr(joint, longName=attr_name, attributeType=attr_type)
                    if default is not None:
                        cmds.setAttr(f"{joint}.{attr_name}", default)
