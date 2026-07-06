"""AnimationPresenter headless unit tests — no Maya dependency."""

import json
import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter  # noqa: E402


SAMPLE_FRAMES_JSON = json.dumps(
    [
        {
            "name": "Root",
            "name_english": "Root",
            "special_flag": 1,
            "elements": [{"type": 0, "index": 0}],
        },
        {
            "name": "表情",
            "name_english": "Exp",
            "special_flag": 1,
            "elements": [{"type": 1, "index": 0}, {"type": 1, "index": 1}],
        },
        {
            "name": "体(上)",
            "name_english": "Upper Body",
            "special_flag": 0,
            "elements": [
                {"type": 0, "index": 3},
                {"type": 0, "index": 4},
            ],
        },
    ],
    ensure_ascii=False,
)


# --- Fake Qt widgets ---


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, cb):
        self.callbacks.append(cb)

    def emit(self, *args):
        for cb in self.callbacks:
            cb(*args)


class _FakeButton:
    def __init__(self):
        self.clicked = _FakeSignal()


class _FakeComboBox:
    def __init__(self):
        self._items = []
        self._index = -1
        self.currentTextChanged = _FakeSignal()

    def clear(self):
        self._items.clear()
        self._index = -1

    def addItem(self, text, data=None):
        self._items.append((text, data))

    def findText(self, text):
        for i, (t, _) in enumerate(self._items):
            if t == text:
                return i
        return -1

    def setCurrentIndex(self, idx):
        self._index = idx

    def blockSignals(self, block):
        pass

    def setSizeAdjustPolicy(self, policy):
        pass

    def setToolTip(self, _):
        pass


class _FakeLabel:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def setAlignment(self, _):
        pass


class _FakeTreeItem:
    def __init__(self, texts=None):
        self._texts = texts or [""]
        self._data = {}
        self._children = []
        self._expanded = False

    def text(self, col=0):
        return self._texts[col] if col < len(self._texts) else ""

    def data(self, col, role):
        return self._data.get((col, role))

    def setData(self, col, role, value):
        self._data[(col, role)] = value

    def addChild(self, child):
        self._children.append(child)

    def setExpanded(self, expanded):
        self._expanded = expanded

    def childCount(self):
        return len(self._children)

    def child(self, idx):
        return self._children[idx]


class _FakeTreeWidget:
    def __init__(self):
        self._items = []
        self.itemClicked = _FakeSignal()

    def clear(self):
        self._items.clear()

    def addTopLevelItem(self, item):
        self._items.append(item)

    def topLevelItemCount(self):
        return len(self._items)

    def topLevelItem(self, idx):
        return self._items[idx]

    def setHeaderHidden(self, _):
        pass


class _FakeView:
    def __init__(self):
        self.model_combo = _FakeComboBox()
        self.refresh_btn = _FakeButton()
        self.clear_btn = _FakeButton()
        self.status_label = _FakeLabel()
        self.display_frame_tree = _FakeTreeWidget()
        self.body_placeholder = _FakeLabel()
        self.finger_placeholder = _FakeLabel()
        self.morph_placeholder = _FakeLabel()
        self.picker_tabs = type("FakeTabWidget", (), {"setObjectName": lambda s, _: None})()


class _FakeAppState:
    def __init__(self, model_root=None):
        self.current_model_changed = _FakeSignal()
        self.model_list_updated = _FakeSignal()
        self._current_model_root = model_root

    @property
    def current_model_root(self):
        return self._current_model_root

    @current_model_root.setter
    def current_model_root(self, value):
        self._current_model_root = value

    def refresh_model_list(self):
        pass


class _FakeAdapter:
    def __init__(self, joints_by_index=None, display_json=None):
        self._joints_by_index = joints_by_index or {}
        self._display_json = display_json
        self.selected = []

    def ls(self, nodes, type=None):
        return nodes

    def list_relatives(self, node, **kwargs):
        return list(self._joints_by_index.values())

    def attribute_exists(self, attr, node):
        if attr == "mmd_bone_index":
            return node in self._joints_by_index.values()
        if attr == "mmd_display_frames_json":
            return self._display_json is not None
        return False

    def get_attr(self, attr_path):
        node, attr = attr_path.rsplit(".", 1)
        if attr == "mmd_bone_index":
            for idx, name in self._joints_by_index.items():
                if name == node:
                    return idx
            return -1
        if attr == "mmd_display_frames_json":
            return self._display_json
        return None

    def select(self, nodes, replace=True):
        self.selected = list(nodes)


_USER_ROLE = 0x0100


class TestAnimationPresenter(unittest.TestCase):
    def _make(self, joints=None, display_json=None, model_root=None):
        view = _FakeView()
        app_state = _FakeAppState(model_root=model_root)
        adapter = _FakeAdapter(joints_by_index=joints or {}, display_json=display_json)
        presenter = AnimationPresenter(view, app_state, maya_adapter=adapter)
        return presenter, view, app_state, adapter

    def test_initial_no_model(self):
        presenter, view, _, _ = self._make()
        self.assertEqual(view.display_frame_tree.topLevelItemCount(), 0)

    def test_model_change_populates_tree(self):
        joints = {0: "center", 3: "upper_body", 4: "neck"}
        presenter, view, app_state, _ = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
        )

        app_state.current_model_changed.emit("test_model")

        self.assertEqual(view.display_frame_tree.topLevelItemCount(), 3)
        root_group = view.display_frame_tree.topLevelItem(0)
        self.assertIn("Root", root_group.text(0))
        self.assertEqual(root_group.childCount(), 1)

    def test_tree_bone_item_has_user_data(self):
        joints = {0: "center"}
        presenter, view, _, _ = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        root_group = view.display_frame_tree.topLevelItem(0)
        child = root_group.child(0)
        self.assertEqual(child.data(0, _USER_ROLE), "center")

    def test_tree_unresolved_item_has_no_user_data(self):
        presenter, view, _, _ = self._make(
            joints={},
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        root_group = view.display_frame_tree.topLevelItem(0)
        child = root_group.child(0)
        self.assertIsNone(child.data(0, _USER_ROLE))
        self.assertIn("#0", child.text(0))

    def test_click_item_selects_in_maya(self):
        joints = {0: "center"}
        presenter, view, _, adapter = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        root_group = view.display_frame_tree.topLevelItem(0)
        child = root_group.child(0)
        presenter.on_display_frame_item_clicked(child)

        self.assertEqual(adapter.selected, ["center"])
        self.assertEqual(view.status_label.text(), "center")

    def test_click_unresolved_item_does_nothing(self):
        presenter, view, _, adapter = self._make(
            joints={},
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        root_group = view.display_frame_tree.topLevelItem(0)
        child = root_group.child(0)
        presenter.on_display_frame_item_clicked(child)

        self.assertEqual(adapter.selected, [])

    def test_clear_button_clears_selection(self):
        joints = {0: "center"}
        presenter, view, _, adapter = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        view.status_label.setText("center")
        presenter.on_clear_clicked()

        self.assertEqual(adapter.selected, [])
        self.assertEqual(view.status_label.text(), "")

    def test_model_change_to_empty_clears_tree(self):
        joints = {0: "center"}
        presenter, view, app_state, _ = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )
        self.assertGreater(view.display_frame_tree.topLevelItemCount(), 0)

        app_state.current_model_changed.emit("")

        self.assertEqual(view.display_frame_tree.topLevelItemCount(), 0)

    def test_fallback_flat_list_when_no_display_frames(self):
        joints = {0: "center", 1: "upper_body"}
        presenter, view, _, _ = self._make(
            joints=joints,
            display_json=None,
            model_root="test_model",
        )

        self.assertEqual(view.display_frame_tree.topLevelItemCount(), 1)
        group = view.display_frame_tree.topLevelItem(0)
        self.assertEqual(group.childCount(), 2)

    def test_special_flag_group_count(self):
        joints = {0: "center"}
        presenter, view, _, _ = self._make(
            joints=joints,
            display_json=SAMPLE_FRAMES_JSON,
            model_root="test_model",
        )

        special = [
            view.display_frame_tree.topLevelItem(i)
            for i in range(view.display_frame_tree.topLevelItemCount())
        ]
        self.assertEqual(view.display_frame_tree.topLevelItemCount(), 3)
        self.assertIn("Root", special[0].text(0))
        self.assertIn("表情", special[1].text(0))

    def test_item_display_text_strips_namespace_and_path(self):
        joints = {0: "|root|ns:center_jnt"}
        presenter, view, _, _ = self._make(
            joints=joints,
            display_json=json.dumps([{
                "name": "Root",
                "name_english": "Root",
                "special_flag": 0,
                "elements": [{"type": 0, "index": 0}],
            }]),
            model_root="test_model",
        )

        child = view.display_frame_tree.topLevelItem(0).child(0)
        self.assertEqual(child.text(0), "center_jnt")

    def test_model_combo_updated_on_model_list_signal(self):
        presenter, view, app_state, _ = self._make()

        presenter.on_model_list_updated(["model_A", "model_B"])

        self.assertEqual(len(view.model_combo._items), 2)


if __name__ == "__main__":
    unittest.main()
