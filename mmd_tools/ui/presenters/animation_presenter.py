"""Presenter for the Animator Toolset tab."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.constants import (
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
)
from ...core.display_frame_resolver import PickerGroup, resolve_display_frames
from ...core.logger import get_logger
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
        self.view.status_label.setText("")

    def _clear_all(self):
        self._picker_groups = []
        self.view.display_frame_tree.clear()
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
