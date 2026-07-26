"""Focused tests for the bundled native release smoke."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.release import bundled_native_smoke as smoke


class BundledNativeSmokeTest(unittest.TestCase):
    def test_case_matrix_uses_only_bundled_release_paths(self):
        root = Path("F:/repo")
        plugins, runtimes = smoke.bundled_cases(root)
        self.assertEqual([case["maya"] for case in plugins], ["2024", "2025", "2026", "2027"])
        self.assertTrue(all("/Release/mmd_tools_cpp.mll" in case["path"].replace("\\", "/") for case in plugins))
        self.assertEqual(len(runtimes), 5)
        self.assertTrue(all(case["path"].endswith("mmd_runtime_ffi.dll") for case in runtimes))
        self.assertIn("mmd_tools/native/win64", runtimes[0]["path"].replace("\\", "/"))

    def test_mayapy_matrix_resolves_each_requested_version(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            paths = [smoke.mayapy_for_version(version) for version in smoke.MAYA_VERSIONS]
        self.assertEqual([path.parts[-3] for path in paths], ["Maya2024", "Maya2025", "Maya2026", "Maya2027"])

    def test_missing_runtime_is_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            result = smoke._runtime_probe(Path(directory) / smoke.RUNTIME_NAME)
        self.assertEqual(result["status"], "fail")

    def test_runtime_loaded_from_wrong_path_is_a_failure(self):
        class FakeFunction:
            def __init__(self, value):
                self.value = value

            def __call__(self):
                return self.value

        fake_library = mock.Mock()
        fake_library.mmd_runtime_abi_version = FakeFunction(smoke.CURRENT_ABI_VERSION)
        fake_library.mmd_runtime_feature_flags = FakeFunction(smoke.REQUIRED_FEATURE_FLAGS)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / smoke.RUNTIME_NAME
            runtime.write_bytes(b"fixture")
            with mock.patch.object(smoke.ctypes, "WinDLL", return_value=fake_library, create=True):
                with mock.patch.object(smoke, "_windows_module_path", return_value="F:/wrong/mmd_runtime_ffi.dll"):
                    result = smoke._runtime_probe(runtime)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["loadedPath"], "F:/wrong/mmd_runtime_ffi.dll")

    def test_current_runtime_abi_from_expected_path_passes(self):
        class FakeFunction:
            def __init__(self, value):
                self.value = value

            def __call__(self):
                return self.value

        fake_library = mock.Mock()
        fake_library.mmd_runtime_abi_version = FakeFunction(smoke.CURRENT_ABI_VERSION)
        fake_library.mmd_runtime_feature_flags = FakeFunction(smoke.REQUIRED_FEATURE_FLAGS)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / smoke.RUNTIME_NAME
            runtime.write_bytes(b"fixture")
            with mock.patch.object(smoke.ctypes, "WinDLL", return_value=fake_library, create=True):
                with mock.patch.object(smoke, "_windows_module_path", return_value=str(runtime.resolve())):
                    result = smoke._runtime_probe(runtime)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["abi"], smoke.CURRENT_ABI_VERSION)

    def test_summary_is_fail_closed(self):
        self.assertEqual(smoke.summarize([{"status": "pass"}, {"status": "fail"}])["status"], "fail")
        self.assertEqual(smoke.summarize([{"status": "pass"}])["status"], "pass")
        self.assertEqual(smoke.summarize([])["status"], "fail")

    def test_reports_preserve_wrong_path_evidence(self):
        payload = {
            "status": "fail",
            "summary": {"passed": 0, "failed": 1},
            "results": [{"name": "maya-2024-plugin", "status": "fail", "path": "expected", "loadedPath": "wrong"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "report.json"
            md_path = Path(directory) / "report.md"
            smoke.write_reports(payload, json_path, md_path)
            self.assertIn('"loadedPath": "wrong"', json_path.read_text(encoding="utf-8"))
            self.assertIn("loaded=wrong", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
