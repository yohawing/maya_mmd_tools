"""Focus protection must never change an unrelated Maya or user window."""

import unittest
from unittest.mock import Mock

from tools.maya_gui.background_launch import FocusGuard


class TestFocusGuard(unittest.TestCase):
    def setUp(self):
        self.windows = Mock()
        self.owners = {10: 100, 11: 100, 20: 200, 30: 300}
        self.windows.pid.side_effect = lambda hwnd: self.owners.get(hwnd, 0)
        self.windows.windows.return_value = [10, 11, 20, 30]
        self.windows.foreground.return_value = 20
        self.record = Mock()
        self.guard = FocusGuard(self.windows, 100, 20, self.record)

    def test_only_owned_windows_are_lowered_once(self):
        self.guard.tick()
        self.guard.tick()
        self.assertEqual({10, 11}, {call.args[0] for call in self.windows.background.call_args_list})
        self.assertEqual(2, self.windows.background.call_count)
        self.windows.restore.assert_not_called()

    def test_takeover_restores_latest_user_window_not_initial_window(self):
        self.windows.foreground.return_value = 30
        self.guard.tick()
        self.windows.foreground.return_value = 10
        self.windows.restore.return_value = False
        self.guard.tick()
        self.windows.restore.assert_called_once_with(30)
        self.record.assert_called_with("focus_takeover", hwnd=10, restored=False)

    def test_closed_user_window_is_not_restored(self):
        del self.owners[20]
        self.windows.foreground.return_value = 10
        self.guard.tick()
        self.windows.restore.assert_not_called()

    def test_transient_owned_window_disappears(self):
        def close(hwnd):
            del self.owners[hwnd]
            raise OSError("window closed")

        self.windows.background.side_effect = close
        self.guard.tick()
        self.assertFalse(self.guard.prepared)

    def test_failure_on_live_owned_window_is_not_silenced(self):
        self.windows.background.side_effect = OSError("access denied")
        with self.assertRaisesRegex(OSError, "access denied"):
            self.guard.tick()


if __name__ == "__main__":
    unittest.main()
