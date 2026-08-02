"""Focused tests for the Nox Maya process/path compatibility layer."""

from __future__ import annotations

import os
import sys
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
from tools.nox import maya


class NoxMayaTest(unittest.TestCase):
    def test_mayapy_env_uses_explicit_root_and_extra_values(self):
        mayapy = Path("F:/Maya2024/bin/mayapy.exe")
        root = Path("F:/repo")
        with mock.patch.dict(os.environ, {"PYTHONPATH": "host/python"}, clear=True):
            with mock.patch.object(maya, "_maya_pythonpath", return_value="F:/repo;host/python") as pythonpath:
                env = maya._mayapy_env(
                    mayapy,
                    root,
                    preserve_pythonpath=True,
                    MAYA_VERSION="2024",
                )

        pythonpath.assert_called_once_with(
            mayapy,
            root,
            "host/python",
            preserve_existing=True,
        )
        self.assertEqual(env["PYTHONPATH"], "F:/repo;host/python")
        self.assertEqual(env["MAYA_VERSION"], "2024")

    def test_mayapy_path_helpers_pass_explicit_root(self):
        mayapy = Path("F:/Maya2024/bin/mayapy.exe")
        root = Path("F:/repo")
        with mock.patch.object(maya, "_maya_process_path", return_value="script.py") as process_path:
            script = maya._mayapy_script(mayapy, root, "tests/script.py")
        process_path.assert_called_once_with(mayapy, root / "tests/script.py")
        self.assertEqual(script, "script.py")

        with mock.patch.object(maya, "_resolve_maya_path", return_value="input.pmx") as resolve_path:
            value = maya._mayapy_arg_path(mayapy, root, "input.pmx")
        resolve_path.assert_called_once_with(mayapy, root, "input.pmx")
        self.assertEqual(value, "input.pmx")

        args = ["--model", "model.pmx", "--flag"]
        with mock.patch.object(maya, "_convert_maya_path_options", return_value=args) as convert_options:
            converted = maya._convert_mayapy_path_options(mayapy, root, args, {"--model"})
        convert_options.assert_called_once_with(mayapy, root, args, {"--model"})
        self.assertIs(converted, args)

    def test_noxfile_wrappers_keep_historical_names_and_current_root(self):
        mayapy = Path("F:/Maya2024/bin/mayapy.exe")
        root = Path("F:/patched-repo")
        with mock.patch.object(noxfile, "ROOT", root):
            with mock.patch.object(noxfile, "_common_mayapy_env", return_value={}) as env:
                self.assertEqual(noxfile._mayapy_env(mayapy, MAYA_VERSION="2024"), {})
            env.assert_called_once_with(mayapy, root, preserve_pythonpath=False, MAYA_VERSION="2024")

            with mock.patch.object(noxfile, "_common_mayapy_script", return_value="script.py") as script:
                self.assertEqual(noxfile._mayapy_script(mayapy, "tests/script.py"), "script.py")
            script.assert_called_once_with(mayapy, root, "tests/script.py")

            with mock.patch.object(noxfile, "_common_mayapy_arg_path", return_value="input.pmx") as arg_path:
                self.assertEqual(noxfile._mayapy_arg_path(mayapy, "input.pmx"), "input.pmx")
            arg_path.assert_called_once_with(mayapy, root, "input.pmx")

            args = ["--model", "model.pmx"]
            with mock.patch.object(noxfile, "_common_convert_mayapy_path_options", return_value=args) as convert:
                self.assertIs(noxfile._convert_mayapy_path_options(mayapy, args, {"--model"}), args)
            convert.assert_called_once_with(mayapy, root, args, {"--model"})
