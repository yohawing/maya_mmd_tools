"""Focus protection must never change an unrelated Maya or user window."""

import unittest
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import Mock, patch

from tools.maya_gui import background_launch as launcher
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


class TestShutdown(unittest.TestCase):
    def test_already_exited_child_does_not_contact_port(self):
        process = Mock()
        process.poll.return_value = 0
        with patch.object(launcher.socket, "create_connection") as connect:
            launcher.stop_owned_process(process, 7738, Mock())
        connect.assert_not_called()
        process.terminate.assert_not_called()

    def test_graceful_exit_waits_without_termination_and_checks_pid(self):
        process = Mock(pid=1234)
        process.poll.return_value = None
        with patch.object(launcher.socket, "create_connection") as connect:
            launcher.stop_owned_process(process, 7738, Mock())
        payload = connect.return_value.__enter__.return_value.sendall.call_args.args[0]
        self.assertIn(b"os.getpid() == 1234", payload)
        process.wait.assert_called_once_with(timeout=30)
        process.terminate.assert_not_called()

    def test_timeout_terminates_and_waits_for_owned_handle_only(self):
        process = Mock(pid=1234)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("maya", 30), 1]
        with patch.object(launcher.socket, "create_connection", side_effect=OSError):
            launcher.stop_owned_process(process, 7738, Mock())
        process.terminate.assert_called_once_with()
        self.assertEqual([30, 10], [call.kwargs["timeout"] for call in process.wait.call_args_list])

    def test_owner_exit_interrupt_and_guard_error_cleanup_but_preview_survives(self):
        for keep_open, error in ((False, None), (False, KeyboardInterrupt()),
                                 (False, RuntimeError("guard")), (True, RuntimeError("guard"))):
            with self.subTest(keep_open=keep_open, error=error), tempfile.TemporaryDirectory() as out:
                config = {"output": out, "cwd": out, "env": {}, "command": ["maya"], "port": 7738}
                process = Mock(pid=1234)
                process.poll.return_value = None
                process.returncode = 0
                owner = Mock()
                owner.alive.return_value = error is not None
                with patch.object(launcher, "Windows"), \
                     patch.object(launcher.subprocess, "STARTUPINFO", return_value=Mock(dwFlags=0), create=True), \
                     patch.object(launcher.subprocess, "STARTF_USESHOWWINDOW", 1, create=True), \
                     patch.object(launcher.subprocess, "Popen", return_value=process), \
                     patch.object(launcher, "FocusGuard") as guard, \
                     patch.object(launcher, "stop_owned_process") as stop:
                    guard.return_value.tick.side_effect = error
                    if error is None:
                        launcher.run_child(config, Mock(), owner, keep_open)
                    else:
                        with self.assertRaises(type(error)):
                            launcher.run_child(config, Mock(), None if keep_open else owner, keep_open)
                    self.assertEqual(not keep_open, stop.called)
                    if stop.called:
                        self.assertIs(process, stop.call_args.args[0])

    def test_preview_does_not_monitor_owner_and_legacy_config_does_not_launch(self):
        with tempfile.TemporaryDirectory() as out:
            config_path = Path(out) / "config.json"
            for keep_open in (False, True):
                config_path.write_text(json.dumps({"output": out, "keep_open": keep_open}))
                with patch.object(launcher, "OwnerProcess") as owner, \
                     patch.object(launcher, "run_child") as run:
                    if keep_open:
                        launcher.main(config_path)
                        self.assertEqual((None, True), run.call_args.args[2:])
                    else:
                        with self.assertRaises(KeyError):
                            launcher.main(config_path)
                        run.assert_not_called()
                    owner.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows process handles")
    def test_real_owner_handle_observes_exit_and_cleanup_spares_unrelated_process(self):
        children = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
                    for _ in range(2)]
        owner = launcher.OwnerProcess(children[0].pid)
        try:
            self.assertTrue(owner.alive())
            with patch.object(launcher.socket, "create_connection", side_effect=OSError):
                launcher.stop_owned_process(children[0], 7738, Mock(), timeout=0.01)
            self.assertFalse(owner.alive())
            self.assertIsNone(children[1].poll())
            self.assertIsNotNone(children[0].returncode)
        finally:
            owner.close()
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
