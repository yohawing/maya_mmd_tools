import json
import re

from mmd_tools.adapters import MayaCmdsAdapter
from ...core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON
from ...core.logger import get_logger
from ...core.maya_attribute_utils import set_custom_attributes, set_attribute
from ..qt_compat import QTimer, QListWidgetItem
from .list_presenter_helpers import apply_list_filter, reload_for_current_model_change, tr_message, tr_message_format

logger = get_logger(__name__)


_BONE_MORPH_TYPE_INDEX = 10
_MATERIAL_MORPH_TYPE_INDEX = 11
_GROUP_MORPH_TYPE_INDEX = 12
_NETWORK_MORPH_TYPES = frozenset({"bone", "material", "group"})
_NETWORK_MORPH_TYPE_INDEX = {
    "bone": _BONE_MORPH_TYPE_INDEX,
    "material": _MATERIAL_MORPH_TYPE_INDEX,
    "group": _GROUP_MORPH_TYPE_INDEX,
}
_WEIGHT_INDEX_RE = re.compile(r"\[(\d+)\]")


class MorphPresenter:
    def __init__(self, view, app_state, maya_adapter=None):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
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
        reload_for_current_model_change(logger, "MorphPresenter", model_root, self.load_morphs)

    def load_morphs(self):
        """モーフをロード"""
        self.view.morph_list.clear()
        self.morph_data.clear()
        self.group_morphs.clear()
        self.current_morph = None
        self.view.set_morph_details_enabled(False)

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            return

        # MMDモーフデータを収集
        self._load_mmd_morphs(current_model_root)
        allow_metadata_entries = not bool(self.morph_data)

        # ブレンドシェイプノードを検索
        self._load_blend_shapes(current_model_root, allow_metadata_entries=allow_metadata_entries)

        # bone/material/group morph の network node を検索
        self._load_network_morphs(current_model_root, allow_metadata_entries=allow_metadata_entries)

        # グループごとにモーフを整理
        self._organize_morphs_by_group()

        # 全てのモーフを表示
        self._display_all_morphs()

        # プリセットを読み込み
        self._load_presets(current_model_root)

        logger.debug(f"Loaded {self.view.morph_list.count()} morphs for model: {current_model_root}")

    def _load_mmd_morphs(self, model_root):
        """MMDモーフデータをロード"""
        # MMDモーフアトリビュートを検索
        morph_data_json = self._get_attr_safe(model_root, "mmdMorphData", "")
        if morph_data_json:
            try:
                self.morph_data = json.loads(morph_data_json)
            except Exception as e:
                logger.error(f"Failed to parse MMD morph data: {e}", exc_info=True)

    def _load_blend_shapes(self, model_root, allow_metadata_entries=True):
        """ブレンドシェイプを検索"""
        shapes = self.maya_adapter.list_relatives(model_root, allDescendents=True, type="mesh") or []
        if not shapes:
            return

        # 全てのブレンドシェイプノードを収集
        for shape in shapes:
            history = self.maya_adapter.list_history(shape) or []
            blend_shape_nodes = self.maya_adapter.ls(history, type="blendShape") or []

            for bs_node in blend_shape_nodes:
                # 最初のブレンドシェイプノードをデフォルトとして保存
                if not self.blend_shape_node:
                    self.blend_shape_node = bs_node

                raw_names = self._load_blend_shape_morph_name_mapping(bs_node)

                # ブレンドシェイプターゲットを取得
                aliases = self.maya_adapter.alias_attr(bs_node, query=True) or []
                for i in range(0, len(aliases), 2):
                    target_name = aliases[i]
                    target_attr = aliases[i + 1] if i + 1 < len(aliases) else ""
                    weight_index = self._parse_weight_index(target_attr)
                    raw_name = raw_names.get(weight_index) or target_name
                    if not raw_name:
                        continue
                    morph_key = raw_name if raw_name in self.morph_data else target_name

                    # MMDデータと照合、なければ新規作成
                    if morph_key not in self.morph_data:
                        if not allow_metadata_entries:
                            continue
                        morph_key = raw_name
                        self.morph_data[morph_key] = {
                            "name_jp": raw_name,
                            "name_en": "",
                            "panel": 0,
                            "type": 0,  # 頂点モーフ
                            "group": "その他",
                        }

                    # ブレンドシェイプ情報を追加
                    self._add_blend_shape_target(self.morph_data[morph_key], bs_node, target_name, target_attr)

    def _load_blend_shape_morph_name_mapping(self, blend_shape_node):
        """blendShape node の weight index -> PMX raw morph name 対応を読む。"""
        raw_json = self._get_attr_safe(blend_shape_node, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, "")
        if not raw_json:
            return {}

        try:
            parsed = json.loads(raw_json)
        except (TypeError, ValueError) as e:
            logger.debug(f"Failed to parse blendShape morph name mapping: {blend_shape_node}: {e}")
            return {}

        if not isinstance(parsed, dict):
            return {}

        mapping = {}
        for key, value in parsed.items():
            try:
                mapping[int(key)] = str(value)
            except (TypeError, ValueError):
                continue
        return mapping

    def _load_network_morphs(self, model_root=None, allow_metadata_entries=True):
        """bone/material/group morph の network node metadata を一覧用に読む。"""
        network_nodes = self.maya_adapter.ls(type="network") or []
        for morph_node in network_nodes:
            if not self.maya_adapter.attribute_exists("mmd_morph_type", morph_node):
                continue

            # model root が指定され、ノードに mmd_model_root 接続がある場合はスコープチェック
            if model_root and self.maya_adapter.attribute_exists("mmd_model_root", morph_node):
                connected_roots = self.maya_adapter.list_connections(
                    f"{morph_node}.mmd_model_root"
                ) or []
                if model_root not in connected_roots:
                    continue

            morph_type = self._get_attr_safe(morph_node, "mmd_morph_type", "")
            if morph_type not in _NETWORK_MORPH_TYPES:
                continue

            raw_name = self._get_attr_safe(morph_node, "mmd_morph_name", "") or morph_node
            morph_key = raw_name if raw_name in self.morph_data else morph_node
            if morph_key not in self.morph_data:
                if not allow_metadata_entries:
                    continue
                morph_key = raw_name
                panel = self._get_attr_safe(morph_node, "mmd_morph_panel", 0)
                try:
                    panel = int(panel)
                except (TypeError, ValueError):
                    panel = 0
                self.morph_data[morph_key] = {
                    "name_jp": raw_name,
                    "name_en": self._get_attr_safe(morph_node, "mmd_morph_name_en", ""),
                    "panel": panel,
                    "type": _NETWORK_MORPH_TYPE_INDEX.get(morph_type, 0),
                    "group": "その他",
                }

            data = self.morph_data[morph_key]
            english_name = self._get_attr_safe(morph_node, "mmd_morph_name_en", "")
            if english_name and not data.get("name_en"):
                data["name_en"] = english_name
            data["morph_node"] = morph_node
            data["morph_weight_attr"] = "weight"
            data["mmd_morph_type"] = morph_type

    def _add_blend_shape_target(self, data, blend_shape_node, target_name, target_attr):
        """Morph data に blendShape target 接続情報を追加する。"""
        target = target_name or target_attr
        if not target:
            return

        targets = data.setdefault("blend_shape_targets", [])
        target_info = {"node": blend_shape_node, "target": target, "weight_attr": target_attr or target}
        if target_info not in targets:
            targets.append(target_info)

        # 既存 UI/保存ロジックとの互換用に先頭 target を従来フィールドへも保持する。
        data.setdefault("blend_shape_node", blend_shape_node)
        data.setdefault("blend_shape_target", target)
        data.setdefault("blend_shape_weight_attr", target_attr or target)

    def _parse_weight_index(self, weight_attr):
        """aliasAttr の weight[0]/w[0] 形式から index を取得する。"""
        if not weight_attr:
            return None
        match = _WEIGHT_INDEX_RE.search(str(weight_attr))
        if not match:
            return None
        return int(match.group(1))

    def _iter_blend_shape_targets(self, data):
        """Morph data に保存された blendShape target を順に返す。"""
        targets = data.get("blend_shape_targets") or []
        for target_info in targets:
            node = target_info.get("node")
            target = target_info.get("target")
            if node and target:
                yield node, target

        if targets:
            return

        node = data.get("blend_shape_node")
        target = data.get("blend_shape_target")
        if node and target:
            yield node, target

    def _canonical_weight_attr(self, blend_shape_node, target, stored_weight_attr=None):
        """Return canonical ``weight[n]`` for a target alias or stored plug."""
        target_weight_index = self._parse_weight_index(target)
        if target_weight_index is not None:
            return f"weight[{target_weight_index}]"

        aliases = self.maya_adapter.alias_attr(blend_shape_node, query=True) or []
        for index in range(0, len(aliases), 2):
            alias = aliases[index]
            alias_attr = aliases[index + 1] if index + 1 < len(aliases) else ""
            if alias != target:
                continue
            weight_index = self._parse_weight_index(alias_attr)
            if weight_index is not None:
                return f"weight[{weight_index}]"

        # A stored index is only a cache. If the alias no longer resolves, using
        # it could silently drive a different morph after scene edits.
        return None

    def _iter_blend_shape_weight_plugs(self, data, morph_name):
        """Yield validated canonical blendShape weight plugs for one morph."""
        targets = data.get("blend_shape_targets") or []
        if targets:
            entries = (
                (
                    target_info.get("node"),
                    target_info.get("target"),
                    target_info.get("weight_attr"),
                )
                for target_info in targets
            )
        else:
            entries = (
                (
                    data.get("blend_shape_node"),
                    data.get("blend_shape_target"),
                    data.get("blend_shape_weight_attr"),
                ),
            )

        for blend_shape_node, target, stored_weight_attr in entries:
            if not blend_shape_node or not target or not self.maya_adapter.object_exists(blend_shape_node):
                continue
            weight_attr = self._canonical_weight_attr(
                blend_shape_node,
                target,
                stored_weight_attr,
            )
            unresolved_plug = stored_weight_attr or target
            if weight_attr is None:
                logger.warning(
                    "Skipping stale blendShape morph mapping: morph=%s node=%s plug=%s",
                    morph_name,
                    blend_shape_node,
                    unresolved_plug,
                )
                continue
            plug = f"{blend_shape_node}.{weight_attr}"
            if not self.maya_adapter.object_exists(plug):
                logger.warning(
                    "Skipping missing blendShape weight plug: morph=%s node=%s plug=%s",
                    morph_name,
                    blend_shape_node,
                    plug,
                )
                continue
            yield plug

    def _iter_network_morph_weight_plugs(self, data, morph_name):
        """Yield scoped network morph weight plugs (bone/material/group)."""
        morph_node = data.get("morph_node")
        weight_attr = data.get("morph_weight_attr") or "weight"
        if not morph_node or not weight_attr:
            return
        if not self.maya_adapter.object_exists(morph_node):
            logger.warning(
                "Skipping missing network morph node: morph=%s node=%s",
                morph_name,
                morph_node,
            )
            return
        plug = f"{morph_node}.{weight_attr}"
        yield plug

    def _iter_morph_weight_plugs(self, data, morph_name):
        """Yield deduplicated writable morph weight plugs for one morph.

        Canonical blendShape ``weight[n]`` plugs come first, then the scoped
        network ``morph_node.weight`` used by bone/material/group morphs.
        """
        seen = set()
        for plug in self._iter_blend_shape_weight_plugs(data, morph_name):
            if plug in seen:
                continue
            seen.add(plug)
            yield plug
        for plug in self._iter_network_morph_weight_plugs(data, morph_name):
            if plug in seen:
                continue
            seen.add(plug)
            yield plug

    def _get_first_weight(self, data, morph_name="<unknown>", default=0.0):
        """UI 表示用に最初に見つかった morph weight を取得する。"""
        for plug in self._iter_morph_weight_plugs(data, morph_name):
            try:
                return self.maya_adapter.get_attr(plug)
            except Exception as e:
                logger.warning(
                    "Failed to read morph weight: morph=%s plug=%s error=%s",
                    morph_name,
                    plug,
                    e,
                )
        return default

    def _set_morph_weight(self, data, weight, morph_name="<unknown>"):
        """接続済み morph weight plug 全てに weight を設定する。"""
        for plug in self._iter_morph_weight_plugs(data, morph_name):
            try:
                self.maya_adapter.set_attr(plug, weight)
            except Exception as e:
                logger.warning(
                    "Failed to set morph weight: morph=%s plug=%s error=%s",
                    morph_name,
                    plug,
                    e,
                )

    def _set_blend_shape_weight(self, data, weight, morph_name="<unknown>"):
        """接続済み blendShape target 全てに weight を設定する。"""
        for plug in self._iter_blend_shape_weight_plugs(data, morph_name):
            try:
                self.maya_adapter.set_attr(plug, weight)
            except Exception as e:
                logger.warning(
                    "Failed to set blendShape weight: morph=%s plug=%s error=%s",
                    morph_name,
                    plug,
                    e,
                )

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
        logger.debug(f"Selected morph: {morph_name}")

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
        if blend_shape_node and self.maya_adapter.object_exists(blend_shape_node):
            self.view.blend_shape_edit.setText(blend_shape_node)
            self.view.target_name_edit.setText(data.get("blend_shape_target", ""))
            self.view.connection_status_label.setText("Connected")
            self.view.connection_status_label.setStyleSheet("color: green;")

            # 現在の値を取得
            weight = self._get_first_weight(data, morph_name)
            self.view.morph_slider.setValue(int(weight * 100))
            self.view.morph_value_label.setText(f"{int(weight * 100)}%")
        elif data.get("morph_node") and self.maya_adapter.object_exists(data["morph_node"]):
            self.view.blend_shape_edit.setText(data["morph_node"])
            self.view.target_name_edit.setText(data.get("morph_weight_attr", "weight"))
            self.view.connection_status_label.setText("Metadata only")
            self.view.connection_status_label.setStyleSheet("color: #a66a00;")
            weight = self._get_first_weight(data, morph_name)
            self.view.morph_slider.setValue(int(weight * 100))
            self.view.morph_value_label.setText(f"{int(weight * 100)}%")
        else:
            self.view.blend_shape_edit.clear()
            self.view.target_name_edit.clear()
            self.view.connection_status_label.setText("Not connected")
            self.view.connection_status_label.setStyleSheet("color: red;")
            self.view.morph_slider.setValue(0)
            self.view.morph_value_label.setText("0%")

        # オフセット情報を更新
        self.update_offset_table(morph_name)

        self.is_updating = False

    def update_offset_table(self, morph_name):
        """オフセットテーブルを更新する。

        モーフのオフセットデータ（頂点/材質オフセット等）の表示は未対応。
        編集ボタンは morph_tab 側で無効化済み。ここでは表を空にし、
        ラベルで未対応であることを明示する（数値 0 件と誤認させない）。
        """
        self.view.offset_table.setRowCount(0)
        self.view.offset_count_label.setText(self.view.tr("offset_not_supported", "labels"))

    def on_morph_slider_changed(self, value):
        """スライダーが変更されたときの処理"""
        if self.is_updating or not self.current_morph:
            return

        # ラベルを更新
        self.view.morph_value_label.setText(f"{value}%")

        # BlendShape / network morph の共通 weight 契約へ適用
        if self.current_morph in self.morph_data:
            data = self.morph_data[self.current_morph]
            weight = value / 100.0

            # 詳細設定を適用
            if self.view.invert_check.isChecked():
                weight = 1.0 - weight
            weight *= self.view.multiplier_spin.value()

            self._set_morph_weight(data, weight, self.current_morph)

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
        apply_list_filter(
            (self.view.morph_list.item(i) for i in range(self.view.morph_list.count())),
            text,
            lambda item: (item.text(),),
        )

    def select_morph_in_maya(self):
        """Mayaでモーフ（ブレンドシェイプノード）を選択"""
        if not self.current_morph:
            return

        data = self.morph_data.get(self.current_morph, {})
        blend_shape_node = data.get("blend_shape_node")

        if blend_shape_node and self.maya_adapter.object_exists(blend_shape_node):
            self.maya_adapter.select(blend_shape_node, replace=True)
            logger.debug(f"Selected blend shape node in Maya: {blend_shape_node}")
            self.app_state.emit_status(tr_message_format("blend_shape_node_selected", node=blend_shape_node))

    def reset_current_morph(self):
        """現在のモーフをリセット"""
        self.view.morph_slider.setValue(0)

    def reset_all_morphs(self):
        """全てのモーフをリセット"""
        reset_count = 0
        for morph_name, data in self.morph_data.items():
            for plug in self._iter_morph_weight_plugs(data, morph_name):
                try:
                    current_value = self.maya_adapter.get_attr(plug)
                    if current_value != 0:
                        self.maya_adapter.set_attr(plug, 0)
                        reset_count += 1
                except Exception as e:
                    logger.warning(
                        "Failed to reset morph weight: morph=%s plug=%s error=%s",
                        morph_name,
                        plug,
                        e,
                    )

        # 現在のスライダーもリセット
        self.view.morph_slider.setValue(0)
        self.app_state.emit_status(tr_message_format("morphs_reset_count", count=reset_count))
        logger.info(f"Reset all morphs complete: reset {reset_count} morph(s)")

    def on_morph_type_changed(self, index):
        """モーフタイプが変更されたときの処理"""
        # タイプに応じてUIを更新（将来の拡張用）
        pass

    def add_group(self):
        """グループを追加"""
        from ..qt_compat import QInputDialog

        # グループ名を入力
        group_name, ok = QInputDialog.getText(self.view, "Add Group", "Enter a new group name:")

        if ok and group_name:
            # 既存のグループと重複チェック
            existing_groups = []
            for i in range(self.view.group_list.count()):
                existing_groups.append(self.view.group_list.item(i).text())

            if group_name in existing_groups:
                self.app_state.emit_status(tr_message_format("morph_group_exists", group=group_name), "warning")
                return

            # グループリストに追加
            self.view.group_list.addItem(group_name)
            self.group_morphs[group_name] = []

            # グループコンボボックスにも追加
            self.view.group_combo.addItem(group_name)

            logger.info(f"Added group: {group_name}")
            self.app_state.emit_status(tr_message_format("morph_group_added", group=group_name))

    def remove_group(self):
        """グループを削除"""
        current = self.view.group_list.currentItem()
        if current and current.text() not in ["眉", "目", "口", "その他"]:
            # カスタムグループのみ削除可能
            self.view.group_list.takeItem(self.view.group_list.row(current))

    def connect_blend_shape(self):
        """ブレンドシェイプを連携"""
        if not self.current_morph:
            self.app_state.emit_status(tr_message("select_morph"), "warning")
            return

        # UI から情報を取得
        blend_shape_node = self.view.blend_shape_edit.text()
        target_name = self.view.target_name_edit.text()

        if not blend_shape_node or not target_name:
            self.app_state.emit_status(tr_message("enter_blend_shape_node_and_target"), "warning")
            return

        # ノードの存在確認
        if not self.maya_adapter.object_exists(blend_shape_node):
            self.app_state.emit_status(tr_message_format("blend_shape_node_not_found", node=blend_shape_node), "error")
            return

        # ターゲットの存在確認
        try:
            self.maya_adapter.get_attr(f"{blend_shape_node}.{target_name}")
        except Exception:
            self.app_state.emit_status(tr_message_format("target_not_found", target=target_name), "error")
            return

        # 連携を設定
        weight_attr = self._canonical_weight_attr(blend_shape_node, target_name)
        if weight_attr is None or not self.maya_adapter.object_exists(f"{blend_shape_node}.{weight_attr}"):
            self.app_state.emit_status(tr_message_format("target_not_found", target=target_name), "error")
            return
        data = self.morph_data[self.current_morph]
        data["blend_shape_node"] = blend_shape_node
        data["blend_shape_target"] = target_name
        data["blend_shape_weight_attr"] = weight_attr
        data["blend_shape_targets"] = [
            {"node": blend_shape_node, "target": target_name, "weight_attr": weight_attr}
        ]

        # UIを更新
        self.load_morph_details(self.current_morph)

        logger.info(f"Connected morph: {self.current_morph} -> {blend_shape_node}.{target_name}")
        self.app_state.emit_status(tr_message_format("morph_connected", morph=self.current_morph))

    def disconnect_blend_shape(self):
        """ブレンドシェイプの連携を解除"""
        if not self.current_morph:
            return

        if self.current_morph in self.morph_data:
            self.morph_data[self.current_morph].pop("blend_shape_node", None)
            self.morph_data[self.current_morph].pop("blend_shape_target", None)
            self.morph_data[self.current_morph].pop("blend_shape_weight_attr", None)
            self.morph_data[self.current_morph].pop("blend_shape_targets", None)
            self.load_morph_details(self.current_morph)
            self.app_state.emit_status(tr_message("blend_shape_disconnected"))

    def auto_connect_blend_shapes(self):
        """ブレンドシェイプを自動連携"""
        logger.info("Starting auto-connect")

        connected_count = 0
        current_model_root = self.app_state.current_model_root
        if not current_model_root:
            return

        # 全てのブレンドシェイプノードを収集
        shapes = self.maya_adapter.list_relatives(current_model_root, allDescendents=True, type="mesh") or []
        blend_shape_nodes = []

        for shape in shapes:
            history = self.maya_adapter.list_history(shape) or []
            bs_nodes = self.maya_adapter.ls(history, type="blendShape") or []
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
                aliases = self.maya_adapter.alias_attr(bs_node, query=True) or []
                for i in range(0, len(aliases), 2):
                    target_name = aliases[i]
                    target_attr = aliases[i + 1] if i + 1 < len(aliases) else ""

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
                            data["blend_shape_weight_attr"] = self._canonical_weight_attr(
                                bs_node,
                                target_name,
                                target_attr,
                            )
                            data["blend_shape_targets"] = [
                                {
                                    "node": bs_node,
                                    "target": target_name,
                                    "weight_attr": data["blend_shape_weight_attr"],
                                }
                            ]
                            connected_count += 1
                            logger.debug(f"Auto-connect succeeded: {morph_name} -> {bs_node}.{target_name}")
                            break

                    if data.get("blend_shape_node"):
                        break

                if data.get("blend_shape_node"):
                    break

        # 現在選択中のモーフの情報を更新
        if self.current_morph:
            self.load_morph_details(self.current_morph)

        # 結果を通知
        self.app_state.emit_status(tr_message_format("morphs_auto_connected_count", count=connected_count))
        logger.info(f"Auto-connect complete: connected {connected_count} morph(s)")

    def select_blend_shape_node(self):
        """ブレンドシェイプノードを選択"""
        selected = self.maya_adapter.ls(selection=True)
        if selected:
            # ブレンドシェイプノードを探す
            for obj in selected:
                if self.maya_adapter.node_type(obj) == "blendShape":
                    self.view.blend_shape_edit.setText(obj)
                    return

                # ヒストリーから探す
                history = self.maya_adapter.list_history(obj) or []
                blend_shapes = self.maya_adapter.ls(history, type="blendShape") or []
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
        if current_model_root and self.maya_adapter.object_exists(current_model_root):
            self._save_mmd_morph_data(current_model_root)

        # グループを再整理
        self._organize_morphs_by_group()

        logger.info(f"Applied changes to morph '{self.current_morph}'")
        self.app_state.emit_status(tr_message_format("morph_changes_applied", morph=self.current_morph))

    def _save_mmd_morph_data(self, model_root):
        """MMDモーフデータを保存"""
        morph_data_json = json.dumps(self.morph_data, ensure_ascii=False)
        set_custom_attributes(model_root, {"mmdMorphData": morph_data_json})

    def reset_changes(self):
        """変更をリセット"""
        if self.current_morph:
            self.load_morph_details(self.current_morph)
            logger.info(f"Reset changes to morph '{self.current_morph}'")

    def save_preset(self):
        """現在のモーフ値をプリセットとして保存"""
        preset_name = self.view.preset_combo.currentText()
        if not preset_name or preset_name == "なし":
            self.app_state.emit_status(tr_message("enter_preset_name"), "warning")
            return

        # 現在のモーフ値を収集
        preset_data = {}
        for morph_name, data in self.morph_data.items():
            for plug in self._iter_morph_weight_plugs(data, morph_name):
                try:
                    value = self.maya_adapter.get_attr(plug)
                    if value != 0:  # 0以外の値のみ保存
                        preset_data[morph_name] = value
                    break
                except Exception as e:
                    logger.warning(
                        "Failed to read preset morph weight: morph=%s plug=%s error=%s",
                        morph_name,
                        plug,
                        e,
                    )

        if not preset_data:
            self.app_state.emit_status(tr_message("no_morph_values_to_save"), "warning")
            return

        # プリセットをモデルのアトリビュートに保存
        current_model_root = self.app_state.current_model_root
        if current_model_root and self.maya_adapter.object_exists(current_model_root):
            # プリセット用アトリビュートを作成
            # プリセット用アトリビュートがなければ作成
            if not self.maya_adapter.attribute_exists("mmdMorphPresets", current_model_root):
                set_custom_attributes(current_model_root, {"mmdMorphPresets": ""})

            # 既存のプリセットを読み込み
            presets = {}
            presets_json = self.maya_adapter.get_attr(f"{current_model_root}.mmdMorphPresets")
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

            logger.info(f"Saved preset '{preset_name}'")
            self.app_state.emit_status(tr_message_format("preset_saved", preset=preset_name))

    def load_preset(self):
        """プリセットを読み込み"""
        preset_name = self.view.preset_combo.currentText()
        if not preset_name or preset_name == "なし":
            return

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            return

        # プリセットを読み込み
        if not self.maya_adapter.attribute_exists("mmdMorphPresets", current_model_root):
            self.app_state.emit_status(tr_message("no_presets_found"), "warning")
            return

        presets_json = self.maya_adapter.get_attr(f"{current_model_root}.mmdMorphPresets")
        if not presets_json:
            self.app_state.emit_status(tr_message("no_presets_found"), "warning")
            return

        try:
            presets = json.loads(presets_json)
            if preset_name not in presets:
                self.app_state.emit_status(tr_message_format("preset_not_found", preset=preset_name), "warning")
                return

            # プリセットの値を適用
            preset_data = presets[preset_name]
            applied_count = 0

            for morph_name, value in preset_data.items():
                if morph_name in self.morph_data:
                    data = self.morph_data[morph_name]
                    applied = False
                    for plug in self._iter_morph_weight_plugs(data, morph_name):
                        try:
                            self.maya_adapter.set_attr(plug, value)
                            applied = True
                        except Exception as e:
                            logger.warning(
                                "Failed to apply preset morph weight: morph=%s plug=%s error=%s",
                                morph_name,
                                plug,
                                e,
                            )
                    if applied:
                        applied_count += 1

            # 現在のモーフのスライダーを更新
            if self.current_morph and self.current_morph in preset_data:
                self.view.morph_slider.setValue(int(preset_data[self.current_morph] * 100))

            logger.info(f"Applied preset '{preset_name}' ({applied_count} morph(s))")
            self.app_state.emit_status(tr_message_format("preset_applied", preset=preset_name))

        except Exception as e:
            logger.error(f"Failed to load preset: {str(e)}")
            self.app_state.emit_status(tr_message("preset_load_failed"), "error")

    def delete_preset(self):
        """プリセットを削除"""
        preset_name = self.view.preset_combo.currentText()
        if not preset_name or preset_name == "なし":
            return

        # デフォルトプリセットは削除不可
        if preset_name in ["笑顔", "ウィンク", "驚き", "悲しみ"]:
            self.app_state.emit_status(tr_message("default_presets_cannot_delete"), "warning")
            return

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            return

        # プリセットを読み込み
        if not self.maya_adapter.attribute_exists("mmdMorphPresets", current_model_root):
            return

        presets_json = self.maya_adapter.get_attr(f"{current_model_root}.mmdMorphPresets")
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

                logger.info(f"Deleted preset '{preset_name}'")
                self.app_state.emit_status(tr_message_format("preset_deleted", preset=preset_name))
        except Exception:
            pass

    def _get_attr_safe(self, node, attr, default=None):
        """属性を安全に取得"""
        try:
            if self.maya_adapter.attribute_exists(attr, node):
                value = self.maya_adapter.get_attr(f"{node}.{attr}")
                return value if value is not None else default
        except Exception as e:
            logger.debug(f"Failed to get attribute {node}.{attr}: {e}")
        return default

    def _load_presets(self, model_root):
        """プリセットを読み込み"""
        # コンボボックスをクリア（デフォルトは残す）
        self.view.preset_combo.clear()
        self.view.preset_combo.addItems(["なし", "笑顔", "ウィンク", "驚き", "悲しみ"])

        if not self.maya_adapter.attribute_exists("mmdMorphPresets", model_root):
            return

        presets_json = self.maya_adapter.get_attr(f"{model_root}.mmdMorphPresets")
        if not presets_json:
            return

        try:
            presets = json.loads(presets_json)
            for preset_name in presets.keys():
                if preset_name not in ["なし", "笑顔", "ウィンク", "驚き", "悲しみ"]:
                    self.view.preset_combo.addItem(preset_name)
        except Exception:
            pass
