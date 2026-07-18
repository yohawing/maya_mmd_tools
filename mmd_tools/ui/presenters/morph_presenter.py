import json
import re

from mmd_tools.adapters import MayaCmdsAdapter
from ...core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON
from ...core.logger import get_logger
from ...core.maya_attribute_utils import set_custom_attributes
from ...core.morph_metadata_reader import (
    MORPH_TAB_GROUP_ORDER,
    PMX_TYPE_TO_UI_INDEX,
    UI_INDEX_TO_PMX_TYPE,
    group_morph_names_by_panel,
    morph_info_from_presenter_entry,
    parse_blendshape_morph_entries,
)
from ...converters.morph_runtime_common import parse_morph_offsets_json
from ..qt_compat import Qt, QTimer, QListWidgetItem
from .list_presenter_helpers import apply_list_filter, reload_for_current_model_change, tr_message_format

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
# Fallback panel for morphs without explicit PMX panel metadata (not System/0).
_DEFAULT_USER_PANEL = 4
# Backward-compatible private aliases; the canonical table lives in the reader.
_PMX_TYPE_TO_UI_INDEX = PMX_TYPE_TO_UI_INDEX
_UI_INDEX_TO_PMX_TYPE = UI_INDEX_TO_PMX_TYPE
_MORPH_TYPE_LETTERS = {0: "G", 1: "V", 2: "B", 3: "U", 4: "U", 5: "U", 6: "U", 7: "U", 8: "M", 9: "F", 10: "I"}
# Runtime capability is intentionally centralized here.
_DIRECT_RUNTIME_MORPH_CAPABILITIES = {
    1: True,   # vertex
    2: True,   # bone
    3: False,  # UV
    4: False,  # additional UV1
    5: False,  # additional UV2
    6: False,  # additional UV3
    7: False,  # additional UV4
    8: True,   # material (complete hardware-shader runtime; per material fail-closed)
    9: False,  # flip
    10: False, # impulse
}


class MorphPresenter:
    def __init__(self, view, app_state, maya_adapter=None):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.blend_shape_node = None
        self.current_morph = None
        self.morph_data = {}  # MMDモーフデータのキャッシュ
        self._blendshape_metadata_bindings = {}
        self._morph_capability_cache = {}
        self._morphs_by_index = {}
        self._loaded_model_root = None
        self._morph_controller = None
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
        self.view.search_edit.textChanged.connect(self.filter_morphs)

        # スライダー関連
        self.view.morph_slider.valueChanged.connect(self.on_morph_slider_changed)
        self.view.reset_slider_btn.clicked.connect(self.reset_current_morph)
        self.view.reset_all_btn.clicked.connect(self.reset_all_morphs)

        # 基本情報タブ
        self.view.morph_type_combo.currentIndexChanged.connect(self.on_morph_type_changed)

        # 適用/リセットボタン
        self.view.apply_btn.clicked.connect(self.apply_changes)
        self.view.reset_btn.clicked.connect(self.reset_changes)

    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        reload_for_current_model_change(logger, "MorphPresenter", model_root, self.load_morphs)

    def ensure_morphs_loaded(self):
        """Load once when the active model has not populated this presenter yet."""
        model_root = self.app_state.current_model_root
        if model_root and model_root != self._loaded_model_root:
            self.load_morphs()

    def load_morphs(self):
        """モーフをロード"""
        self.view.morph_list.clear()
        self.morph_data.clear()
        self._blendshape_metadata_bindings.clear()
        self._morph_capability_cache.clear()
        self._morphs_by_index.clear()
        self.group_morphs.clear()
        self.current_morph = None
        self.view.set_morph_details_enabled(False)

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            self._loaded_model_root = None
            self._morph_controller = None
            return

        controllers = []
        if self.maya_adapter.attribute_exists("mmd_morph_controller", current_model_root):
            controllers = self.maya_adapter.list_connections(
                f"{current_model_root}.mmd_morph_controller", source=True, destination=False
            ) or []
        self._morph_controller = controllers[0] if len(controllers) == 1 else None

        # MMDモーフデータを収集
        self._load_mmd_morphs(current_model_root)
        allow_metadata_entries = not bool(self.morph_data)

        # ブレンドシェイプノードを検索
        self._load_blend_shapes(current_model_root, allow_metadata_entries=allow_metadata_entries)

        # bone/material/group morph の network node を検索
        self._load_network_morphs(current_model_root, allow_metadata_entries=allow_metadata_entries)

        # Strip the legacy custom annotation once at the input boundary.
        for data in self.morph_data.values():
            data.pop("group", None)

        self._cache_morph_capabilities()

        # グループごとにモーフを整理
        self._organize_morphs_by_group()

        # 全てのモーフを表示
        self._display_all_morphs()

        self._loaded_model_root = current_model_root

        logger.debug(f"Loaded {self.view.morph_list.count()} morphs for model: {current_model_root}")

    def _load_mmd_morphs(self, model_root):
        """MMDモーフデータをロード"""
        # MMDモーフアトリビュートを検索
        morph_data_json = self._get_attr_safe(model_root, "mmdMorphData", "")
        if morph_data_json:
            try:
                parsed = json.loads(morph_data_json)
                if isinstance(parsed, dict):
                    self.morph_data = parsed
                elif isinstance(parsed, list):
                    self.morph_data = self._index_morph_metadata(parsed)
                else:
                    logger.warning("Ignoring unsupported mmdMorphData schema: %s", type(parsed).__name__)
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
                    entry = raw_names.get(weight_index)
                    raw_name = entry["name"] if entry is not None else target_name
                    global_index = entry.get("index") if entry is not None else None
                    if not raw_name:
                        continue
                    morph_key = self._resolve_blendshape_metadata_key(
                        raw_name, global_index=global_index, weight_index=weight_index
                    )
                    if morph_key is None:
                        morph_key = raw_name if raw_name in self.morph_data else target_name

                    # MMDデータと照合、なければ新規作成
                    if morph_key not in self.morph_data:
                        if not allow_metadata_entries:
                            continue
                        morph_key = raw_name
                        # No inventing panel=0 (System). Unknown BS morphs default to Other.
                        panel = _DEFAULT_USER_PANEL
                        self.morph_data[morph_key] = {
                            "name_jp": raw_name,
                            "name_en": "",
                            "panel": panel,
                            "type": 0,  # 頂点モーフ
                            "index": global_index if global_index is not None else (
                                weight_index if weight_index is not None else -1
                            ),
                        }
                    else:
                        # Multi-mesh / multi-alias merge: keep first panel/type/index.
                        data = self.morph_data[morph_key]
                        if data.get("index") is None or data.get("index") == -1:
                            if weight_index is not None:
                                data["index"] = weight_index

                    # ブレンドシェイプ情報を追加
                    self._add_blend_shape_target(self.morph_data[morph_key], bs_node, target_name, target_attr)

    @staticmethod
    def _index_morph_metadata(entries):
        """Convert lossless list metadata to the presenter's unique-key mapping."""
        result = {}
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            data = dict(entry)
            data["_pmx_type_raw"] = True
            index = data.get("index", position)
            raw_name = str(data.get("name_jp", "") or "")
            key = raw_name
            if not key or key in result:
                key = f"{raw_name or '<unnamed>'} [{index}]"
                suffix = 2
                while key in result:
                    key = f"{raw_name or '<unnamed>'} [{index}]#{suffix}"
                    suffix += 1
            result[key] = data
        return result

    def _resolve_blendshape_metadata_key(self, raw_name, *, global_index=None, weight_index=None):
        """Resolve by PMX global index, with deterministic legacy-name fallback."""
        if global_index is not None:
            for key, data in self.morph_data.items():
                if data.get("_pmx_type_raw") and int(data.get("index", -1)) == int(global_index):
                    return key

        binding_key = (str(raw_name), weight_index)
        if binding_key in self._blendshape_metadata_bindings:
            return self._blendshape_metadata_bindings[binding_key]

        candidates = [
            (key, data)
            for key, data in self.morph_data.items()
            if str(data.get("name_jp", "") or "") == str(raw_name)
            and int(data.get("type", 1)) == 1
        ]
        candidates.sort(key=lambda item: int(item[1].get("index", -1)))
        already_bound = set(self._blendshape_metadata_bindings.values())
        selected = next((key for key, _data in candidates if key not in already_bound), None)
        if selected is None and candidates:
            selected = candidates[0][0]
        if selected is not None:
            self._blendshape_metadata_bindings[binding_key] = selected
        return selected

    def _load_blend_shape_morph_name_mapping(self, blend_shape_node):
        """Read weight index -> raw name/global PMX index metadata."""
        raw_json = self._get_attr_safe(blend_shape_node, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, "")
        if not raw_json:
            return {}

        try:
            parsed = json.loads(raw_json)
        except (TypeError, ValueError) as e:
            logger.debug(f"Failed to parse blendShape morph name mapping: {blend_shape_node}: {e}")
            return {}

        return parse_blendshape_morph_entries(parsed)

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
            panel = self._read_network_panel(morph_node)
            morph_index = self._read_network_index(morph_node)
            if morph_key not in self.morph_data:
                if not allow_metadata_entries:
                    continue
                morph_key = raw_name
                self.morph_data[morph_key] = {
                    "name_jp": raw_name,
                    "name_en": self._get_attr_safe(morph_node, "mmd_morph_name_en", ""),
                    "panel": panel,
                    "type": _NETWORK_MORPH_TYPE_INDEX.get(morph_type, 0),
                    "index": morph_index,
                }

            data = self.morph_data[morph_key]
            # Multi-target / namespace merge: keep first deterministic panel/type/index.
            if data.get("panel") is None:
                data["panel"] = panel
            if data.get("index") is None or data.get("index") == -1:
                if morph_index is not None and morph_index >= 0:
                    data["index"] = morph_index
            english_name = self._get_attr_safe(morph_node, "mmd_morph_name_en", "")
            if english_name and not data.get("name_en"):
                data["name_en"] = english_name
            data["morph_node"] = morph_node
            data["morph_weight_attr"] = "weight"
            data["mmd_morph_type"] = morph_type
            if morph_type == "group":
                data["group_morph_offsets"] = self._read_group_morph_offsets(morph_node)

    def _read_group_morph_offsets(self, morph_node):
        """Read group references fail-closed for capability decisions."""
        offsets = parse_morph_offsets_json(
            morph_node,
            "mmd_group_morph_offsets_json",
            get_attr=lambda plug: self.maya_adapter.get_attr(plug),
        )
        return offsets or []

    def _raw_pmx_type(self, data):
        try:
            stored_type = int(data.get("type", 0))
        except (TypeError, ValueError):
            return None
        return stored_type if data.get("_pmx_type_raw") else UI_INDEX_TO_PMX_TYPE.get(stored_type)

    def _cache_morph_capabilities(self):
        """Evaluate graph-dependent capabilities once for the loaded model."""
        self._morph_capability_cache.clear()
        self._morphs_by_index = {}
        for data in self.morph_data.values():
            try:
                index = int(data.get("index", -1))
            except (TypeError, ValueError):
                continue
            if index >= 0:
                self._morphs_by_index[index] = data

        # Material graph traversal must finish before group references consume it.
        ordered = sorted(
            self.morph_data.values(),
            key=lambda data: self._raw_pmx_type(data) == 0,
        )
        for data in ordered:
            self._morph_capability_cache[id(data)] = self._evaluate_morph_controls_supported(data)

    def _morph_controls_supported(self, data):
        """Return whether changing this morph's weight drives supported runtime output."""
        cached = self._morph_capability_cache.get(id(data))
        if cached is not None:
            return cached
        return self._evaluate_morph_controls_supported(data)

    def _evaluate_morph_controls_supported(self, data):
        """Compute capability; callers should normally use the cached wrapper."""
        raw_type = self._raw_pmx_type(data)
        if raw_type == 8:
            # The material morph node's canonical weight is the user-facing
            # runtime input. Shader routing may be rebuilt independently and
            # must not disable editing of that input in MorphTab.
            morph_node = data.get("morph_node")
            return bool(morph_node and self.maya_adapter.object_exists(morph_node))
        if raw_type != 0:
            return _DIRECT_RUNTIME_MORPH_CAPABILITIES.get(raw_type, False)

        if self._morph_controller:
            try:
                source_index = int(data.get("index", -1))
                topology = json.loads(self.maya_adapter.get_attr(
                    f"{self._morph_controller}.groupTopology"
                ) or "{}")
            except (RuntimeError, TypeError, ValueError):
                return False
            if not isinstance(topology, dict):
                return False
            for target, sources in topology.items():
                if not any(int(group) == source_index for group, _rate in sources):
                    continue
                if self.maya_adapter.list_connections(
                    f"{self._morph_controller}.outputWeight[{int(target)}]",
                    source=False,
                    destination=True,
                ):
                    return True
            return False

        by_index = self._morphs_by_index
        if not by_index:
            by_index = {
                int(candidate.get("index", -1)): candidate
                for candidate in self.morph_data.values()
                if str(candidate.get("index", -1)).lstrip("-").isdigit()
                and int(candidate.get("index", -1)) >= 0
            }
        for offset in data.get("group_morph_offsets", []):
            if not isinstance(offset, dict):
                continue
            try:
                referenced = by_index.get(int(offset.get("morph_index", -1)))
                rate = float(offset.get("morph_rate", 0.0))
            except (TypeError, ValueError):
                continue
            if referenced is None or rate == 0.0:
                continue
            referenced_type = self._raw_pmx_type(referenced)
            if referenced_type == 2 and referenced.get("morph_node"):
                return True
            if referenced_type == 8 and self._morph_controls_supported(referenced):
                return True
        return False

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
        try:
            morph_index = int(data.get("index", -1))
        except (TypeError, ValueError):
            morph_index = -1
        if self._morph_controller and morph_index >= 0:
            yield f"{self._morph_controller}.inputWeight[{morph_index}]"
            return

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
        """PMX panel 1-4 でモーフを整理する。

        Source of truth は ``panel`` メタデータ（MorphInfo + categorize_morphs）。
        panel 0 (System) はリスト本体には残しつつグループから除外。
        """
        morph_infos = [
            morph_info_from_presenter_entry(name, data)
            for name, data in self.morph_data.items()
        ]
        self.group_morphs = group_morph_names_by_panel(morph_infos)
        # Ensure stable key order even when a category is empty.
        for group in MORPH_TAB_GROUP_ORDER:
            self.group_morphs.setdefault(group, [])

    def _display_all_morphs(self):
        """全てのモーフを表示"""
        self._display_morphs(self.morph_data)

    def _display_morphs(self, morph_keys):
        """安定キーを保持し、PMX global index 順でモーフを表示する。"""
        def sort_key(morph_key):
            try:
                index = int(self.morph_data[morph_key].get("index", -1))
            except (TypeError, ValueError):
                index = -1
            return (index < 0, index if index >= 0 else 0, morph_key)

        for morph_key in sorted(morph_keys, key=sort_key):
            data = self.morph_data[morph_key]
            try:
                index = int(data.get("index", -1))
            except (TypeError, ValueError):
                index = -1
            raw_type = data.get("type", 0)
            if not data.get("_pmx_type_raw"):
                raw_type = UI_INDEX_TO_PMX_TYPE.get(int(raw_type), 1)
            type_letter = _MORPH_TYPE_LETTERS.get(int(raw_type), "?")
            name = data.get("name_jp") or morph_key
            index_text = f"{index:03d}" if index >= 0 else "---"
            item = QListWidgetItem(f"{index_text}:{type_letter}|{name}")
            item.setData(Qt.UserRole, morph_key)
            self.view.morph_list.addItem(item)

    def on_morph_selected(self, current, previous):
        """モーフが選択されたときの処理"""
        if not current or self.is_updating:
            self.view.set_morph_details_enabled(False)
            return

        morph_name = current.data(Qt.UserRole)
        if morph_name not in self.morph_data:
            return
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
        stored_type = data.get("type", 0)
        ui_type = (
            PMX_TYPE_TO_UI_INDEX.get(int(stored_type), 0)
            if data.get("_pmx_type_raw")
            else int(stored_type)
        )
        self.view.morph_type_combo.setCurrentIndex(ui_type)

        # 現在の適用率
        blend_shape_node = data.get("blend_shape_node")
        if blend_shape_node and self.maya_adapter.object_exists(blend_shape_node):
            weight = self._get_first_weight(data, morph_name)
            self.view.morph_slider.setValue(int(weight * 100))
            self.view.morph_value_label.setText(f"{int(weight * 100)}%")
        elif data.get("morph_node") and self.maya_adapter.object_exists(data["morph_node"]):
            weight = self._get_first_weight(data, morph_name)
            self.view.morph_slider.setValue(int(weight * 100))
            self.view.morph_value_label.setText(f"{int(weight * 100)}%")
        else:
            self.view.morph_slider.setValue(0)
            self.view.morph_value_label.setText("0%")

        supported = self._morph_controls_supported(data)
        tooltip = "" if supported else self.view.tr("morph_runtime_unsupported", "tooltips")
        self.view.set_morph_controls_enabled(supported, tooltip)

        self.is_updating = False

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

    def filter_morphs(self, text):
        """検索テキストでモーフをフィルタ"""
        apply_list_filter(
            (self.view.morph_list.item(i) for i in range(self.view.morph_list.count())),
            text,
            lambda item: (
                self.morph_data.get(item.data(Qt.UserRole), {}).get("name_jp", ""),
                self.morph_data.get(item.data(Qt.UserRole), {}).get("name_en", ""),
            ),
        )

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

    def apply_changes(self):
        """変更を適用"""
        if not self.current_morph:
            return

        # モーフデータを更新
        data = self.morph_data[self.current_morph]
        data["name_jp"] = self.view.morph_name_jp_edit.text()
        data["name_en"] = self.view.morph_name_en_edit.text()
        data["panel"] = self.view.panel_combo.currentIndex()
        ui_type = self.view.morph_type_combo.currentIndex()
        data["type"] = (
            UI_INDEX_TO_PMX_TYPE.get(ui_type, 1)
            if data.get("_pmx_type_raw")
            else ui_type
        )
        data.pop("group", None)

        # MMDアトリビュートに保存
        current_model_root = self.app_state.current_model_root
        if current_model_root and self.maya_adapter.object_exists(current_model_root):
            self._save_mmd_morph_data(current_model_root)

        # Type changes alter this morph and any group that references it.
        self._cache_morph_capabilities()

        # グループを再整理
        self._organize_morphs_by_group()

        logger.info(f"Applied changes to morph '{self.current_morph}'")
        self.app_state.emit_status(tr_message_format("morph_changes_applied", morph=self.current_morph))

    def _save_mmd_morph_data(self, model_root):
        """MMDモーフデータを保存"""
        if any(data.get("_pmx_type_raw") for data in self.morph_data.values()):
            payload = [
                {key: value for key, value in data.items() if key not in {"_pmx_type_raw", "group"}}
                for data in sorted(
                    self.morph_data.values(), key=lambda item: int(item.get("index", -1))
                )
            ]
        else:
            payload = {
                morph_key: {key: value for key, value in data.items() if key != "group"}
                for morph_key, data in self.morph_data.items()
            }
        morph_data_json = json.dumps(payload, ensure_ascii=False)
        set_custom_attributes(model_root, {"mmdMorphData": morph_data_json})

    def reset_changes(self):
        """変更をリセット"""
        if self.current_morph:
            self.load_morph_details(self.current_morph)
            logger.info(f"Reset changes to morph '{self.current_morph}'")

    def _read_network_panel(self, morph_node):
        """Read ``mmd_morph_panel``; missing attr defaults to Other, not System."""
        if not self.maya_adapter.attribute_exists("mmd_morph_panel", morph_node):
            return _DEFAULT_USER_PANEL
        panel = self._get_attr_safe(morph_node, "mmd_morph_panel", _DEFAULT_USER_PANEL)
        try:
            return int(panel)
        except (TypeError, ValueError):
            return _DEFAULT_USER_PANEL

    def _read_network_index(self, morph_node):
        """Read ``mmd_morph_index`` when present; otherwise ``-1``."""
        if not self.maya_adapter.attribute_exists("mmd_morph_index", morph_node):
            return -1
        index = self._get_attr_safe(morph_node, "mmd_morph_index", -1)
        try:
            return int(index)
        except (TypeError, ValueError):
            return -1

    def _get_attr_safe(self, node, attr, default=None):
        """属性を安全に取得"""
        try:
            if self.maya_adapter.attribute_exists(attr, node):
                value = self.maya_adapter.get_attr(f"{node}.{attr}")
                return value if value is not None else default
        except Exception as e:
            logger.debug(f"Failed to get attribute {node}.{attr}: {e}")
        return default
