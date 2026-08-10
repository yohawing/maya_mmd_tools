"""DisplayPanePresenterの作業コピー編集と保存契約を検証する。"""

import json
import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.display_pane_presenter import DisplayPanePresenter
from mmd_tools.ui.translations import UITranslator


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class _Button:
    def __init__(self):
        self.clicked = _Signal()


class _ListItem:
    def __init__(self, text):
        self.value = text

    def setText(self, text):
        self.value = text


class _List:
    def __init__(self):
        self.currentRowChanged = _Signal()
        self.items = []
        self.row = -1

    def clear(self):
        self.items = []
        self.row = -1

    def addItem(self, text):
        self.items.append(_ListItem(text))

    def setCurrentRow(self, row):
        self.row = row
        self.currentRowChanged.emit(row)

    def currentRow(self):
        return self.row

    def item(self, row):
        return self.items[row] if 0 <= row < len(self.items) else None


class _LineEdit:
    def __init__(self):
        self.textChanged = _Signal()
        self.value = ""

    def setText(self, value):
        self.value = value
        self.textChanged.emit(value)

    def text(self):
        return self.value

    def clear(self):
        self.setText("")


class _CheckBox:
    def __init__(self):
        self.stateChanged = _Signal()
        self.checked = False

    def setChecked(self, checked):
        self.checked = bool(checked)
        self.stateChanged.emit(2 if checked else 0)

    def isChecked(self):
        return self.checked


class _Table:
    def __init__(self):
        self.rows = []
        self.row = -1

    def setRowCount(self, count):
        self.rows = [[None, None, None] for _ in range(count)]
        if count == 0:
            self.row = -1

    def setItem(self, row, column, item):
        self.rows[row][column] = item

    def selectRow(self, row):
        self.row = row

    def currentRow(self):
        return self.row


class _Label:
    def __init__(self):
        self.value = ""

    def setText(self, value):
        self.value = value


class _View:
    def __init__(self):
        self.frame_list = _List()
        self.name_jp_edit = _LineEdit()
        self.name_en_edit = _LineEdit()
        self.special_frame_check = _CheckBox()
        self.item_table = _Table()
        self.status_label = _Label()
        self.enabled = False
        for name in (
            "add_frame_btn",
            "delete_frame_btn",
            "move_frame_up_btn",
            "move_frame_down_btn",
            "add_element_btn",
            "delete_item_btn",
            "move_item_up_btn",
            "move_item_down_btn",
            "refresh_btn",
            "apply_btn",
            "reset_btn",
        ):
            setattr(self, name, _Button())

    def set_editor_enabled(self, enabled):
        self.enabled = bool(enabled)


class _AppState:
    def __init__(self):
        self.current_model_changed = _Signal()
        self.current_model_root = "model_root"
        self.statuses = []

    def emit_status(self, message):
        self.statuses.append(message)


class _Adapter:
    def __init__(self):
        self.attrs = {
            "model_root.mmd_display_frames_json": json.dumps(
                [
                    {
                        "name": "Root",
                        "name_english": "Root",
                        "special_flag": 1,
                        "elements": [{"type": 0, "index": 0}],
                    },
                    {
                        "name": "表情",
                        "name_english": "Facial",
                        "special_flag": 1,
                        "elements": [{"type": 1, "index": 2}],
                    },
                    {
                        "name": "操作",
                        "name_english": "Controls",
                        "special_flag": 0,
                        "elements": [],
                    },
                ],
                ensure_ascii=False,
            ),
            "model_root.mmdMorphData": json.dumps(
                [{"name_jp": "笑い", "name_en": "Smile", "index": 2}],
                ensure_ascii=False,
            ),
            "|model_root|全ての親.mmd_bone_index": 0,
            "|model_root|全ての親.mmd_bone_name": "全ての親",
            "|model_root|センター.mmd_bone_index": 1,
            "|model_root|センター.mmd_bone_name": "センター",
        }
        self.set_calls = []
        self.undo_calls = []

    def object_exists(self, node):
        return node == "model_root"

    def attribute_exists(self, attr, node):
        return f"{node}.{attr}" in self.attrs

    def get_attr(self, plug):
        return self.attrs.get(plug)

    def list_relatives(self, *_args, **_kwargs):
        return ["|model_root|全ての親", "|model_root|センター"]

    def add_attr(self, node, longName, **_kwargs):
        self.attrs[f"{node}.{longName}"] = ""

    def set_attr(self, plug, value, **kwargs):
        self.attrs[plug] = value
        self.set_calls.append((plug, value, kwargs))

    def undo_info(self, **kwargs):
        self.undo_calls.append(kwargs)


class TestDisplayPanePresenter(unittest.TestCase):
    def setUp(self):
        self.view = _View()
        self.app_state = _AppState()
        self.adapter = _Adapter()
        self.choices = []

        def choice_provider(_title, choices):
            self.choices.append(choices)
            return choices[-1] if choices else None

        self.presenter = DisplayPanePresenter(
            self.view,
            self.app_state,
            maya_adapter=self.adapter,
            choice_provider=choice_provider,
        )
        self.presenter.refresh()

    def test_refresh_loads_metadata_and_resolves_items(self):
        self.assertEqual([frame["name"] for frame in self.presenter.frames], ["Root", "表情", "操作"])
        self.assertEqual(len(self.view.frame_list.items), 3)
        self.assertEqual(
            [item.value for item in self.view.frame_list.items],
            ["0:★ Root [Root]", "1:★ 表情 [Facial]", "2:操作 [Controls]"],
        )
        self.assertTrue(self.view.enabled)
        self.assertEqual(len(self.view.item_table.rows), 1)

    def test_edit_add_move_and_delete_regular_frame(self):
        self.view.frame_list.setCurrentRow(2)
        self.view.name_jp_edit.setText("アクセサリ")
        self.assertEqual(self.presenter.frames[2]["name"], "アクセサリ")

        self.presenter.add_frame()
        self.assertEqual(len(self.presenter.frames), 4)
        self.presenter.move_frame(-1)
        self.assertEqual(self.presenter.frames[2]["name"], "New Frame")
        self.presenter.delete_frame()
        self.assertEqual(len(self.presenter.frames), 3)

    def test_special_frame_cannot_be_deleted(self):
        self.view.frame_list.setCurrentRow(0)
        self.presenter.delete_frame()
        self.assertEqual(len(self.presenter.frames), 3)
        self.assertIn("cannot be deleted", self.view.status_label.value)

    def test_add_item_uses_type_and_global_index_and_excludes_duplicates(self):
        self.view.frame_list.setCurrentRow(2)
        self.presenter.add_item(0)
        self.assertEqual(self.presenter.frames[2]["elements"], [{"type": 0, "index": 1}])
        self.presenter.add_item(0)
        self.assertEqual(self.presenter.frames[2]["elements"], [
            {"type": 0, "index": 1},
            {"type": 0, "index": 0},
        ])
        self.presenter.add_item(0)
        self.assertEqual(len(self.presenter.frames[2]["elements"]), 2)
        expected_status = UITranslator.instance().translate(
            "no_display_element_candidates", "messages"
        )
        self.assertEqual(self.view.status_label.value, expected_status)

    def test_same_item_can_belong_to_multiple_frames(self):
        self.view.frame_list.setCurrentRow(2)
        self.presenter.add_item(0)
        self.presenter.add_frame()
        self.view.frame_list.setCurrentRow(3)
        self.presenter.add_item(0)
        self.assertIn({"type": 0, "index": 1}, self.presenter.frames[2]["elements"])
        self.assertIn({"type": 0, "index": 1}, self.presenter.frames[3]["elements"])

    def test_apply_writes_one_string_attribute_and_undo_chunk(self):
        self.view.frame_list.setCurrentRow(2)
        self.view.name_en_edit.setText("Props")
        self.assertTrue(self.presenter.apply())
        self.assertEqual(len(self.adapter.set_calls), 1)
        plug, raw, kwargs = self.adapter.set_calls[0]
        self.assertEqual(plug, "model_root.mmd_display_frames_json")
        self.assertEqual(kwargs, {"type": "string"})
        self.assertEqual(json.loads(raw)[2]["name_english"], "Props")
        self.assertEqual(
            self.adapter.undo_calls,
            [{"openChunk": True, "chunkName": "Edit Display Frames"}, {"closeChunk": True}],
        )

    def test_apply_rejects_missing_special_frames(self):
        for frame in self.presenter.frames:
            frame["special_flag"] = 0
        self.assertFalse(self.presenter.apply())
        self.assertEqual(self.adapter.set_calls, [])
        self.assertIn("special display frames", self.view.status_label.value)

    def test_apply_rejects_renamed_required_special_frame(self):
        self.presenter.frames[0]["name"] = "Custom"
        self.presenter.frames[0]["name_english"] = "Custom"
        self.assertFalse(self.presenter.apply())
        self.assertIn("Root and facial", self.view.status_label.value)

    def test_apply_rejects_missing_element_reference(self):
        self.presenter.frames[2]["elements"].append({"type": 0, "index": 999})
        self.assertFalse(self.presenter.apply())
        self.assertIn("missing bone index: 999", self.view.status_label.value)

    def test_reset_discards_working_copy_changes(self):
        self.view.frame_list.setCurrentRow(2)
        self.view.name_jp_edit.setText("変更")
        self.presenter.reset()
        self.assertEqual(self.presenter.frames[2]["name"], "操作")


if __name__ == "__main__":
    unittest.main()
