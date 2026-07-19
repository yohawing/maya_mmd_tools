"""Regression tests for the pure-Python nox unit-test runner."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import TestCase, mock

try:
    import nox  # noqa: F401
except ModuleNotFoundError:
    nox_stub = types.ModuleType("nox")
    nox_stub.options = types.SimpleNamespace(sessions=[])
    nox_stub.Session = object
    nox_stub.session = lambda **_kwargs: lambda func: func
    sys.modules["nox"] = nox_stub

import noxfile


class CiUnitRunnerTest(TestCase):
    def test_ci_unit_runs_importable_modules_with_pytest(self):
        class FakeSession:
            def __init__(self):
                self.logs = []

            def log(self, message):
                self.logs.append(message)

            def error(self, message):
                raise AssertionError(message)

        with mock.patch.object(noxfile, "ROOT", Path("F:/ci-unit-test")):
            with mock.patch(
                "noxfile.subprocess.run",
                return_value=types.SimpleNamespace(returncode=0, stderr=""),
            ) as probe_mock:
                with mock.patch(
                    "noxfile.Path.glob",
                    return_value=[Path("F:/ci-unit-test/tests/unit/test_module.py")],
                ):
                    with mock.patch(
                        "noxfile._run_logged_subprocess",
                        return_value=(0, Path("F:/ci-unit-test/build/ci.log"), (0, 0)),
                    ) as run_mock:
                        noxfile.ci_unit(FakeSession())

        self.assertEqual(probe_mock.call_count, 1)
        self.assertEqual(
            run_mock.call_args.args[0],
            ["uvx", "--with", "pytest", "--", "python", "-m", "pytest", "--pyargs", "tests.unit.test_module"],
        )

    def test_ci_unit_rejects_pytest_no_tests_exit_code(self):
        class FakeSession:
            def log(self, _message):
                pass

            def error(self, message):
                raise RuntimeError(message)

        with mock.patch.object(noxfile, "ROOT", Path("F:/ci-unit-test")):
            with mock.patch(
                "noxfile.subprocess.run",
                return_value=types.SimpleNamespace(returncode=0, stderr=""),
            ):
                with mock.patch(
                    "noxfile.Path.glob",
                    return_value=[Path("F:/ci-unit-test/tests/unit/test_empty.py")],
                ):
                    with mock.patch(
                        "noxfile._run_logged_subprocess",
                        return_value=(5, Path("F:/ci-unit-test/build/ci.log"), (0, 0)),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "exit code 5"):
                            noxfile.ci_unit(FakeSession())
