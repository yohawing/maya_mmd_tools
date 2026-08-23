"""Focused tests for the shared Maya commandPort E2E host harness."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.viewport import maya_e2e_harness as harness


class TestMayaE2EHarness(unittest.TestCase):
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

        with mock.patch.object(
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

    def test_run_removes_profile_when_launch_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "out"
            profile = out_dir / "maya-app-2024-7788"

            def fail_launch(**_kwargs):
                profile.mkdir(parents=True, exist_ok=True)
                raise RuntimeError("launch failed")

            with mock.patch.object(harness.maya_commandport, "remove_stale_logs"), mock.patch.object(
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

            def wait_for_close(*_args, **_kwargs):
                self.assertTrue(profile.exists())

            with mock.patch.object(harness.maya_commandport, "remove_stale_logs"), mock.patch.object(
                harness.maya_commandport, "is_port_open", return_value=False
            ), mock.patch.object(
                harness.maya_commandport, "launch_maya", side_effect=launch
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
                harness.maya_commandport, "wait_for_port_close", side_effect=wait_for_close
            ), mock.patch.object(
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


if __name__ == "__main__":
    unittest.main()
