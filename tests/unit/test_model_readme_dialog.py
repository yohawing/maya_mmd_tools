"""Focused unit tests for model-readme extraction and display policy."""

import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.constants import ATTR_MMD_COMMENT, ATTR_MMD_COMMENT_EN  # noqa: E402
from mmd_tools.ui.model_readme_dialog import (  # noqa: E402
    ModelReadme,
    ModelReadmeDialogAdapter,
    read_model_readme,
)


class _SceneService:
    def __init__(self, japanese="", english=""):
        self.values = {
            ATTR_MMD_COMMENT: japanese,
            ATTR_MMD_COMMENT_EN: english,
        }

    def get_attr_safe(self, _root, attr, default=None):
        return self.values.get(attr, default)


class TestModelReadmeExtraction(unittest.TestCase):
    def test_reads_japanese_only_without_normalizing_content(self):
        readme = read_model_readme(_SceneService("  日本語\n", ""), "model_root")

        self.assertEqual(readme.japanese, "  日本語\n")
        self.assertEqual(readme.english, "")
        self.assertIn("Japanese (JP):\n  日本語\n", readme.to_plain_text())

    def test_reads_english_only(self):
        readme = read_model_readme(_SceneService("", "English readme"), "model_root")

        self.assertEqual(readme.english, "English readme")
        self.assertNotIn("Japanese (JP)", readme.to_plain_text())

    def test_keeps_japanese_and_english_distinct(self):
        readme = read_model_readme(_SceneService("日本語", "English"), "model_root")

        self.assertEqual(readme.to_plain_text(), "Japanese (JP):\n日本語\n\nEnglish (EN):\nEnglish")

    def test_whitespace_only_comments_are_ignored(self):
        self.assertIsNone(read_model_readme(_SceneService(" \n\t", "  "), "model_root"))

    def test_missing_root_or_service_is_ignored(self):
        self.assertIsNone(read_model_readme(_SceneService("comment"), None))
        self.assertIsNone(read_model_readme(None, "model_root"))


class TestModelReadmeDialogPolicy(unittest.TestCase):
    def setUp(self):
        self.readme = ModelReadme(japanese="日本語", english="English")

    def test_development_mode_suppresses_dialog(self):
        adapter = ModelReadmeDialogAdapter(
            development_mode_getter=lambda: True,
            batch_getter=lambda: False,
        )

        with patch("mmd_tools.ui.qt_compat.QDialog") as dialog:
            self.assertFalse(adapter.show(self.readme))
            dialog.assert_not_called()

    def test_batch_suppresses_dialog_even_when_cmds_stub_is_truthy(self):
        adapter = ModelReadmeDialogAdapter(
            development_mode_getter=lambda: False,
            batch_getter=lambda: True,
        )

        with patch("mmd_tools.ui.qt_compat.QDialog") as dialog:
            self.assertFalse(adapter.show(self.readme))
            dialog.assert_not_called()

    def test_explicit_skip_suppresses_dialog_without_settings_changes(self):
        adapter = ModelReadmeDialogAdapter(enabled=False)

        with patch("mmd_tools.ui.qt_compat.QDialog") as dialog:
            self.assertFalse(adapter.show(self.readme))
            dialog.assert_not_called()

    def test_default_adapter_builds_selectable_read_only_plain_text_dialog(self):
        adapter = ModelReadmeDialogAdapter(
            development_mode_getter=lambda: False,
            batch_getter=lambda: False,
        )

        with patch("mmd_tools.ui.qt_compat.QDialog") as dialog_cls, patch(
            "mmd_tools.ui.qt_compat.QVBoxLayout"
        ), patch("mmd_tools.ui.qt_compat.QLabel"), patch(
            "mmd_tools.ui.qt_compat.QTextEdit"
        ) as text_cls, patch("mmd_tools.ui.qt_compat.QPushButton"):
            self.assertTrue(adapter.show(self.readme, model_path="model.pmx"))

        text_cls.return_value.setReadOnly.assert_called_once_with(True)
        text_cls.return_value.setPlainText.assert_called_once_with(self.readme.to_plain_text())
        dialog_cls.return_value.exec.assert_called_once()


if __name__ == "__main__":
    unittest.main()
