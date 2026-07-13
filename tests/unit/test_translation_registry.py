"""Unit tests for tab-local translation registry helpers."""

import unittest

from mmd_tools.ui.tabs.translation_registry import apply_translation_registry


class _FakeWidget:
    def __init__(self):
        self.text = None
        self.placeholder = None

    def setText(self, value):  # noqa: N802
        self.text = value

    def setPlaceholderText(self, value):  # noqa: N802
        self.placeholder = value


class _FakeTab:
    def __init__(self):
        self.label = _FakeWidget()
        self.search = _FakeWidget()

    def tr(self, key, category):
        return f"{category}.{key}"


class TestTranslationRegistry(unittest.TestCase):
    def test_apply_translation_registry_calls_requested_widget_setters(self):
        tab = _FakeTab()

        apply_translation_registry(
            tab,
            (
                ("label", "setText", "name", "fields"),
                ("search", "setPlaceholderText", "search_name", "placeholders"),
            ),
        )

        self.assertEqual(tab.label.text, "fields.name")
        self.assertEqual(tab.search.placeholder, "placeholders.search_name")


if __name__ == "__main__":
    unittest.main()
