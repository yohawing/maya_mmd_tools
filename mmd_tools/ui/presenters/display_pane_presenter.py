"""DisplayPaneTabとPMX表示枠metadataを接続するPresenter。"""

from __future__ import annotations

from copy import deepcopy
import json
from collections.abc import Mapping
from typing import Callable, Optional

from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core.constants import (
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_MORPH_DATA,
)
from ...core.display_frame_metadata import display_frames_from_json, display_frames_to_json
from ...core.logger import get_logger
from ..translations.translator import UITranslator
from .list_presenter_helpers import format_indexed_name_label

logger = get_logger(__name__)


class DisplayPanePresenter:
    """表示枠の作業コピーを管理し、Apply時だけsceneへ反映する。"""

    def __init__(
        self,
        view,
        app_state,
        maya_adapter=None,
        choice_provider: Optional[Callable[[str, list[dict]], object]] = None,
    ):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self._choice_provider = choice_provider or self._show_element_dialog
        self.frames: list[dict] = []
        self._original_frames: list[dict] = []
        self._bone_choices: dict[str, int] = {}
        self._morph_choices: dict[str, int] = {}
        self._loading = False
        self._connect_signals()

    def _connect_signals(self) -> None:
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        self.view.frame_list.currentRowChanged.connect(self.on_frame_selected)
        self.view.add_frame_btn.clicked.connect(self.add_frame)
        self.view.delete_frame_btn.clicked.connect(self.delete_frame)
        self.view.move_frame_up_btn.clicked.connect(lambda: self.move_frame(-1))
        self.view.move_frame_down_btn.clicked.connect(lambda: self.move_frame(1))
        self.view.add_element_btn.clicked.connect(lambda: self.add_item())
        self.view.delete_item_btn.clicked.connect(self.delete_item)
        self.view.move_item_up_btn.clicked.connect(lambda: self.move_item(-1))
        self.view.move_item_down_btn.clicked.connect(lambda: self.move_item(1))
        self.view.name_jp_edit.textChanged.connect(self.on_frame_properties_changed)
        self.view.name_en_edit.textChanged.connect(self.on_frame_properties_changed)
        self.view.special_frame_check.stateChanged.connect(self.on_frame_properties_changed)
        self.view.refresh_btn.clicked.connect(self.refresh)
        self.view.apply_btn.clicked.connect(self.apply)
        self.view.reset_btn.clicked.connect(self.reset)

    def on_current_model_changed(self, _model_root: str) -> None:
        """共有モデル選択が変わったら表示枠を読み直す。"""
        self.refresh()

    def refresh(self) -> None:
        """scene metadataから作業コピーと候補一覧を再構築する。"""
        root = self.app_state.current_model_root
        self.frames = []
        self._original_frames = []
        self._bone_choices = {}
        self._morph_choices = {}
        if not root or not self.maya_adapter.object_exists(root):
            self._render_frames()
            self._set_status("Select an MMD model")
            return

        raw = ""
        if self.maya_adapter.attribute_exists(ATTR_MMD_DISPLAY_FRAMES_JSON, root):
            raw = self.maya_adapter.get_attr(f"{root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}") or ""
        self.frames = self._parse_frames(raw)
        self._original_frames = deepcopy(self.frames)
        self._bone_choices = self._collect_bone_choices(root)
        self._morph_choices = self._collect_morph_choices(root)
        self._render_frames()
        self._set_status(f"Loaded {len(self.frames)} display frames")

    def reset(self) -> None:
        """未適用の編集を破棄して最後に読み込んだ状態へ戻す。"""
        self.frames = deepcopy(self._original_frames)
        self._render_frames()
        self._set_status("Display frame edits reset")

    def add_frame(self) -> None:
        """通常表示枠を末尾へ追加する。"""
        if not self.app_state.current_model_root:
            self._set_status("Select an MMD model")
            return
        self.frames.append(
            {"name": "New Frame", "name_english": "New Frame", "special_flag": 0, "elements": []}
        )
        self._render_frames(select_row=len(self.frames) - 1)

    def delete_frame(self) -> None:
        """選択中の通常表示枠を削除する。特殊枠は保護する。"""
        row = self.view.frame_list.currentRow()
        if not self._valid_frame_row(row):
            return
        if self.frames[row].get("special_flag"):
            self._set_status("Special display frames cannot be deleted")
            return
        del self.frames[row]
        self._render_frames(select_row=min(row, len(self.frames) - 1))

    def move_frame(self, offset: int) -> None:
        """選択中の枠を1段移動する。"""
        row = self.view.frame_list.currentRow()
        target = row + offset
        if not self._valid_frame_row(row) or not self._valid_frame_row(target):
            return
        self.frames[row], self.frames[target] = self.frames[target], self.frames[row]
        self._render_frames(select_row=target)

    def on_frame_selected(self, row: int) -> None:
        """選択した枠のpropertiesと要素を表示する。"""
        self._loading = True
        try:
            enabled = self._valid_frame_row(row)
            self.view.set_editor_enabled(enabled)
            if not enabled:
                self.view.name_jp_edit.clear()
                self.view.name_en_edit.clear()
                self.view.special_frame_check.setChecked(False)
                self.view.item_table.setRowCount(0)
                return
            frame = self.frames[row]
            self.view.name_jp_edit.setText(frame["name"])
            self.view.name_en_edit.setText(frame["name_english"])
            self.view.special_frame_check.setChecked(bool(frame["special_flag"]))
            self._render_items(frame["elements"])
        finally:
            self._loading = False

    def on_frame_properties_changed(self, *_args) -> None:
        """property fieldの変更を作業コピーへ反映する。"""
        if self._loading:
            return
        row = self.view.frame_list.currentRow()
        if not self._valid_frame_row(row):
            return
        frame = self.frames[row]
        frame["name"] = self.view.name_jp_edit.text()
        frame["name_english"] = self.view.name_en_edit.text()
        frame["special_flag"] = 1 if self.view.special_frame_check.isChecked() else 0
        item = self.view.frame_list.item(row)
        if item is not None:
            item.setText(self._frame_label(frame, row))

    def add_item(self, element_type: Optional[int] = None) -> None:
        """候補dialogから表示枠要素を1つ追加する。

        ``element_type`` は既存のプログラム呼び出し向けに残す狭い
        compatibility seamで、UIは引数なしで呼びdialog内で種別を選ぶ。
        """
        frame_row = self.view.frame_list.currentRow()
        if not self._valid_frame_row(frame_row):
            return

        frame = self.frames[frame_row]
        is_facial = self._is_facial_frame(frame)
        if is_facial:
            allowed_types = (1,)
            if element_type is not None:
                try:
                    requested_type = int(element_type)
                except (TypeError, ValueError):
                    requested_type = -1
                if requested_type != 1:
                    self._set_status(self._tr("facial_display_frame_morph_only", "messages"))
                    return
        elif element_type is None:
            allowed_types = (0, 1)
        else:
            try:
                allowed_types = (int(element_type),)
            except (TypeError, ValueError):
                self._set_status(self._tr("invalid_display_element", "messages"))
                return
            if allowed_types[0] not in (0, 1):
                self._set_status(self._tr("invalid_display_element", "messages"))
                return

        candidates = self._element_candidates(frame, allowed_types)
        if not candidates:
            self._set_status(self._tr("no_display_element_candidates", "messages"))
            return

        selected = self._choice_provider(self._tr("add_element", "buttons"), candidates)
        if selected is None:
            return
        identity = self._normalize_choice(selected, candidates)
        if identity is None:
            self._set_status(self._tr("invalid_display_element", "messages"))
            return

        selected_type, index = identity
        element = {"type": selected_type, "index": index}
        if element in frame["elements"]:
            self._set_status(self._tr("duplicate_display_element", "messages"))
            return
        if not self._candidate_identity_exists(identity, candidates):
            self._set_status(self._tr("invalid_display_element", "messages"))
            return
        frame["elements"].append(element)
        self._render_items(frame["elements"], select_row=len(frame["elements"]) - 1)
        self._set_status(
            self._tr("display_element_added", "messages").format(
                element_type=self._element_type_label(selected_type), index=index
            )
        )

    def delete_item(self) -> None:
        """選択中の表示要素を削除する。"""
        frame_row = self.view.frame_list.currentRow()
        item_row = self.view.item_table.currentRow()
        if not self._valid_item_row(frame_row, item_row):
            return
        del self.frames[frame_row]["elements"][item_row]
        self._render_items(
            self.frames[frame_row]["elements"],
            select_row=min(item_row, len(self.frames[frame_row]["elements"]) - 1),
        )

    def move_item(self, offset: int) -> None:
        """選択中の表示要素を1段移動する。"""
        frame_row = self.view.frame_list.currentRow()
        item_row = self.view.item_table.currentRow()
        target = item_row + offset
        if not self._valid_item_row(frame_row, item_row) or not self._valid_item_row(frame_row, target):
            return
        elements = self.frames[frame_row]["elements"]
        elements[item_row], elements[target] = elements[target], elements[item_row]
        self._render_items(elements, select_row=target)

    def apply(self) -> bool:
        """作業コピーを検証し、1つのundo chunkでroot metadataへ保存する。"""
        root = self.app_state.current_model_root
        if not root or not self.maya_adapter.object_exists(root):
            self._set_status("Select an MMD model")
            return False
        error = self._validation_error()
        if error:
            self._set_status(error)
            return False

        payload = display_frames_to_json(self.frames)
        self.maya_adapter.undo_info(openChunk=True, chunkName="Edit Display Frames")
        try:
            if not self.maya_adapter.attribute_exists(ATTR_MMD_DISPLAY_FRAMES_JSON, root):
                self.maya_adapter.add_attr(root, longName=ATTR_MMD_DISPLAY_FRAMES_JSON, dataType="string")
            self.maya_adapter.set_attr(
                f"{root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}", payload, type="string"
            )
        except Exception as exc:
            logger.error("Failed to apply display frames", exc_info=True)
            self._set_status(f"Failed to apply display frames: {exc}")
            return False
        finally:
            self.maya_adapter.undo_info(closeChunk=True)

        self._original_frames = deepcopy(self.frames)
        self._set_status(f"Applied {len(self.frames)} display frames")
        return True

    def _validation_error(self) -> str:
        if not self.frames:
            return "At least one display frame is required"
        for index, frame in enumerate(self.frames):
            if not frame["name"].strip() and not frame["name_english"].strip():
                return f"Display frame {index + 1} requires a name"
        root_frame = next((frame for frame in self.frames if self._is_root_frame(frame)), None)
        facial_frame = next((frame for frame in self.frames if self._is_facial_frame(frame)), None)
        if root_frame is None or facial_frame is None:
            return "Root and facial special display frames are required"
        if not any(element["type"] == 0 for element in root_frame["elements"]):
            return "The Root display frame requires at least one bone"
        if any(element["type"] != 1 for element in facial_frame["elements"]):
            return "The facial display frame can contain only morphs"

        bone_indices = set(self._bone_choices.values())
        morph_indices = set(self._morph_choices.values())
        for frame_index, frame in enumerate(self.frames):
            for element in frame["elements"]:
                valid_indices = bone_indices if element["type"] == 0 else morph_indices
                if element["index"] not in valid_indices:
                    item_type = "bone" if element["type"] == 0 else "morph"
                    return (
                        f"Display frame {frame_index + 1} contains a missing {item_type} "
                        f"index: {element['index']}"
                    )
        return ""

    @staticmethod
    def _is_root_frame(frame: dict) -> bool:
        """PMXの必須Root枠を特殊フラグと既知名で識別する。"""
        return bool(frame["special_flag"]) and (
            frame["name"].strip().casefold() == "root"
            or frame["name_english"].strip().casefold() == "root"
        )

    @staticmethod
    def _is_facial_frame(frame: dict) -> bool:
        """PMXの必須表情枠を特殊フラグと既知名で識別する。"""
        english_names = {"exp", "facial", "expression", "expressions"}
        return bool(frame["special_flag"]) and (
            frame["name"].strip() == "表情"
            or frame["name_english"].strip().casefold() in english_names
        )

    @staticmethod
    def _parse_frames(raw: str) -> list[dict]:
        return display_frames_from_json(raw)

    def _collect_bone_choices(self, root: str) -> dict[str, int]:
        result = {}
        joints = self.maya_adapter.list_relatives(root, allDescendents=True, fullPath=True, type="joint") or []
        for joint in joints:
            try:
                if not self.maya_adapter.attribute_exists(ATTR_MMD_BONE_INDEX, joint):
                    continue
                index = int(self.maya_adapter.get_attr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))
                name = joint.rsplit("|", 1)[-1]
                if self.maya_adapter.attribute_exists(ATTR_MMD_BONE_NAME, joint):
                    name = self.maya_adapter.get_attr(f"{joint}.{ATTR_MMD_BONE_NAME}") or name
                result[f"{name} [{index}]"] = index
            except Exception:
                continue
        return dict(sorted(result.items(), key=lambda item: item[1]))

    def _collect_morph_choices(self, root: str) -> dict[str, int]:
        if not self.maya_adapter.attribute_exists(ATTR_MMD_MORPH_DATA, root):
            return {}
        try:
            parsed = json.loads(self.maya_adapter.get_attr(f"{root}.{ATTR_MMD_MORPH_DATA}") or "[]")
        except (TypeError, ValueError):
            return {}
        result = {}
        for entry in parsed if isinstance(parsed, list) else []:
            if not isinstance(entry, dict):
                continue
            try:
                index = int(entry.get("index", -1))
            except (TypeError, ValueError):
                continue
            if index < 0:
                continue
            name = str(entry.get("name_jp") or entry.get("name_en") or f"Morph {index}")
            result[f"{name} [{index}]"] = index
        return dict(sorted(result.items(), key=lambda item: item[1]))

    def _element_candidates(self, frame: dict, allowed_types: tuple[int, ...]) -> list[dict]:
        """Build stable identity records while excluding frame-local duplicates."""
        existing = set()
        for element in frame.get("elements", []):
            if not isinstance(element, dict):
                continue
            try:
                existing.add((int(element.get("type", -1)), int(element.get("index", -1))))
            except (TypeError, ValueError):
                continue
        choices_by_type = ((0, self._bone_choices), (1, self._morph_choices))
        candidates: list[dict] = []
        for element_type, choices in choices_by_type:
            if element_type not in allowed_types:
                continue
            for label, index in choices.items():
                identity = (element_type, int(index))
                if identity in existing:
                    continue
                candidates.append(
                    {
                        "type": element_type,
                        "index": int(index),
                        "name": str(label).rsplit(" [", 1)[0],
                    }
                )
        return sorted(candidates, key=lambda item: (item["type"], item["index"], item["name"].casefold()))

    @staticmethod
    def _normalize_choice(selected: object, candidates: list[dict]) -> tuple[int, int] | None:
        """Normalize provider output to a type/index identity, never display text."""
        if isinstance(selected, Mapping):
            raw_type, raw_index = selected.get("type"), selected.get("index")
        elif isinstance(selected, (tuple, list)) and len(selected) == 2:
            raw_type, raw_index = selected
        else:
            return None
        try:
            return int(raw_type), int(raw_index)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _candidate_identity_exists(identity: tuple[int, int], candidates: list[dict]) -> bool:
        return any((candidate["type"], candidate["index"]) == identity for candidate in candidates)

    def _element_type_label(self, element_type: int) -> str:
        key = "display_element_type_bone" if element_type == 0 else "display_element_type_morph"
        return self._tr(key, "fields")

    def _tr(self, key: str, category: str) -> str:
        translator = getattr(self.view, "_translator", None) or UITranslator.instance()
        return translator.translate(key, category)

    def _render_frames(self, select_row: int = 0) -> None:
        self._loading = True
        try:
            self.view.frame_list.clear()
            for index, frame in enumerate(self.frames):
                self.view.frame_list.addItem(self._frame_label(frame, index))
        finally:
            self._loading = False
        if self.frames:
            self.view.frame_list.setCurrentRow(max(0, min(select_row, len(self.frames) - 1)))
        else:
            self.on_frame_selected(-1)

    def _render_items(self, elements: list[dict], select_row: int = -1) -> None:
        from ..qt_compat import QTableWidgetItem, Qt

        self.view.item_table.setRowCount(len(elements))
        for row, element in enumerate(elements):
            element_type = int(element["type"])
            index = int(element["index"])
            type_name = "Bone" if element_type == 0 else "Morph"
            choices = self._bone_choices if element_type == 0 else self._morph_choices
            name = next((label.rsplit(" [", 1)[0] for label, value in choices.items() if value == index), "(missing)")
            for column, value in enumerate((type_name, name, str(index))):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.view.item_table.setItem(row, column, item)
        if 0 <= select_row < len(elements):
            self.view.item_table.selectRow(select_row)

    @staticmethod
    def _frame_label(frame: dict, index: int) -> str:
        prefix = "★ " if frame["special_flag"] else ""
        return format_indexed_name_label(
            index,
            frame["name"],
            frame["name_english"],
            prefix=prefix,
        )

    def _valid_frame_row(self, row: int) -> bool:
        return 0 <= row < len(self.frames)

    def _valid_item_row(self, frame_row: int, item_row: int) -> bool:
        return self._valid_frame_row(frame_row) and 0 <= item_row < len(self.frames[frame_row]["elements"])

    def _set_status(self, message: str) -> None:
        self.view.status_label.setText(message)
        self.app_state.emit_status(message)

    def _show_element_dialog(self, title: str, candidates: list[dict]) -> Optional[dict]:
        """Open the dedicated element selector only after the add button click."""
        from ..widgets.display_frame_element_dialog import DisplayFrameElementDialog

        allowed_types = tuple(dict.fromkeys(int(candidate["type"]) for candidate in candidates))
        dialog = DisplayFrameElementDialog(candidates, allowed_types=allowed_types, parent=self.view)
        dialog.setWindowTitle(title)
        if not dialog.exec_modal():
            return None
        return dialog.selected_element
