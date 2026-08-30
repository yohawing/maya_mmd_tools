"""Logging boundary tests for list_presenter_helpers."""

import unittest
from unittest.mock import MagicMock

from mmd_tools.ui.presenters.list_presenter_helpers import (
    format_indexed_name_label,
    format_indexed_node_label,
    maya_node_leaf_name,
    reload_for_current_model_change,
)
from mmd_tools.ui.translations import UITranslator


class TestNodeListLabels(unittest.TestCase):
    """Material/Bone list labels hide Maya qualification consistently."""

    def setUp(self):
        self.translator = UITranslator.instance()
        self.previous_language = self.translator.get_language()
        self.translator.set_language("ja")

    def tearDown(self):
        self.translator.set_language(self.previous_language)

    def test_leaf_name_removes_dag_path_and_nested_namespace(self):
        cases = {
            "model:node": "node",
            "outer:model:node": "node",
            "|root|model:node": "node",
            "plain_node": "plain_node",
        }
        for node, expected in cases.items():
            with self.subTest(node=node):
                self.assertEqual(maya_node_leaf_name(node), expected)

    def test_indexed_label_uses_leaf_and_keeps_pmx_names(self):
        self.assertEqual(
            format_indexed_node_label(
                0,
                "操作中心",
                "|root|Sangonomiya_Kokomi:manipulation_center",
                "Manipulation Center",
            ),
            "0:操作中心（manipulation_center） [Manipulation Center]",
        )

    def test_non_node_label_uses_the_same_index_and_english_name_style(self):
        self.assertEqual(
            format_indexed_name_label(2, "笑顔", "Smile", prefix="V|"),
            "2:V|笑顔 [Smile]",
        )
        self.assertEqual(format_indexed_name_label("-", "", "Blink"), "-:Blink")

    def test_same_leaf_nodes_remain_distinguishable_by_index(self):
        first = format_indexed_node_label(3, "材質", "model_a:body", "")
        second = format_indexed_node_label(9, "材質", "model_b:body", "")
        self.assertNotEqual(first, second)
        self.assertIn("（body）", first)
        self.assertIn("（body）", second)

    def test_english_ui_prefers_english_and_never_falls_back_to_japanese(self):
        self.translator.set_language("en")
        try:
            self.assertEqual(
                format_indexed_node_label(2, "左腕", "|root|left_arm", "Left Arm"),
                "2:Left Arm (left_arm)",
            )
            self.assertEqual(
                format_indexed_node_label(3, "右腕", "|root|right_arm", ""),
                "3:right_arm",
            )
            self.assertEqual(
                format_indexed_name_label(4, "笑顔", "", fallback="Morph 4"),
                "4:Morph 4",
            )
            self.assertEqual(
                format_indexed_name_label(5, "blink", ""),
                "5:blink",
            )
        finally:
            self.translator.set_language("ja")


def _msgs(mock_log):
    # call[0] is args tuple (Py3.7-safe; _Call.args is 3.8+).
    return [c[0][0] for c in mock_log.call_args_list if c[0]]


class TestReloadForCurrentModelChangeLogging(unittest.TestCase):
    """Current-model-changed callback detail is DEBUG, not INFO."""

    def test_model_change_detail_is_debug_not_info(self):
        logger = MagicMock()
        reload_cb = MagicMock()

        reload_for_current_model_change(logger, "MorphPresenter", "model_root", reload_cb)

        reload_cb.assert_called_once_with()
        expected = "%s: Current model changed to %s"
        self.assertIn(expected, _msgs(logger.debug))
        self.assertNotIn(expected, _msgs(logger.info))
        # Format args are preserved for the DEBUG call.
        debug_call = logger.debug.call_args
        self.assertEqual(debug_call[0][1], "MorphPresenter")
        self.assertEqual(debug_call[0][2], "model_root")


if __name__ == "__main__":
    unittest.main()
