"""Focused tests for the shared Maya commandPort E2E host harness."""

import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.viewport import maya_e2e_harness as harness


class TestMayaE2EHarness(unittest.TestCase):
    def test_monitor_escapes_log_lines_for_cp932_console(self):
        class Cp932Console(io.StringIO):
            encoding = "cp932"

            def write(self, value):
                value.encode(self.encoding)
                return super().write(value)

        console = Cp932Console()
        with mock.patch.object(harness.sys, "stdout", console):
            harness._print_log_line("model=珈乐\\n")
        self.assertEqual("model=\\u73c8\\u4e50\\n", console.getvalue())

    def test_monitor_reads_marker_and_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log = root / "probe.log"
            report = root / "probe.json"
            log.write_text('RESULT_JSON: {"status": "pass"}\nDONE\n', encoding="utf-8")
            report.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
            with mock.patch("builtins.print"):
                result = harness.monitor_result(log, report, "DONE", 1.0)
            self.assertEqual({"status": "pass"}, result)

    def test_monitor_rejects_result_status_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log = root / "probe.log"
            report = root / "probe.json"
            log.write_text('RESULT_JSON: {"status": "pass"}\nDONE\n', encoding="utf-8")
            report.write_text(json.dumps({"status": "blocked"}), encoding="utf-8")
            with mock.patch("builtins.print"), self.assertRaisesRegex(
                RuntimeError, "status disagree"
            ):
                harness.monitor_result(log, report, "DONE", 1.0)

    def test_monitor_timeout_and_missing_report_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log = root / "probe.log"
            report = root / "probe.json"
            with self.assertRaises(TimeoutError):
                harness.monitor_result(log, report, "DONE", 0.0)
            log.write_text("DONE\n", encoding="utf-8")
            with self.assertRaisesRegex(TimeoutError, "missing report"):
                harness.monitor_result(log, report, "DONE", 1.0, wait_report_timeout=0)

    def test_run_orders_cleanup_launch_send_and_close(self):
        events = []
        process = mock.Mock()
        process.poll.return_value = None

        def record(name, result=None):
            def call(*_args, **_kwargs):
                events.append(name)
                return result

            return call

        process.terminate.side_effect = record("terminate")
        process.wait.side_effect = record("process-wait")

        with mock.patch.object(harness.sys, "platform", "linux"), mock.patch.object(
            harness.maya_commandport,
            "remove_stale_logs",
            side_effect=record("stale"),
        ), mock.patch.object(
            harness.maya_commandport, "is_port_open", side_effect=record("open", False)
        ), mock.patch.object(
            harness.maya_commandport, "launch_maya", side_effect=record("launch", process)
        ) as launch, mock.patch.object(
            harness.maya_commandport, "wait_for_port", side_effect=record("wait")
        ), mock.patch.object(
            harness.maya_commandport, "send_python", side_effect=record("send")
        ) as send_python, mock.patch.object(
            harness, "monitor_result", side_effect=record("monitor", {"status": "pass"})
        ), mock.patch.object(
            harness.maya_commandport, "quit_maya", side_effect=record("quit")
        ), mock.patch.object(
            harness.time, "sleep", side_effect=record("sleep")
        ), mock.patch.object(
            harness.maya_commandport, "close_process_logs", side_effect=record("close")
        ):
            result = harness.run_maya_e2e(
                project_root=Path("repo"),
                version="2024",
                out_dir=Path("out"),
                port=7788,
                timeout=1.0,
                log_path=Path("probe.log"),
                report_path=Path("probe.json"),
                command="run()",
                marker="DONE",
                send_label="<unit>",
                stale_paths=[Path("stale")],
                quit_delay=0.0,
            )

        self.assertEqual({"status": "pass"}, result)
        sent_command = next(
            call.args[1] for call in send_python.call_args_list
            if call.args[0] == 7788
        )
        self.assertLess(
            sent_command.index("activate_qsettings_isolation()"),
            sent_command.index("run()"),
        )
        self.assertEqual(
            [
                "stale",
                "open",
                "launch",
                "wait",
                "send",
                "monitor",
                "quit",
                "sleep",
                "terminate",
                "process-wait",
                "close",
            ],
            events,
        )
        process.terminate.assert_called_once_with()
        maya_app_dir = Path(launch.call_args.kwargs["env_overrides"]["MAYA_APP_DIR"])
        self.assertTrue(maya_app_dir.is_absolute())
        self.assertFalse(maya_app_dir.exists())

    def test_run_rejects_preexisting_matching_detached_script_when_port_is_free(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "out"
            commandport_script = (out_dir / "commandport_7788.mel").resolve()
            with mock.patch.object(harness.sys, "platform", "win32"), mock.patch.object(
                harness.maya_commandport, "is_port_open", return_value=False
            ), mock.patch.object(
                harness.maya_commandport,
                "find_maya_process_id",
                return_value=1234,
            ) as find_process, mock.patch.object(
                harness.maya_commandport, "launch_maya"
            ) as launch:
                with self.assertRaisesRegex(RuntimeError, "already owns"):
                    harness.run_maya_e2e(
                        project_root=Path(temp_dir) / "project",
                        version="2024",
                        out_dir=out_dir,
                        port=7788,
                        timeout=1.0,
                        log_path=out_dir / "probe.log",
                        report_path=out_dir / "probe.json",
                        command="run()",
                        marker="DONE",
                        send_label="<unit>",
                    )

            find_process.assert_called_once_with(commandport_script)
            launch.assert_not_called()

    def test_run_refuses_send_when_listener_ownership_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "out"
            with mock.patch.object(harness.sys, "platform", "win32"), mock.patch.object(
                harness.maya_commandport, "is_port_open", return_value=False
            ), mock.patch.object(
                harness.maya_commandport, "find_maya_process_id", return_value=None
            ), mock.patch.object(
                harness.maya_commandport, "launch_maya", return_value=None
            ), mock.patch.object(
                harness.maya_commandport, "wait_for_maya_process_id", return_value=1234
            ), mock.patch.object(
                harness.maya_commandport,
                "query_maya_process_for_script",
                return_value=True,
            ), mock.patch.object(
                harness.maya_commandport,
                "is_commandport_owned_by_process",
                side_effect=RuntimeError("listener query failed"),
            ) as owns_listener, mock.patch.object(
                harness.maya_commandport, "wait_for_port"
            ), mock.patch.object(
                harness.maya_commandport, "send_python"
            ) as send_python, mock.patch.object(
                harness.maya_commandport, "quit_maya"
            ) as quit_maya, mock.patch.object(
                harness.maya_commandport, "wait_for_maya_process_exit", return_value=False
            ), mock.patch.object(
                harness.maya_commandport, "terminate_maya_process", return_value=False
            ), mock.patch.object(
                harness.maya_commandport, "close_process_logs"
            ):
                with self.assertRaisesRegex(RuntimeError, "listener query failed"):
                    harness.run_maya_e2e(
                        project_root=Path(temp_dir) / "project",
                        version="2024",
                        out_dir=out_dir,
                        port=7788,
                        timeout=1.0,
                        log_path=out_dir / "probe.log",
                        report_path=out_dir / "probe.json",
                        command="run()",
                        marker="DONE",
                        send_label="<unit>",
                    )

            self.assertTrue((out_dir / "maya-app-2024-7788").exists())
            send_python.assert_not_called()
            quit_maya.assert_not_called()
            self.assertEqual(
                [mock.call(7788, 1234)] * 2,
                owns_listener.call_args_list,
            )

    def test_run_removes_profile_when_launch_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "out"
            profile = out_dir / "maya-app-2024-7788"

            def fail_launch(**_kwargs):
                profile.mkdir(parents=True, exist_ok=True)
                raise RuntimeError("launch failed")

            with mock.patch.object(harness.sys, "platform", "linux"), mock.patch.object(
                harness.maya_commandport, "remove_stale_logs"
            ), mock.patch.object(
                harness.maya_commandport, "is_port_open", return_value=False
            ), mock.patch.object(
                harness.maya_commandport, "launch_maya", side_effect=fail_launch
            ), self.assertRaisesRegex(RuntimeError, "launch failed"):
                harness.run_maya_e2e(
                    project_root=Path("repo"),
                    version="2024",
                    out_dir=out_dir,
                    port=7788,
                    timeout=1.0,
                    log_path=Path("probe.log"),
                    report_path=Path("probe.json"),
                    command="run()",
                    marker="DONE",
                    send_label="<unit>",
                )

            self.assertFalse(profile.exists())

    def test_run_waits_for_detached_maya_before_profile_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "out"
            profile = out_dir / "maya-app-2024-7788"

            project_root = Path(temp_dir) / "project"

            def launch(**_kwargs):
                profile.mkdir(parents=True, exist_ok=True)
                prefs_path = profile / "2024" / "prefs" / "userPrefs.mel"
                prefs = prefs_path.read_text(encoding="utf-8")
                expected_plugin_dir = (project_root / "mmd_tools").resolve().as_posix()
                self.assertTrue(prefs.startswith("//Maya Preference 2024 (Release 1)"))
                self.assertIn("optionVar -version 3;", prefs)
                self.assertIn('optionVar -cat "Security"', prefs)
                self.assertIn('-sa "SafeModeAllowedlistPaths"', prefs)
                self.assertIn(
                    f'SafeModeAllowedlistPaths" "{expected_plugin_dir}"',
                    prefs,
                )
                self.assertNotIn("MAYA_SECURE_OPTOUT", prefs)
                localized_prefs = (
                    profile / "2024" / "ja_JP" / "prefs" / "userPrefs.mel"
                ).read_text(encoding="utf-8")
                self.assertEqual(prefs, localized_prefs)
                self.assertNotIn("startup_mel", _kwargs)
                return None

            def wait_for_exit(*_args, **_kwargs):
                self.assertTrue(profile.exists())
                return True

            with mock.patch.object(harness.sys, "platform", "win32"), mock.patch.object(
                harness.maya_commandport, "remove_stale_logs"
            ), mock.patch.object(
                harness.maya_commandport, "is_port_open", return_value=False
            ), mock.patch.object(
                harness.maya_commandport, "launch_maya", side_effect=launch
            ), mock.patch.object(
                harness.maya_commandport,
                "find_maya_process_id",
                return_value=None,
            ), mock.patch.object(
                harness.maya_commandport,
                "wait_for_maya_process_id",
                return_value=1234,
            ) as discover_process, mock.patch.object(
                harness.maya_commandport,
                "query_maya_process_for_script",
                return_value=True,
            ), mock.patch.object(
                harness.maya_commandport,
                "is_commandport_owned_by_process",
                return_value=True,
            ), mock.patch.object(
                harness.maya_commandport, "wait_for_port"
            ) as wait_for_port, mock.patch.object(
                harness.maya_commandport, "send_python"
            ), mock.patch.object(
                harness, "monitor_result", return_value={"status": "pass"}
            ), mock.patch.object(
                harness.maya_commandport, "quit_maya"
            ), mock.patch.object(
                harness.time, "sleep"
            ), mock.patch.object(
                harness.maya_commandport, "wait_for_port_close"
            ) as wait_for_port_close, mock.patch.object(
                harness.maya_commandport,
                "wait_for_maya_process_exit",
                side_effect=wait_for_exit,
            ) as wait_for_process_exit, mock.patch.object(
                harness.maya_commandport, "close_process_logs"
            ):
                harness.run_maya_e2e(
                    project_root=project_root,
                    version="2024",
                    out_dir=out_dir,
                    port=7788,
                    timeout=1.0,
                    log_path=Path("probe.log"),
                    report_path=Path("probe.json"),
                    command="run()",
                    marker="DONE",
                    send_label="<unit>",
                    quit_delay=0.0,
                )

            self.assertFalse(profile.exists())
            wait_for_port_close.assert_not_called()
            discovery_timeout = discover_process.call_args.kwargs["timeout"]
            port_timeout = wait_for_port.call_args.kwargs["timeout"]
            self.assertGreaterEqual(discovery_timeout, port_timeout)
            wait_for_process_exit.assert_called_once_with(
                1234,
                out_dir / "commandport_7788.mel",
                timeout=30,
            )

    def test_run_timeout_terminates_hung_owned_detached_maya(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "out"
            commandport_script = (out_dir / "commandport_7788.mel").resolve()
            with mock.patch.object(harness.sys, "platform", "win32"), mock.patch.object(
                harness.maya_commandport, "is_port_open", return_value=False
            ), mock.patch.object(
                harness.maya_commandport, "find_maya_process_id", return_value=None
            ), mock.patch.object(
                harness.maya_commandport, "launch_maya", return_value=None
            ), mock.patch.object(
                harness.maya_commandport, "wait_for_maya_process_id", return_value=5678
            ), mock.patch.object(
                harness.maya_commandport,
                "query_maya_process_for_script",
                side_effect=[True, True],
            ) as owns_process, mock.patch.object(
                harness.maya_commandport,
                "is_commandport_owned_by_process",
                return_value=True,
            ) as owns_listener, mock.patch.object(
                harness.maya_commandport, "wait_for_port"
            ), mock.patch.object(
                harness.maya_commandport, "send_python"
            ), mock.patch.object(
                harness,
                "monitor_result",
                side_effect=TimeoutError("probe timeout"),
            ), mock.patch.object(
                harness.maya_commandport, "quit_maya"
            ) as quit_maya, mock.patch.object(
                harness.time, "sleep"
            ), mock.patch.object(
                harness.maya_commandport, "wait_for_maya_process_exit", return_value=False
            ) as wait_for_process_exit, mock.patch.object(
                harness.maya_commandport,
                "terminate_maya_process",
                return_value=True,
            ) as terminate_process, mock.patch.object(
                harness.maya_commandport, "close_process_logs"
            ):
                with self.assertRaisesRegex(TimeoutError, "probe timeout"):
                    harness.run_maya_e2e(
                        project_root=Path(temp_dir) / "project",
                        version="2024",
                        out_dir=out_dir,
                        port=7788,
                        timeout=1.0,
                        log_path=out_dir / "probe.log",
                        report_path=out_dir / "probe.json",
                        command="run()",
                        marker="DONE",
                        send_label="<unit>",
                        quit_delay=0.0,
                    )

            self.assertFalse((out_dir / "maya-app-2024-7788").exists())
            self.assertEqual(
                [mock.call(5678, commandport_script)] * 2,
                owns_process.call_args_list,
            )
            self.assertEqual(
                [mock.call(7788, 5678)] * 2,
                owns_listener.call_args_list,
            )
            quit_maya.assert_called_once_with(7788)
            wait_for_process_exit.assert_called_once_with(
                5678, commandport_script, timeout=30
            )
            terminate_process.assert_called_once_with(5678, commandport_script)

    def test_run_skips_quit_and_preserves_profile_when_detached_ownership_is_lost(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "out"
            with mock.patch.object(harness.sys, "platform", "win32"), mock.patch.object(
                harness.maya_commandport, "is_port_open", return_value=False
            ), mock.patch.object(
                harness.maya_commandport, "find_maya_process_id", return_value=None
            ), mock.patch.object(
                harness.maya_commandport, "launch_maya", return_value=None
            ), mock.patch.object(
                harness.maya_commandport, "wait_for_maya_process_id", return_value=6789
            ), mock.patch.object(
                harness.maya_commandport,
                "query_maya_process_for_script",
                side_effect=[True, False],
            ), mock.patch.object(
                harness.maya_commandport,
                "is_commandport_owned_by_process",
                return_value=True,
            ) as owns_listener, mock.patch.object(
                harness.maya_commandport, "wait_for_port"
            ), mock.patch.object(
                harness.maya_commandport, "send_python"
            ), mock.patch.object(
                harness,
                "monitor_result",
                side_effect=TimeoutError("probe timeout"),
            ), mock.patch.object(
                harness.maya_commandport, "quit_maya"
            ) as quit_maya, mock.patch.object(
                harness.time, "sleep"
            ), mock.patch.object(
                harness.maya_commandport, "wait_for_maya_process_exit"
            ) as wait_for_process_exit, mock.patch.object(
                harness.maya_commandport, "terminate_maya_process"
            ) as terminate_process, mock.patch.object(
                harness.maya_commandport, "close_process_logs"
            ):
                with self.assertRaisesRegex(TimeoutError, "probe timeout"):
                    harness.run_maya_e2e(
                        project_root=Path(temp_dir) / "project",
                        version="2024",
                        out_dir=out_dir,
                        port=7788,
                        timeout=1.0,
                        log_path=out_dir / "probe.log",
                        report_path=out_dir / "probe.json",
                        command="run()",
                        marker="DONE",
                        send_label="<unit>",
                        quit_delay=0.0,
                    )

            self.assertTrue((out_dir / "maya-app-2024-7788").exists())
            quit_maya.assert_not_called()
            owns_listener.assert_called_once_with(7788, 6789)
            wait_for_process_exit.assert_not_called()
            terminate_process.assert_not_called()

    def test_run_preserves_profile_and_fails_when_termination_is_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "out"
            commandport_script = (out_dir / "commandport_7788.mel").resolve()
            with mock.patch.object(harness.sys, "platform", "win32"), mock.patch.object(
                harness.maya_commandport, "is_port_open", return_value=False
            ), mock.patch.object(
                harness.maya_commandport, "find_maya_process_id", return_value=None
            ), mock.patch.object(
                harness.maya_commandport, "launch_maya", return_value=None
            ), mock.patch.object(
                harness.maya_commandport, "wait_for_maya_process_id", return_value=7890
            ), mock.patch.object(
                harness.maya_commandport,
                "query_maya_process_for_script",
                return_value=True,
            ), mock.patch.object(
                harness.maya_commandport,
                "is_commandport_owned_by_process",
                return_value=True,
            ), mock.patch.object(
                harness.maya_commandport, "wait_for_port"
            ), mock.patch.object(
                harness.maya_commandport, "send_python"
            ), mock.patch.object(
                harness, "monitor_result", return_value={"status": "pass"}
            ), mock.patch.object(
                harness.maya_commandport, "quit_maya"
            ), mock.patch.object(
                harness.time, "sleep"
            ), mock.patch.object(
                harness.maya_commandport, "wait_for_maya_process_exit", return_value=False
            ) as wait_for_process_exit, mock.patch.object(
                harness.maya_commandport, "terminate_maya_process"
            ) as terminate_process, mock.patch.object(
                harness.maya_commandport, "close_process_logs"
            ):
                with self.assertRaisesRegex(RuntimeError, "exit was not verified"):
                    harness.run_maya_e2e(
                        project_root=Path(temp_dir) / "project",
                        version="2024",
                        out_dir=out_dir,
                        port=7788,
                        timeout=1.0,
                        log_path=out_dir / "probe.log",
                        report_path=out_dir / "probe.json",
                        command="run()",
                        marker="DONE",
                        send_label="<unit>",
                        terminate_process=False,
                        quit_delay=0.0,
                    )

            self.assertTrue((out_dir / "maya-app-2024-7788").exists())
            wait_for_process_exit.assert_called_once_with(
                7890, commandport_script, timeout=30
            )
            terminate_process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
