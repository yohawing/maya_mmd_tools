"""Pure-Python coverage for the GUI commandPort runner protocol."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock


def _install_maya_stub() -> None:
    maya_module = types.ModuleType("maya")
    cmds_module = types.ModuleType("maya.cmds")
    maya_module.cmds = cmds_module
    sys.modules.setdefault("maya", maya_module)
    sys.modules.setdefault("maya.cmds", cmds_module)


_install_maya_stub()

from tests import run_gui_tests
from tests.common.gui_test_base import GuiTestRunner


class _PassingCase(unittest.TestCase):
    def test_pass(self):
        pass


class _FailingCase(unittest.TestCase):
    def test_fail(self):
        self.fail("expected failure")


class GuiTestRunnerTests(unittest.TestCase):
    def run_runner(self, discovered_suite=None, discover_error=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "gui.log"
            with mock.patch.object(unittest.TestLoader, "discover", side_effect=discover_error or (lambda *args, **kwargs: discovered_suite)):
                with redirect_stdout(sys.__stdout__), redirect_stderr(sys.__stderr__):
                    status = GuiTestRunner.run_tests_from_command(str(log_path), "tests/gui")
            return status, log_path.read_text(encoding="utf-8")

    def test_pass_status_is_encoded(self):
        status, log = self.run_runner(unittest.defaultTestLoader.loadTestsFromTestCase(_PassingCase))
        self.assertEqual("PASS", status)
        self.assertIn("//-- GUI TEST FINISHED --// status=PASS", log)

    def test_failure_status_is_encoded(self):
        status, log = self.run_runner(unittest.defaultTestLoader.loadTestsFromTestCase(_FailingCase))
        self.assertEqual("FAIL", status)
        self.assertIn("//-- GUI TEST FINISHED --// status=FAIL", log)

    def test_no_tests_status_is_encoded(self):
        status, log = self.run_runner(unittest.TestSuite())
        self.assertEqual("NO_TESTS", status)
        self.assertIn("//-- GUI TEST FINISHED --// status=NO_TESTS", log)

    def test_exception_status_is_encoded(self):
        status, log = self.run_runner(discover_error=RuntimeError("discover failed"))
        self.assertEqual("ERROR", status)
        self.assertIn("//-- GUI TEST FINISHED --// status=ERROR", log)

    def test_completion_parser_requires_known_exact_status(self):
        self.assertEqual("PASS", run_gui_tests.parse_completion_status("//-- GUI TEST FINISHED --// status=PASS\n"))
        self.assertIsNone(run_gui_tests.parse_completion_status("//-- GUI TEST FINISHED --//"))
        self.assertIsNone(run_gui_tests.parse_completion_status("//-- GUI TEST FINISHED --// status=UNKNOWN"))

    def test_monitor_reads_completion_written_before_open(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "gui.log"
            log_path.write_text(
                "//-- GUI TEST FINISHED --// status=NO_TESTS\n",
                encoding="utf-8",
            )
            self.assertEqual(
                "NO_TESTS",
                run_gui_tests.monitor_log_file(log_path, timeout=0.1),
            )

    def test_explorer_cleanup_quits_without_process_handle(self):
        with mock.patch.object(run_gui_tests.maya_commandport, "maya_exe", return_value=Path("maya.exe")), \
             mock.patch.object(run_gui_tests.maya_commandport, "launch_maya", return_value=None) as launch, \
             mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port"), \
             mock.patch.object(run_gui_tests.maya_commandport, "send_python"), \
             mock.patch.object(run_gui_tests.maya_commandport, "quit_maya") as quit_maya, \
             mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port_close") as wait_for_port_close, \
             mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
             mock.patch.object(run_gui_tests, "monitor_log_file", return_value="PASS"), \
             mock.patch.object(run_gui_tests, "LOG_FILE_NAME", "unit_gui_runner_pass.log"), \
             mock.patch.object(sys, "argv", ["run_gui_tests.py"]):
            self.assertEqual(0, run_gui_tests.main())

        self.assertEqual("explorer" if sys.platform == "win32" else "direct", launch.call_args.kwargs["launch_mode"])
        maya_app_dir = Path(launch.call_args.kwargs["env_overrides"]["MAYA_APP_DIR"])
        self.assertTrue(maya_app_dir.is_absolute())
        self.assertFalse(maya_app_dir.exists())
        quit_maya.assert_called_once_with(run_gui_tests.COMMAND_PORT)
        wait_for_port_close.assert_called_once_with(run_gui_tests.COMMAND_PORT, timeout=30)

    def test_host_returns_one_for_completed_failure(self):
        with mock.patch.object(run_gui_tests.maya_commandport, "maya_exe", return_value=Path("maya.exe")), \
             mock.patch.object(run_gui_tests.maya_commandport, "launch_maya", return_value=None), \
             mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port"), \
             mock.patch.object(run_gui_tests.maya_commandport, "send_python"), \
             mock.patch.object(run_gui_tests.maya_commandport, "quit_maya"), \
             mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port_close"), \
             mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
             mock.patch.object(run_gui_tests, "monitor_log_file", return_value="FAIL"), \
             mock.patch.object(run_gui_tests, "LOG_FILE_NAME", "unit_gui_runner_fail.log"), \
             mock.patch.object(sys, "argv", ["run_gui_tests.py"]):
            self.assertEqual(1, run_gui_tests.main())

    def test_startup_failure_removes_profile_after_direct_process_exits(self):
        process = mock.MagicMock()
        process.poll.return_value = 1
        with mock.patch.object(run_gui_tests.maya_commandport, "maya_exe", return_value=Path("maya.exe")), \
             mock.patch.object(run_gui_tests.maya_commandport, "launch_maya", return_value=process) as launch, \
             mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port", side_effect=RuntimeError("startup failed")), \
             mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
             mock.patch.object(run_gui_tests, "LOG_FILE_NAME", "unit_gui_runner_startup_failure.log"), \
             mock.patch.object(sys, "argv", ["run_gui_tests.py"]):
            self.assertEqual(1, run_gui_tests.main())

        maya_app_dir = Path(launch.call_args.kwargs["env_overrides"]["MAYA_APP_DIR"])
        self.assertFalse(maya_app_dir.exists())
        process.kill.assert_not_called()


def load_tests(loader, tests, pattern):
    """Keep helper TestCases out of this module's own unittest discovery."""
    return loader.loadTestsFromTestCase(GuiTestRunnerTests)
