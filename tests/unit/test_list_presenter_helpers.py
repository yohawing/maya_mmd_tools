"""Logging boundary tests for list_presenter_helpers."""

import unittest
from unittest.mock import MagicMock

from mmd_tools.ui.presenters.list_presenter_helpers import reload_for_current_model_change


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
