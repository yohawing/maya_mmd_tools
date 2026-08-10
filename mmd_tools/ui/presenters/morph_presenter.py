"""Present runtime preview and coordinator-routed PMX morph authoring UI."""

from dataclasses import replace
import json
import re

from mmd_tools.adapters import MayaCmdsAdapter
from ...core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON
from ...core.logger import get_logger
from ...core.morph_metadata_reader import (
    MORPH_TAB_GROUP_ORDER,
    PMX_TYPE_TO_UI_INDEX,
    UI_INDEX_TO_PMX_TYPE,
    group_morph_names_by_panel,
    morph_info_from_presenter_entry,
    parse_blendshape_morph_entries,
    PMX_MORPH_TYPE_NAMES,
)
from ...core.model_authoring_spec import MmdMorphSpec
from ...core.model_registry import (
    REGISTRY_CATEGORY_MORPH,
    list_model_registry_members_from_adapter,
)
from ...converters.morph_runtime_common import parse_morph_offsets_json
from ..qt_compat import Qt, QTimer, QListWidgetItem
from .list_presenter_helpers import (
    apply_list_filter,
    format_indexed_name_label,
    reload_for_current_model_change,
    tr_message,
    tr_message_format,
)

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
_CREATE_MORPH_TYPES = (
    "vertex",
    "uv",
    "additional_uv1",
    "additional_uv2",
    "additional_uv3",
    "additional_uv4",
    "bone",
    "material",
    "group",
    "flip",
    "impulse",
)
_EDITABLE_OFFSET_TYPES = frozenset({"vertex", "bone", "group", "material"})
_ROUNDTRIP_ONLY_OFFSET_TYPES = frozenset(
    {"uv", "additional_uv1", "additional_uv2", "additional_uv3", "additional_uv4"}
)
class MorphPresenter:
    def __init__(
        self,
        view,
        app_state,
        maya_adapter=None,
        authoring_coordinator=None,
        material_morph_work=None,
    ):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.authoring_coordinator = authoring_coordinator
        self.material_morph_work = material_morph_work
        self.blend_shape_node = None
        self.current_morph = None
        self.morph_data = {}  # MMDモーフデータのキャッシュ
        self._blendshape_metadata_bindings = {}
        self._morph_capability_cache = {}
        self._morphs_by_index = {}
        self._loaded_model_root = None
        self._morph_controller = None
        self._authoring_spec = None
        self._authoring_morphs_by_index = {}
        self._authoring_ready = False
        self.group_morphs = {}  # グループごとのモーフリスト
        self.is_updating = False

        self.connect_signals()
        self._set_authoring_available()

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

        # Semantic authoring is optional at composition time.  Runtime preview
        # remains usable when no coordinator has been injected.
        optional_signals = (
            ("create_morph_btn", "clicked", self.create_morph),
            ("delete_morph_btn", "clicked", self.delete_current_morph),
            ("move_morph_up_btn", "clicked", lambda: self.move_current_morph(-1)),
            ("move_morph_down_btn", "clicked", lambda: self.move_current_morph(1)),
            ("apply_offsets_btn", "clicked", self.apply_offsets),
            ("create_work_material_btn", "clicked", self.create_work_material),
            ("apply_work_material_btn", "clicked", self.apply_work_material),
            ("clear_work_material_btn", "clicked", self.clear_work_material),
        )
        for widget_name, signal_name, callback in optional_signals:
            widget = getattr(self.view, widget_name, None)
            signal = getattr(widget, signal_name, None)
            if signal is not None:
                signal.connect(callback)

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
        self._authoring_spec = None
        self._authoring_morphs_by_index.clear()
        self._authoring_ready = False
        self.group_morphs.clear()
        self.current_morph = None
        self.view.set_morph_details_enabled(False)

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            self._loaded_model_root = None
            self._morph_controller = None
            setter = getattr(self.view, "set_authoring_controls_enabled", None)
            if callable(setter):
                setter(False, "Select an MMD model to author morphs", "authoring_selection_required")
            return

        self._read_authoring_spec(current_model_root)

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

        self._merge_authoring_morphs()

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

    def _set_authoring_available(self):
        """Disable semantic widgets until a complete coordinator is injected."""
        required = (
            "read_spec",
            "create_morph",
            "replace_morph",
            "replace_morph_offsets",
            "delete_morph",
            "move_morph",
            "reindex_morphs",
        )
        available = self.authoring_coordinator is not None and all(
            callable(getattr(self.authoring_coordinator, method, None)) for method in required
        )
        setter = getattr(self.view, "set_authoring_controls_enabled", None)
        if callable(setter):
            setter(
                bool(available),
                "" if available else "Authoring coordinator is not available",
                "" if available else "authoring_unavailable",
            )
        self._authoring_available = bool(available)
        self._authoring_ready = False

    def _read_authoring_spec(self, root):
        if not self._authoring_available:
            return
        try:
            spec = self.authoring_coordinator.read_spec(root)
            self._authoring_spec = spec
            self._authoring_morphs_by_index = {morph.index: morph for morph in spec.morphs}
            self._authoring_ready = True
            setter = getattr(self.view, "set_authoring_controls_enabled", None)
            if callable(setter):
                setter(True, "", "")
        except Exception as exc:
            logger.error("Failed to read morph authoring spec: %s", exc, exc_info=True)
            self._authoring_spec = None
            self._authoring_morphs_by_index = {}
            self._authoring_ready = False
            setter = getattr(self.view, "set_authoring_controls_enabled", None)
            if callable(setter):
                setter(False, f"Authoring metadata unavailable: {exc}", "authoring_unavailable")

    def _merge_authoring_morphs(self):
        """Overlay immutable semantic names/types/offsets by global PMX index."""
        for morph in self._authoring_morphs_by_index.values():
            key = next(
                (
                    candidate
                    for candidate, data in self.morph_data.items()
                    if int(data.get("index", -1)) == morph.index
                ),
                None,
            )
            if key is None:
                key = morph.name or f"Morph [{morph.index}]"
                while key in self.morph_data:
                    key += "#"
                self.morph_data[key] = {}
            data = self.morph_data[key]
            pmx_type = next(
                (value for value, name in PMX_MORPH_TYPE_NAMES.items() if name == morph.morph_type),
                1,
            )
            data.update(
                {
                    "name_jp": morph.name,
                    "name_en": morph.name_english,
                    "panel": morph.panel,
                    "type": pmx_type,
                    "index": morph.index,
                    "offsets": morph.to_mapping()["offsets"],
                    "mmd_morph_type": morph.morph_type,
                    "_pmx_type_raw": True,
                }
            )
            if morph.binding_identity:
                data.setdefault("morph_node", morph.binding_identity)

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
        registry_members = self._registry_morph_members(model_root)
        network_nodes = (
            registry_members
            if registry_members is not None
            else self.maya_adapter.ls(type="network") or []
        )
        for morph_node in network_nodes:
            if not self.maya_adapter.attribute_exists("mmd_morph_type", morph_node):
                continue

            # model root が指定され、ノードに mmd_model_root 接続がある場合はスコープチェック
            if model_root and registry_members is None and self.maya_adapter.attribute_exists(
                "mmd_model_root", morph_node
            ):
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

    def _registry_morph_members(self, model_root):
        """Return registry morph members, or None for an old scene fallback."""
        return list_model_registry_members_from_adapter(
            self.maya_adapter,
            model_root,
            REGISTRY_CATEGORY_MORPH,
        )

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

    def _output_weight_connections(self, index):
        """Return destinations for one generated controller element fail-soft."""

        if not self._morph_controller or index < 0:
            return []
        plug = f"{self._morph_controller}.outputWeight[{index}]"
        try:
            if not self.maya_adapter.object_exists(plug):
                return []
            return self.maya_adapter.list_connections(
                plug,
                source=False,
                destination=True,
            ) or []
        except Exception:
            # Sparse multi elements can be absent on old or partially built
            # controllers.  Capability discovery treats them as unsupported.
            return []

    def _evaluate_morph_controls_supported(self, data):
        """Compute capability; callers should normally use the cached wrapper."""
        raw_type = self._raw_pmx_type(data)
        if raw_type == 8:
            try:
                index = int(data.get("index", -1))
            except (TypeError, ValueError):
                index = -1
            if self._output_weight_connections(index):
                return True
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
                if self._output_weight_connections(int(target)):
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
            index_text = str(index) if index >= 0 else "-"
            item = QListWidgetItem(
                format_indexed_name_label(
                    index_text,
                    name,
                    data.get("name_en", ""),
                    prefix=f"{type_letter}|",
                )
            )
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
        set_type_enabled = getattr(self.view.morph_type_combo, "setEnabled", None)
        if callable(set_type_enabled):
            set_type_enabled(False)
            self.view.morph_type_combo.setToolTip(
                "Morph type is fixed after creation; create a new morph to change type"
            )

        semantic = self._semantic_morph(data)
        offsets_edit = getattr(self.view, "offsets_edit", None)
        if offsets_edit is not None:
            offsets = semantic.to_mapping()["offsets"] if semantic else data.get("offsets", [])
            offsets_edit.setPlainText(json.dumps(offsets, ensure_ascii=False, indent=2))
        self._update_offset_policy(semantic)
        self._update_work_material_policy(semantic)

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

    def _semantic_morph(self, data=None):
        data = data or self.morph_data.get(self.current_morph, {})
        try:
            return self._authoring_morphs_by_index.get(int(data.get("index", -1)))
        except (TypeError, ValueError):
            return None

    def _update_offset_policy(self, morph):
        setter = getattr(self.view, "set_offsets_editable", None)
        if not callable(setter):
            return
        if not self._authoring_ready or morph is None:
            setter(False, "Authoring coordinator is not available")
            return
        data = self.morph_data.get(self.current_morph, {})
        if morph.morph_type == "vertex" and not data.get("blend_shape_targets"):
            setter(False, "Vertex offsets require an exact imported blendShape target binding")
        elif morph.morph_type in _EDITABLE_OFFSET_TYPES:
            setter(True, "Canonical PMX offsets (JSON)")
        elif morph.morph_type in _ROUNDTRIP_ONLY_OFFSET_TYPES:
            setter(False, "Round-trip metadata only; runtime editing is not supported")
        else:
            setter(False, "PMX 2.1 Flip/Impulse authoring is rejected by policy")

    def _update_work_material_policy(self, morph):
        setter = getattr(self.view, "set_work_material_controls", None)
        if not callable(setter):
            return
        if (
            not self._authoring_ready
            or self.material_morph_work is None
            or morph is None
            or morph.morph_type != "material"
        ):
            setter(False, (), "Select a material morph with an injected work-material service")
            return
        offsets = []
        for offset_index, offset in enumerate(morph.offsets):
            target = offset.get("material_index", "?")
            operation = {0: "multiply", 1: "add"}.get(offset.get("operation_type"), "unsupported")
            offsets.append((offset_index, f"Offset {offset_index}: material {target} ({operation})"))
        setter(bool(offsets), offsets, "Temporary work shader; raw offsets change only on Apply")

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

        current_model_root = self.app_state.current_model_root
        data = self.morph_data[self.current_morph]
        morph = self._semantic_morph(data)
        if not self._authoring_ready:
            self._emit_authoring_error("Authoring coordinator is not available")
            return
        if not current_model_root or morph is None:
            return
        updated = replace(
            morph,
            name=self.view.morph_name_jp_edit.text(),
            name_english=self.view.morph_name_en_edit.text(),
            panel=self.view.panel_combo.currentIndex(),
        )
        if self._run_authoring(
            "replace morph",
            self.authoring_coordinator.replace_morph,
            current_model_root,
            updated,
            select_index=morph.index,
        ):
            logger.info("Applied semantic changes to morph index %s", morph.index)

    def create_morph(self):
        """Choose a supported type at creation time and append one semantic morph."""
        root = self.app_state.current_model_root
        choose_type = getattr(self.view, "choose_create_morph_type", None)
        if not self._authoring_ready or not root or not callable(choose_type):
            return
        capabilities = self._create_type_capabilities()
        morph_type = choose_type(capabilities)
        if morph_type is None:
            return
        capability = next(
            (item for item in capabilities if item[0] == morph_type),
            None,
        )
        if capability is None or not capability[1]:
            self._emit_authoring_error(
                capability[2] if capability is not None else f"Unknown morph type: {morph_type}"
            )
            return
        morph = MmdMorphSpec(name="New Morph", name_english="New Morph", panel=4, morph_type=morph_type)
        self._run_authoring(
            "create morph",
            self.authoring_coordinator.create_morph,
            root,
            morph,
            select_index="last",
        )

    def _create_type_capabilities(self):
        """Return localized capability rows for the creation-only type menu."""
        disabled = {
            "flip": tr_message("morph_create_flip_unsupported"),
            "impulse": tr_message("morph_create_impulse_unsupported"),
        }
        if not self._owned_mesh_shapes():
            disabled["vertex"] = tr_message("morph_create_vertex_mesh_required")
        return tuple(
            (
                morph_type,
                self._authoring_ready and morph_type not in disabled,
                disabled.get(morph_type, ""),
            )
            for morph_type in _CREATE_MORPH_TYPES
        )

    def _owned_mesh_shapes(self):
        root = self.app_state.current_model_root
        try:
            candidates = tuple(
                self.maya_adapter.list_relatives(root, allDescendents=True, type="mesh")
                if root
                else ()
            )
            return tuple(
                shape
                for shape in candidates
                if not bool(self.maya_adapter.get_attr(f"{shape}.intermediateObject"))
            )
        except Exception:
            return ()

    def delete_current_morph(self):
        morph = self._semantic_morph()
        root = self.app_state.current_model_root
        if self._authoring_ready and root and morph is not None:
            self._run_authoring(
                "delete morph",
                self.authoring_coordinator.delete_morph,
                root,
                morph.index,
                select_index=max(0, morph.index - 1),
            )

    def move_current_morph(self, delta):
        morph = self._semantic_morph()
        root = self.app_state.current_model_root
        if not self._authoring_ready or not root or morph is None or self._authoring_spec is None:
            return
        target = max(0, min(len(self._authoring_spec.morphs) - 1, morph.index + int(delta)))
        if target != morph.index:
            self._run_authoring(
                "move morph",
                self.authoring_coordinator.move_morph,
                root,
                morph.index,
                target,
                select_index=target,
            )

    def apply_offsets(self):
        """Persist canonical raw offsets without changing preview weight."""
        root = self.app_state.current_model_root
        morph = self._semantic_morph()
        editor = getattr(self.view, "offsets_edit", None)
        if not self._authoring_ready or not root or morph is None or editor is None:
            return
        if morph.morph_type not in _EDITABLE_OFFSET_TYPES:
            self._emit_authoring_error(f"{morph.morph_type} offsets are read-only")
            return
        try:
            offsets = json.loads(editor.toPlainText())
        except (TypeError, ValueError) as exc:
            self._emit_authoring_error(f"Invalid offsets JSON: {exc}")
            return
        if not isinstance(offsets, list):
            self._emit_authoring_error("Offsets JSON must be a list")
            return
        self._run_authoring(
            "replace morph offsets",
            self.authoring_coordinator.replace_morph_offsets,
            root,
            morph.index,
            offsets,
            select_index=morph.index,
        )

    def _selected_work_offset_index(self):
        combo = getattr(self.view, "work_offset_combo", None)
        if combo is None or combo.currentIndex() < 0:
            return None
        current_data = getattr(combo, "currentData", None)
        value = (
            current_data()
            if callable(current_data)
            else combo.itemData(combo.currentIndex())
        )
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(combo.currentIndex())

    def create_work_material(self):
        """Create an owned temporary shader; canonical raw offsets stay unchanged."""
        root = self.app_state.current_model_root
        morph = self._semantic_morph()
        offset_index = self._selected_work_offset_index()
        if not root or morph is None or offset_index is None or self.material_morph_work is None:
            return
        self._run_work_action(
            "create material morph work",
            self.material_morph_work.create,
            root,
            morph.index,
            offset_index,
        )

    def apply_work_material(self):
        """Apply work shader values through the canonical coordinator transaction."""
        root = self.app_state.current_model_root
        morph = self._semantic_morph()
        offset_index = self._selected_work_offset_index()
        if not root or morph is None or offset_index is None or self.material_morph_work is None:
            return
        self._run_authoring(
            "apply material morph work",
            self.material_morph_work.apply,
            root,
            morph.index,
            offset_index,
            select_index=morph.index,
        )

    def clear_work_material(self):
        """Delete the temporary binding without touching canonical raw offsets."""
        root = self.app_state.current_model_root
        if not root or self.material_morph_work is None:
            return
        self._run_work_action(
            "clear material morph work",
            self.material_morph_work.clear,
            root,
        )

    def _run_work_action(self, operation, callback, *args):
        try:
            callback(*args)
        except Exception as exc:
            logger.error("Morph work action failed (%s): %s", operation, exc, exc_info=True)
            self._emit_authoring_error(f"{operation} failed: {exc}")
            return False
        self.app_state.emit_status(f"{operation} completed")
        return True

    def _run_authoring(self, operation, callback, *args, select_index=None):
        try:
            result = callback(*args)
        except Exception as exc:
            logger.error("Morph authoring failed (%s): %s", operation, exc, exc_info=True)
            self._emit_authoring_error(f"{operation} failed: {exc}")
            return False
        self.load_morphs()
        if select_index == "last" and getattr(result, "morphs", None):
            select_index = max(item.index for item in result.morphs)
        if isinstance(select_index, int):
            self._select_morph_index(select_index)
        self.app_state.emit_status(f"{operation} completed")
        return True

    def _select_morph_index(self, index):
        """Restore list selection by semantic PMX index after a refresh."""
        for row in range(self.view.morph_list.count()):
            item = self.view.morph_list.item(row)
            key = item.data(Qt.UserRole)
            try:
                candidate = int(self.morph_data[key].get("index", -1))
            except (KeyError, TypeError, ValueError):
                continue
            if candidate == index:
                self.view.morph_list.setCurrentItem(item)
                return

    def _emit_authoring_error(self, message):
        try:
            self.app_state.emit_status(message, level="error")
        except TypeError:
            self.app_state.emit_status(message)

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
