"""Focused tests for generic Python-backed Nox session delegation."""

from __future__ import annotations

import unittest

from tools.nox.common import _option
from tools.nox.sessions import run_control_rig_vmd_roundtrip, run_python_module


class _FakeSession:
    def __init__(self, posargs=None):
        self.posargs = list(posargs or [])
        self.runs = []

    def run(self, *args, **kwargs):
        self.runs.append((args, kwargs))


class NoxSessionsTest(unittest.TestCase):
    def test_python_module_forwards_posargs_and_environment(self):
        session = _FakeSession(["--versions", "2024"])
        environment = {"MMD_TOOLS_CPP_PLUGIN_2024": "plugin.mll"}
        run_python_module(
            session,
            module="tests.viewport.example",
            posargs=session.posargs,
            python_executable="python.exe",
            environment=environment,
        )
        self.assertEqual(
            session.runs,
            [
                (
                    ("python.exe", "-m", "tests.viewport.example", "--versions", "2024"),
                    {"env": environment, "external": True},
                )
            ],
        )

    def test_control_rig_roundtrip_keeps_integration_test_contract(self):
        session = _FakeSession(["--maya", "2026"])
        run_control_rig_vmd_roundtrip(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2024",
            python_executable="python.exe",
        )
        self.assertEqual(
            session.runs[0],
            (
                (
                    "python.exe",
                    "tests/run_tests.py",
                    "--type",
                    "integration",
                    "--test",
                    "test_mmd_control_rig_analyzer",
                    "--maya",
                    "2026",
                ),
                {"external": True},
            ),
        )


if __name__ == "__main__":
    unittest.main()
