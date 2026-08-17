"""HeaderWidget の再翻訳ロジックを headless に検証する。"""

import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.components import header_widget as header_widget_module  # noqa: E402
from mmd_tools.ui.components.header_widget import HeaderWidget  # noqa: E402
from mmd_tools.ui.translations import UITranslator  # noqa: E402


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _FakeButton:
    def __init__(self):
        self.tooltip = ""

    def setToolTip(self, text):
        self.tooltip = text


class _FakeCombo:
    def __init__(self):
        self.items = []
        self._current_index = -1

    def clear(self):
        self.items.clear()
        self._current_index = -1

    def addItem(self, text, userData=None):
        self.items.append([text, userData])

    def count(self):
        return len(self.items)

    def itemData(self, index):
        return self.items[index][1]

    def setItemText(self, index, text):
        self.items[index][0] = text

    def currentIndex(self):
        return self._current_index

    def setCurrentIndex(self, index):
        self._current_index = index


class _FakeAppState:
    def __init__(self):
        self.current_model_root = None
        self.refresh_calls = 0
        self.selection_sync_calls = 0

    def get_model_info(self, _model):
        return None

    def refresh_model_list(self):
        self.refresh_calls += 1

    def select_model_from_maya_selection(self):
        self.selection_sync_calls += 1


class _ExplicitAppState(_FakeAppState):
    def __init__(self):
        super().__init__()
        self.explicit_calls = []
        self.refresh_generation = 0

    def refresh_model_list(self, explicit=False):
        self.explicit_calls.append(explicit)


class TestHeaderWidgetTranslation(unittest.TestCase):
    def setUp(self):
        self.translator = UITranslator.instance()
        self.previous_language = self.translator.get_language()
        self.widget = HeaderWidget.__new__(HeaderWidget)
        self.widget.app_state = _FakeAppState()
        self.widget._translator = self.translator
        self.widget.model_label = _FakeLabel()
        self.widget.refresh_btn = _FakeButton()
        self.widget.model_combo = _FakeCombo()
        self.widget.is_updating = False

    def tearDown(self):
        self.translator.set_language(self.previous_language)

    def test_retranslate_updates_header_fixed_text(self):
        self.translator.set_language("en")

        HeaderWidget.retranslateUi(self.widget)

        self.assertEqual(self.widget.model_label.text, "Current Model:")
        self.assertEqual(self.widget.refresh_btn.tooltip, "Refresh the list")

    def test_empty_model_placeholder_retranslates(self):
        self.translator.set_language("ja")
        HeaderWidget.on_model_list_updated(self.widget, [])
        self.assertEqual(self.widget.model_combo.items, [["MMDモデルが見つかりません", None]])

        self.translator.set_language("en")
        HeaderWidget.retranslateUi(self.widget)

        self.assertEqual(self.widget.model_combo.items, [["No MMD models found", None]])

    def test_refresh_updates_list_then_resyncs_maya_selection(self):
        HeaderWidget.refresh_model_list(self.widget)

        self.assertEqual(self.widget.app_state.refresh_calls, 1)
        self.assertEqual(self.widget.app_state.selection_sync_calls, 1)

    def test_explicit_refresh_does_not_resync_maya_selection(self):
        self.widget.app_state = _ExplicitAppState()

        HeaderWidget.refresh_model_list(self.widget)

        self.assertEqual(self.widget.app_state.explicit_calls, [True])
        self.assertEqual(self.widget.app_state.selection_sync_calls, 0)


class TestHeaderWidgetModelSelectionLogging(unittest.TestCase):
    """コンボ選択の副作用とログ境界を headless で検証する。"""

    @staticmethod
    def _call_messages(mock_method):
        """Python 3.7 互換: call_args_list から第1位置引数のメッセージを集める。"""
        messages = []
        for call in mock_method.call_args_list:
            args = call[0]
            if args:
                messages.append(args[0])
        return messages

    def setUp(self):
        self.widget = HeaderWidget.__new__(HeaderWidget)
        self.widget.app_state = _FakeAppState()
        self.widget.model_combo = _FakeCombo()
        self.widget.is_updating = False
        self.widget.model_combo.addItem("Model A [model_a_root]", userData="model_a_root")
        self.widget.model_combo.setCurrentIndex(0)

    def test_combo_selection_logs_at_debug_not_info(self):
        with patch.object(header_widget_module, "logger") as mock_logger:
            HeaderWidget.on_combo_selection_changed(self.widget, "Model A [model_a_root]")

        self.assertEqual(self.widget.app_state.current_model_root, "model_a_root")

        expected = "HeaderWidget: Model selected from combo: model_a_root"
        debug_messages = self._call_messages(mock_logger.debug)
        info_messages = self._call_messages(mock_logger.info)
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)


if __name__ == "__main__":
    unittest.main()
