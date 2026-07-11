"""Focused tests for the aggregate release-gate result contract."""

from __future__ import annotations

import json
import inspect
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    import nox  # noqa: F401
except ModuleNotFoundError:
    nox_stub = types.ModuleType("nox")
    nox_stub.options = types.SimpleNamespace(sessions=[])
    nox_stub.Session = object
    nox_stub.session = lambda **_kwargs: lambda func: func
    sys.modules["nox"] = nox_stub

try:
    import tomllib  # noqa: F401
except ModuleNotFoundError:
    tomllib_stub = types.ModuleType("tomllib")

    def _loads_pyproject(text):
        project = text.split("[project]", 1)[1].split("[", 1)[0]
        version = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE)
        if version is None:
            raise ValueError("project.version not found")
        return {"project": {"version": version.group(1)}}

    tomllib_stub.loads = _loads_pyproject
    sys.modules["tomllib"] = tomllib_stub

import noxfile


class ReleaseGateContractTest(unittest.TestCase):
    def test_version_check_rejects_cpp_plugin_mismatch(self):
        real_read_text = Path.read_text

        def mismatched_read_text(path, *args, **kwargs):
            if path == noxfile.ROOT / "cpp" / "src" / "pluginMain.cpp":
                return 'MFnPlugin plugin(obj, "yohawing", "9.9.9", "Any");'
            return real_read_text(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", mismatched_read_text):
            with self.assertRaisesRegex(RuntimeError, "pluginMain.cpp version"):
                noxfile._release_gate_version_check()

    def test_child_skip_is_not_treated_as_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "child.json"
            report.write_text('{"status":"skip"}', encoding="utf-8")
            results = []
            with mock.patch("noxfile.subprocess.run", return_value=mock.Mock(returncode=0)):
                with mock.patch.object(Path, "unlink"):
                    noxfile._run_release_gate_command("local", ["child"], results, result_report=report)
            self.assertEqual(results[0]["status"], "skip")

    def test_strict_local_promotes_required_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "child.json"
            report.write_text('{"status":"skipped"}', encoding="utf-8")
            results = []
            with mock.patch("noxfile.subprocess.run", return_value=mock.Mock(returncode=0)):
                with mock.patch.object(Path, "unlink"):
                    noxfile._run_release_gate_command(
                        "local", ["child"], results, result_report=report,
                        required_local=True, strict_local=True,
                    )
            self.assertEqual(results[0]["status"], "fail")

    def test_report_summary_keeps_optional_skip_and_passes_aggregate(self):
        results = [
            {"name": "unit", "status": "pass", "duration_sec": 1.0, "command": ["unit"]},
            {"name": "optional", "status": "skip", "duration_sec": 0.0, "command": ["optional"]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(noxfile, "ROOT", root):
                md_path, json_path = noxfile._write_release_gate_reports(results, quick=False)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"], {"pass": 1, "fail": 0, "skip": 1})
            self.assertIn("pass=1, fail=0, skip=1", md_path.read_text(encoding="utf-8"))

    def test_local_child_report_all_skip_and_strict_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "local-assets.json"
            markdown = Path(directory) / "local-assets.md"
            report.write_text(
                json.dumps({"status": "pass", "results": [{"status": "skip"}, {"status": "skipped"}]}),
                encoding="utf-8",
            )
            markdown.write_text(
                "# Local Assets Check\n\n- Status: pass\n\n| Asset | Status |\n| --- | --- |\n| fixture | skip |\n",
                encoding="utf-8",
            )
            self.assertEqual(
                noxfile._normalize_local_gate_report(report, strict_local=False, markdown_path=markdown),
                "skip",
            )
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"], {"pass": 0, "fail": 0, "skip": 2})
            markdown_text = markdown.read_text(encoding="utf-8")
            self.assertIn("- Status: skip", markdown_text)
            self.assertIn("- Summary: pass=0, fail=0, skip=2", markdown_text)
            self.assertIn("| fixture | skip |", markdown_text)
            self.assertEqual(
                noxfile._normalize_local_gate_report(report, strict_local=True, markdown_path=markdown),
                "fail",
            )
            strict_markdown = markdown.read_text(encoding="utf-8")
            self.assertIn("- Status: fail", strict_markdown)
            self.assertEqual(strict_markdown.count("- Summary:"), 1)
            self.assertIn("| fixture | skip |", strict_markdown)

    def test_local_child_report_failure_wins_and_pass_with_skip_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "local-assets.json"
            report.write_text(
                json.dumps({"results": [{"status": "pass"}, {"status": "skip"}]}),
                encoding="utf-8",
            )
            self.assertEqual(noxfile._normalize_local_gate_report(report, strict_local=True), "pass")
            report.write_text(
                json.dumps({"results": [{"status": "pass"}, {"status": "fail"}]}),
                encoding="utf-8",
            )
            self.assertEqual(noxfile._normalize_local_gate_report(report, strict_local=False), "fail")

    def test_full_release_gate_includes_bundled_native_smoke(self):
        source = inspect.getsource(noxfile.release_gate)
        self.assertIn('"tier2:bundled-native-smoke"', source)
        self.assertIn('["uvx", "nox", "-s", "bundled_native_smoke"]', source)

    def test_full_release_gate_includes_native_physics_release_gate(self):
        source = inspect.getsource(noxfile.release_gate)
        self.assertIn('"tier2:native-physics-release-gate"', source)
        self.assertIn('["uvx", "nox", "-s", "native_physics_release_gate"]', source)
        gate_source = inspect.getsource(noxfile.native_physics_release_gate)
        self.assertIn('tests/data/physics/test_hair_physics.pmx', gate_source)
        self.assertIn('tests/data/mmt_test_model_test_motion.vmd', gate_source)
        self.assertIn("_bundled_physics_runtime()", gate_source)

    def test_bundled_physics_runtime_selects_supported_platform(self):
        windows = noxfile._bundled_physics_runtime("Windows")
        macos = noxfile._bundled_physics_runtime("Darwin")
        self.assertTrue(str(windows).replace("\\", "/").endswith("mmd_tools/native/win64/mmd_runtime_ffi.dll"))
        self.assertTrue(str(macos).replace("\\", "/").endswith("mmd_tools/native/macos/libmmd_runtime_ffi.dylib"))
        with self.assertRaisesRegex(RuntimeError, "unsupported on Linux"):
            noxfile._bundled_physics_runtime("Linux")

    def test_native_physics_release_gate_clears_all_reports_before_inputs(self):
        source = inspect.getsource(noxfile.native_physics_release_gate)
        cleanup = source.index("for stale_report in")
        input_check = source.index("for required in")
        self.assertLess(cleanup, input_check)
        self.assertIn("(*run_reports, comparison_json, comparison_md)", source)


if __name__ == "__main__":
    unittest.main()
