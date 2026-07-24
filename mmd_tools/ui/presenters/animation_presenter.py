"""Presenter for the Animator Toolset tab."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ...core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_MORPH_DATA,
)
from ...actions.rest_pose_action import get_rest_pose_manager
from ...core.display_frame_resolver import PickerGroup, resolve_display_frames
from ...core.logger import get_logger
from ...core.mmd_bone_names import normalize_mmd_bone_name
from ...core.morph_metadata_reader import (
    CategorizedMorphs,
    MorphInfo,
    categorize_morphs,
    parse_blendshape_morph_entries,
    morph_info_from_presenter_entry,
    PANEL_GROUP_LABELS,
)
from ...core.visibility_state import (
    get_visibility_category,
    set_visibility_category,
    sync_visibility_connections,
)
from ..combo_box_utils import add_combo_item_with_tooltip

if TYPE_CHECKING:
    from ..application_state import ApplicationState

logger = get_logger(__name__)

_USER_ROLE = 0x0100  # Qt.UserRole


class AnimationPresenter:
    """Drives the AnimationTab (Body/Finger/Morph/Display picker + tools)."""

    def __init__(
        self,
        view,
        app_state: ApplicationState,
        maya_adapter=None,
        rest_pose_manager=None,
    ):
        self.view = view
        self.app_state = app_state
        if maya_adapter is None:
            from ...adapters.maya_cmds_adapter import MayaCmdsAdapter

            maya_adapter = MayaCmdsAdapter()
        self.maya_adapter = maya_adapter
        self._picker_groups: list[PickerGroup] = []
        self._bone_name_to_joint: dict[str, str] = {}
        self._morph_sliders: dict[str, object] = {}
        self._morph_targets: dict[str, list[tuple[str, int]]] = {}
        self._network_morph_targets: dict[str, list[str]] = {}
        self._morph_indices: dict[str, int] = {}
        self._morph_controller: str | None = None
        self._pose_clipboard: dict | None = None
        self._all_model_joints: list[str] = []
        self.rest_pose_manager = rest_pose_manager or get_rest_pose_manager()
        self.rest_pose_manager.add_listener(self._on_rest_pose_state_changed)
        self.connect_signals()
        self._on_rest_pose_state_changed(self.rest_pose_manager.state())

        if self.app_state.current_model_root:
            self._reload_for_model(self.app_state.current_model_root)

    def connect_signals(self):
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        self.app_state.model_list_updated.connect(self.on_model_list_updated)
        self.view.model_combo.currentTextChanged.connect(self.on_model_selected)
        self.view.refresh_btn.clicked.connect(self.on_refresh_clicked)
        self.view.clear_btn.clicked.connect(self.on_clear_clicked)
        display_pressed = getattr(self.view.display_frame_tree, "itemPressed", None)
        if display_pressed is not None:
            display_pressed.connect(self.on_display_frame_item_clicked)
        else:
            self.view.display_frame_tree.itemClicked.connect(self.on_display_frame_item_clicked)
        self.view.body_picker.region_clicked.connect(self.on_body_region_clicked)
        if hasattr(self.view.body_picker, "regions_selected"):
            self.view.body_picker.regions_selected.connect(self.on_body_regions_selected)
        self.view.body_picker.goto_finger_clicked.connect(self.on_goto_finger)
        self.view.body_picker.mirror_selection_clicked.connect(self.on_mirror_selection)
        if hasattr(self.view.body_picker, "reset_pose_clicked"):
            self.view.body_picker.reset_pose_clicked.connect(self._on_reset_pose)
        self.view.finger_picker.region_clicked.connect(self.on_finger_region_clicked)
        if hasattr(self.view.finger_picker, "regions_selected"):
            self.view.finger_picker.regions_selected.connect(self.on_finger_regions_selected)
        self.view.finger_picker.goto_body_clicked.connect(self.on_goto_body)
        self.view.finger_picker.mirror_selection_clicked.connect(self.on_mirror_selection)
        if hasattr(self.view, "finger_body_btn"):
            self.view.finger_body_btn.clicked.connect(self.on_goto_body)
        if hasattr(self.view, "select_all_btn"):
            self.view.select_all_btn.clicked.connect(self.on_select_all)
        for key, cb in self.view.vis_checkboxes.items():
            cb.stateChanged.connect(
                lambda state, k=key: self._on_visibility_changed(k, state != 0)
            )
        for key, btn in self.view.tool_buttons.items():
            btn.clicked.connect(
                lambda _checked=False, k=key: self._on_tool_clicked(k)
            )

    def disconnect_signals(self):
        self.rest_pose_manager.remove_listener(self._on_rest_pose_state_changed)
        try:
            self.app_state.current_model_changed.disconnect(self.on_current_model_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self.app_state.model_list_updated.disconnect(self.on_model_list_updated)
        except (RuntimeError, TypeError):
            pass

    # -- Signal handlers -----------------------------------------------

    def on_current_model_changed(self, model_root: str):
        self.rest_pose_manager.ensure_model(model_root or "")
        if model_root:
            self._reload_for_model(model_root)
        else:
            self._clear_all()

    def on_model_list_updated(self, models: list):
        self._update_model_combo(models)

    def on_model_selected(self, model_text: str):
        if model_text and model_text != self.app_state.current_model_root:
            self.app_state.current_model_root = model_text

    def on_refresh_clicked(self):
        self.app_state.refresh_model_list()
        model = self.app_state.current_model_root
        if model:
            self._reload_for_model(model)

    def on_clear_clicked(self):
        self._set_picker_selection_from_nodes([])
        try:
            self._select_nodes([])
        except Exception:
            pass
        self.view.status_label.setText("")

    def on_select_all(self):
        """Select every indexed joint belonging to the current MMD model."""

        joints = list(self._all_model_joints)
        self._set_picker_selection_from_nodes(joints)
        if not joints:
            self.view.status_label.setText("(選択できるボーンがありません)")
            return
        try:
            self._select_nodes(joints)
            self.view.status_label.setText(f"全ボーンを選択 ({len(joints)})")
        except Exception:
            self.view.status_label.setText("(全ボーンの選択に失敗)")

    def on_display_frame_item_clicked(self, item, _column=0):
        node_name = item.data(0, _USER_ROLE)
        if not node_name:
            return
        try:
            self._set_picker_selection_from_nodes([node_name])
            self._select_nodes([node_name])
            self.view.status_label.setText(item.text(0))
        except Exception:
            self.view.status_label.setText(f"(not found: {node_name})")

    def on_body_region_clicked(self, region_id: str):
        self._select_picker_regions([region_id], picker="body")

    def on_body_regions_selected(self, region_ids: list[str]):
        self._select_picker_regions(region_ids, picker="body")

    def on_finger_region_clicked(self, region_id: str):
        self._select_picker_regions([region_id], picker="finger")

    def on_finger_regions_selected(self, region_ids: list[str]):
        self._select_picker_regions(region_ids, picker="finger")

    def _select_picker_regions(self, region_ids: list[str], *, picker: str) -> None:
        """Resolve one or more picker regions and update the UI before Maya blocks."""

        if picker == "body":
            from ..widgets.body_picker_widget import _BODY_REGIONS as regions
        else:
            from ..widgets.finger_picker_widget import _FINGER_REGIONS as regions

        by_id = {region["id"]: region["bone_name"] for region in regions}
        labels = [by_id[region_id] for region_id in region_ids if region_id in by_id]
        joints = []
        for label in labels:
            normalized = normalize_mmd_bone_name(label) or label
            joint = self._bone_name_to_joint.get(normalized)
            if joint and joint not in joints:
                joints.append(joint)

        self._set_picker_selection_from_nodes(joints)
        self.view.status_label.setText("、".join(labels))
        if not joints:
            if labels:
                self.view.status_label.setText(f"(未割当: {'、'.join(labels)})")
            return
        try:
            self._select_nodes(joints)
        except Exception:
            self.view.status_label.setText(f"(選択失敗: {'、'.join(labels)})")

    def _set_picker_selection_from_nodes(self, nodes: list[str]) -> None:
        """Reflect Maya joint names as strong picker highlights synchronously."""

        from ..widgets.body_picker_widget import _BODY_REGIONS
        from ..widgets.finger_picker_widget import _FINGER_REGIONS

        joint_to_bone = {joint: bone for bone, joint in self._bone_name_to_joint.items()}
        selected_bones = {joint_to_bone[node] for node in nodes if node in joint_to_bone}

        def selected_ids(regions):
            return [
                region["id"]
                for region in regions
                if (normalize_mmd_bone_name(region["bone_name"]) or region["bone_name"])
                in selected_bones
            ]

        if hasattr(self.view.body_picker, "set_selected_regions"):
            self.view.body_picker.set_selected_regions(selected_ids(_BODY_REGIONS))
        if hasattr(self.view.finger_picker, "set_selected_regions"):
            self.view.finger_picker.set_selected_regions(selected_ids(_FINGER_REGIONS))

    def _select_nodes(self, nodes: list[str]) -> None:
        """Use the API 2.0 selection path when the production adapter exposes it."""

        select_fast = getattr(self.maya_adapter, "select_fast", None)
        if callable(select_fast):
            select_fast(nodes, replace=True)
        else:
            self.maya_adapter.select(nodes, replace=True)

    def on_goto_finger(self):
        self.view.picker_tabs.setCurrentIndex(self.view.TAB_FINGER)

    def on_goto_body(self):
        self.view.picker_tabs.setCurrentIndex(self.view.TAB_BODY)

    def on_mirror_selection(self):
        _MIRROR_PAIRS = {"左": "右", "右": "左"}
        try:
            sel = self.maya_adapter.ls(selection=True) or []
        except Exception:
            return
        joint_to_bone = {v: k for k, v in self._bone_name_to_joint.items()}
        mirrored = []
        for node in sel:
            bone_name = joint_to_bone.get(node)
            if bone_name:
                found = False
                for jp, mirror_jp in _MIRROR_PAIRS.items():
                    if jp in bone_name:
                        mirror_bone = bone_name.replace(jp, mirror_jp, 1)
                        mirror_joint = self._bone_name_to_joint.get(mirror_bone)
                        if mirror_joint:
                            mirrored.append(mirror_joint)
                            found = True
                            break
                if not found:
                    mirrored.append(node)
            else:
                mirrored.append(node)
        if mirrored:
            try:
                self._set_picker_selection_from_nodes(mirrored)
                self._select_nodes(mirrored)
                selected_names = [joint_to_bone.get(node, node) for node in mirrored]
                self.view.status_label.setText("、".join(selected_names))
            except Exception:
                pass

    # -- Visibility -------------------------------------------------------

    def _on_visibility_changed(self, category: str, visible: bool):
        model_root = self.app_state.current_model_root
        if not model_root:
            return
        if category == "morphs":
            return
        try:
            set_visibility_category(self.maya_adapter, model_root, category, visible)
            sync_visibility_connections(self.maya_adapter, model_root, category)
        except Exception as exc:
            logger.debug("Visibility toggle failed for %s: %s", category, exc)

    # -- Internal ------------------------------------------------------

    def _reload_for_model(self, model_root: str):
        bone_map = self._build_bone_index_map(model_root)
        self._all_model_joints = [bone_map[index] for index in sorted(bone_map)]
        self._bone_name_to_joint = self._build_bone_name_map(model_root)
        self._sync_picker_regions()
        bone_display_names = self._build_bone_display_name_map(bone_map)
        morph_metadata = self._read_morph_metadata(model_root)
        self._morph_controller = self._find_morph_controller(model_root)
        morph_display_names = {
            index: info.name for index, info in morph_metadata.items()
        }
        display_json = self._read_display_frames_json(model_root)
        self._picker_groups = resolve_display_frames(
            display_json,
            bone_map,
            bone_display_name_map=bone_display_names,
            morph_display_name_map=morph_display_names,
        )
        self._populate_display_frame_tree(self._picker_groups)
        self._reload_morph_tab(model_root, morph_metadata)
        self._sync_visibility_controls(model_root)
        self.view.status_label.setText("")

    def _sync_picker_regions(self):
        """Keep missing bones non-interactive while navigation stays available."""

        from ..widgets.body_picker_widget import _BODY_REGIONS
        from ..widgets.finger_picker_widget import _FINGER_REGIONS

        available_names = set(self._bone_name_to_joint)
        body_ids = {
            region["id"]
            for region in _BODY_REGIONS
            if (normalize_mmd_bone_name(region["bone_name"]) or region["bone_name"])
            in available_names
        }
        body_ids.update({"reset_pose", "mirror_sel", "fingers_left", "fingers_right"})
        if hasattr(self.view.body_picker, "set_enabled_regions"):
            self.view.body_picker.set_enabled_regions(body_ids)

        finger_ids = {
            region["id"]
            for region in _FINGER_REGIONS
            if (normalize_mmd_bone_name(region["bone_name"]) or region["bone_name"])
            in available_names
        }
        if hasattr(self.view.finger_picker, "set_enabled_regions"):
            self.view.finger_picker.set_enabled_regions(finger_ids)

    def _clear_all(self):
        self._picker_groups = []
        self._all_model_joints = []
        self._bone_name_to_joint.clear()
        self._sync_picker_regions()
        self.view.display_frame_tree.clear()
        self._clear_morph_tab()
        self.view.status_label.setText("")

    def _sync_visibility_controls(self, model_root: str):
        try:
            sync_visibility_connections(self.maya_adapter, model_root)
            for key, cb in self.view.vis_checkboxes.items():
                if key == "morphs":
                    continue
                cb.setChecked(get_visibility_category(self.maya_adapter, model_root, key))
        except Exception as exc:
            logger.debug("Visibility control sync failed: %s", exc)

    def _update_model_combo(self, models: list):
        combo = self.view.model_combo
        combo.blockSignals(True)
        combo.clear()
        for model in models:
            add_combo_item_with_tooltip(combo, model)
        current = self.app_state.current_model_root
        if current:
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _build_bone_index_map(self, model_root: str) -> dict[int, str]:
        try:
            joints = self.maya_adapter.ls(
                self.maya_adapter.list_relatives(model_root, allDescendents=True, type="joint") or [],
                type="joint",
            ) or []
        except Exception:
            return {}

        bone_map: dict[int, str] = {}
        for joint in joints:
            try:
                if not self.maya_adapter.attribute_exists(ATTR_MMD_BONE_INDEX, joint):
                    continue
                idx = int(self.maya_adapter.get_attr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))
                bone_map[idx] = joint
            except Exception:
                continue
        return bone_map

    def _build_bone_name_map(self, model_root: str) -> dict[str, str]:
        try:
            joints = self.maya_adapter.ls(
                self.maya_adapter.list_relatives(model_root, allDescendents=True, type="joint") or [],
                type="joint",
            ) or []
        except Exception:
            return {}

        name_map: dict[str, str] = {}
        for joint in joints:
            try:
                if not self.maya_adapter.attribute_exists(ATTR_MMD_BONE_NAME, joint):
                    continue
                bone_name = self.maya_adapter.get_attr(f"{joint}.{ATTR_MMD_BONE_NAME}")
                if bone_name:
                    normalized = normalize_mmd_bone_name(bone_name) or bone_name
                    name_map[normalized] = joint
            except Exception:
                continue
        return name_map

    def _build_bone_display_name_map(self, bone_map: dict[int, str]) -> dict[int, str]:
        names = {}
        for index, joint in bone_map.items():
            try:
                if self.maya_adapter.attribute_exists(ATTR_MMD_BONE_NAME, joint):
                    name = self.maya_adapter.get_attr(f"{joint}.{ATTR_MMD_BONE_NAME}")
                    if name:
                        names[index] = str(name)
            except Exception:
                continue
        return names

    def _read_morph_metadata(self, model_root: str) -> dict[int, MorphInfo]:
        """Read authoritative PMX morph names/panels keyed by global index."""

        try:
            if not self.maya_adapter.attribute_exists(ATTR_MMD_MORPH_DATA, model_root):
                return {}
            raw = self.maya_adapter.get_attr(f"{model_root}.{ATTR_MMD_MORPH_DATA}")
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, OSError):
            return {}

        entries = []
        if isinstance(parsed, list):
            entries = [(str(position), entry, True) for position, entry in enumerate(parsed)]
        elif isinstance(parsed, dict):
            entries = [(str(key), entry, False) for key, entry in parsed.items()]

        result = {}
        for fallback_key, raw_entry, is_raw_pmx in entries:
            if not isinstance(raw_entry, dict):
                continue
            entry = dict(raw_entry)
            if is_raw_pmx:
                entry["_pmx_type_raw"] = True
            name = str(entry.get("name_jp") or entry.get("name") or fallback_key)
            info = morph_info_from_presenter_entry(name, entry)
            index = info.index
            if index < 0:
                try:
                    index = int(fallback_key)
                except (TypeError, ValueError):
                    continue
                info = MorphInfo(
                    name=info.name,
                    name_english=info.name_english,
                    panel=info.panel,
                    morph_type=info.morph_type,
                    index=index,
                )
            result[index] = info
        return result

    def _read_display_frames_json(self, model_root: str) -> str | None:
        try:
            if not self.maya_adapter.attribute_exists(ATTR_MMD_DISPLAY_FRAMES_JSON, model_root):
                return {}
            return self.maya_adapter.get_attr(f"{model_root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}")
        except Exception:
            return None

    def _populate_display_frame_tree(self, groups: list[PickerGroup]):
        from ..qt_compat import QTreeWidgetItem

        tree = self.view.display_frame_tree
        tree.clear()

        for group in groups:
            label = group.name or group.name_english
            group_item = QTreeWidgetItem([label])

            for picker_item in group.items:
                display = self._item_display_text(picker_item)
                child = QTreeWidgetItem([display])
                child.setData(0, _USER_ROLE, picker_item.resolved_name or None)
                group_item.addChild(child)

            tree.addTopLevelItem(group_item)
            group_item.setExpanded(False)

    @staticmethod
    def _item_display_text(picker_item) -> str:
        if picker_item.display_name:
            return picker_item.display_name
        name = picker_item.resolved_name
        if not name:
            kind = "bone" if picker_item.element_type == 0 else "morph"
            return f"[{kind} #{picker_item.index}]"
        short = name.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        return short

    # -- Morph tab ---------------------------------------------------------

    def _reload_morph_tab(
        self,
        model_root: str,
        morph_metadata: dict[int, MorphInfo] | None = None,
    ):
        self._clear_morph_tab()
        morph_infos = self._collect_morph_infos(model_root, morph_metadata or {})
        categorized = categorize_morphs(morph_infos)
        self._populate_morph_groups(categorized)

    def _clear_morph_tab(self):
        self._morph_sliders.clear()
        self._morph_targets.clear()
        self._network_morph_targets.clear()
        self._morph_indices.clear()
        layout = self.view.morph_groups_layout
        while layout.count() > 1:
            child = layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.deleteLater()

    def _collect_morph_infos(
        self,
        model_root: str,
        morph_metadata: dict[int, MorphInfo] | None = None,
    ) -> list[MorphInfo]:
        metadata = morph_metadata or {}
        self._morph_indices.update({info.name: info.index for info in metadata.values()})
        metadata_by_name = {info.name: info for info in metadata.values()}
        blend_nodes = self._find_blend_shape_nodes(model_root)
        seen_names: set[str] = set()
        unique_morphs: list[MorphInfo] = []
        for bs_node in blend_nodes:
            entries = self._read_blend_morph_entries(bs_node)
            for weight_index, entry in entries.items():
                raw_name = str(entry.get("name", ""))
                global_index = entry.get("index")
                info = metadata.get(global_index) if isinstance(global_index, int) else None
                info = info or metadata_by_name.get(raw_name)
                if info is None:
                    info = MorphInfo(raw_name, "", 4, "vertex", weight_index)
                self._morph_targets.setdefault(info.name, []).append((bs_node, weight_index))
                if info.name not in seen_names:
                    seen_names.add(info.name)
                    unique_morphs.append(info)

        self._collect_network_morph_targets(model_root, metadata, unique_morphs, seen_names)
        for info in sorted(metadata.values(), key=lambda item: item.index):
            if info.name not in seen_names:
                seen_names.add(info.name)
                unique_morphs.append(info)
        return unique_morphs

    def _find_morph_controller(self, model_root: str) -> str | None:
        try:
            if not self.maya_adapter.attribute_exists("mmd_morph_controller", model_root):
                return None
            controllers = self.maya_adapter.list_connections(
                f"{model_root}.mmd_morph_controller",
                source=True,
                destination=False,
            ) or []
            return controllers[0] if len(controllers) == 1 else None
        except Exception:
            return None

    def _collect_network_morph_targets(
        self,
        model_root: str,
        metadata: dict[int, MorphInfo],
        morphs: list[MorphInfo],
        seen_names: set[str],
    ) -> None:
        try:
            network_nodes = self.maya_adapter.ls(type="network") or []
        except Exception:
            return
        for node in network_nodes:
            try:
                if not self.maya_adapter.attribute_exists("mmd_morph_type", node):
                    continue
                if self.maya_adapter.attribute_exists("mmd_model_root", node):
                    roots = self.maya_adapter.list_connections(f"{node}.mmd_model_root") or []
                    if roots and model_root not in roots:
                        continue
                name = self.maya_adapter.get_attr(f"{node}.mmd_morph_name") or node
                index = -1
                if self.maya_adapter.attribute_exists("mmd_morph_index", node):
                    index = int(self.maya_adapter.get_attr(f"{node}.mmd_morph_index"))
                info = metadata.get(index) or MorphInfo(str(name), "", 4, "other", index)
                self._network_morph_targets.setdefault(info.name, []).append(f"{node}.weight")
                if info.name not in seen_names:
                    seen_names.add(info.name)
                    morphs.append(info)
            except Exception:
                continue

    def _find_blend_shape_nodes(self, model_root: str) -> list[str]:
        try:
            meshes = self.maya_adapter.list_relatives(
                model_root, allDescendents=True, type="mesh"
            ) or []
            bs_nodes = []
            for mesh in meshes:
                history = self.maya_adapter.list_history(mesh) or []
                for node in history:
                    try:
                        if self.maya_adapter.node_type(node) == "blendShape":
                            if node not in bs_nodes:
                                bs_nodes.append(node)
                    except Exception:
                        continue
            return bs_nodes
        except Exception:
            return []

    def _read_blend_morph_entries(self, bs_node: str) -> dict[int, dict[str, object]]:
        try:
            if not self.maya_adapter.attribute_exists(
                ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, bs_node
            ):
                return {}
            raw = self.maya_adapter.get_attr(
                f"{bs_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}"
            )
            if not raw:
                return {}
            return parse_blendshape_morph_entries(json.loads(raw))
        except Exception:
            return {}

    def _populate_morph_groups(self, categorized: CategorizedMorphs):
        from ..qt_compat import (
            QHBoxLayout,
            QLabel,
            QPushButton,
            QSlider,
            QVBoxLayout,
            QWidget,
            Qt,
        )

        layout = self.view.morph_groups_layout
        categories = [
            (f"{PANEL_GROUP_LABELS[1]}モーフ", categorized.eyebrow),
            (f"{PANEL_GROUP_LABELS[2]}モーフ", categorized.eye),
            (f"{PANEL_GROUP_LABELS[3]}モーフ", categorized.mouth),
            ("未分類（PMX: その他）", categorized.other),
        ]

        for cat_name, morphs in categories:
            if not morphs:
                continue
            group = QWidget()
            group.setObjectName("MorphPickerGroup")
            group.setStyleSheet(
                "QWidget#MorphPickerGroup { background: #383838; border: none; }"
            )
            group_layout = QVBoxLayout()
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(2)
            header = QPushButton(f"▾  {cat_name}    {len(morphs)}")
            header.setCheckable(True)
            header.setChecked(True)
            header.setStyleSheet(
                "QPushButton { text-align: left; padding: 5px 7px; background: #454545; "
                "color: #dedede; border: none; font-weight: 600; } "
                "QPushButton:hover { background: #505050; }"
            )
            group_layout.addWidget(header)

            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(8, 4, 6, 5)
            content_layout.setSpacing(3)

            for morph in morphs:
                row = QHBoxLayout()
                row.setSpacing(8)
                label = QLabel(morph.name)
                label.setMinimumWidth(72)
                label.setMaximumWidth(120)
                if morph.name_english:
                    label.setToolTip(morph.name_english)
                row.addWidget(label)

                slider = QSlider(Qt.Horizontal)
                slider.setRange(0, 100)
                slider.setStyleSheet(
                    "QSlider::groove:horizontal { height: 3px; background: #252525; } "
                    "QSlider::sub-page:horizontal { background: #5d8faa; } "
                    "QSlider::handle:horizontal { width: 10px; margin: -4px 0; "
                    "border-radius: 5px; background: #aeb4b8; } "
                    "QSlider::handle:horizontal:hover { background: #79cfff; }"
                )
                initial_value = round(self._morph_value(morph.name) * 100.0)
                slider.setValue(initial_value)
                slider.setEnabled(
                    (self._morph_controller is not None and morph.index >= 0)
                    or bool(self._morph_targets.get(morph.name))
                    or bool(self._network_morph_targets.get(morph.name))
                )
                row.addWidget(slider, 1)

                value_label = QLabel(str(initial_value))
                value_label.setFixedWidth(28)
                value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                value_label.setStyleSheet("color: #8fc5e8;")
                row.addWidget(value_label)

                morph_name = morph.name
                slider.valueChanged.connect(
                    lambda val, name=morph_name, lbl=value_label: self._on_morph_slider_changed(
                        name, val, lbl
                    )
                )
                self._morph_sliders[morph_name] = slider
                content_layout.addLayout(row)

            group.setLayout(group_layout)
            group_layout.addWidget(content)

            def toggle_group(
                expanded,
                panel=content,
                button=header,
                title=cat_name,
                count=len(morphs),
            ):
                panel.setVisible(expanded)
                button.setText(f"{'▾' if expanded else '▸'}  {title}    {count}")

            header.toggled.connect(toggle_group)
            insert_pos = max(0, layout.count() - 1)
            layout.insertWidget(insert_pos, group)

    def _on_morph_slider_changed(self, morph_name: str, value: int, label):
        label.setText(str(value))
        weight = value / 100.0
        morph_index = self._morph_indices.get(morph_name, -1)
        if self._morph_controller and morph_index >= 0:
            try:
                self.maya_adapter.set_attr(
                    f"{self._morph_controller}.inputWeight[{morph_index}]",
                    weight,
                )
            except Exception as exc:
                logger.debug("Morph controller weight set failed for %s: %s", morph_name, exc)
            return
        targets = self._morph_targets.get(morph_name, [])
        for bs_node, weight_idx in targets:
            try:
                self.maya_adapter.set_attr(f"{bs_node}.weight[{weight_idx}]", weight)
            except Exception as exc:
                logger.debug("Morph slider set failed for %s: %s", morph_name, exc)
        for plug in self._network_morph_targets.get(morph_name, []):
            try:
                self.maya_adapter.set_attr(plug, weight)
            except Exception as exc:
                logger.debug("Morph network weight set failed for %s: %s", morph_name, exc)

    def _morph_value(self, morph_name: str) -> float:
        morph_index = self._morph_indices.get(morph_name, -1)
        if self._morph_controller and morph_index >= 0:
            try:
                return float(
                    self.maya_adapter.get_attr(
                        f"{self._morph_controller}.inputWeight[{morph_index}]"
                    )
                )
            except Exception:
                pass
        targets = self._morph_targets.get(morph_name, [])
        plugs = [f"{node}.weight[{index}]" for node, index in targets]
        plugs.extend(self._network_morph_targets.get(morph_name, []))
        for plug in plugs:
            try:
                value = self.maya_adapter.get_attr(plug)
                if value is not None:
                    return float(value)
            except Exception:
                continue
        return 0.0

    # -- Tools section ----------------------------------------------------

    _TOOL_HANDLERS = {
        "copy": "_on_copy_pose",
        "paste": "_on_paste_pose",
        "mirror": "_on_mirror_pose",
        "reset": "_on_reset_pose",
        "clean": "_on_clean_curves",
        "bake": "_on_bake_animation",
    }

    def _on_tool_clicked(self, tool_key: str):
        handler_name = self._TOOL_HANDLERS.get(tool_key)
        if handler_name:
            getattr(self, handler_name)()

    def _selected_joints(self) -> list[str]:
        try:
            return self.maya_adapter.ls(selection=True, type="joint") or []
        except Exception:
            return []

    def _on_copy_pose(self):
        from ...actions.pose_actions import CopyPoseAction, CopyPoseRequest

        joints = self._selected_joints()
        if not joints:
            self.view.status_label.setText("No joints selected")
            return
        result = CopyPoseAction(self.maya_adapter).execute(
            CopyPoseRequest(joints=joints)
        )
        if result.succeeded:
            self._pose_clipboard = result.pose
            self.view.status_label.setText(f"Copied pose ({len(result.pose)} joints)")
        else:
            self.view.status_label.setText(f"Copy failed: {result.error}")

    def _on_paste_pose(self):
        from ...actions.pose_actions import PastePoseAction, PastePoseRequest

        if not self._pose_clipboard:
            self.view.status_label.setText("No pose copied")
            return
        result = PastePoseAction(self.maya_adapter).execute(
            PastePoseRequest(pose=self._pose_clipboard)
        )
        if result.succeeded:
            self.view.status_label.setText(
                f"Pasted pose ({result.applied_count} joints)"
            )
        else:
            self.view.status_label.setText(f"Paste failed: {result.error}")

    def _on_reset_pose(self):
        result = self.rest_pose_manager.toggle(self.app_state.current_model_root or "")
        if result.succeeded:
            action = "Rest Pose" if result.active else "Motion"
            self.view.status_label.setText(f"{action} ({result.joint_count} joints)")
        else:
            self.view.status_label.setText(f"Rest Pose failed: {result.error}")

    def _on_rest_pose_state_changed(self, result):
        """Synchronize Animation Toolset controls with the shared session."""
        reset_button = self.view.tool_buttons.get("reset")
        if reset_button is not None and hasattr(reset_button, "setText"):
            reset_button.setText("Return to Motion" if result.active else "Rest Pose")
        if hasattr(self.view.picker_tabs, "setEnabled"):
            self.view.picker_tabs.setEnabled(not result.active)
        for key, button in self.view.tool_buttons.items():
            if key != "reset" and hasattr(button, "setEnabled"):
                button.setEnabled(not result.active)
        if hasattr(self.view.body_picker, "setToolTip"):
            self.view.body_picker.setToolTip(
                "Return to Motion" if result.active else "Display model Rest Pose"
            )

    def _on_mirror_pose(self):
        from ...actions.pose_actions import MirrorPoseAction, MirrorPoseRequest

        joints = self._selected_joints()
        if not joints:
            self.view.status_label.setText("No joints selected")
            return
        result = MirrorPoseAction(self.maya_adapter).execute(
            MirrorPoseRequest(joints=joints)
        )
        if result.succeeded:
            self.view.status_label.setText("Mirrored pose")
        else:
            self.view.status_label.setText(f"Mirror: {result.error}")

    def _on_bake_animation(self):
        from ...actions.pose_actions import BakeAnimationAction, BakeAnimationRequest

        joints = self._selected_joints()
        if not joints:
            self.view.status_label.setText("No joints selected")
            return
        result = BakeAnimationAction(self.maya_adapter).execute(
            BakeAnimationRequest(joints=joints)
        )
        if result.succeeded:
            self.view.status_label.setText("Baked animation")
        else:
            self.view.status_label.setText(f"Bake: {result.error}")

    def _on_clean_curves(self):
        from ...actions.pose_actions import CleanCurvesAction, CleanCurvesRequest

        joints = self._selected_joints()
        if not joints:
            self.view.status_label.setText("No joints selected")
            return
        result = CleanCurvesAction(self.maya_adapter).execute(
            CleanCurvesRequest(joints=joints)
        )
        if result.succeeded:
            self.view.status_label.setText("Cleaned curves")
        else:
            self.view.status_label.setText(f"Clean: {result.error}")
