"""Focused tests for native build paths, allowlists, and noxfile wrappers."""

from __future__ import annotations

import os
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

import noxfile
from tools.nox import native


class NoxNativeTest(unittest.TestCase):
    def test_native_paths_use_explicit_repository_root(self):
        root = Path("F:/repo")

        self.assertEqual(native._cpp_build_dir(root, "2024"), root / "build/cpp/maya2024")
        with mock.patch.object(native.platform, "system", return_value="Windows"):
            self.assertEqual(
                native._cpp_smoke_exe(root, "2024", "Debug"),
                root / "build/cpp/maya2024/Debug/mmd_runtime_smoke.exe",
            )
        with mock.patch.object(native.platform, "system", return_value="Linux"):
            self.assertEqual(
                native._cpp_smoke_exe(root, "2024", "Release"),
                root / "build/cpp/maya2024/Release/mmd_runtime_smoke",
            )

    def test_devkit_and_vswhere_environment_overrides(self):
        with mock.patch.dict(
            os.environ,
            {
                "MAYA_DEVKIT_ROOT_2024": "F:/version-devkit",
                "MAYA_DEVKIT_ROOT": "F:/common-devkit",
                "VSWHERE_PATH": "F:/tools/vswhere.exe",
            },
            clear=True,
        ):
            self.assertEqual(native._maya_devkit_root("2024"), Path("F:/version-devkit"))
            self.assertEqual(native._vswhere_path(), Path("F:/tools/vswhere.exe"))

        with mock.patch.dict(os.environ, {"MAYA_DEVKIT_ROOT": "F:/common-devkit"}, clear=True):
            self.assertEqual(native._maya_devkit_root("2025"), Path("F:/common-devkit"))

    def test_expected_environment_import_allowlist_is_fail_closed(self):
        self.assertTrue(native._is_expected_environment_import_failure("""traceback
ModuleNotFoundError: No module named 'maya.cmds'
"""))
        self.assertTrue(native._is_expected_environment_import_failure("""ModuleNotFoundError: No module named 'PySide6.QtCore'
"""))
        self.assertFalse(native._is_expected_environment_import_failure("""ModuleNotFoundError: No module named 'numpy'
"""))
        self.assertFalse(native._is_expected_environment_import_failure("""ValueError: No module named 'maya.cmds'
"""))
        self.assertFalse(native._is_expected_environment_import_failure("""ModuleNotFoundError: No module named 'maya.cmds'
RuntimeError: failed
"""))

    def test_find_vsdevcmd_accepts_existing_explicit_path_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "VsDevCmd.bat"
            path.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {"VSDEVCMD_PATH": str(path)}, clear=True):
                self.assertEqual(native._find_vsdevcmd(), path)

            with mock.patch.dict(
                os.environ,
                {"VSDEVCMD_PATH": str(path.with_name("missing.bat"))},
                clear=True,
            ):
                self.assertIsNone(native._find_vsdevcmd())

    def test_noxfile_wrappers_delegate_with_root_and_preserve_defaults(self):
        root = Path("F:/patched-repo")
        session = object()
        with mock.patch.object(noxfile, "ROOT", root):
            with mock.patch.object(noxfile, "_common_maya_devkit_root", return_value=Path("F:/devkit")) as devkit:
                self.assertEqual(noxfile._maya_devkit_root("2024"), Path("F:/devkit"))
            devkit.assert_called_once_with("2024")

            with mock.patch.object(noxfile, "_common_cpp_build_dir", return_value=Path("F:/build")) as build_dir:
                self.assertEqual(noxfile._cpp_build_dir("2024"), Path("F:/build"))
            build_dir.assert_called_once_with(root, "2024")

            with mock.patch.object(noxfile, "_common_cmake_configure") as configure:
                noxfile._cmake_configure(session, "2024")
            configure.assert_called_once_with(session, root, "2024", noxfile.DEFAULT_CMAKE_CONFIG)

            with mock.patch.object(noxfile, "_common_cmake_build") as build:
                noxfile._cmake_build(session, "2024", "Release", clean_first=True)
            build.assert_called_once_with(session, root, "2024", "Release", clean_first=True)

            with mock.patch.object(noxfile, "_common_run_cli_smoke") as cli_smoke:
                noxfile._run_cli_smoke(session, "2024", "Debug", "manifest.json", "case", "2")
            cli_smoke.assert_called_once_with(session, root, "2024", "Debug", "manifest.json", "case", "2")
