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
    __test__ = False

    def test_pass(self):
        pass


class _FailingCase(unittest.TestCase):
    __test__ = False

    def test_fail(self):
        self.fail("expected failure")


class GuiTestRunnerTests(unittest.TestCase):
    def run_runner(self, discovered_suite=None, discover_error=None, test_filter=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "gui.log"
            with mock.patch.object(unittest.TestLoader, "discover", side_effect=discover_error or (lambda *args, **kwargs: discovered_suite)):
                with redirect_stdout(sys.__stdout__), redirect_stderr(sys.__stderr__):
                    status = GuiTestRunner.run_tests_from_command(str(log_path), "tests/gui", test_filter)
            return status, log_path.read_text(encoding="utf-8")

    def test_pass_status_is_encoded(self):
        status, log = self.run_runner(unittest.defaultTestLoader.loadTestsFromTestCase(_PassingCase))
        self.assertEqual("PASS", status)
        self.assertIn("//-- GUI TEST FINISHED --// status=PASS", log)
        self.assertRegex(log, r"\[GUI TEST\] START .*_PassingCase\.test_pass")
        self.assertRegex(log, r"\[GUI TEST\] END .*_PassingCase\.test_pass outcome=success")

    def test_failure_status_is_encoded(self):
        status, log = self.run_runner(unittest.defaultTestLoader.loadTestsFromTestCase(_FailingCase))
        self.assertEqual("FAIL", status)
        self.assertIn("//-- GUI TEST FINISHED --// status=FAIL", log)
        self.assertRegex(log, r"\[GUI TEST\] START .*_FailingCase\.test_fail")
        self.assertRegex(log, r"\[GUI TEST\] END .*_FailingCase\.test_fail outcome=failure")

    def test_no_tests_status_is_encoded(self):
        status, log = self.run_runner(unittest.TestSuite())
        self.assertEqual("NO_TESTS", status)
        self.assertIn("//-- GUI TEST FINISHED --// status=NO_TESTS", log)

    def test_filter_keeps_matching_test_ids(self):
        suite = unittest.TestSuite(
            [
                _PassingCase("test_pass"),
                _FailingCase("test_fail"),
            ]
        )
        status, log = self.run_runner(suite, test_filter="_PassingCase")
        self.assertEqual("PASS", status)
        self.assertIn("_PassingCase.test_pass", log)
        self.assertNotIn("_FailingCase.test_fail", log)

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

    def test_maya_python_readiness_accepts_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker_path = Path(temp_dir) / "ready.txt"
            marker_path.write_text("ready", encoding="utf-8")
            self.assertIsNone(run_gui_tests.wait_for_maya_python_ready(marker_path, timeout=0.1))

    def test_maya_python_readiness_fails_without_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker_path = Path(temp_dir) / "missing.txt"
            with self.assertRaisesRegex(TimeoutError, "readiness marker"):
                run_gui_tests.wait_for_maya_python_ready(marker_path, timeout=0.01)

    def test_explorer_cleanup_quits_without_process_handle(self):
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.object(run_gui_tests.maya_commandport, "maya_exe", return_value=Path("maya.exe")), \
             mock.patch.object(run_gui_tests.maya_commandport, "launch_maya", return_value=None) as launch, \
             mock.patch.object(run_gui_tests.maya_commandport, "ensure_port_available"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_maya_process_id", return_value=1234), \
                 mock.patch.object(run_gui_tests.maya_commandport, "send_python"), \
                 mock.patch.object(run_gui_tests, "wait_for_maya_python_ready"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "quit_maya") as quit_maya, \
             mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port_close") as wait_for_port_close, \
             mock.patch.object(run_gui_tests.maya_commandport, "wait_for_maya_process_exit", return_value=True) as wait_for_process_exit, \
             mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
             mock.patch.object(run_gui_tests, "monitor_log_file", return_value="PASS"), \
             mock.patch.object(run_gui_tests, "LOG_FILE_NAME", "unit_gui_runner_pass.log"), \
             mock.patch.object(sys, "argv", ["run_gui_tests.py"]):
            self.assertEqual(0, run_gui_tests.main())

        self.assertEqual("explorer", launch.call_args.kwargs["launch_mode"])
        maya_app_dir = Path(launch.call_args.kwargs["env_overrides"]["MAYA_APP_DIR"])
        self.assertTrue(maya_app_dir.is_absolute())
        self.assertFalse(maya_app_dir.exists())
        self.assertEqual(
            str(run_gui_tests._PROJECT_ROOT / "mmd_tools"),
            launch.call_args.kwargs["env_overrides"]["MAYA_PLUG_IN_PATH"],
        )
        quit_maya.assert_called_once_with(run_gui_tests.COMMAND_PORT)
        wait_for_port_close.assert_called_once_with(run_gui_tests.COMMAND_PORT, timeout=30)
        wait_for_process_exit.assert_called_once()
        process_exit_args, process_exit_kwargs = wait_for_process_exit.call_args
        self.assertEqual(1234, process_exit_args[0])
        self.assertEqual("commandport_7720.mel", process_exit_args[1].name)
        self.assertEqual(30, process_exit_kwargs["timeout"])

    def test_explorer_cleanup_terminates_only_the_owned_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "gui.log"
            with mock.patch.object(sys, "platform", "win32"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "maya_exe", return_value=Path("maya.exe")), \
                 mock.patch.object(run_gui_tests.maya_commandport, "launch_maya", return_value=None), \
                 mock.patch.object(run_gui_tests.maya_commandport, "ensure_port_available"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_maya_process_id", return_value=1234), \
                 mock.patch.object(run_gui_tests.maya_commandport, "send_python"), \
                 mock.patch.object(run_gui_tests, "wait_for_maya_python_ready"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "quit_maya"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port_close") as wait_for_port_close, \
                 mock.patch.object(
                     run_gui_tests.maya_commandport,
                     "wait_for_maya_process_exit",
                     return_value=False,
                 ) as wait_for_process_exit, \
                 mock.patch.object(
                     run_gui_tests.maya_commandport,
                     "terminate_maya_process",
                     return_value=True,
                 ) as terminate_process, \
                 mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
                 mock.patch.object(run_gui_tests, "monitor_log_file", return_value="PASS"), \
                 mock.patch.object(sys, "argv", ["run_gui_tests.py", "--log_path", str(log_path)]):
                self.assertEqual(0, run_gui_tests.main())

            commandport_script = log_path.parent / "commandport_7720.mel"
            wait_for_port_close.assert_called_once_with(run_gui_tests.COMMAND_PORT, timeout=30)
            wait_for_process_exit.assert_called_once_with(1234, commandport_script, timeout=30)
            terminate_process.assert_called_once_with(1234, commandport_script)

    def test_custom_log_path_is_forwarded_to_maya_and_monitor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_log_path = Path(temp_dir) / "nested" / "gui.log"
            with mock.patch.object(run_gui_tests.maya_commandport, "maya_exe", return_value=Path("maya.exe")), \
                 mock.patch.object(run_gui_tests.maya_commandport, "launch_maya", return_value=None) as launch, \
                 mock.patch.object(run_gui_tests.maya_commandport, "ensure_port_available"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_maya_process_id", return_value=None), \
                 mock.patch.object(run_gui_tests.maya_commandport, "send_python") as send_python, \
                 mock.patch.object(run_gui_tests, "wait_for_maya_python_ready"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "quit_maya"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port_close"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
                 mock.patch.object(run_gui_tests, "monitor_log_file", return_value="PASS") as monitor, \
                 mock.patch.object(sys, "argv", ["run_gui_tests.py", "--log_path", str(custom_log_path)]):
                self.assertEqual(0, run_gui_tests.main())

            resolved_log_path = custom_log_path.resolve()
            self.assertTrue(resolved_log_path.parent.is_dir())
            monitor.assert_called_once_with(resolved_log_path, run_gui_tests.TEST_EXECUTION_TIMEOUT)
            self.assertEqual(resolved_log_path.parent, launch.call_args.kwargs["output_dir"])
            self.assertIn(f"log_path = {str(resolved_log_path)!r}", send_python.call_args.args[1])

    def test_host_returns_one_for_completed_failure(self):
        with mock.patch.object(run_gui_tests.maya_commandport, "maya_exe", return_value=Path("maya.exe")), \
             mock.patch.object(run_gui_tests.maya_commandport, "launch_maya", return_value=None), \
             mock.patch.object(run_gui_tests.maya_commandport, "ensure_port_available"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_maya_process_id", return_value=None), \
                 mock.patch.object(run_gui_tests.maya_commandport, "send_python"), \
                 mock.patch.object(run_gui_tests, "wait_for_maya_python_ready"), \
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
             mock.patch.object(run_gui_tests.maya_commandport, "ensure_port_available"), \
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
