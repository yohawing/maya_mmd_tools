"""Regression tests for the pure-Python nox unit-test runner."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
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
from tools.nox import ci_unit_runner


class CiUnitRunnerTest(TestCase):
    def _make_unit_files(self, root, names):
        unit_dir = root / "tests" / "unit"
        unit_dir.mkdir(parents=True)
        for name in names:
            (unit_dir / name).write_text("", encoding="utf-8")

    def test_ci_unit_launches_one_outer_runner_command(self):
        class FakeSession:
            def __init__(self):
                self.logs = []

            def log(self, message):
                self.logs.append(message)

            def error(self, message):
                raise AssertionError(message)

        root = Path("F:/ci-unit-test")
        with mock.patch.object(noxfile, "ROOT", root):
            with mock.patch.object(
                noxfile,
                "_run_logged_subprocess",
                return_value=(0, root / "build/ci-unit-tests.log", (0, 0)),
            ) as run_mock:
                with mock.patch.object(noxfile.subprocess, "run") as probe_mock:
                    noxfile.ci_unit(FakeSession())

        probe_mock.assert_not_called()
        run_mock.assert_called_once()
        self.assertEqual(
            run_mock.call_args.args[0],
            ["uvx", "--with", "pytest", "--", "python", "-m", "tools.nox.ci_unit_runner"],
        )
        self.assertEqual(run_mock.call_args.kwargs["log_path"], root / "build/reports/ci_unit_tests.log")

    def test_runner_probes_each_module_in_sorted_isolated_child_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_unit_files(root, ["test_z.py", "test_a.py"])
            probe_calls = []

            def probe(command, **kwargs):
                probe_calls.append((command, kwargs))
                return types.SimpleNamespace(returncode=0, stderr="")

            pytest_calls = []
            result = ci_unit_runner.run_ci_unit(
                root,
                run_process=probe,
                pytest_main=lambda args: pytest_calls.append(args) or 0,
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            [call[0] for call in probe_calls],
            [
                [sys.executable, "-c", "import tests.unit.test_a"],
                [sys.executable, "-c", "import tests.unit.test_z"],
            ],
        )
        self.assertTrue(all(call[1]["timeout"] == 30 for call in probe_calls))
        self.assertEqual(pytest_calls, [["--pyargs", "tests.unit.test_a", "tests.unit.test_z"]])

    def test_runner_skips_only_allowlisted_environment_import_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_unit_files(root, ["test_maya.py", "test_pure.py"])

            def probe(command, **_kwargs):
                if command[-1].endswith("test_maya"):
                    return types.SimpleNamespace(
                        returncode=1,
                        stderr="ModuleNotFoundError: No module named 'maya.cmds'",
                    )
                return types.SimpleNamespace(returncode=0, stderr="")

            pytest_calls = []
            result = ci_unit_runner.run_ci_unit(
                root,
                run_process=probe,
                pytest_main=lambda args: pytest_calls.append(args) or 0,
            )

        self.assertEqual(result, 0)
        self.assertEqual(pytest_calls, [["--pyargs", "tests.unit.test_pure"]])

    def test_runner_rejects_non_environment_import_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_unit_files(root, ["test_bad.py"])
            pytest_mock = mock.Mock(return_value=0)
            output = io.StringIO()
            error = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                result = ci_unit_runner.run_ci_unit(
                    root,
                    run_process=lambda *_args, **_kwargs: types.SimpleNamespace(
                        returncode=1,
                        stderr="ModuleNotFoundError: No module named 'numpy'",
                    ),
                    pytest_main=pytest_mock,
                )

        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertTrue(error.getvalue().startswith("[ci_unit] test_bad.py: import failed"))
        pytest_mock.assert_not_called()

    def test_runner_rejects_import_probe_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_unit_files(root, ["test_slow.py"])
            pytest_mock = mock.Mock(return_value=0)

            def probe(command, **_kwargs):
                raise subprocess.TimeoutExpired(command, 30)

            result = ci_unit_runner.run_ci_unit(root, run_process=probe, pytest_main=pytest_mock)

        self.assertEqual(result, 1)
        pytest_mock.assert_not_called()

    def test_runner_rejects_missing_or_empty_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pytest_mock = mock.Mock(return_value=0)
            self.assertEqual(ci_unit_runner.run_ci_unit(root, pytest_main=pytest_mock), 1)
            self._make_unit_files(root, ["test_maya.py"])

            result = ci_unit_runner.run_ci_unit(
                root,
                run_process=lambda *_args, **_kwargs: types.SimpleNamespace(
                    returncode=1,
                    stderr="ModuleNotFoundError: No module named 'PySide6.QtCore'",
                ),
                pytest_main=pytest_mock,
            )

        self.assertEqual(result, 1)
        pytest_mock.assert_not_called()

    def test_runner_reports_sorted_classification_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_unit_files(root, ["test_z.py", "test_a.py"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = ci_unit_runner.run_ci_unit(
                    root,
                    run_process=lambda *_args, **_kwargs: types.SimpleNamespace(
                        returncode=0,
                        stderr="",
                    ),
                    pytest_main=lambda _args: 0,
                )

        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("discovered 2 test module(s)", text)
        self.assertIn("classified 2 module(s): importable=2 environment-only=0", text)
        self.assertLess(text.index("test_a.py: importable"), text.index("test_z.py: importable"))

    def test_runner_does_not_relaunch_pytest_through_os_argv_and_preserves_exit_5(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_unit_files(root, ["test_module.py"])
            pytest_calls = []
            result = ci_unit_runner.run_ci_unit(
                root,
                run_process=lambda *_args, **_kwargs: types.SimpleNamespace(
                    returncode=0,
                    stderr="",
                ),
                pytest_main=lambda args: pytest_calls.append(args) or 5,
            )

        self.assertEqual(result, 5)
        self.assertEqual(pytest_calls, [["--pyargs", "tests.unit.test_module"]])

    def test_ci_unit_propagates_outer_pytest_failure_to_session_error(self):
        class FakeSession:
            def log(self, _message):
                pass

            def error(self, message):
                raise RuntimeError(message)

        root = Path("F:/ci-unit-test")
        with mock.patch.object(noxfile, "ROOT", root):
            with mock.patch.object(
                noxfile,
                "_run_logged_subprocess",
                return_value=(5, root / "build/ci-unit-tests.log", (0, 0)),
            ):
                with self.assertRaisesRegex(RuntimeError, "exit code 5"):
                    noxfile.ci_unit(FakeSession())
