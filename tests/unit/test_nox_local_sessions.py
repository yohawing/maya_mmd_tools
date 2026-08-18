"""Focused tests for optional local-asset session delegation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.nox.common import _has_flag, _option
from tools.nox.local_sessions import (
    run_local_asset_roundtrip,
    run_local_assets_check,
    run_local_camera_motion_oracle,
    run_local_parity,
    run_semistandard_name_audit,
)


class _FakeSession:
    def __init__(self, posargs=None):
        self.posargs = list(posargs or [])
        self.runs = []
        self.logs = []

    def run(self, *args, **kwargs):
        self.runs.append((args, kwargs))

    def log(self, message):
        self.logs.append(message)

    def error(self, message):
        raise RuntimeError(message)


class LocalSessionsTest(unittest.TestCase):
    def test_local_asset_roundtrip_forwards_manifest_and_repo_pythonpath(self):
        session = _FakeSession(["--manifest", "build/representative.json", "--case", "sparse"])
        root = Path("F:/repo")

        run_local_asset_roundtrip(
            session,
            posargs=session.posargs,
            option=_option,
            root=root,
            python_executable="python.exe",
        )

        args, kwargs = session.runs[0]
        self.assertEqual(args[:2], ("python.exe", str(root / "tools" / "local_asset_roundtrip.py")))
        self.assertIn("--manifest", args)
        self.assertIn("--case", args)
        self.assertIn(str(root), kwargs["env"]["PYTHONPATH"])

    def test_missing_local_assets_manifest_is_skipped_with_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _FakeSession()
            run_local_assets_check(
                session,
                posargs=session.posargs,
                option=_option,
                has_flag=_has_flag,
                default_maya_version="2024",
                root=root,
                require_build_path=lambda _session, value, _name: root / value,
                mayapy=mock.Mock(),
                mayapy_env=mock.Mock(),
                mayapy_arg_path=mock.Mock(),
                mayapy_script=mock.Mock(),
                normalize_local_gate_report=mock.Mock(),
            )
            report = root / "build/reports/local_assets_check.json"
            self.assertEqual(report.exists(), True)
            self.assertEqual(report.read_text(encoding="utf-8").count('"status": "skip"'), 2)
            self.assertFalse(session.runs)

    def test_semistandard_audit_adds_build_reports_and_repo_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _FakeSession(["--scan-root", "F:/assets", "--strict-local"])
            run_semistandard_name_audit(
                session,
                posargs=session.posargs,
                option=_option,
                root=root,
                require_build_path=lambda _session, value, _name: root / value,
                python_executable="python.exe",
            )
            args, kwargs = session.runs[0]
            self.assertEqual(args[:3], ("python.exe", "tests/local/semistandard_name_audit.py", "--scan-root"))
            self.assertIn("--out-json", args)
            self.assertIn(str(root), kwargs["env"]["PYTHONPATH"])

    def test_local_camera_oracle_strips_nox_maya_option(self):
        session = _FakeSession(["--maya", "2026", "--case", "camera-case", "--current-report-only"])
        mayapy = mock.Mock()
        run_local_camera_motion_oracle(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2024",
            root=Path("F:/repo"),
            mayapy=lambda _version: mayapy,
            mayapy_env=lambda _mayapy, **_values: {"MAYA_VERSION": "2026"},
            mayapy_script=lambda _mayapy, script: script,
            maya_process_path=lambda _mayapy, root: str(root),
            convert_mayapy_path_options=lambda _mayapy, args, _options: args,
            copy_parity_vmd=lambda _session, args: args,
        )
        args, _kwargs = session.runs[0]
        self.assertNotIn("--maya", args)
        self.assertIn("--case", args)
        self.assertIn("--current-report-only", args)

    def test_local_parity_forwards_existing_manifest_and_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text("{}\n", encoding="utf-8")
            session = _FakeSession(["--maya", "2024", "--manifest", str(manifest), "--skip-fbx"])
            mayapy = mock.Mock()
            run_local_parity(
                session,
                posargs=session.posargs,
                option=_option,
                has_flag=_has_flag,
                default_maya_version="2026",
                root=root,
                require_build_path=lambda _session, value, _name: root / value,
                mayapy=lambda _version: mayapy,
                mayapy_env=lambda _mayapy, **values: {"PYTHONPATH": "repo", **values},
                mayapy_script=lambda _mayapy, script: script,
                convert_mayapy_path_options=lambda _mayapy, args, _options: args,
            )
            args, kwargs = session.runs[0]
            self.assertEqual(args[1], "tests/viewport/local_asset_motion_compare.py")
            self.assertIn("--manifest", args)
            self.assertIn("--skip-fbx", args)
            self.assertTrue(kwargs["env"].get("PYTHONPATH") is not None)


if __name__ == "__main__":
    unittest.main()
