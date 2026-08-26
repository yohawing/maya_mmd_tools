"""Focused tests for the bounded external MMD-Anim evidence gate."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "gates" / "export_validation_gate.py"
SPEC = importlib.util.spec_from_file_location("export_validation_gate", SCRIPT)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


class ExportValidationGateTest(unittest.TestCase):
    def _build_report_with_fake_commands(self, command_results, assets=("model.pmx",)):
        def fake_run(command, _root, _timeout):
            operation = command[1]
            return command_results[operation]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for asset in assets:
                (root / asset).write_bytes(asset.encode("ascii"))
            with mock.patch.object(GATE, "_submodule_revision", return_value="v0.2.0"):
                with mock.patch.object(GATE, "_run", side_effect=fake_run):
                    return GATE.build_report(
                        root,
                        Path("mmd-anim"),
                        tuple(Path(asset) for asset in assets),
                        None,
                        None,
                        None,
                        1.0,
                    )

    def test_run_keeps_version_line_and_hashes_without_raw_output(self):
        stdout = "\n  mmd-anim 0.2.0\n" + ("x" * 400)
        stderr = "diagnostic\n" + ("y" * 400)
        completed = SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)
        with mock.patch.object(GATE.subprocess, "run", return_value=completed):
            result = GATE._run(["mmd-anim", "--version"], Path("."), 1.0)

        self.assertEqual(result["stdout_first_line"], "mmd-anim 0.2.0")
        self.assertEqual(result["stderr_first_line"], "diagnostic")
        self.assertIn("command_sha256", result)
        self.assertIn("stdout_sha256", result)
        self.assertIn("stderr_sha256", result)
        self.assertNotIn("stdout", result)
        self.assertNotIn("stderr", result)

    def test_run_bounds_structured_json_summary(self):
        payload = {
            "status": "pass",
            "summary": {"message": "x" * 1000},
            "perCase": [{"status": "pass", "detail": "y" * 1000}] * 40,
            "unbounded_output": "should not be retained",
        }
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(GATE.subprocess, "run", return_value=completed):
            result = GATE._run(["mmd-anim", "verify", "--json"], Path("."), 1.0)

        summary = result["json"]
        self.assertEqual(summary["status"], "pass")
        self.assertEqual(len(summary["summary"]["message"]), GATE.MAX_JSON_SUMMARY_STRING_LENGTH)
        self.assertEqual(len(summary["perCase"]), GATE.MAX_JSON_SUMMARY_ITEMS + 1)
        self.assertEqual(summary["perCase"][0]["status"], "pass")
        self.assertNotIn("unbounded_output", summary)

    def test_build_report_runs_version_once_and_records_case_statuses(self):
        calls = []

        def fake_run(command, _root, _timeout):
            calls.append(list(command))
            if command[1:] == ["--version"]:
                return {
                    "status": "pass",
                    "command": list(command),
                    "stdout_first_line": "mmd-anim 0.2.0",
                }
            if command[1] == "inspect":
                return {"status": "pass", "command": list(command), "json": {"metadata": {}}}
            if command[1] == "roundtrip":
                return {"status": "pass", "command": list(command), "json": {"status": "ok"}}
            if command[1] == "import":
                return {"status": "pass", "command": list(command), "json": {"summary": {}}}
            return {"status": "pass", "command": list(command)}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.pmx"
            motion = root / "motion.vmd"
            model.write_bytes(b"pmx")
            motion.write_bytes(b"vmd")
            with mock.patch.object(GATE, "_submodule_revision", return_value="v0.2.0"):
                with mock.patch.object(GATE, "_run", side_effect=fake_run):
                    report = GATE.build_report(
                        root,
                        Path("mmd-anim"),
                        (Path("model.pmx"), Path("motion.vmd")),
                        Path("model.pmx"),
                        Path("motion.vmd"),
                        "mmd-anim 0.2.0",
                        1.0,
                    )

        version_calls = [command for command in calls if command[1:] == ["--version"]]
        self.assertEqual(len(version_calls), 1)
        self.assertEqual(report["cli_version"], "mmd-anim 0.2.0")
        self.assertEqual([case["status"] for case in report["cases"]], ["pass", "pass", "pass"])
        self.assertEqual(report["summary"]["cases"], {"total": 3, "pass": 3, "fail": 0})

    def test_inspect_invalid_json_fails_case_and_records_bounded_error(self):
        long_asset = "model.pmx"
        report = self._build_report_with_fake_commands(
            {
                "--version": {"status": "pass", "stdout_first_line": "mmd-anim 0.2.0"},
                "inspect": {"status": "pass", "json": None},
                "roundtrip": {"status": "pass", "json": {"status": "ok"}},
            },
            assets=(long_asset,),
        )

        case = report["cases"][0]
        self.assertEqual(case["status"], "fail")
        self.assertIn("inspect returned invalid JSON", case["error"])
        self.assertLessEqual(len(case["error"]), GATE.MAX_CASE_ERROR_LENGTH)
        self.assertEqual(report["status"], "fail")

    def test_roundtrip_status_failure_fails_case_even_with_zero_exit_code(self):
        report = self._build_report_with_fake_commands(
            {
                "--version": {"status": "pass", "stdout_first_line": "mmd-anim 0.2.0"},
                "inspect": {"status": "pass", "json": {"metadata": {}}},
                "roundtrip": {"status": "pass", "json": {"status": "failed"}},
            }
        )

        case = report["cases"][0]
        self.assertEqual(case["status"], "fail")
        self.assertIn("expected 'ok'", case["error"])
        self.assertEqual(len(report["blockers"]), 1)

    def test_runtime_import_requires_json_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.pmx"
            motion = root / "motion.vmd"
            model.write_bytes(b"pmx")
            motion.write_bytes(b"vmd")

            def fake_run(command, _root, _timeout):
                if command[1:] == ["--version"]:
                    return {"status": "pass", "stdout_first_line": "mmd-anim 0.2.0"}
                return {"status": "pass", "json": {"status": "ok"}}

            with mock.patch.object(GATE, "_submodule_revision", return_value="v0.2.0"):
                with mock.patch.object(GATE, "_run", side_effect=fake_run):
                    report = GATE.build_report(
                        root,
                        Path("mmd-anim"),
                        (),
                        Path("model.pmx"),
                        Path("motion.vmd"),
                        None,
                        1.0,
                    )

        self.assertEqual(report["cases"][0]["status"], "fail")
        self.assertIn("missing summary", report["cases"][0]["error"])


    def test_main_strict_only_changes_exit_code_for_recorded_blockers(self):
        report = {
            "status": "fail",
            "cli": "mmd-anim",
            "cli_version": None,
            "expected_cli_version": "mmd-anim 0.2.0",
            "submodule_revision": "unavailable",
            "version_match": False,
            "cases": [],
            "blockers": ["mmd-anim CLI could not be executed"],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "gate.json"
            with mock.patch.object(GATE, "build_report", return_value=report):
                strict_code = GATE.main(["--cli", "mmd-anim", "--out", str(output), "--strict"])
                non_strict_code = GATE.main(["--cli", "mmd-anim", "--out", str(output)])

        self.assertEqual(strict_code, 1)
        self.assertEqual(non_strict_code, 0)

    def test_main_normalizes_markdown_out_to_distinct_json_and_markdown_files(self):
        report = {
            "status": "pass",
            "cli": "mmd-anim",
            "cli_version": "mmd-anim 0.2.0",
            "expected_cli_version": "mmd-anim 0.2.0",
            "submodule_revision": "v0.2.0",
            "version_match": True,
            "cases": [],
            "blockers": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            markdown_output = Path(directory) / "report.md"
            json_output = markdown_output.with_suffix(".json")
            with mock.patch.object(GATE, "build_report", return_value=report):
                return_code = GATE.main(["--cli", "mmd-anim", "--out", str(markdown_output)])

            self.assertEqual(return_code, 0)
            self.assertTrue(json_output.is_file())
            self.assertTrue(markdown_output.is_file())
            self.assertEqual(json.loads(json_output.read_text(encoding="utf-8"))["status"], "pass")
            self.assertIn("# Export Validation", markdown_output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
