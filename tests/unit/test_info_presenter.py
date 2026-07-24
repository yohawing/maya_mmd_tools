"""InfoPresenterのMaya非依存ロジックを検証するテスト。

import 連鎖で maya.cmds と PySide6 が必要になるため、
``install_headless_ui_stubs()`` でスタブ化してから presenter を import する。
これにより本テストは mayapy / Qt なしの ``nox -s ci_unit`` で実行できる。
"""

import unittest
from unittest.mock import MagicMock, Mock, patch

from tests.common.mock_ui import attach_mocks
from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.constants import (  # noqa: E402
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
)
from mmd_tools.ui.presenters.info_presenter import InfoPresenter  # noqa: E402
from mmd_tools.ui.components.header_widget import HeaderWidget  # noqa: E402

_MOD = "mmd_tools.ui.presenters.info_presenter"
TEST_MODEL = "test_mmd_model"

_ATTR_VALUES = {
    f"{TEST_MODEL}.{ATTR_MMD_MODEL_NAME}": "テストモデル",
    f"{TEST_MODEL}.{ATTR_MMD_MODEL_NAME_EN}": "Test Model",
    f"{TEST_MODEL}.{ATTR_MMD_COMMENT}": "テストコメント",
    f"{TEST_MODEL}.{ATTR_MMD_COMMENT_EN}": "Test Comment",
}


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for cb in self._callbacks:
            cb(*args)

    def disconnect(self, callback):
        if callback in self._callbacks:
            self._callbacks.remove(callback)


class _FakeAppState:
    def __init__(self, current_model_root=None, scene_model_service=None):
        self._current_model_root = current_model_root
        self.scene_model_service = scene_model_service or _FakeSceneModelService()
        self.current_model_changed = _FakeSignal()

    @property
    def current_model_root(self):
        return self._current_model_root

    @current_model_root.setter
    def current_model_root(self, value):
        self._current_model_root = value

    def clear_cache(self):
        pass


class _FakeSceneModelService:
    def __init__(self, exists=True, attr_values=None, display_names=None, attr_exists=True):
        self.exists = exists
        self.attr_values = attr_values or {}
        self.display_names = display_names or {}
        self.attr_exists = attr_exists

    def object_exists(self, node):
        return bool(node and self.exists)

    def attribute_exists(self, node, attr):
        return bool(node and self.attr_exists)

    def get_attr_safe(self, node, attr, default=None):
        if not self.attribute_exists(node, attr):
            return default
        value = self.attr_values.get(f"{node}.{attr}", default)
        return value if value is not None else default

    def get_model_display_name(self, model_root):
        return self.display_names.get(model_root, model_root)


def _make_mock_view():
    view = Mock()
    attach_mocks(view, ["set_fields_enabled"])

    for attr in ("model_name_jp_edit", "model_name_en_edit", "comment_jp_edit", "comment_en_edit"):
        widget = Mock()
        widget.textChanged = MagicMock()
        widget.textChanged.disconnect = Mock()
        widget.textChanged.connect = Mock()
        setattr(view, attr, widget)

    return view


def _make_presenter_with_model(model=TEST_MODEL, attr_values=None):
    """モデルが選択された状態でプレゼンターを生成するヘルパー。"""
    values = attr_values if attr_values is not None else {k: "" for k in _ATTR_VALUES}
    view = _make_mock_view()
    service = _FakeSceneModelService(attr_values=values)
    app_state = _FakeAppState(current_model_root=model, scene_model_service=service)
    presenter = InfoPresenter(view, app_state)
    return presenter, view, app_state


class TestInitialization(unittest.TestCase):
    def test_enables_fields_when_model_set(self):
        _, view, _ = _make_presenter_with_model()
        view.set_fields_enabled.assert_called_with(True)

    def test_no_fields_enabled_when_no_model(self):
        view = _make_mock_view()
        app_state = _FakeAppState(current_model_root=None)
        InfoPresenter(view, app_state)
        view.set_fields_enabled.assert_not_called()

    def test_signal_connections(self):
        _, view, _ = _make_presenter_with_model()
        for attr in ("model_name_jp_edit", "model_name_en_edit", "comment_jp_edit", "comment_en_edit"):
            getattr(view, attr).textChanged.connect.assert_called()


class TestLoadModelInfo(unittest.TestCase):
    def setUp(self):
        self.presenter, self.view, self.app_state = _make_presenter_with_model()

    def test_sets_text_fields_from_scene_model_service_attrs(self):
        self.app_state.scene_model_service.attr_values = _ATTR_VALUES
        with patch(f"{_MOD}.logger") as mock_logger:
            self.presenter.load_model_info()

        self.view.model_name_jp_edit.setText.assert_called_with("テストモデル")
        self.view.model_name_en_edit.setText.assert_called_with("Test Model")
        self.view.comment_jp_edit.setPlainText.assert_called_with("テストコメント")
        self.view.comment_en_edit.setPlainText.assert_called_with("Test Comment")

        # モデル info ロード詳細は DEBUG のみ（INFO には出さない）
        expected = f"Loaded model info for {TEST_MODEL}"
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_clears_fields_when_no_model(self):
        self.app_state._current_model_root = None

        self.presenter.load_model_info()

        self.view.model_name_jp_edit.clear.assert_called()
        self.view.model_name_en_edit.clear.assert_called()
        self.view.comment_jp_edit.clear.assert_called()
        self.view.comment_en_edit.clear.assert_called()

    def test_clears_fields_when_model_does_not_exist(self):
        self.app_state.scene_model_service.exists = False
        self.presenter.load_model_info()

        self.view.set_fields_enabled.assert_called_with(False)
        self.view.model_name_jp_edit.clear.assert_called()

    def test_uses_empty_string_when_attrs_are_missing_or_none(self):
        self.app_state.scene_model_service.attr_values = {
            f"{TEST_MODEL}.{ATTR_MMD_MODEL_NAME}": None,
            f"{TEST_MODEL}.{ATTR_MMD_MODEL_NAME_EN}": None,
            f"{TEST_MODEL}.{ATTR_MMD_COMMENT}": None,
            f"{TEST_MODEL}.{ATTR_MMD_COMMENT_EN}": None,
        }
        self.presenter.load_model_info()

        self.view.model_name_jp_edit.setText.assert_called_with("")
        self.view.model_name_en_edit.setText.assert_called_with("")
        self.view.comment_jp_edit.setPlainText.assert_called_with("")
        self.view.comment_en_edit.setPlainText.assert_called_with("")

    def test_uses_empty_string_when_attrs_do_not_exist(self):
        self.app_state.scene_model_service.attr_exists = False
        self.presenter.load_model_info()

        self.view.model_name_jp_edit.setText.assert_called_with("")
        self.view.model_name_en_edit.setText.assert_called_with("")
        self.view.comment_jp_edit.setPlainText.assert_called_with("")
        self.view.comment_en_edit.setPlainText.assert_called_with("")


class TestUpdateModelInfo(unittest.TestCase):
    def setUp(self):
        self.presenter, self.view, self.app_state = _make_presenter_with_model()

    def test_calls_set_custom_attributes_with_view_values(self):
        self.view.model_name_jp_edit.text.return_value = "新しい名前"
        self.view.model_name_en_edit.text.return_value = "New Name"
        self.view.comment_jp_edit.toPlainText.return_value = "新しいコメント"
        self.view.comment_en_edit.toPlainText.return_value = "New Comment"

        with patch(f"{_MOD}.set_custom_attributes") as mock_set:
            self.presenter.update_model_info()

        mock_set.assert_called_once_with(
            TEST_MODEL,
            {
                ATTR_MMD_MODEL_NAME: "新しい名前",
                ATTR_MMD_MODEL_NAME_EN: "New Name",
                ATTR_MMD_COMMENT: "新しいコメント",
                ATTR_MMD_COMMENT_EN: "New Comment",
            },
        )

    def test_skips_when_no_model(self):
        self.app_state._current_model_root = None

        with patch(f"{_MOD}.set_custom_attributes") as mock_set:
            self.presenter.update_model_info()

        mock_set.assert_not_called()

    def test_header_namespaced_selection_drives_info_write_target(self):
        selected = "outer:model:root"
        header = HeaderWidget.__new__(HeaderWidget)
        header.app_state = self.app_state
        header.is_updating = False
        header.model_combo = Mock()
        header.model_combo.currentIndex.return_value = 0
        header.model_combo.itemData.return_value = selected
        HeaderWidget.on_combo_selection_changed(header, "Selected [outer:model:root]")

        self.view.model_name_jp_edit.text.return_value = "選択中"
        self.view.model_name_en_edit.text.return_value = "Selected"
        self.view.comment_jp_edit.toPlainText.return_value = ""
        self.view.comment_en_edit.toPlainText.return_value = ""

        with patch(f"{_MOD}.set_custom_attributes") as mock_set:
            self.presenter.update_model_info()

        mock_set.assert_called_once()
        self.assertEqual(mock_set.call_args[0][0], selected)


class TestCurrentModelChanged(unittest.TestCase):
    def setUp(self):
        self.presenter, self.view, self.app_state = _make_presenter_with_model()

    def test_enables_fields_and_loads_info_for_new_model(self):
        new_model = "new_test_model"
        self.app_state._current_model_root = new_model
        self.view.set_fields_enabled.reset_mock()
        self.view.model_name_jp_edit.setText.reset_mock()

        new_values = {
            f"{new_model}.{ATTR_MMD_MODEL_NAME}": "新モデル",
            f"{new_model}.{ATTR_MMD_MODEL_NAME_EN}": "",
            f"{new_model}.{ATTR_MMD_COMMENT}": "",
            f"{new_model}.{ATTR_MMD_COMMENT_EN}": "",
        }
        self.app_state.scene_model_service.attr_values = new_values
        self.presenter.on_current_model_changed(new_model)

        self.view.set_fields_enabled.assert_called_with(True)
        all_calls = [c[0][0] for c in self.view.model_name_jp_edit.setText.call_args_list]
        self.assertIn("新モデル", all_calls)

    def test_disables_fields_and_clears_for_none(self):
        self.view.set_fields_enabled.reset_mock()
        self.presenter.on_current_model_changed(None)

        self.view.set_fields_enabled.assert_called_with(False)
        self.view.model_name_jp_edit.clear.assert_called()


if __name__ == "__main__":
    unittest.main()
