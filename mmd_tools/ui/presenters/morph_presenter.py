"""Present runtime preview and coordinator-routed PMX morph authoring UI."""

from dataclasses import replace

from mmd_tools.adapters import MayaCmdsAdapter
from mmd_tools.adapters.maya_morph_authoring_snapshot_provider import (
    MayaMorphAuthoringSnapshotProvider,
)
from ...core.logger import get_logger
from ...core.name_display import morph_name_fallback
from ...core.morph_metadata_reader import (
    MORPH_TAB_GROUP_ORDER,
    PMX_TYPE_TO_UI_INDEX,
    UI_INDEX_TO_PMX_TYPE,
    group_morph_names_by_panel,
    morph_info_from_presenter_entry,
    PMX_MORPH_TYPE_NAMES,
)
from ...core.morph_authoring import (
    MorphReindexResult,
    classify_morph_change,
    swap_adjacent_morphs,
)
from ...core.morph_read_projection import (
    MorphProjectionRequest,
    normalize_morph_authoring_snapshot,
    project_runtime_capabilities,
)
from ...core.morph_topology import MorphTopologyInspection
from ...core.model_authoring_spec import MmdMorphSpec
from ..qt_compat import QApplication, Qt, QTimer, QListWidgetItem
from .list_presenter_helpers import (
    apply_list_filter,
    format_indexed_name_label,
    reload_for_current_model_change,
    tr_message,
    tr_message_format,
)

logger = get_logger(__name__)


# Backward-compatible private aliases; the canonical table lives in the reader.
_PMX_TYPE_TO_UI_INDEX = PMX_TYPE_TO_UI_INDEX
_UI_INDEX_TO_PMX_TYPE = UI_INDEX_TO_PMX_TYPE
_MORPH_TYPE_LETTERS = {0: "G", 1: "V", 2: "B", 3: "U", 4: "U", 5: "U", 6: "U", 7: "U", 8: "M", 9: "F", 10: "I"}
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
class MorphPresenter:
    def __init__(
        self,
        view,
        app_state,
        maya_adapter=None,
        authoring_coordinator=None,
        material_morph_work=None,
        morph_snapshot_provider=None,
    ):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.authoring_coordinator = authoring_coordinator
        coordinator_reader = getattr(
            authoring_coordinator, "read_morph_authoring_snapshot", None
        )
        if callable(coordinator_reader):
            self.morph_snapshot_provider = authoring_coordinator
        elif morph_snapshot_provider is not None:
            if not callable(
                getattr(morph_snapshot_provider, "read_morph_authoring_snapshot", None)
            ):
                raise TypeError("invalid morph authoring snapshot provider")
            self.morph_snapshot_provider = morph_snapshot_provider
        else:
            self.morph_snapshot_provider = MayaMorphAuthoringSnapshotProvider(
                self.maya_adapter
            )
        preview_methods = (
            "begin_morph_preview",
            "update_morph_preview",
            "commit_morph_preview",
            "rollback_morph_preview",
            "set_morph_preview",
            "reset_morph_preview",
        )
        if authoring_coordinator is not None and all(
            callable(getattr(authoring_coordinator, method, None))
            for method in preview_methods
        ):
            self._preview_coordinator = authoring_coordinator
        elif all(
            callable(getattr(self.morph_snapshot_provider, method, None))
            for method in preview_methods
        ):
            self._preview_coordinator = self.morph_snapshot_provider
        else:
            self._preview_coordinator = None
        self.material_morph_work = material_morph_work
        self.blend_shape_node = None
        self.current_morph = None
        self.morph_data = {}  # MMDモーフデータのキャッシュ
        self._morph_capability_cache = {}
        self._morphs_by_index = {}
        self._loaded_model_root = None
        self._morph_controller = None
        self._controller_topology = {}
        self._topology_inspection = None
        self._authoring_spec = None
        self._authoring_spec_baseline = None
        self._morph_edit_baseline = None
        self._authoring_morphs_by_index = {}
        self._authoring_ready = False
        self.group_morphs = {}  # グループごとのモーフリスト
        self.is_updating = False
        self._pending_refresh_generation = None
        self._last_refresh_generation = None
        self._morph_preview_session = None
        self._morph_preview_dragging = False
        self._morph_preview_ui_preimage = 0
        self._last_morph_preview_value = 0

        self.connect_signals()
        self._set_authoring_available()

        # 既に選択されているモデルがある場合はロード
        if self.app_state.current_model_root and self._has_qt_event_loop():
            QTimer.singleShot(100, self._load_initial_morphs)

    @staticmethod
    def _has_qt_event_loop():
        """Return whether Qt can queue the constructor's deferred load."""
        instance = getattr(QApplication, "instance", None)
        return callable(instance) and instance() is not None

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        refresh_signal = getattr(self.app_state, "model_refresh_completed", None)
        if refresh_signal is not None and hasattr(refresh_signal, "connect"):
            refresh_signal.connect(self.on_model_refresh)

        # モーフリスト関連
        self.view.morph_list.currentItemChanged.connect(self.on_morph_selected)
        self.view.refresh_morphs_btn.clicked.connect(self.load_morphs)
        self.view.search_edit.textChanged.connect(self.filter_morphs)

        # スライダー関連
        self.view.morph_slider.valueChanged.connect(self.on_morph_slider_changed)
        pressed = getattr(self.view.morph_slider, "sliderPressed", None)
        released = getattr(self.view.morph_slider, "sliderReleased", None)
        if pressed is not None:
            pressed.connect(self.begin_morph_slider_drag)
        if released is not None:
            released.connect(self.end_morph_slider_drag)
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
            ("create_work_material_btn", "clicked", self.create_work_material),
            ("apply_work_material_btn", "clicked", self.apply_work_material),
            ("clear_work_material_btn", "clicked", self.clear_work_material),
            ("repair_topology_btn", "clicked", self.repair_morph_topology),
        )
        for widget_name, signal_name, callback in optional_signals:
            widget = getattr(self.view, widget_name, None)
            signal = getattr(widget, signal_name, None)
            if signal is not None:
                signal.connect(callback)

    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        self._rollback_active_morph_preview()
        if getattr(self.app_state, "refreshing", False) is True:
            self.on_model_refresh(getattr(self.app_state, "refresh_generation", 0))
            return
        self._pending_refresh_generation = None
        reload_for_current_model_change(logger, "MorphPresenter", model_root, self.load_morphs)

    def on_model_refresh(self, generation):
        """Mark morph data stale without replacing a pending work copy."""
        self._rollback_active_morph_preview()
        self._pending_refresh_generation = generation

    def _has_pending_refresh_work(self):
        if (
            self._authoring_spec is not None
            and self._authoring_spec_baseline is not None
            and self._authoring_spec != self._authoring_spec_baseline
        ):
            return True
        if self.current_morph and self._morph_edit_baseline is not None:
            try:
                current = (
                    self.view.morph_name_jp_edit.text(),
                    self.view.morph_name_en_edit.text(),
                    self.view.panel_combo.currentIndex(),
                    self.view.morph_type_combo.currentIndex(),
                )
                if current != self._morph_edit_baseline:
                    return True
            except Exception:
                return True
        work = self.material_morph_work
        if work is None:
            return False
        for name in ("is_dirty", "dirty", "has_unsaved_changes", "pending"):
            value = getattr(work, name, False)
            try:
                value = value() if callable(value) else value
            except Exception:
                value = False
            if value:
                return True
        return False

    def refresh_for_generation(self, generation):
        """Reload a visible tab once per generation when its work copy is clean."""
        if self._pending_refresh_generation != generation:
            if self._last_refresh_generation == generation:
                return True
            self.load_morphs()
            self._last_refresh_generation = generation
            return True
        if self._has_pending_refresh_work():
            self._last_refresh_generation = generation
            return True
        self.load_morphs()
        self._pending_refresh_generation = None
        self._last_refresh_generation = generation
        return True

    def ensure_morphs_loaded(self):
        """Load once when the active model has not populated this presenter yet."""
        generation = getattr(self.app_state, "refresh_generation", 0)
        self.refresh_for_generation(generation)

    def _load_initial_morphs(self):
        """Load the deferred constructor projection unless activation did it first."""
        if self._pending_refresh_generation is not None:
            return
        generation = getattr(self.app_state, "refresh_generation", 0)
        if self._last_refresh_generation == generation:
            return
        self.load_morphs()

    def load_morphs(self):
        """モーフをロード"""
        self._rollback_active_morph_preview()
        refresh_generation = getattr(self.app_state, "refresh_generation", 0)
        self._last_refresh_generation = refresh_generation
        self._pending_refresh_generation = None
        self.view.morph_list.clear()
        self.morph_data.clear()
        self._morph_capability_cache.clear()
        self._morphs_by_index.clear()
        self._authoring_spec = None
        self._authoring_spec_baseline = None
        self._morph_edit_baseline = None
        self._authoring_morphs_by_index.clear()
        self._authoring_ready = False
        self._controller_topology = {}
        self._topology_inspection = None
        self.blend_shape_node = None
        topology_setter = getattr(self.view, "set_topology_repair_state", None)
        if callable(topology_setter):
            topology_setter("", False)
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

        reader = getattr(self.morph_snapshot_provider, "read_morph_authoring_snapshot", None)
        if not callable(reader):
            self._loaded_model_root = None
            self._morph_controller = None
            self._set_authoring_error("Morph authoring snapshot reader is unavailable")
            return
        try:
            snapshot = reader(current_model_root)
            if self.app_state.current_model_root != current_model_root:
                raise RuntimeError("current model changed during Morph snapshot read")
            if getattr(self.app_state, "refresh_generation", 0) != refresh_generation:
                raise RuntimeError("refresh generation changed during Morph snapshot read")
            self._consume_authoring_snapshot(current_model_root, snapshot)
        except Exception as exc:
            self._loaded_model_root = None
            self._morph_controller = None
            self.blend_shape_node = None
            self.morph_data.clear()
            self.group_morphs.clear()
            self._morphs_by_index.clear()
            self._morph_capability_cache.clear()
            self._set_authoring_error(f"Authoring metadata unavailable: {exc}")
            logger.error("Failed to read morph authoring snapshot: %s", exc, exc_info=True)
            return

        # Strip the legacy custom annotation once at the input boundary.
        for data in self.morph_data.values():
            data.pop("group", None)

        # グループごとにモーフを整理
        self._organize_morphs_by_group()

        # 全てのモーフを表示
        self._display_all_morphs()

        self._loaded_model_root = current_model_root

        logger.debug(f"Loaded {self.view.morph_list.count()} morphs for model: {current_model_root}")

    def _set_authoring_available(self):
        """Disable semantic widgets until a complete coordinator is injected."""
        required = (
            "read_morph_authoring_snapshot",
            "create_morph",
            "replace_morph",
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

    def _set_authoring_error(self, message):
        self._authoring_spec = None
        self._authoring_spec_baseline = None
        self._authoring_morphs_by_index = {}
        self._authoring_ready = False
        setter = getattr(self.view, "set_authoring_controls_enabled", None)
        if callable(setter):
            setter(False, message, "authoring_unavailable")

    def _consume_authoring_snapshot(self, root, snapshot):
        """Publish one root/generation-validated immutable projection."""
        snapshot, _ = normalize_morph_authoring_snapshot(snapshot)
        projection = snapshot.projection
        if projection.root_identity != root and not self._root_alias_matches_projection(
            root,
            projection.root_identity,
        ):
            raise ValueError("morph authoring snapshot root identity is stale")

        self._authoring_spec = snapshot.spec
        self._authoring_spec_baseline = snapshot.spec
        self._authoring_morphs_by_index = {
            morph.index: morph for morph in snapshot.spec.morphs
        } if snapshot.spec is not None else {}
        self._authoring_ready = bool(
            self._authoring_available and snapshot.spec is not None
        )
        self._morph_controller = projection.controller_identity
        self._topology_inspection = snapshot.topology_inspection
        self._controller_topology = (
            dict(snapshot.topology_inspection.stored)
            if snapshot.topology_inspection.valid
            else {}
        )
        self.blend_shape_node = (
            projection.owned_blend_shape_identities[0]
            if projection.owned_blend_shape_identities
            else None
        )
        if snapshot.spec is not None:
            self._merge_authoring_morphs()
        else:
            self._merge_runtime_only_morphs(projection.morphs)
        self._morphs_by_index = {
            int(data["index"]): data
            for data in self.morph_data.values()
            if data.get("semantic_registered", True)
            and isinstance(data.get("index"), int)
            and int(data["index"]) >= 0
        }
        self._morph_capability_cache.clear()
        for projected in projection.morphs:
            if projected.semantic_registered:
                data = self._morphs_by_index[projected.global_morph_index]
            else:
                data = next(
                    row
                    for row in self.morph_data.values()
                    if row.get("runtime_projection_index")
                    == projected.global_morph_index
                )
            targets = tuple(projected.runtime_targets)
            if not projected.semantic_registered:
                expected_targets = tuple(projected.runtime_targets)
                if not expected_targets:
                    raise ValueError("runtime-only morph has no canonical preview targets")
            elif projection.controller_identity:
                expected_targets = (
                    "{}.inputWeight[{}]".format(
                        projection.controller_identity,
                        projected.global_morph_index,
                    ),
                )
            elif (
                projected.runtime_supported
                and data.get("mmd_morph_type") in {"bone", "material"}
            ):
                expected_targets = (
                    "{}.weight".format(projected.binding_identity),
                )
            else:
                expected_targets = ()
            if targets != expected_targets:
                raise ValueError("morph authoring snapshot runtime target identity mismatch")
            data["runtime_targets"] = targets
            data["blend_shape_targets"] = [
                {
                    "node": binding.blend_shape_identity,
                    "target": binding.alias,
                    "weight_attr": "weight[{}]".format(binding.logical_target_index),
                }
                for binding in projected.bindings
            ]
            if data["blend_shape_targets"]:
                first = data["blend_shape_targets"][0]
                data["blend_shape_node"] = first["node"]
                data["blend_shape_target"] = first["target"]
                data["blend_shape_weight_attr"] = first["weight_attr"]
            self._morph_capability_cache[id(data)] = projected.runtime_supported

        setter = getattr(self.view, "set_authoring_controls_enabled", None)
        if callable(setter):
            setter(
                self._authoring_ready,
                "" if self._authoring_ready else "Authoring coordinator is unavailable",
                "" if self._authoring_ready else "authoring_unavailable",
            )
        topology_setter = getattr(self.view, "set_topology_repair_state", None)
        if callable(topology_setter):
            diagnostic = "; ".join(
                f"{item.code}: {item.detail}"
                for item in snapshot.topology_inspection.diagnostics
            )
            topology_setter(diagnostic, snapshot.topology_inspection.repairable)

    def _root_alias_matches_projection(self, root, projected_root):
        """Accept one unique long-name spelling without weakening stale-root checks."""

        try:
            matches = self.maya_adapter.ls(root, long=True) or ()
        except Exception:
            return False
        return (
            not isinstance(matches, (str, bytes, bytearray))
            and tuple(matches) == (projected_root,)
        )

    def _merge_runtime_only_morphs(self, projections):
        """Publish bare blendShape aliases without inventing semantic metadata."""

        for projected in projections:
            key = projected.raw_pmx_name
            while key in self.morph_data:
                key += "#"
            self.morph_data[key] = {
                "name_jp": projected.raw_pmx_name,
                "name_en": "",
                "panel": 4,
                "type": 1,
                "index": projected.global_morph_index,
                "offsets": [],
                "mmd_morph_type": "vertex",
                "_pmx_type_raw": True,
                "semantic_registered": False,
                "runtime_projection_index": projected.global_morph_index,
            }

    def _inspect_morph_topology(self, root):
        """Project topology diagnostics without repairing during load."""
        inspect = getattr(self.authoring_coordinator, "inspect_morph_topology", None)
        setter = getattr(self.view, "set_topology_repair_state", None)
        if not callable(inspect):
            self._controller_topology = {}
            if callable(setter):
                setter("malformed: topology inspection is unavailable", False)
            return
        try:
            result = inspect(root)
            if not isinstance(result, MorphTopologyInspection):
                raise TypeError("invalid morph topology inspection")
            self._topology_inspection = result
            self._controller_topology = dict(result.stored) if result.valid else {}
            diagnostic = "; ".join(
                f"{item.code}: {item.detail}" for item in result.diagnostics
            )
            if callable(setter):
                setter(diagnostic, result.repairable)
        except Exception as exc:
            self._controller_topology = {}
            logger.error("Failed to inspect morph topology: %s", exc, exc_info=True)
            if callable(setter):
                setter(f"malformed: {exc}", False)

    def repair_morph_topology(self):
        """Run the explicit raw-offset-authoritative repair action."""
        root = self.app_state.current_model_root
        repair = getattr(self.authoring_coordinator, "repair_morph_topology", None)
        if not root or not callable(repair):
            return
        try:
            result = repair(root)
            if not isinstance(result, MorphTopologyInspection) or not result.valid:
                raise RuntimeError("topology repair did not produce a valid readback")
            self.load_morphs()
        except Exception as exc:
            logger.error("Failed to repair morph topology: %s", exc, exc_info=True)
            self._inspect_morph_topology(root)

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
                data["binding_identity"] = morph.binding_identity
                data.setdefault("morph_node", morph.binding_identity)

    def _morph_controls_supported(self, data):
        """Return the immutable capability projected for this refresh."""
        return bool(self._morph_capability_cache.get(id(data), False))

    def _iter_morph_weight_plugs(self, data, morph_name):
        """Yield fixed canonical targets from the current projection only."""
        del morph_name
        yield from tuple(data.get("runtime_targets") or ())

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

    def _morph_row_labels(self):
        """Allocate display labels across all morphs in stable PMX order."""
        labels = {}
        used_names = set()

        def sort_key(morph_key):
            try:
                index = int(self.morph_data[morph_key].get("index", -1))
            except (TypeError, ValueError):
                index = -1
            return (index < 0, index if index >= 0 else 0, morph_key)

        for fallback_order, morph_key in enumerate(sorted(self.morph_data, key=sort_key)):
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
            fallback_index = index if index >= 0 else fallback_order
            labels[morph_key] = format_indexed_name_label(
                index_text, name, data.get("name_en", ""), prefix=f"{type_letter}|",
                fallback=lambda: morph_name_fallback(data.get("name_jp"), fallback_index),
                used_names=used_names,
            )
        return labels

    def _display_morphs(self, morph_keys):
        """Display stable keys without changing suffixes when filtering."""
        for key, label in self._morph_row_labels().items():
            if key in morph_keys:
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, key)
                self.view.morph_list.addItem(item)

    def _refresh_morph_row_labels(self):
        """Refresh labels in place after edits/reordering, retaining selection."""
        labels = self._morph_row_labels()
        for row in range(self.view.morph_list.count()):
            item = self.view.morph_list.item(row)
            if item is not None and item.data(Qt.UserRole) in labels:
                item.setText(labels[item.data(Qt.UserRole)])

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

        if self._morph_controller and self.maya_adapter.object_exists(self._morph_controller):
            try:
                self.maya_adapter.select(self._morph_controller, replace=True)
            except Exception as exc:
                logger.debug("Failed to select morph controller %s: %s", self._morph_controller, exc)

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
        self._update_work_material_policy(semantic)

        # 現在の適用率
        if data.get("runtime_targets"):
            weight = self._get_first_weight(data, morph_name)
            self.view.morph_slider.setValue(int(weight * 100))
            self.view.morph_value_label.setText(f"{int(weight * 100)}%")
            self._last_morph_preview_value = int(weight * 100)
        else:
            self.view.morph_slider.setValue(0)
            self.view.morph_value_label.setText("0%")
            self._last_morph_preview_value = 0

        supported = self._morph_controls_supported(data)
        tooltip = "" if supported else self.view.tr("morph_runtime_unsupported", "tooltips")
        self.view.set_morph_controls_enabled(supported, tooltip)
        self._morph_edit_baseline = (
            self.view.morph_name_jp_edit.text(),
            self.view.morph_name_en_edit.text(),
            self.view.panel_combo.currentIndex(),
            self.view.morph_type_combo.currentIndex(),
        )

        self.is_updating = False

    def _semantic_morph(self, data=None):
        data = data or self.morph_data.get(self.current_morph, {})
        try:
            return self._authoring_morphs_by_index.get(int(data.get("index", -1)))
        except (TypeError, ValueError):
            return None

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

        weight = value / 100.0
        if self.view.invert_check.isChecked():
            weight = 1.0 - weight
        weight *= self.view.multiplier_spin.value()
        if not self._morph_preview_dragging:
            self._morph_preview_ui_preimage = self._last_morph_preview_value
        try:
            if self._morph_preview_dragging:
                if self._morph_preview_session is not None:
                    self._preview_coordinator.update_morph_preview(
                        self._morph_preview_session, weight
                    )
                return
            targets = self._preview_targets_for_morph(self.current_morph)
            self._preview_coordinator.set_morph_preview(
                self._loaded_model_root, targets, weight
            )
            self._last_morph_preview_value = int(value)
        except Exception as exc:
            self._morph_preview_session = None
            self._morph_preview_dragging = False
            self._restore_morph_preview_ui()
            logger.error("Morph preview update failed: %s", exc, exc_info=True)

    def begin_morph_slider_drag(self):
        """Open one fixed-target transaction for a slider press/release action."""
        if self.is_updating or not self.current_morph:
            return
        self._rollback_active_morph_preview()
        self._morph_preview_dragging = True
        self._morph_preview_ui_preimage = self._last_morph_preview_value
        try:
            targets = self._preview_targets_for_morph(self.current_morph)
            self._morph_preview_session = self._preview_coordinator.begin_morph_preview(
                self._loaded_model_root, targets
            )
        except Exception as exc:
            self._morph_preview_dragging = False
            self._restore_morph_preview_ui()
            logger.error("Morph preview drag could not start: %s", exc, exc_info=True)

    def end_morph_slider_drag(self):
        """Commit the active drag without rediscovering or retargeting plugs."""
        session = self._morph_preview_session
        self._morph_preview_session = None
        self._morph_preview_dragging = False
        if session is None:
            return
        try:
            self._preview_coordinator.commit_morph_preview(session)
            self._last_morph_preview_value = self._slider_value()
        except Exception as exc:
            self._restore_morph_preview_ui()
            logger.error("Morph preview drag commit failed: %s", exc, exc_info=True)

    def _rollback_active_morph_preview(self):
        session = self._morph_preview_session
        self._morph_preview_session = None
        self._morph_preview_dragging = False
        if session is None:
            return
        try:
            self._preview_coordinator.rollback_morph_preview(session)
            self._restore_morph_preview_ui()
        except Exception as exc:
            logger.error("Morph preview rollback failed: %s", exc, exc_info=True)

    def _slider_value(self):
        value = getattr(self.view.morph_slider, "value", 0)
        return int(value() if callable(value) else value)

    def _restore_morph_preview_ui(self):
        """Restore the cached pre-action UI value without querying Maya."""
        value = int(self._morph_preview_ui_preimage)
        self.is_updating = True
        try:
            self.view.morph_slider.setValue(value)
            self.view.morph_value_label.setText(f"{value}%")
        finally:
            self.is_updating = False

    def _preview_targets_for_morph(self, morph_name):
        """Resolve one cached morph to canonical writer targets once per action."""
        if self._preview_coordinator is None or not self._loaded_model_root:
            raise RuntimeError("Morph preview coordinator is unavailable")
        data = self.morph_data[morph_name]
        if self._morph_capability_cache.get(id(data)) is False:
            raise RuntimeError("Morph projection does not support runtime preview")
        targets = tuple(data.get("runtime_targets") or ())
        if not targets and self._morph_controller:
            index = data.get("index")
            if isinstance(index, int) and index >= 0:
                targets = (f"{self._morph_controller}.inputWeight[{index}]",)
        if not targets:
            raise RuntimeError("Morph projection has no canonical preview target")
        return targets

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
        if not self.current_morph:
            return
        self._rollback_active_morph_preview()
        try:
            targets = self._preview_targets_for_morph(self.current_morph)
            self._preview_coordinator.reset_morph_preview(
                self._loaded_model_root, targets
            )
        except Exception as exc:
            logger.error("Reset current morph failed: %s", exc, exc_info=True)
            return
        self.is_updating = True
        try:
            self.view.morph_slider.setValue(0)
            self.view.morph_value_label.setText("0%")
            self._last_morph_preview_value = 0
        finally:
            self.is_updating = False

    def reset_all_morphs(self):
        """全てのモーフをリセット"""
        self._rollback_active_morph_preview()
        try:
            targets = tuple(
                dict.fromkeys(
                    plug
                    for morph_name in self.morph_data
                    for plug in self._preview_targets_for_morph(morph_name)
                )
            )
            reset_count = self._preview_coordinator.reset_morph_preview(
                self._loaded_model_root, targets
            )
        except Exception as exc:
            logger.error("Reset all morphs failed: %s", exc, exc_info=True)
            return
        self.is_updating = True
        try:
            self.view.morph_slider.setValue(0)
            self.view.morph_value_label.setText("0%")
            self._last_morph_preview_value = 0
        finally:
            self.is_updating = False
        self.app_state.emit_status(tr_message_format("morphs_reset_count", count=reset_count))
        logger.info("Reset all morphs complete: reset %s target(s)", reset_count)

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
        selected_reader = getattr(self.authoring_coordinator, "read_morph_value", None)
        selected_writer = getattr(self.authoring_coordinator, "apply_morph_value_patch", None)
        try:
            if callable(selected_reader) and callable(selected_writer):
                prior = selected_reader(current_model_root, morph.index, morph.binding_identity)
                route = classify_morph_change(prior, updated)
                if route in {"value", "noop"}:
                    result = prior if route == "noop" else selected_writer(current_model_root, updated)
                    self._update_selected_row_after_patch(result)
                    self.load_morph_details(self.current_morph)
                    self.app_state.emit_status("replace morph completed")
                    logger.info("Applied narrow semantic changes to morph index %s", morph.index)
                    return
            # Structural/mixed edits retain the established full transaction.
            if not callable(getattr(self.authoring_coordinator, "replace_morph", None)):
                raise TypeError("authoring coordinator lacks replace_morph")
            if callable(getattr(self.authoring_coordinator, "read_spec", None)):
                current = self.authoring_coordinator.read_spec(current_model_root)
                prior = next((item for item in current.morphs if item.index == morph.index), None)
                if prior is None or prior.binding_identity != morph.binding_identity:
                    raise ValueError("selected morph is not the current registered binding")
                updated = replace(
                    prior,
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
        except Exception as exc:
            self._emit_authoring_error(f"replace morph failed: {exc}")

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
        creator = getattr(self.authoring_coordinator, "create_morph", None)
        if not callable(creator):
            self._emit_authoring_error("create morph requires narrow morph transaction support")
            return
        try:
            result = creator(root, morph)
            if not isinstance(result, MmdMorphSpec):
                raise TypeError("coordinator returned an invalid created morph")
            self._append_created_morph_row(result)
            self.app_state.emit_status("create morph completed")
        except Exception as exc:
            self._emit_authoring_error(f"create morph failed: {exc}")

    def _append_created_morph_row(self, morph: MmdMorphSpec) -> None:
        """Append one created row without reloading the Morph list."""
        key = morph.name or f"Morph [{morph.index}]"
        while key in self.morph_data:
            key += "#"
        raw_type = next(
            (value for value, name in PMX_MORPH_TYPE_NAMES.items() if name == morph.morph_type),
            1,
        )
        data = {
            "name_jp": morph.name,
            "name_en": morph.name_english,
            "panel": morph.panel,
            "type": raw_type,
            "index": morph.index,
            "offsets": list(morph.to_mapping()["offsets"]),
            "mmd_morph_type": morph.morph_type,
            "morph_node": morph.binding_identity,
            "_pmx_type_raw": True,
        }
        morph_controller = getattr(self, "_morph_controller", None)
        if morph_controller:
            data["runtime_targets"] = (
                f"{morph_controller}.inputWeight[{morph.index}]",
            )
        self.morph_data[key] = data
        capability = bool(morph.binding_identity) and project_runtime_capabilities(
            (
                MorphProjectionRequest(
                    morph.name,
                    morph.index,
                    morph.binding_identity,
                    morph.morph_type,
                ),
            ),
            {},
            (),
        )[0]
        morph_capability_cache = getattr(self, "_morph_capability_cache", None)
        if morph_capability_cache is None:
            morph_capability_cache = self._morph_capability_cache = {}
        morph_capability_cache[id(data)] = capability
        self._authoring_morphs_by_index[morph.index] = morph
        if self._authoring_spec is not None:
            self._authoring_spec = replace(
                self._authoring_spec,
                morphs=(*self._authoring_spec.morphs, morph),
            )
        self._morphs_by_index[morph.index] = self.morph_data[key]
        item = QListWidgetItem(self._morph_row_labels()[key])
        item.setData(Qt.UserRole, key)
        self.view.morph_list.addItem(item)
        self._refresh_morph_row_labels()
        self.view.morph_list.setCurrentItem(item)
        self.current_morph = key

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
                self.maya_adapter.list_relatives(
                    root,
                    allDescendents=True,
                    type="mesh",
                    fullPath=True,
                )
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
        try:
            step = int(delta)
        except (TypeError, ValueError):
            self._emit_authoring_error("move morph requires an adjacent step")
            return
        target = morph.index + step
        if target < 0 or target >= len(self._authoring_spec.morphs) or abs(target - morph.index) != 1:
            return
        mover = getattr(self.authoring_coordinator, "move_morph", None)
        if not callable(mover):
            self._emit_authoring_error("move morph requires narrow morph reindex support")
            return
        try:
            result = mover(root, morph.index, target)
            if not isinstance(result, MorphReindexResult):
                raise TypeError("coordinator returned an invalid morph reindex result")
            self._swap_morph_rows(result, morph.index, target)
            self.app_state.emit_status("move morph completed")
        except Exception as exc:
            self._emit_authoring_error(f"move morph failed: {exc}")

    def _swap_morph_rows(self, result, old_index, new_index):
        """Apply a successful adjacent swap to only the two visible rows."""
        if tuple(result.swapped_indices) != (old_index, new_index):
            raise ValueError("morph reindex result does not match selected indices")
        keys: dict[int, str] = {}
        for key, data in self.morph_data.items():
            value = data.get("index")
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            if value in {old_index, new_index}:
                keys[value] = key
        if set(keys) != {old_index, new_index}:
            raise ValueError("selected morph rows are not present")
        first_key, second_key = keys[old_index], keys[new_index]
        self.morph_data[first_key]["index"], self.morph_data[second_key]["index"] = (
            new_index,
            old_index,
        )
        morph_controller = getattr(self, "_morph_controller", None)
        if morph_controller:
            self.morph_data[first_key]["runtime_targets"] = (
                f"{morph_controller}.inputWeight[{new_index}]",
            )
            self.morph_data[second_key]["runtime_targets"] = (
                f"{morph_controller}.inputWeight[{old_index}]",
            )
        self._update_morph_row_order(first_key, second_key)

        by_binding = {
            morph.binding_identity: morph
            for morph in self._authoring_morphs_by_index.values()
            if morph.binding_identity
        }
        old_binding = next(
            (morph.binding_identity for morph in by_binding.values() if morph.index == old_index),
            None,
        )
        new_binding = next(
            (morph.binding_identity for morph in by_binding.values() if morph.index == new_index),
            None,
        )
        expected_bindings = {
            new_index: old_binding,
            old_index: new_binding,
        }
        if dict(result.bindings) != expected_bindings:
            raise ValueError("morph reindex result bindings do not match the selected pair")
        for index, binding in result.bindings:
            morph = by_binding.get(binding)
            if morph is None:
                raise ValueError(f"morph reindex result references unknown binding {binding!r}")
            if index not in {old_index, new_index}:
                raise ValueError("morph reindex result contains an unexpected index")
        if len(result.bindings) != 2 or {index for index, _ in result.bindings} != {old_index, new_index}:
            raise ValueError("morph reindex result must contain exactly two bindings")
        self._authoring_spec = swap_adjacent_morphs(self._authoring_spec, old_index, new_index)
        self._authoring_morphs_by_index = {
            morph.index: morph for morph in self._authoring_spec.morphs
        }
        self._remap_cached_morph_references({old_index: new_index, new_index: old_index})
        self._morphs_by_index[old_index] = self.morph_data[second_key]
        self._morphs_by_index[new_index] = self.morph_data[first_key]
        self._select_morph_index(new_index)

    def _remap_cached_morph_references(self, swap):
        """Keep cached Group/Flip offsets coherent without reloading the list."""
        for data in self.morph_data.values():
            if data.get("mmd_morph_type") not in {"group", "flip"}:
                continue
            offsets = data.get("offsets")
            if not isinstance(offsets, list):
                continue
            for offset in offsets:
                if not isinstance(offset, dict):
                    continue
                index = offset.get("morph_index")
                if isinstance(index, bool) or not isinstance(index, int):
                    continue
                if index in swap:
                    offset["morph_index"] = swap[index]

    def _update_morph_row_order(self, first_key, second_key):
        widget = self.view.morph_list
        rows = {
            widget.item(row).data(Qt.UserRole): row
            for row in range(widget.count())
            if widget.item(row) is not None
        }
        row_a, row_b = rows.get(first_key), rows.get(second_key)
        if row_a is None or row_b is None:
            raise ValueError("selected morph rows are not visible")
        if hasattr(widget, "takeItem") and hasattr(widget, "insertItem"):
            if row_a < row_b:
                item_b = widget.takeItem(row_b)
                item_a = widget.takeItem(row_a)
                widget.insertItem(row_a, item_b)
                widget.insertItem(row_b, item_a)
            else:
                item_a = widget.takeItem(row_a)
                item_b = widget.takeItem(row_b)
                widget.insertItem(row_b, item_a)
                widget.insertItem(row_a, item_b)
        elif hasattr(widget, "items"):
            widget.items[row_a], widget.items[row_b] = widget.items[row_b], widget.items[row_a]
        else:
            raise ValueError("morph list does not support local row swaps")
        self._refresh_morph_row_labels()

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

    def _update_selected_row_after_patch(self, result):
        """Update only the selected morph row/state after a narrow patch."""
        if not isinstance(result, MmdMorphSpec):
            return
        key = self.current_morph
        if key not in self.morph_data:
            return
        data = self.morph_data[key]
        data.update(
            {
                "name_jp": result.name,
                "name_en": result.name_english,
                "panel": result.panel,
                "offsets": result.to_mapping()["offsets"],
            }
        )
        self._refresh_morph_row_labels()
        self._authoring_morphs_by_index[result.index] = result

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
