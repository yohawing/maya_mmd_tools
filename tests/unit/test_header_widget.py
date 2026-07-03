"""HeaderWidget の再翻訳ロジックを headless に検証する。"""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

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

    def clear(self):
        self.items.clear()

    def addItem(self, text, userData=None):
        self.items.append([text, userData])

    def count(self):
        return len(self.items)

    def itemData(self, index):
        return self.items[index][1]

    def setItemText(self, index, text):
        self.items[index][0] = text


class _FakeAppState:
    current_model_root = None

    def get_model_info(self, _model):
        return None


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


if __name__ == "__main__":
    unittest.main()
