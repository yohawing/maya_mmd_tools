"""DisplayPanePresenterのMaya非依存ロジックを検証するテスト。"""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.display_pane_presenter import DisplayPanePresenter  # noqa: E402

TEST_MODEL = "test_mmd_model"


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)


class _FakeList:
    def __init__(self):
        self.currentItemChanged = _FakeSignal()
        self.clear_calls = 0
        self.items = []

    def clear(self):
        self.clear_calls += 1
        self.items.clear()

    def addItem(self, item):
        self.items.append(item)


class _FakeView:
    def __init__(self):
        self.display_pane_list = _FakeList()
        self.contained_items_list = _FakeList()


class _FakeAppState:
    def __init__(self, current_model_root=None):
        self.current_model_root = current_model_root
        self.current_model_changed = _FakeSignal()


class _FakeMayaAdapter:
    def __init__(self, exists=True, attr_exists=True, attr_value=None):
        self.exists = exists
        self.attr_exists = attr_exists
        self.attr_value = attr_value
        self.calls = []

    def object_exists(self, node):
        self.calls.append(("object_exists", node))
        return self.exists

    def attribute_exists(self, attr, node):
        self.calls.append(("attribute_exists", attr, node))
        return self.attr_exists

    def get_attr(self, attr_path):
        self.calls.append(("get_attr", attr_path))
        return self.attr_value


def _make_presenter(model=TEST_MODEL, adapter=None):
    view = _FakeView()
    app_state = _FakeAppState(model)
    adapter = adapter or _FakeMayaAdapter()
    presenter = DisplayPanePresenter(view, app_state, maya_adapter=adapter)
    return presenter, view, app_state, adapter


class TestDisplayPanePresenter(unittest.TestCase):
    def test_load_display_panes_clears_and_returns_when_no_model(self):
        presenter, view, _, adapter = _make_presenter(model=None)

        presenter.load_display_panes()

        self.assertEqual(view.display_pane_list.clear_calls, 1)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(view.display_pane_list.items, [])

    def test_load_display_panes_returns_when_model_does_not_exist(self):
        adapter = _FakeMayaAdapter(exists=False)
        presenter, view, _, adapter = _make_presenter(adapter=adapter)

        presenter.load_display_panes()

        self.assertEqual(adapter.calls, [("object_exists", TEST_MODEL)])
        self.assertEqual(view.display_pane_list.items, [])

    def test_load_display_panes_skips_get_attr_when_attribute_missing(self):
        adapter = _FakeMayaAdapter(attr_exists=False)
        presenter, view, _, adapter = _make_presenter(adapter=adapter)

        presenter.load_display_panes()

        self.assertEqual(
            adapter.calls,
            [
                ("object_exists", TEST_MODEL),
                ("attribute_exists", "mmd_display_panes", TEST_MODEL),
            ],
        )
        self.assertEqual(view.display_pane_list.items, [])

    def test_load_display_panes_adds_items_from_adapter_attr(self):
        adapter = _FakeMayaAdapter(attr_value=["Root", "表情", "Bone"])
        presenter, view, _, adapter = _make_presenter(adapter=adapter)

        presenter.load_display_panes()

        self.assertEqual(
            adapter.calls,
            [
                ("object_exists", TEST_MODEL),
                ("attribute_exists", "mmd_display_panes", TEST_MODEL),
                ("get_attr", f"{TEST_MODEL}.mmd_display_panes"),
            ],
        )
        self.assertEqual(view.display_pane_list.items, ["Root", "表情", "Bone"])


if __name__ == "__main__":
    unittest.main()
