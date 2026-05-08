import json
from maya import cmds
from ...core.logger import get_logger
from ...core.maya_utils import set_custom_attributes, set_attribute
from ..qt_compat import QTimer, QListWidgetItem

logger = get_logger(__name__)


class MorphPresenter:
    def __init__(self, view, app_state):
        self.view = view
        self.app_state = app_state
        self.blend_shape_node = None
        self.current_morph = None
        self.morph_data = {}  # MMDモーフデータのキャッシュ
        self.group_morphs = {}  # グループごとのモーフリスト
        self.is_updating = False

        self.connect_signals()

        # 既に選択されているモデルがある場合はロード
        if self.app_state.current_model_root:
            QTimer.singleShot(100, self.load_morphs)

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)

        # モーフリスト関連
        self.view.morph_list.currentItemChanged.connect(self.on_morph_selected)
        self.view.refresh_morphs_btn.clicked.connect(self.load_morphs)
        self.view.select_in_maya_btn.clicked.connect(self.select_morph_in_maya)
        self.view.search_edit.textChanged.connect(self.filter_morphs)

        # グループリスト関連
        self.view.group_list.currentItemChanged.connect(self.on_group_selected)
        self.view.add_group_btn.clicked.connect(self.add_group)
        self.view.remove_group_btn.clicked.connect(self.remove_group)

        # スライダー関連
        self.view.morph_slider.valueChanged.connect(self.on_morph_slider_changed)
        self.view.reset_slider_btn.clicked.connect(self.reset_current_morph)
        self.view.reset_all_btn.clicked.connect(self.reset_all_morphs)

        # 基本情報タブ
        self.view.morph_type_combo.currentIndexChanged.connect(self.on_morph_type_changed)

        # Maya連携タブ
        self.view.connect_btn.clicked.connect(self.connect_blend_shape)
        self.view.disconnect_btn.clicked.connect(self.disconnect_blend_shape)
        self.view.auto_connect_btn.clicked.connect(self.auto_connect_blend_shapes)
        self.view.select_blend_shape_btn.clicked.connect(self.select_blend_shape_node)

        # 適用/リセットボタン
        self.view.apply_btn.clicked.connect(self.apply_changes)
        self.view.reset_btn.clicked.connect(self.reset_changes)

        # プリセット関連
        self.view.save_preset_btn.clicked.connect(self.save_preset)
        self.view.load_preset_btn.clicked.connect(self.load_preset)
        self.view.delete_preset_btn.clicked.connect(self.delete_preset)

    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        logger.info(f"MorphPresenter: Current model changed to {model_root}")
        self.load_morphs()

    def load_morphs(self):
        """モーフをロード"""
        self.view.morph_list.clear()
        self.morph_data.clear()
        self.group_morphs.clear()
        self.current_morph = None
        self.view.set_morph_details_enabled(False)

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not cmds.objExists(current_model_root):
            return

        # MMDモーフデータを収集
        self._load_mmd_morphs(current_model_root)

        # ブレンドシェイプノードを検索
        self._load_blend_shapes(current_model_root)

        # グループごとにモーフを整理
        self._organize_morphs_by_group()

        # 全てのモーフを表示
        self._display_all_morphs()

        # プリセットを読み込み
        self._load_presets(current_model_root)

        logger.info(f"Loaded {self.view.morph_list.count()} morphs for model: {current_model_root}")

    def _load_mmd_morphs(self, model_root):
        """MMDモーフデータをロード"""
        # MMDモーフアトリビュートを検索
        morph_data_json = self._get_attr_safe(model_root, "mmdMorphData", "")
        if morph_data_json:
            try:
                self.morph_data = json.loads(morph_data_json)
            except Exception as e:
                logger.error(f"Failed to parse MMD morph data: {e}", exc_info=True)

    def _load_blend_shapes(self, model_root):
        """ブレンドシェイプを検索"""
        shapes = cmds.listRelatives(model_root, allDescendents=True, type="mesh") or []
        if not shapes:
            return

        # 全てのブレンドシェイプノードを収集
        for shape in shapes:
            history = cmds.listHistory(shape) or []
            blend_shape_nodes = cmds.ls(history, type="blendShape") or []

            for bs_node in blend_shape_nodes:
                # 最初のブレンドシェイプノードをデフォルトとして保存
                if not self.blend_shape_node:
                    self.blend_shape_node = bs_node

                # ブレンドシェイプターゲットを取得
                aliases = cmds.aliasAttr(bs_node, query=True) or []
                for i in range(0, len(aliases), 2):
                    target_name = aliases[i]

                    # MMDデータと照合、なければ新規作成
                    if target_name not in self.morph_data:
                        self.morph_data[target_name] = {
                            "name_jp": target_name,
                            "name_en": "",
                            "panel": 0,
                            "type": 0,  # 頂点モーフ
                            "group": "その他",
                            "blend_shape_node": bs_node,
                            "blend_shape_target": target_name,
                        }
                    else:
                        # ブレンドシェイプ情報を追加
                        self.morph_data[target_name]["blend_shape_node"] = bs_node
                        self.morph_data[target_name]["blend_shape_target"] = target_name

    def _organize_morphs_by_group(self):
        """グループごとにモーフを整理"""
        # デフォルトグループ
        for group in ["眉", "目", "口", "その他"]:
            self.group_morphs[group] = []

        # モーフをグループに振り分け
        for morph_name, data in self.morph_data.items():
            group = data.get("group", "その他")
            if group not in self.group_morphs:
                self.group_morphs[group] = []
            self.group_morphs[group].append(morph_name)

    def _display_all_morphs(self):
        """全てのモーフを表示"""
        for morph_name in sorted(self.morph_data.keys()):
            item = QListWidgetItem(morph_name)
            self.view.morph_list.addItem(item)

    def on_morph_selected(self, current, previous):
        """モーフが選択されたときの処理"""
        if not current or self.is_updating:
            self.view.set_morph_details_enabled(False)
            return

        morph_name = current.text()
        self.current_morph = morph_name
        logger.info(f"Selected morph: {morph_name}")

        self.view.set_morph_details_enabled(True)
        self.load_morph_details(morph_name)

    def load_morph_details(self, morph_name):
        """モーフの詳細情報をロード"""
        if morph_name not in self.morph_data:
            return

        self.is_updating = True
        data = self.morph_data[morph_name]

        # 基本情報
        self.view.morph_name_jp_edit.setText(data.get("name_jp", morph_name))
        self.view.morph_name_en_edit.setText(data.get("name_en", ""))
        self.view.panel_combo.setCurrentIndex(data.get("panel", 0))
        self.view.morph_type_combo.setCurrentIndex(data.get("type", 0))
        self.view.group_combo.setCurrentText(data.get("group", "その他"))

        # Maya連携情報
        blend_shape_node = data.get("blend_shape_node")
        if blend_shape_node and cmds.objExists(blend_shape_node):
            self.view.blend_shape_edit.setText(blend_shape_node)
            self.view.target_name_edit.setText(data.get("blend_shape_target", ""))
            self.view.connection_status_label.setText("連携中")
            self.view.connection_status_label.setStyleSheet("color: green;")

            # 現在の値を取得
            target = data.get("blend_shape_target", morph_name)
            try:
                weight = cmds.getAttr(f"{blend_shape_node}.{target}")
                self.view.morph_slider.setValue(int(weight * 100))
                self.view.morph_value_label.setText(f"{int(weight * 100)}%")
            except Exception as e:
                logger.debug(f"Failed to get blend shape weight: {e}")
        else:
            self.view.blend_shape_edit.clear()
            self.view.target_name_edit.clear()
            self.view.connection_status_label.setText("未連携")
            self.view.connection_status_label.setStyleSheet("color: red;")
            self.view.morph_slider.setValue(0)
            self.view.morph_value_label.setText("0%")

        # オフセット情報を更新
        self.update_offset_table(morph_name)

        self.is_updating = False

    def update_offset_table(self, morph_name):
        """オフセットテーブルを更新"""
        self.view.offset_table.setRowCount(0)

        # TODO: オフセットデータの実装
        # 現在はプレースホルダー
        self.view.offset_count_label.setText("オフセット数: 0")

    def on_morph_slider_changed(self, value):
        """スライダーが変更されたときの処理"""
        if self.is_updating or not self.current_morph:
            return

        # ラベルを更新
        self.view.morph_value_label.setText(f"{value}%")

        # ブレンドシェイプに適用
        if self.current_morph in self.morph_data:
            data = self.morph_data[self.current_morph]
            blend_shape_node = data.get("blend_shape_node")
            target = data.get("blend_shape_target")

            if blend_shape_node and target and cmds.objExists(blend_shape_node):
                weight = value / 100.0

                # 詳細設定を適用
                if self.view.invert_check.isChecked():
                    weight = 1.0 - weight
                weight *= self.view.multiplier_spin.value()

                try:
                    cmds.setAttr(f"{blend_shape_node}.{target}", weight)
                except Exception as e:
                    logger.error(f"Failed to set blend shape weight: {blend_shape_node}.{target}: {e}")

    def on_group_selected(self, current, previous):
        """グループが選択されたときの処理"""
        if not current:
            return

        group_name = current.text()
        self.filter_morphs_by_group(group_name)

    def filter_morphs_by_group(self, group_name):
        """グループでモーフをフィルタ"""
        self.view.morph_list.clear()

        if group_name == "全て表示":
            # 全てのモーフを表示
            self._display_all_morphs()
        elif group_name in self.group_morphs:
            for morph_name in sorted(self.group_morphs[group_name]):
                item = QListWidgetItem(morph_name)
                self.view.morph_list.addItem(item)

    def filter_morphs(self, text):
        """検索テキストでモーフをフィルタ"""
        # 全アイテムを表示/非表示
        for i in range(self.view.morph_list.count()):
            item = self.view.morph_list.item(i)
            if text.lower() in item.text().lower():
                item.setHidden(False)
            else:
                item.setHidden(True)

    def select_morph_in_maya(self):
        """Mayaでモーフ（ブレンドシェイプノード）を選択"""
        if not self.current_morph:
            return

        data = self.morph_data.get(self.current_morph, {})
        blend_shape_node = data.get("blend_shape_node")

        if blend_shape_node and cmds.objExists(blend_shape_node):
            cmds.select(blend_shape_node, replace=True)
            logger.info(f"Selected blend shape node in Maya: {blend_shape_node}")
            self.app_state.emit_status(f"ブレンドシェイプノードを選択しました: {blend_shape_node}")

    def reset_current_morph(self):
        """現在のモーフをリセット"""
        self.view.morph_slider.setValue(0)

    def reset_all_morphs(self):
        """全てのモーフをリセット"""
        reset_count = 0
        for morph_name, data in self.morph_data.items():
            blend_shape_node = data.get("blend_shape_node")
            target = data.get("blend_shape_target")

            if blend_shape_node and target and cmds.objExists(blend_shape_node):
                try:
                    current_value = cmds.getAttr(f"{blend_shape_node}.{target}")
                    if current_value != 0:
                        cmds.setAttr(f"{blend_shape_node}.{target}", 0)
                        reset_count += 1
                except Exception as e:
                    logger.debug(f"Failed to reset morph: {e}")

        # 現在のスライダーもリセット
        self.view.morph_slider.setValue(0)
        self.app_state.emit_status(f"{reset_count}個のモーフをリセットしました")
        logger.info(f"全モーフリセット完了: {reset_count}個のモーフをリセット")

    def on_morph_type_changed(self, index):
        """モーフタイプが変更されたときの処理"""
        # タイプに応じてUIを更新（将来の拡張用）
        pass

    def add_group(self):
        """グループを追加"""
        from ..qt_compat import QInputDialog

        # グループ名を入力
        group_name, ok = QInputDialog.getText(self.view, "グループ追加", "新しいグループ名を入力してください:")

        if ok and group_name:
            # 既存のグループと重複チェック
            existing_groups = []
            for i in range(self.view.group_list.count()):
                existing_groups.append(self.view.group_list.item(i).text())

            if group_name in existing_groups:
                self.app_state.emit_status(f"グループ '{group_name}' は既に存在します", "warning")
                return

            # グループリストに追加
            self.view.group_list.addItem(group_name)
            self.group_morphs[group_name] = []

            # グループコンボボックスにも追加
            self.view.group_combo.addItem(group_name)

            logger.info(f"グループを追加しました: {group_name}")
            self.app_state.emit_status(f"グループを追加しました: {group_name}")

    def remove_group(self):
        """グループを削除"""
        current = self.view.group_list.currentItem()
        if current and current.text() not in ["眉", "目", "口", "その他"]:
            # カスタムグループのみ削除可能
            self.view.group_list.takeItem(self.view.group_list.row(current))

    def connect_blend_shape(self):
        """ブレンドシェイプを連携"""
        if not self.current_morph:
            self.app_state.emit_status("モーフを選択してください", "warning")
            return

        # UI から情報を取得
        blend_shape_node = self.view.blend_shape_edit.text()
        target_name = self.view.target_name_edit.text()

        if not blend_shape_node or not target_name:
            self.app_state.emit_status("ブレンドシェイプノードとターゲット名を入力してください", "warning")
            return

        # ノードの存在確認
        if not cmds.objExists(blend_shape_node):
            self.app_state.emit_status(f"ブレンドシェイプノードが見つかりません: {blend_shape_node}", "error")
            return

        # ターゲットの存在確認
        try:
            cmds.getAttr(f"{blend_shape_node}.{target_name}")
        except Exception:
            self.app_state.emit_status(f"ターゲットが見つかりません: {target_name}", "error")
            return

        # 連携を設定
        data = self.morph_data[self.current_morph]
        data["blend_shape_node"] = blend_shape_node
        data["blend_shape_target"] = target_name

        # UIを更新
        self.load_morph_details(self.current_morph)

        logger.info(f"モーフを連携しました: {self.current_morph} -> {blend_shape_node}.{target_name}")
        self.app_state.emit_status(f"モーフを連携しました: {self.current_morph}")

    def disconnect_blend_shape(self):
        """ブレンドシェイプの連携を解除"""
        if not self.current_morph:
            return

        if self.current_morph in self.morph_data:
            self.morph_data[self.current_morph].pop("blend_shape_node", None)
            self.morph_data[self.current_morph].pop("blend_shape_target", None)
            self.load_morph_details(self.current_morph)
            self.app_state.emit_status("ブレンドシェイプの連携を解除しました")

    def auto_connect_blend_shapes(self):
        """ブレンドシェイプを自動連携"""
        logger.info("自動連携を開始します")

        connected_count = 0
        current_model_root = self.app_state.current_model_root
        if not current_model_root:
            return

        # 全てのブレンドシェイプノードを収集
        shapes = cmds.listRelatives(current_model_root, allDescendents=True, type="mesh") or []
        blend_shape_nodes = []

        for shape in shapes:
            history = cmds.listHistory(shape) or []
            bs_nodes = cmds.ls(history, type="blendShape") or []
            blend_shape_nodes.extend(bs_nodes)

        # 各モーフに対して名前マッチングを試みる
        for morph_name, data in self.morph_data.items():
            # 既に連携済みの場合はスキップ
            if data.get("blend_shape_node"):
                continue

            # 日本語名と英語名で検索
            search_names = [morph_name]
            if data.get("name_jp"):
                search_names.append(data["name_jp"])
            if data.get("name_en"):
                search_names.append(data["name_en"])

            # ブレンドシェイプノードから一致するターゲットを探す
            for bs_node in blend_shape_nodes:
                aliases = cmds.aliasAttr(bs_node, query=True) or []
                for i in range(0, len(aliases), 2):
                    target_name = aliases[i]

                    # 名前が一致するか確認
                    for search_name in search_names:
                        if (
                            target_name.lower() == search_name.lower()
                            or search_name in target_name
                            or target_name in search_name
                        ):
                            # 連携を設定
                            data["blend_shape_node"] = bs_node
                            data["blend_shape_target"] = target_name
                            connected_count += 1
                            logger.info(f"自動連携成功: {morph_name} -> {bs_node}.{target_name}")
                            break

                    if data.get("blend_shape_node"):
                        break

                if data.get("blend_shape_node"):
                    break

        # 現在選択中のモーフの情報を更新
        if self.current_morph:
            self.load_morph_details(self.current_morph)

        # 結果を通知
        self.app_state.emit_status(f"{connected_count}個のモーフを自動連携しました")
        logger.info(f"自動連携完了: {connected_count}個のモーフを連携")

    def select_blend_shape_node(self):
        """ブレンドシェイプノードを選択"""
        selected = cmds.ls(selection=True)
        if selected:
            # ブレンドシェイプノードを探す
            for obj in selected:
                if cmds.nodeType(obj) == "blendShape":
                    self.view.blend_shape_edit.setText(obj)
                    return

                # ヒストリーから探す
                history = cmds.listHistory(obj) or []
                blend_shapes = cmds.ls(history, type="blendShape") or []
                if blend_shapes:
                    self.view.blend_shape_edit.setText(blend_shapes[0])
                    return

    def apply_changes(self):
        """変更を適用"""
        if not self.current_morph:
            return

        # モーフデータを更新
        data = self.morph_data[self.current_morph]
        data["name_jp"] = self.view.morph_name_jp_edit.text()
        data["name_en"] = self.view.morph_name_en_edit.text()
        data["panel"] = self.view.panel_combo.currentIndex()
        data["type"] = self.view.morph_type_combo.currentIndex()
        data["group"] = self.view.group_combo.currentText()

        # MMDアトリビュートに保存
        current_model_root = self.app_state.current_model_root
        if current_model_root and cmds.objExists(current_model_root):
            self._save_mmd_morph_data(current_model_root)

        # グループを再整理
        self._organize_morphs_by_group()

        logger.info(f"モーフ '{self.current_morph}' の変更を適用しました")
        self.app_state.emit_status(f"モーフの変更を適用しました: {self.current_morph}")

    def _save_mmd_morph_data(self, model_root):
        """MMDモーフデータを保存"""
        morph_data_json = json.dumps(self.morph_data, ensure_ascii=False)
        set_custom_attributes(model_root, {"mmdMorphData": morph_data_json})

    def reset_changes(self):
        """変更をリセット"""
        if self.current_morph:
            self.load_morph_details(self.current_morph)
            logger.info(f"モーフ '{self.current_morph}' の変更をリセットしました")

    def save_preset(self):
        """現在のモーフ値をプリセットとして保存"""
        preset_name = self.view.preset_combo.currentText()
        if not preset_name or preset_name == "なし":
            self.app_state.emit_status("プリセット名を入力してください", "warning")
            return

        # 現在のモーフ値を収集
        preset_data = {}
        for morph_name, data in self.morph_data.items():
            blend_shape_node = data.get("blend_shape_node")
            target = data.get("blend_shape_target")

            if blend_shape_node and target and cmds.objExists(blend_shape_node):
                try:
                    value = cmds.getAttr(f"{blend_shape_node}.{target}")
                    if value != 0:  # 0以外の値のみ保存
                        preset_data[morph_name] = value
                except Exception:
                    pass

        if not preset_data:
            self.app_state.emit_status("保存するモーフ値がありません", "warning")
            return

        # プリセットをモデルのアトリビュートに保存
        current_model_root = self.app_state.current_model_root
        if current_model_root and cmds.objExists(current_model_root):
            # プリセット用アトリビュートを作成
            # プリセット用アトリビュートがなければ作成
            if not cmds.attributeQuery("mmdMorphPresets", node=current_model_root, exists=True):
                set_custom_attributes(current_model_root, {"mmdMorphPresets": ""})

            # 既存のプリセットを読み込み
            presets = {}
            presets_json = cmds.getAttr(f"{current_model_root}.mmdMorphPresets")
            if presets_json:
                try:
                    presets = json.loads(presets_json)
                except Exception:
                    pass

            # 新しいプリセットを追加
            presets[preset_name] = preset_data

            # 保存
            presets_json = json.dumps(presets, ensure_ascii=False)
            set_attribute(current_model_root, "mmdMorphPresets", presets_json, "string")

            # コンボボックスに追加（重複チェック）
            if self.view.preset_combo.findText(preset_name) == -1:
                self.view.preset_combo.addItem(preset_name)

            logger.info(f"プリセット '{preset_name}' を保存しました")
            self.app_state.emit_status(f"プリセット '{preset_name}' を保存しました")

    def load_preset(self):
        """プリセットを読み込み"""
        preset_name = self.view.preset_combo.currentText()
        if not preset_name or preset_name == "なし":
            return

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not cmds.objExists(current_model_root):
            return

        # プリセットを読み込み
        if not cmds.attributeQuery("mmdMorphPresets", node=current_model_root, exists=True):
            self.app_state.emit_status("プリセットが見つかりません", "warning")
            return

        presets_json = cmds.getAttr(f"{current_model_root}.mmdMorphPresets")
        if not presets_json:
            self.app_state.emit_status("プリセットが見つかりません", "warning")
            return

        try:
            presets = json.loads(presets_json)
            if preset_name not in presets:
                self.app_state.emit_status(f"プリセット '{preset_name}' が見つかりません", "warning")
                return

            # プリセットの値を適用
            preset_data = presets[preset_name]
            applied_count = 0

            for morph_name, value in preset_data.items():
                if morph_name in self.morph_data:
                    data = self.morph_data[morph_name]
                    blend_shape_node = data.get("blend_shape_node")
                    target = data.get("blend_shape_target")

                    if blend_shape_node and target and cmds.objExists(blend_shape_node):
                        try:
                            cmds.setAttr(f"{blend_shape_node}.{target}", value)
                            applied_count += 1
                        except Exception:
                            pass

            # 現在のモーフのスライダーを更新
            if self.current_morph and self.current_morph in preset_data:
                self.view.morph_slider.setValue(int(preset_data[self.current_morph] * 100))

            logger.info(f"プリセット '{preset_name}' を適用しました ({applied_count}個のモーフ)")
            self.app_state.emit_status(f"プリセット '{preset_name}' を適用しました")

        except Exception as e:
            logger.error(f"プリセットの読み込みに失敗しました: {str(e)}")
            self.app_state.emit_status("プリセットの読み込みに失敗しました", "error")

    def delete_preset(self):
        """プリセットを削除"""
        preset_name = self.view.preset_combo.currentText()
        if not preset_name or preset_name == "なし":
            return

        # デフォルトプリセットは削除不可
        if preset_name in ["笑顔", "ウィンク", "驚き", "悲しみ"]:
            self.app_state.emit_status("デフォルトプリセットは削除できません", "warning")
            return

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not cmds.objExists(current_model_root):
            return

        # プリセットを読み込み
        if not cmds.attributeQuery("mmdMorphPresets", node=current_model_root, exists=True):
            return

        presets_json = cmds.getAttr(f"{current_model_root}.mmdMorphPresets")
        if not presets_json:
            return

        try:
            presets = json.loads(presets_json)
            if preset_name in presets:
                del presets[preset_name]

                # 保存
                presets_json = json.dumps(presets, ensure_ascii=False)
                set_attribute(current_model_root, "mmdMorphPresets", presets_json, "string")

                # コンボボックスから削除
                index = self.view.preset_combo.findText(preset_name)
                if index != -1:
                    self.view.preset_combo.removeItem(index)

                logger.info(f"プリセット '{preset_name}' を削除しました")
                self.app_state.emit_status(f"プリセット '{preset_name}' を削除しました")
        except Exception:
            pass

    def _get_attr_safe(self, node, attr, default=None):
        """属性を安全に取得"""
        try:
            if cmds.attributeQuery(attr, node=node, exists=True):
                value = cmds.getAttr(f"{node}.{attr}")
                return value if value is not None else default
        except Exception as e:
            logger.debug(f"Failed to get attribute {node}.{attr}: {e}")
        return default

    def _load_presets(self, model_root):
        """プリセットを読み込み"""
        # コンボボックスをクリア（デフォルトは残す）
        self.view.preset_combo.clear()
        self.view.preset_combo.addItems(["なし", "笑顔", "ウィンク", "驚き", "悲しみ"])

        if not cmds.attributeQuery("mmdMorphPresets", node=model_root, exists=True):
            return

        presets_json = cmds.getAttr(f"{model_root}.mmdMorphPresets")
        if not presets_json:
            return

        try:
            presets = json.loads(presets_json)
            for preset_name in presets.keys():
                if preset_name not in ["なし", "笑顔", "ウィンク", "驚き", "悲しみ"]:
                    self.view.preset_combo.addItem(preset_name)
        except Exception:
            pass
