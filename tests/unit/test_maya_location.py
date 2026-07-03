"""Unit tests for shared Maya path discovery helpers."""

import os
import unittest
from pathlib import Path
from unittest import mock

from tests.common import maya_location


class MayaLocationTest(unittest.TestCase):
    """Maya location resolution should be shared by nox and test runners."""

    def test_version_specific_location_env_wins(self):
        with mock.patch.dict(
            os.environ,
            {"MAYA_LOCATION_2025": "D:/Autodesk/Maya2025", "MAYA_LOCATION": "D:/Autodesk/Common"},
            clear=True,
        ):
            self.assertEqual(maya_location.maya_location(2025), Path("D:/Autodesk/Maya2025"))

    def test_common_location_env_is_fallback(self):
        with mock.patch.dict(os.environ, {"MAYA_LOCATION": "D:/Autodesk/Common"}, clear=True):
            self.assertEqual(maya_location.maya_location("2024"), Path("D:/Autodesk/Common"))

    def test_windows_default_mayapy_adds_exe_suffix(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Windows"):
                self.assertEqual(
                    maya_location.mayapy("2024"),
                    Path("C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe"),
                )

    def test_macos_default_location(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                self.assertEqual(
                    maya_location.maya_location("2024"),
                    Path("/Applications/Autodesk/maya2024/Maya.app/Contents"),
                )

    def test_wsl_to_windows_path_converts_mnt_drive(self):
        self.assertEqual(
            maya_location.wsl_to_windows_path("/mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe"),
            r"C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe",
        )

    def test_wsl_to_windows_path_converts_mnt_drive_root(self):
        self.assertEqual(maya_location.wsl_to_windows_path("/mnt/f"), "F:\\")

    def test_path_for_maya_process_converts_when_mayapy_is_windows_from_wsl(self):
        self.assertEqual(
            maya_location.path_for_maya_process(
                "/mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe",
                "/mnt/f/Develop/maya_mmd_tools/tests/cpp/smoke_runtime_node.py",
            ),
            r"F:\Develop\maya_mmd_tools\tests\cpp\smoke_runtime_node.py",
        )

    def test_path_for_maya_process_leaves_native_paths_unchanged(self):
        self.assertEqual(
            maya_location.path_for_maya_process(
                r"C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe",
                r"F:\Develop\maya_mmd_tools",
            ),
            r"F:\Develop\maya_mmd_tools",
        )

    def test_resolve_path_for_maya_process_preserves_windows_absolute_path(self):
        self.assertEqual(
            maya_location.resolve_path_for_maya_process(
                "/mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe",
                "/mnt/f/Develop/maya_mmd_tools",
                r"F:\MMD\model.pmx",
            ),
            r"F:\MMD\model.pmx",
        )

    def test_resolve_path_for_maya_process_resolves_relative_path(self):
        self.assertEqual(
            maya_location.resolve_path_for_maya_process(
                "/mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe",
                "/mnt/f/Develop/maya_mmd_tools",
                "tests/cpp/smoke_runtime_node.py",
            ),
            r"F:\Develop\maya_mmd_tools\tests\cpp\smoke_runtime_node.py",
        )

    def test_convert_path_options_for_maya_process_converts_runner_paths(self):
        self.assertEqual(
            maya_location.convert_path_options_for_maya_process(
                "/mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe",
                "/mnt/f/Develop/maya_mmd_tools",
                [
                    "--scan-root",
                    "/mnt/f/MMD",
                    "--write-manifest",
                    "build/batch-import/manifest.json",
                    "--out-dir",
                    "/mnt/f/Develop/maya_mmd_tools/build/batch-import",
                    "--limit",
                    "1",
                ],
                {"--manifest", "--out-dir", "--scan-root", "--write-manifest"},
            ),
            [
                "--scan-root",
                r"F:\MMD",
                "--write-manifest",
                r"F:\Develop\maya_mmd_tools\build\batch-import\manifest.json",
                "--out-dir",
                r"F:\Develop\maya_mmd_tools\build\batch-import",
                "--limit",
                "1",
            ],
        )

    def test_convert_path_options_for_maya_process_converts_inline_values(self):
        self.assertEqual(
            maya_location.convert_path_options_for_maya_process(
                "/mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe",
                "/mnt/f/Develop/maya_mmd_tools",
                ["--manifest=/mnt/f/MMD/manifest.json", "--limit=1"],
                {"--manifest"},
            ),
            [r"--manifest=F:\MMD\manifest.json", "--limit=1"],
        )

    def test_pythonpath_for_maya_process_defaults_to_repo_root_only(self):
        self.assertEqual(
            maya_location.pythonpath_for_maya_process(
                r"C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe",
                r"F:\Develop\maya_mmd_tools",
                r"D:\deps;E:\override",
                host_pathsep=";",
            ),
            r"F:\Develop\maya_mmd_tools",
        )

    def test_pythonpath_for_maya_process_preserves_existing_native_entries_when_requested(self):
        self.assertEqual(
            maya_location.pythonpath_for_maya_process(
                r"C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe",
                r"F:\Develop\maya_mmd_tools",
                r"D:\deps;E:\override",
                host_pathsep=";",
                preserve_existing=True,
            ),
            r"F:\Develop\maya_mmd_tools;D:\deps;E:\override",
        )

    def test_pythonpath_for_maya_process_converts_wsl_entries_for_windows_mayapy_when_requested(self):
        self.assertEqual(
            maya_location.pythonpath_for_maya_process(
                "/mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe",
                "/mnt/f/Develop/maya_mmd_tools",
                "/mnt/d/deps:/mnt/e/override",
                host_pathsep=":",
                preserve_existing=True,
            ),
            r"F:\Develop\maya_mmd_tools;D:\deps;E:\override",
        )

    def test_pythonpath_for_maya_process_handles_windows_entries_on_wsl_when_requested(self):
        self.assertEqual(
            maya_location.pythonpath_for_maya_process(
                "/mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe",
                "/mnt/f/Develop/maya_mmd_tools",
                r"C:\deps;D:\override",
                host_pathsep=":",
                preserve_existing=True,
            ),
            r"F:\Develop\maya_mmd_tools;C:\deps;D:\override",
        )

    def test_pythonpath_for_maya_process_handles_single_windows_entry_on_wsl_when_requested(self):
        self.assertEqual(
            maya_location.pythonpath_for_maya_process(
                "/mnt/c/Program Files/Autodesk/Maya2024/bin/mayapy.exe",
                "/mnt/f/Develop/maya_mmd_tools",
                r"C:\deps",
                host_pathsep=":",
                preserve_existing=True,
            ),
            r"F:\Develop\maya_mmd_tools;C:\deps",
        )


if __name__ == "__main__":
    unittest.main()
