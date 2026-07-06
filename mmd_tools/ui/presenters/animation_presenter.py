"""Presenter for the Animator Toolset tab."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
)
from ...core.display_frame_resolver import PickerGroup, resolve_display_frames
from ...core.logger import get_logger
from ...core.morph_metadata_reader import (
    CategorizedMorphs,
    MorphInfo,
    categorize_morphs,
    read_morph_list_from_blendshape_json,
    PANEL_NAMES,
)
from ..combo_box_utils import add_combo_item_with_tooltip

if TYPE_CHECKING:
    from ..application_state import ApplicationState

logger = get_logger(__name__)

_USER_ROLE = 0x0100  # Qt.UserRole


class AnimationPresenter:
    """Drives the AnimationTab (Body/Finger/Morph/Other picker + tools)."""

    def __init__(self, view, app_state: ApplicationState, maya_adapter=None):
        self.view = view
        self.app_state = app_state
        if maya_adapter is None:
            from ...adapters.maya_cmds_adapter import MayaCmdsAdapter

            maya_adapter = MayaCmdsAdapter()
        self.maya_adapter = maya_adapter
        self._picker_groups: list[PickerGroup] = []
        self._morph_sliders: dict[str, object] = {}
        self._morph_targets: dict[str, list[tuple[str, int]]] = {}
        self.connect_signals()

        if self.app_state.current_model_root:
            self._reload_for_model(self.app_state.current_model_root)

    def connect_signals(self):
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        self.app_state.model_list_updated.connect(self.on_model_list_updated)
        self.view.model_combo.currentTextChanged.connect(self.on_model_selected)
        self.view.refresh_btn.clicked.connect(self.on_refresh_clicked)
        self.view.clear_btn.clicked.connect(self.on_clear_clicked)
        self.view.display_frame_tree.itemClicked.connect(self.on_display_frame_item_clicked)

    def disconnect_signals(self):
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
        try:
            self.maya_adapter.select([], replace=True)
        except Exception:
            pass
        self.view.status_label.setText("")

    def on_display_frame_item_clicked(self, item, _column=0):
        node_name = item.data(0, _USER_ROLE)
        if not node_name:
            return
        try:
            self.maya_adapter.select([node_name], replace=True)
            self.view.status_label.setText(node_name)
        except Exception:
            self.view.status_label.setText(f"(not found: {node_name})")

    # -- Internal ------------------------------------------------------

    def _reload_for_model(self, model_root: str):
        bone_map = self._build_bone_index_map(model_root)
        display_json = self._read_display_frames_json(model_root)
        self._picker_groups = resolve_display_frames(display_json, bone_map)
        self._populate_display_frame_tree(self._picker_groups)
        self._reload_morph_tab(model_root)
        self.view.status_label.setText("")

    def _clear_all(self):
        self._picker_groups = []
        self.view.display_frame_tree.clear()
        self._clear_morph_tab()
        self.view.status_label.setText("")

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

    def _read_display_frames_json(self, model_root: str) -> str | None:
        try:
            if not self.maya_adapter.attribute_exists(ATTR_MMD_DISPLAY_FRAMES_JSON, model_root):
                return None
            return self.maya_adapter.get_attr(f"{model_root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}")
        except Exception:
            return None

    def _populate_display_frame_tree(self, groups: list[PickerGroup]):
        from ..qt_compat import QTreeWidgetItem

        tree = self.view.display_frame_tree
        tree.clear()

        for group in groups:
            label = group.name
            if group.name_english and group.name_english != group.name:
                label = f"{group.name} ({group.name_english})"
            group_item = QTreeWidgetItem([label])

            for picker_item in group.items:
                display = self._item_display_text(picker_item)
                child = QTreeWidgetItem([display])
                child.setData(0, _USER_ROLE, picker_item.resolved_name or None)
                group_item.addChild(child)

            tree.addTopLevelItem(group_item)
            if group.special_flag:
                group_item.setExpanded(True)

    @staticmethod
    def _item_display_text(picker_item) -> str:
        name = picker_item.resolved_name
        if not name:
            kind = "bone" if picker_item.element_type == 0 else "morph"
            return f"[{kind} #{picker_item.index}]"
        short = name.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        return short

    # -- Morph tab ---------------------------------------------------------

    def _reload_morph_tab(self, model_root: str):
        self._clear_morph_tab()
        morph_infos = self._collect_morph_infos(model_root)
        categorized = categorize_morphs(morph_infos)
        self._populate_morph_groups(categorized)

    def _clear_morph_tab(self):
        self._morph_sliders.clear()
        self._morph_targets.clear()
        layout = self.view.morph_groups_layout
        while layout.count() > 1:
            child = layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.deleteLater()

    def _collect_morph_infos(self, model_root: str) -> list[MorphInfo]:
        blend_nodes = self._find_blend_shape_nodes(model_root)
        seen_names: set[str] = set()
        unique_morphs: list[MorphInfo] = []
        for bs_node in blend_nodes:
            names_json = self._read_blend_morph_names(bs_node)
            if names_json:
                morphs = read_morph_list_from_blendshape_json(names_json, panel=4)
                for m in morphs:
                    targets = self._morph_targets.setdefault(m.name, [])
                    targets.append((bs_node, m.index))
                    if m.name not in seen_names:
                        seen_names.add(m.name)
                        unique_morphs.append(m)
        return unique_morphs

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

    def _read_blend_morph_names(self, bs_node: str) -> dict[str, str] | None:
        try:
            if not self.maya_adapter.attribute_exists(
                ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, bs_node
            ):
                return None
            import json

            raw = self.maya_adapter.get_attr(
                f"{bs_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}"
            )
            if not raw:
                return None
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def _populate_morph_groups(self, categorized: CategorizedMorphs):
        from ..qt_compat import (
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QSlider,
            QVBoxLayout,
            Qt,
        )

        layout = self.view.morph_groups_layout
        categories = [
            (PANEL_NAMES[1], categorized.eyebrow),
            (PANEL_NAMES[2], categorized.eye),
            (PANEL_NAMES[3], categorized.mouth),
            (PANEL_NAMES[4], categorized.other),
        ]

        for cat_name, morphs in categories:
            if not morphs:
                continue
            group = QGroupBox(f"{cat_name} ({len(morphs)})")
            group.setCheckable(True)
            group.setChecked(True)
            group_layout = QVBoxLayout()
            group_layout.setContentsMargins(4, 4, 4, 4)
            group_layout.setSpacing(2)

            for morph in morphs:
                row = QHBoxLayout()
                label = QLabel(morph.name)
                label.setMinimumWidth(60)
                label.setMaximumWidth(100)
                row.addWidget(label)

                slider = QSlider(Qt.Horizontal)
                slider.setRange(0, 100)
                slider.setValue(0)
                row.addWidget(slider, 1)

                value_label = QLabel("0")
                value_label.setMinimumWidth(25)
                row.addWidget(value_label)

                morph_name = morph.name
                slider.valueChanged.connect(
                    lambda val, name=morph_name, lbl=value_label: self._on_morph_slider_changed(
                        name, val, lbl
                    )
                )
                self._morph_sliders[morph_name] = slider
                group_layout.addLayout(row)

            group.setLayout(group_layout)
            insert_pos = max(0, layout.count() - 1)
            layout.insertWidget(insert_pos, group)

    def _on_morph_slider_changed(self, morph_name: str, value: int, label):
        label.setText(str(value))
        weight = value / 100.0
        targets = self._morph_targets.get(morph_name)
        if not targets:
            return
        for bs_node, weight_idx in targets:
            try:
                self.maya_adapter.set_attr(f"{bs_node}.weight[{weight_idx}]", weight)
            except Exception as exc:
                logger.debug("Morph slider set failed for %s: %s", morph_name, exc)
