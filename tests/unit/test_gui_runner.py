"""Pure-Python coverage for the GUI commandPort runner protocol."""

from __future__ import annotations

import io
import sys
import tempfile
import json
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

    def run_runner_with_timing(self, discovered_suite=None, discover_error=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "gui.log"
            timing_path = Path(temp_dir) / "gui.timing.json"
            report = run_gui_tests.new_timing_report("2024", "tests/gui", None)
            report["phases"]["startup"] = {"status": "passed", "elapsed_seconds": 1.0}
            run_gui_tests.write_timing_report(timing_path, report)
            with mock.patch.object(
                unittest.TestLoader,
                "discover",
                side_effect=discover_error or (lambda *args, **kwargs: discovered_suite),
            ):
                with redirect_stdout(sys.__stdout__), redirect_stderr(sys.__stderr__):
                    status = GuiTestRunner.run_tests_from_command(
                        str(log_path),
                        "tests/gui",
                        None,
                        str(timing_path),
                    )
            return status, json.loads(timing_path.read_text(encoding="utf-8"))

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

    def test_timing_report_records_discovery_and_failed_test(self):
        status, report = self.run_runner_with_timing(
            unittest.defaultTestLoader.loadTestsFromTestCase(_FailingCase)
        )

        self.assertEqual("FAIL", status)
        self.assertEqual(1, report["schema_version"])
        self.assertEqual("passed", report["phases"]["startup"]["status"])
        self.assertEqual("passed", report["phases"]["discovery"]["status"])
        self.assertEqual("failed", report["phases"]["tests"]["status"])
        self.assertEqual("failure", report["tests"][0]["status"])
        self.assertGreaterEqual(report["tests"][0]["elapsed_seconds"], 0.0)

    def test_timing_recorder_preserves_not_run_tests(self):
        from tests.common.gui_test_base import _TestTimingRecorder

        test = _PassingCase("test_pass")
        recorder = _TestTimingRecorder([test.id(), "second"])
        recorder.start_test(test)
        recorder.finish_test(test, "success")

        self.assertEqual("success", recorder.tests[0]["status"])
        self.assertEqual("not_run", recorder.tests[1]["status"])
        self.assertIsNone(recorder.tests[1]["elapsed_seconds"])

    def test_failure_error_and_skip_timing_finish_after_teardown(self):
        from tests.common.gui_test_base import (
            _LifecycleTextTestResult,
            _TestTimingRecorder,
        )

        for outcome in ("failure", "error", "skipped"):
            state = {"torn_down": False, "finished_after_teardown": False}

            class TimedCase(unittest.TestCase):
                def tearDown(self):
                    state["torn_down"] = True

                def test_outcome(self):
                    if outcome == "failure":
                        self.fail("expected failure")
                    if outcome == "error":
                        raise RuntimeError("expected error")
                    self.skipTest("expected skip")

            class Recorder(_TestTimingRecorder):
                def finish_test(self, test, outcome=None):
                    state["finished_after_teardown"] = state["torn_down"]
                    super().finish_test(test, outcome)

            suite = unittest.defaultTestLoader.loadTestsFromTestCase(TimedCase)
            test_id = next(iter(suite)).id()
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(TimedCase)
            recorder = Recorder([test_id])
            runner = unittest.TextTestRunner(
                stream=io.StringIO(),
                resultclass=lambda *args, **kwargs: _LifecycleTextTestResult(
                    *args,
                    timing_recorder=recorder,
                    **kwargs,
                ),
            )

            runner.run(suite)

            self.assertTrue(state["finished_after_teardown"], outcome)
            self.assertEqual(outcome, recorder.tests[0]["status"])
            self.assertGreaterEqual(recorder.tests[0]["elapsed_seconds"], 0.0)

    def test_failing_subtest_records_parent_failure_after_teardown(self):
        from tests.common.gui_test_base import (
            _LifecycleTextTestResult,
            _TestTimingRecorder,
        )

        state = {"torn_down": False}

        class SubTestCase(unittest.TestCase):
            def tearDown(self):
                state["torn_down"] = True

            def test_subtest(self):
                with self.subTest(case="failure"):
                    self.fail("expected subtest failure")

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SubTestCase)
        test_id = next(iter(suite)).id()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SubTestCase)
        recorder = _TestTimingRecorder([test_id])
        runner = unittest.TextTestRunner(
            stream=io.StringIO(),
            resultclass=lambda *args, **kwargs: _LifecycleTextTestResult(
                *args,
                timing_recorder=recorder,
                **kwargs,
            ),
        )

        result = runner.run(suite)

        self.assertFalse(result.wasSuccessful())
        self.assertTrue(state["torn_down"])
        self.assertEqual("failure", recorder.tests[0]["status"])
        self.assertGreaterEqual(recorder.tests[0]["elapsed_seconds"], 0.0)

    def test_skipped_subtest_updates_only_parent_inventory_entry(self):
        from tests.common.gui_test_base import (
            _LifecycleTextTestResult,
            _TestTimingRecorder,
        )

        class SubTestCase(unittest.TestCase):
            def test_subtest(self):
                with self.subTest(case="skip"):
                    self.skipTest("expected subtest skip")

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SubTestCase)
        test_id = next(iter(suite)).id()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SubTestCase)
        recorder = _TestTimingRecorder([test_id])
        runner = unittest.TextTestRunner(
            stream=io.StringIO(),
            resultclass=lambda *args, **kwargs: _LifecycleTextTestResult(
                *args,
                timing_recorder=recorder,
                **kwargs,
            ),
        )

        result = runner.run(suite)

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(1, len(recorder.tests))
        self.assertEqual(test_id, recorder.tests[0]["id"])
        self.assertEqual("skipped", recorder.tests[0]["status"])
        self.assertGreaterEqual(recorder.tests[0]["elapsed_seconds"], 0.0)

    def test_subtest_failure_is_not_overwritten_by_later_skip(self):
        from tests.common.gui_test_base import (
            _LifecycleTextTestResult,
            _TestTimingRecorder,
        )

        class SubTestCase(unittest.TestCase):
            def test_subtests(self):
                with self.subTest(case="failure"):
                    self.fail("expected subtest failure")
                with self.subTest(case="skip"):
                    self.skipTest("expected subtest skip")

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SubTestCase)
        test_id = next(iter(suite)).id()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SubTestCase)
        recorder = _TestTimingRecorder([test_id])
        runner = unittest.TextTestRunner(
            stream=io.StringIO(),
            resultclass=lambda *args, **kwargs: _LifecycleTextTestResult(
                *args,
                timing_recorder=recorder,
                **kwargs,
            ),
        )

        result = runner.run(suite)

        self.assertFalse(result.wasSuccessful())
        self.assertEqual("failure", recorder.tests[0]["status"])

    def test_slowest_tests_are_limited_sorted_and_exclude_not_run(self):
        report = run_gui_tests.new_timing_report("2024", "tests/gui", None)
        report["tests"] = [
            {"id": f"test_{index:02d}", "status": "success", "elapsed_seconds": float(index)}
            for index in range(25)
        ]
        report["tests"].append(
            {"id": "test_not_run", "status": "not_run", "elapsed_seconds": None}
        )

        run_gui_tests.finalize_timing_report(report)

        self.assertEqual(20, len(report["slowest_tests"]))
        self.assertEqual("test_24", report["slowest_tests"][0]["id"])
        self.assertEqual("test_05", report["slowest_tests"][-1]["id"])
        self.assertEqual({"success": 25, "not_run": 1}, report["test_counts"])

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

    def test_discovery_failure_leaves_tests_blocked(self):
        status, report = self.run_runner_with_timing(
            discover_error=RuntimeError("discover failed")
        )

        self.assertEqual("ERROR", status)
        self.assertEqual("failed", report["phases"]["discovery"]["status"])
        self.assertEqual("blocked", report["phases"]["tests"]["status"])
        self.assertEqual([], report["tests"])

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

    def test_maya_python_readiness_timeout_allows_modern_cold_start(self):
        self.assertEqual(
            run_gui_tests.TEST_EXECUTION_TIMEOUT,
            run_gui_tests.maya_python_ready_timeout("2026"),
        )
        self.assertEqual(
            run_gui_tests.TEST_EXECUTION_TIMEOUT,
            run_gui_tests.maya_python_ready_timeout("2027"),
        )
        self.assertEqual(
            run_gui_tests.TEST_EXECUTION_TIMEOUT,
            run_gui_tests.maya_python_ready_timeout("2026.2"),
        )
        self.assertEqual(
            run_gui_tests.MAYA_PYTHON_READY_TIMEOUT,
            run_gui_tests.maya_python_ready_timeout("2024"),
        )

    def test_attach_handshake_accepts_matching_nonce_and_maya_major(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "attach.json"

            def respond(port, _code, label):
                self.assertEqual(7788, port)
                self.assertEqual("<maya-gui-attach-handshake>", label)
                marker.write_text(
                    json.dumps(
                        {
                            "protocol": run_gui_tests.ATTACH_HANDSHAKE_PROTOCOL,
                            "token": "owned-token",
                            "maya_major": "2024",
                        }
                    ),
                    encoding="utf-8",
                )

            with mock.patch.object(
                run_gui_tests.maya_commandport,
                "send_python",
                side_effect=respond,
            ):
                run_gui_tests.verify_attached_maya(
                    7788,
                    marker,
                    "2024.2",
                    token="owned-token",
                )

    def test_attach_handshake_rejects_an_unowned_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "attach.json"

            def respond(_port, _code, label=None):
                self.assertEqual("<maya-gui-attach-handshake>", label)
                marker.write_text(
                    json.dumps(
                        {
                            "protocol": run_gui_tests.ATTACH_HANDSHAKE_PROTOCOL,
                            "token": "different-owner",
                            "maya_major": "2024",
                        }
                    ),
                    encoding="utf-8",
                )

            with mock.patch.object(
                run_gui_tests.maya_commandport,
                "send_python",
                side_effect=respond,
            ), self.assertRaisesRegex(RuntimeError, "ownership response did not match"):
                run_gui_tests.verify_attached_maya(
                    7788,
                    marker,
                    "2024",
                    token="owned-token",
                )

    def test_attach_handshake_rejects_a_different_maya_major(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "attach.json"

            def respond(_port, _code, label=None):
                marker.write_text(
                    json.dumps(
                        {
                            "protocol": run_gui_tests.ATTACH_HANDSHAKE_PROTOCOL,
                            "token": "owned-token",
                            "maya_major": "2025",
                        }
                    ),
                    encoding="utf-8",
                )

            with mock.patch.object(
                run_gui_tests.maya_commandport,
                "send_python",
                side_effect=respond,
            ), self.assertRaisesRegex(RuntimeError, "ownership response did not match"):
                run_gui_tests.verify_attached_maya(
                    7788,
                    marker,
                    "2024",
                    token="owned-token",
                )

    def test_attach_existing_dispatches_tests_without_launch_or_shutdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "attached.log"
            timing_path = Path(temp_dir) / "attached.timing.json"
            with mock.patch.object(run_gui_tests.maya_commandport, "is_port_open", return_value=True), \
                 mock.patch.object(run_gui_tests, "verify_attached_maya") as verify, \
                 mock.patch.object(run_gui_tests.maya_commandport, "send_python") as send_python, \
                 mock.patch.object(run_gui_tests.maya_commandport, "launch_maya") as launch, \
                 mock.patch.object(run_gui_tests.maya_commandport, "quit_maya") as quit_maya, \
                 mock.patch.object(run_gui_tests.maya_commandport, "terminate_maya_process") as terminate, \
                 mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
                 mock.patch.object(run_gui_tests, "monitor_log_file", return_value="PASS"), \
                 mock.patch.object(
                     sys,
                     "argv",
                     [
                         "run_gui_tests.py",
                         "--attach-existing",
                         "--port",
                         "7788",
                         "--log_path",
                         str(log_path),
                         "--timing_report",
                         str(timing_path),
                     ],
                 ):
                self.assertEqual(0, run_gui_tests.main())

            verify.assert_called_once()
            self.assertEqual(7788, verify.call_args.args[0])
            self.assertTrue(any(call.args[0] == 7788 for call in send_python.call_args_list))
            launch.assert_not_called()
            quit_maya.assert_not_called()
            terminate.assert_not_called()
            report = json.loads(timing_path.read_text(encoding="utf-8"))
            self.assertEqual("PASS", report["status"])
            self.assertEqual("passed", report["phases"]["startup"]["status"])
            self.assertEqual("skipped", report["phases"]["shutdown"]["status"])

    def test_attach_existing_rejects_closed_port_without_lifecycle_actions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "closed.log"
            with mock.patch.object(run_gui_tests.maya_commandport, "is_port_open", return_value=False), \
                 mock.patch.object(run_gui_tests, "verify_attached_maya") as verify, \
                 mock.patch.object(run_gui_tests.maya_commandport, "send_python") as send_python, \
                 mock.patch.object(run_gui_tests.maya_commandport, "launch_maya") as launch, \
                 mock.patch.object(run_gui_tests.maya_commandport, "quit_maya") as quit_maya, \
                 mock.patch.object(run_gui_tests.maya_commandport, "terminate_maya_process") as terminate, \
                 mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
                 mock.patch.object(
                     sys,
                     "argv",
                     [
                         "run_gui_tests.py",
                         "--attach-existing",
                         "--port",
                         "7788",
                         "--log_path",
                         str(log_path),
                     ],
                 ):
                self.assertEqual(1, run_gui_tests.main())

            verify.assert_not_called()
            send_python.assert_not_called()
            launch.assert_not_called()
            quit_maya.assert_not_called()
            terminate.assert_not_called()

    def test_attach_timeout_preserves_external_maya_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "timeout.log"
            timing_path = Path(temp_dir) / "timeout.timing.json"
            with mock.patch.object(run_gui_tests.maya_commandport, "is_port_open", return_value=True), \
                 mock.patch.object(run_gui_tests, "verify_attached_maya"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "send_python"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "quit_maya") as quit_maya, \
                 mock.patch.object(run_gui_tests.maya_commandport, "terminate_maya_process") as terminate, \
                 mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
                 mock.patch.object(
                     run_gui_tests,
                     "monitor_log_file",
                     side_effect=TimeoutError("attached timeout"),
                 ), \
                 mock.patch.object(
                     sys,
                     "argv",
                     [
                         "run_gui_tests.py",
                         "--attach-existing",
                         "--port",
                         "7788",
                         "--log_path",
                         str(log_path),
                         "--timing_report",
                         str(timing_path),
                     ],
                 ):
                self.assertEqual(1, run_gui_tests.main())

            quit_maya.assert_not_called()
            terminate.assert_not_called()
            report = json.loads(timing_path.read_text(encoding="utf-8"))
            self.assertEqual("TIMEOUT", report["status"])
            self.assertEqual("skipped", report["phases"]["shutdown"]["status"])

    def test_attach_existing_rejects_a_process_environment_override(self):
        with mock.patch.object(
            sys,
            "argv",
            [
                "run_gui_tests.py",
                "--attach-existing",
                "--vp2_device_override",
                "VirtualDeviceDx11",
            ],
        ), self.assertRaises(SystemExit) as raised, redirect_stderr(io.StringIO()):
            run_gui_tests.main()

        self.assertEqual(2, raised.exception.code)

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
            default_timing_path = resolved_log_path.with_suffix(".timing.json")
            maya_timing_path = default_timing_path.with_name(
                default_timing_path.name + ".maya-partial"
            )
            self.assertTrue(default_timing_path.is_file())
            self.assertIn(
                f"timing_report_path = {str(maya_timing_path)!r}",
                send_python.call_args.args[1],
            )

    def test_custom_timing_report_path_is_forwarded_and_finalized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "gui.log"
            timing_path = Path(temp_dir) / "reports" / "timing.json"
            with mock.patch.object(run_gui_tests.maya_commandport, "maya_exe", return_value=Path("maya.exe")), \
                 mock.patch.object(run_gui_tests.maya_commandport, "launch_maya", return_value=None), \
                 mock.patch.object(run_gui_tests.maya_commandport, "ensure_port_available"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_maya_process_id", return_value=None), \
                 mock.patch.object(run_gui_tests.maya_commandport, "send_python") as send_python, \
                 mock.patch.object(run_gui_tests, "wait_for_maya_python_ready"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "quit_maya"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port_close"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
                 mock.patch.object(run_gui_tests, "monitor_log_file", return_value="PASS"), \
                 mock.patch.object(
                     sys,
                     "argv",
                     [
                         "run_gui_tests.py",
                         "--log_path",
                         str(log_path),
                         "--timing_report",
                         str(timing_path),
                     ],
                 ):
                self.assertEqual(0, run_gui_tests.main())

            report = json.loads(timing_path.read_text(encoding="utf-8"))
            self.assertEqual("PASS", report["status"])
            self.assertEqual("passed", report["phases"]["startup"]["status"])
            self.assertEqual("blocked", report["phases"]["discovery"]["status"])
            self.assertEqual("blocked", report["phases"]["tests"]["status"])
            self.assertEqual("passed", report["phases"]["shutdown"]["status"])
            maya_timing_path = timing_path.resolve().with_name(
                timing_path.name + ".maya-partial"
            )
            self.assertIn(
                f"timing_report_path = {str(maya_timing_path)!r}",
                send_python.call_args.args[1],
            )
            self.assertFalse(maya_timing_path.exists())

    def test_execution_timeout_is_finalized_without_maya_writer_ownership(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "gui.log"
            timing_path = Path(temp_dir) / "gui.timing.json"
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
                 mock.patch.object(
                     run_gui_tests,
                     "monitor_log_file",
                     side_effect=TimeoutError("test timeout"),
                 ), \
                 mock.patch.object(
                     sys,
                     "argv",
                     [
                         "run_gui_tests.py",
                         "--log_path",
                         str(log_path),
                         "--timing_report",
                         str(timing_path),
                     ],
                 ):
                self.assertEqual(1, run_gui_tests.main())

            report = json.loads(timing_path.read_text(encoding="utf-8"))
            self.assertEqual("TIMEOUT", report["status"])
            self.assertEqual("timed_out", report["phases"]["discovery"]["status"])
            self.assertEqual("blocked", report["phases"]["tests"]["status"])
            self.assertEqual("passed", report["phases"]["shutdown"]["status"])
            maya_timing_path = timing_path.with_name(timing_path.name + ".maya-partial")
            self.assertFalse(maya_timing_path.exists())

    def test_unconfirmed_explorer_shutdown_is_reported_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "gui.log"
            timing_path = Path(temp_dir) / "gui.timing.json"
            with mock.patch.object(sys, "platform", "win32"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "maya_exe", return_value=Path("maya.exe")), \
                 mock.patch.object(run_gui_tests.maya_commandport, "launch_maya", return_value=None), \
                 mock.patch.object(run_gui_tests.maya_commandport, "ensure_port_available"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_maya_process_id", return_value=1234), \
                 mock.patch.object(run_gui_tests.maya_commandport, "send_python"), \
                 mock.patch.object(run_gui_tests, "wait_for_maya_python_ready"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "quit_maya"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port_close"), \
                 mock.patch.object(
                     run_gui_tests.maya_commandport,
                     "wait_for_maya_process_exit",
                     return_value=False,
                 ), \
                 mock.patch.object(
                     run_gui_tests.maya_commandport,
                     "terminate_maya_process",
                     return_value=False,
                 ), \
                 mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
                 mock.patch.object(run_gui_tests, "monitor_log_file", return_value="PASS"), \
                 mock.patch.object(
                     sys,
                     "argv",
                     [
                         "run_gui_tests.py",
                         "--log_path",
                         str(log_path),
                         "--timing_report",
                         str(timing_path),
                     ],
                 ):
                self.assertEqual(0, run_gui_tests.main())

            report = json.loads(timing_path.read_text(encoding="utf-8"))
            self.assertEqual("failed", report["phases"]["shutdown"]["status"])

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
        with tempfile.TemporaryDirectory() as temp_dir:
            timing_path = Path(temp_dir) / "startup_failure.timing.json"
            with mock.patch.object(run_gui_tests.maya_commandport, "maya_exe", return_value=Path("maya.exe")), \
                 mock.patch.object(run_gui_tests.maya_commandport, "launch_maya", return_value=process) as launch, \
                 mock.patch.object(run_gui_tests.maya_commandport, "ensure_port_available"), \
                 mock.patch.object(run_gui_tests.maya_commandport, "wait_for_port", side_effect=RuntimeError("startup failed")), \
                 mock.patch.object(run_gui_tests.maya_commandport, "close_process_logs"), \
                 mock.patch.object(run_gui_tests, "LOG_FILE_NAME", "unit_gui_runner_startup_failure.log"), \
                 mock.patch.object(
                     sys,
                     "argv",
                     ["run_gui_tests.py", "--timing_report", str(timing_path)],
                 ):
                self.assertEqual(1, run_gui_tests.main())

            report = json.loads(timing_path.read_text(encoding="utf-8"))
            self.assertEqual("ERROR", report["status"])
            self.assertEqual("failed", report["phases"]["startup"]["status"])
            self.assertGreaterEqual(report["phases"]["startup"]["elapsed_seconds"], 0.0)

        maya_app_dir = Path(launch.call_args.kwargs["env_overrides"]["MAYA_APP_DIR"])
        self.assertFalse(maya_app_dir.exists())
        process.kill.assert_not_called()


def load_tests(loader, tests, pattern):
    """Keep helper TestCases out of this module's own unittest discovery."""
    return loader.loadTestsFromTestCase(GuiTestRunnerTests)
