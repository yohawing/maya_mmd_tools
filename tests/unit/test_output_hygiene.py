"""Tests for compact terminal output and complete transcript retention."""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.common.output_hygiene import (
    compact_failure_details_from_log,
    format_summary,
    repeated_warning_summary,
    run_logged_subprocess,
    safe_log_name,
    summarize_unittest_result,
    write_full_log,
)


class OutputHygieneTest(unittest.TestCase):
    def test_repeated_warnings_are_counted_without_losing_unique_warnings(self):
        output = "\n".join(
            [
                "Warning: cached value is stale",
                "Warning: cached value is stale",
                "WARNING: another diagnostic",
                "ordinary output",
                "警告: Maya diagnostic",
            ]
        )

        self.assertEqual(repeated_warning_summary(output), (3, 1))

    def test_full_log_preserves_repeated_and_unknown_diagnostics(self):
        output = "Warning: repeated\nWarning: repeated\nUNKNOWN_DIAGNOSTIC payload\n"
        with tempfile.TemporaryDirectory() as directory:
            path = write_full_log(Path(directory) / "gate.log", ["runner", "--flag"], output)
            transcript = path.read_text(encoding="utf-8")

        self.assertIn("Command: runner --flag", transcript)
        child_output = transcript.split("\n\n", 1)[1]
        self.assertEqual(child_output.count("Warning: repeated"), 2)
        self.assertIn("UNKNOWN_DIAGNOSTIC payload", transcript)

    def test_logged_subprocess_streams_complete_output_and_warning_counts(self):
        script = (
            "print('Warning: repeated'); "
            "print('Warning: repeated'); "
            "print('UNKNOWN_DIAGNOSTIC payload')"
        )
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "child.log"
            returncode, resolved, warning_summary = run_logged_subprocess(
                [sys.executable, "-c", script],
                log_path=log,
                cwd=Path(directory),
            )
            transcript = resolved.read_text(encoding="utf-8")

        self.assertEqual(returncode, 0)
        self.assertEqual(warning_summary, (1, 1))
        child_output = transcript.split("\n\n", 1)[1]
        self.assertEqual(child_output.count("Warning: repeated"), 2)
        self.assertIn("UNKNOWN_DIAGNOSTIC payload", transcript)

    def test_logged_subprocess_terminates_child_when_streaming_is_interrupted(self):
        def interrupted_output():
            yield "partial output\n"
            raise KeyboardInterrupt

        process = SimpleNamespace(
            stdout=interrupted_output(),
            poll=mock.Mock(return_value=None),
            terminate=mock.Mock(),
            wait=mock.Mock(return_value=0),
            kill=mock.Mock(),
        )
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch(
                "tests.common.output_hygiene.subprocess.Popen",
                return_value=process,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    run_logged_subprocess(
                        ["child"],
                        log_path=Path(directory) / "child.log",
                        cwd=Path(directory),
                    )

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        process.kill.assert_not_called()

    def test_compact_failure_details_are_recovered_from_nested_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "nested.log"
            log.write_text(
                "[unit] first failure: suite.Case.test_a\n"
                "[unit] failed tests: suite.Case.test_a, suite.Case.test_b\n",
                encoding="utf-8",
            )
            first_failure, failed_tests = compact_failure_details_from_log(log)

        self.assertEqual(first_failure, "suite.Case.test_a")
        self.assertEqual(failed_tests, ["suite.Case.test_a", "suite.Case.test_b"])

    def test_first_diagnostic_is_used_when_child_has_no_compact_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "generic.log"
            log.write_text(
                "Command: git diff --check\n\nfile.py:3: trailing whitespace.\n+bad line\n",
                encoding="utf-8",
            )
            first_failure, failed_tests = compact_failure_details_from_log(log)

        self.assertEqual(first_failure, "file.py:3: trailing whitespace.")
        self.assertEqual(failed_tests, [])

    def test_summary_and_log_names_are_stable(self):
        self.assertEqual(
            format_summary(
                "unit",
                total=12,
                passed=10,
                skipped=1,
                failed=1,
                duration_sec=1.234,
            ),
            "[unit] tests=12 pass=10 skip=1 fail=1 duration=1.23s",
        )
        self.assertEqual(safe_log_name("tier2:mayapy/unit 2024"), "tier2-mayapy-unit-2024")

    def test_multiple_failing_subtests_count_as_one_top_level_failure(self):
        class FakeTest:
            def __init__(self, test_id):
                self._test_id = test_id

            def id(self):
                return self._test_id

        parent = FakeTest("suite.Case.test_values")
        subtest_a = SimpleNamespace(test_case=parent)
        subtest_b = SimpleNamespace(test_case=parent)
        result = SimpleNamespace(
            testsRun=1,
            failures=[(subtest_a, "a"), (subtest_b, "b")],
            errors=[],
            unexpectedSuccesses=[],
            skipped=[],
            expectedFailures=[],
        )

        summary, failed_tests = summarize_unittest_result(result)

        self.assertEqual(summary, {"tests": 1, "pass": 0, "skip": 0, "fail": 1})
        self.assertEqual(failed_tests, ["suite.Case.test_values"])

    def test_nonzero_runner_exit_overrides_a_stale_success_report(self):
        controller = importlib.import_module("tests.run_tests")
        payload = {
            "summary": {"tests": 2, "pass": 2, "skip": 0, "fail": 0},
            "duration_sec": 0.5,
            "first_failure": None,
            "failed_tests": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "result.json"
            log = root / "result.log"
            report.write_text(json.dumps(payload), encoding="utf-8")
            log.write_text("teardown failed\n", encoding="utf-8")
            with mock.patch("builtins.print") as print_mock:
                with self.assertRaisesRegex(SystemExit, "3"):
                    controller._finish_run(
                        test_type="unit",
                        report_path=report,
                        log_path=log,
                        returncode=3,
                        started=time.perf_counter(),
                        repeated_warnings=0,
                    )

        terminal = "\n".join(str(call.args[0]) for call in print_mock.call_args_list)
        self.assertIn("tests=2 pass=1 skip=0 fail=1", terminal)
        self.assertIn("first failure: test runner exited with code 3", terminal)

    def test_direct_mayapy_version_is_inferred_from_executable_path(self):
        controller = importlib.import_module("tests.run_tests")

        self.assertEqual(
            controller._maya_version_from_executable(
                "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe",
                2024,
            ),
            2026,
        )
        self.assertEqual(
            controller._maya_version_from_executable("/custom/mayapy", 2025),
            2025,
        )


if __name__ == "__main__":
    unittest.main()
