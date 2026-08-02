"""Focused tests for generic Python-backed Nox session delegation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.nox.common import _option
from tools.nox.sessions import (
    run_control_rig_vmd_roundtrip,
    run_mmd_anim_binding_gate,
    run_mmd_anim_python_tests,
    run_python_module,
)


class _FakeSession:
    def __init__(self, posargs=None):
        self.posargs = list(posargs or [])
        self.runs = []

    def run(self, *args, **kwargs):
        self.runs.append((args, kwargs))

    def error(self, message):
        raise RuntimeError(message)


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

    def test_mmd_anim_python_tests_builds_binding_contract_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binding_dir = root / "external" / "mmd-anim" / "bindings" / "python"
            (binding_dir / "tests").mkdir(parents=True)
            runtime_library = root / "external" / "mmd-anim" / "target" / "release" / "mmd_runtime_ffi.dll"
            runtime_library.parent.mkdir(parents=True)
            runtime_library.touch()
            session = _FakeSession()

            run_mmd_anim_python_tests(
                session,
                posargs=[],
                option=_option,
                root=root,
                python_executable="python.exe",
                environment={"PATH": "path"},
                platform_name="Windows",
            )

            self.assertEqual(
                session.runs,
                [
                    (
                        (
                            "python.exe",
                            "-m",
                            "unittest",
                            "discover",
                            "-s",
                            str(binding_dir / "tests"),
                        ),
                        {
                            "env": {
                                "PATH": "path",
                                "PYTHONPATH": str(binding_dir),
                                "MMD_RUNTIME_LIBRARY": str(runtime_library),
                            },
                            "external": True,
                        },
                    )
                ],
            )

    def test_mmd_anim_python_tests_accepts_explicit_runtime_library(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binding_dir = root / "external" / "mmd-anim" / "bindings" / "python"
            (binding_dir / "tests").mkdir(parents=True)
            runtime_library = root / "custom" / "mmd_runtime_ffi.dll"
            runtime_library.parent.mkdir()
            runtime_library.touch()
            session = _FakeSession(["--runtime-library", str(runtime_library)])

            run_mmd_anim_python_tests(
                session,
                posargs=session.posargs,
                option=_option,
                root=root,
                python_executable="python.exe",
                environment={},
                platform_name="Windows",
            )

            self.assertEqual(session.runs[0][1]["env"]["MMD_RUNTIME_LIBRARY"], str(runtime_library))

    def test_mmd_anim_binding_gate_forwards_fixture_gate_and_runtime_library(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binding_dir = root / "external" / "mmd-anim" / "bindings" / "python"
            binding_dir.mkdir(parents=True)
            runtime_library = root / "external" / "mmd-anim" / "target" / "release" / "mmd_runtime_ffi.dll"
            runtime_library.parent.mkdir(parents=True)
            runtime_library.touch()
            session = _FakeSession(["--frame", "12"])

            run_mmd_anim_binding_gate(
                session,
                posargs=session.posargs,
                option=_option,
                root=root,
                python_executable="python.exe",
                environment={"PATH": "path"},
                platform_name="Windows",
            )

            self.assertEqual(
                session.runs,
                [
                    (
                        (
                            "python.exe",
                            "tools/mmd_anim_binding_gate.py",
                            "--binding-root",
                            str(binding_dir),
                            "--runtime-library",
                            str(runtime_library),
                            "--frame",
                            "12",
                        ),
                        {"env": {"PATH": "path"}, "external": True},
                    )
                ],
            )


if __name__ == "__main__":
    unittest.main()
