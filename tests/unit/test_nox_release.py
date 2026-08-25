"""Focused tests for extracted release helpers and noxfile dependency wiring."""

from __future__ import annotations

import json
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
from tools.nox import release
from tools.nox.release_sessions import run_flip_report, run_golden_oracle, run_release_camera_motion_oracle


class NoxReleaseTest(unittest.TestCase):
    def test_release_camera_oracle_missing_manifest_is_optional_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = types.SimpleNamespace(posargs=["--manifest", "missing.json"], runs=[], logs=[])
            session.run = lambda *args, **kwargs: session.runs.append((args, kwargs))
            session.log = lambda message: session.logs.append(message)
            session.error = lambda message: (_ for _ in ()).throw(AssertionError(message))
            run_release_camera_motion_oracle(
                session,
                posargs=session.posargs,
                option=noxfile._option,
                has_flag=noxfile._has_flag,
                default_maya_version="2024",
                root=root,
                require_build_path=lambda _session, value, _name: root / value,
                mayapy=mock.Mock(),
                mayapy_env=mock.Mock(),
                mayapy_script=mock.Mock(),
                maya_process_path=mock.Mock(),
                convert_mayapy_path_options=mock.Mock(),
                copy_parity_vmd=mock.Mock(),
                current_epsilon="18.25",
                addiction_camera_vmd="F:/missing/addiction.vmd",
                interpolation_eye_max="2.0",
                interpolation_forward_max_deg="5.0",
                interpolation_up_max_deg="5.0",
                interpolation_rotation_max_deg="5.0",
            )
            report = root / "build/local-camera-motion-oracle/release/manifest-skip.json"
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["status"], "skip")
            self.assertFalse(session.runs)

    def test_flip_report_builds_report_only_command(self):
        session = types.SimpleNamespace(posargs=[
            "--reference", "reference.png",
            "--test", "test.png",
            "--out-dir", "build/flip-report",
            "--basename", "case",
            "--csv", "build/flip-report/results.csv",
        ], runs=[], logs=[])
        session.run = lambda *args, **kwargs: session.runs.append((args, kwargs))
        session.log = lambda message: session.logs.append(message)
        session.error = lambda message: (_ for _ in ()).throw(AssertionError(message))
        with mock.patch("tools.nox.release_sessions.shutil.which", return_value="flip.exe"):
            run_flip_report(
                session,
                posargs=session.posargs,
                option=noxfile._option,
                require_build_path=lambda _session, value, _name: Path("F:/repo") / value,
            )
        args, kwargs = session.runs[0]
        self.assertEqual(args[:6], ("flip.exe", "-r", "reference.png", "-t", "test.png", "-d"))
        self.assertIn(("-c", str(Path("F:/repo/build/flip-report/results.csv"))), zip(args, args[1:]))
        self.assertTrue(kwargs["external"])

    def test_golden_oracle_uses_downloaded_cli_and_manifest_default(self):
        session = types.SimpleNamespace(posargs=[], runs=[])
        session.run = lambda *args, **kwargs: session.runs.append((args, kwargs))
        run_golden_oracle(
            session,
            posargs=session.posargs,
            option=noxfile._option,
            root=Path("F:/repo"),
            downloaded_mmd_anim_cli=lambda _session: Path("F:/tools/mmd-anim.exe"),
        )
        self.assertEqual(
            session.runs[0],
            (
                (
                    str(Path("F:/tools/mmd-anim.exe")),
                    "verify",
                    str(Path("F:/repo/tests/golden-oracle/manifest.json")),
                    "--mode",
                    "numeric",
                ),
                {"external": True},
            ),
        )

    def test_version_check_uses_explicit_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mmd_tools").mkdir()
            (root / "cpp" / "src").mkdir(parents=True)
            (root / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
            (root / "mmd_tools" / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
            (root / "maya_mmd_tools.mod").write_text("maya_mmd_tools 1.2.3\n", encoding="utf-8")
            (root / "cpp" / "src" / "pluginMain.cpp").write_text(
                'MFnPlugin plugin(obj, "mmd_tools", "1.2.3", "Any");\n',
                encoding="utf-8",
            )
            (root / "CHANGELOG.md").write_text("## [1.2.3]\n\n- Release notes\n", encoding="utf-8")

            release._release_gate_version_check(root, expected_version="1.2.3")

    def test_reports_and_local_normalization_preserve_status_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                {"name": "unit", "status": "pass", "duration_sec": 1.0, "command": ["unit"]},
                {"name": "optional", "status": "skip", "duration_sec": 0.0, "command": ["optional"]},
            ]
            markdown_path, json_path = release._write_release_gate_reports(root, results, quick=False)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"], {"pass": 1, "fail": 0, "skip": 1})
            self.assertRegex(payload["run_id"], r"^\d{8}T\d{12}Z-[0-9a-f]{8}$")
            self.assertTrue(payload["timestamp"].endswith("+00:00"))
            self.assertEqual(payload["log_dir"], str(root / "build" / "reports" / "release_gate"))
            self.assertIn("pass=1, fail=0, skip=1", markdown_path.read_text(encoding="utf-8"))

            local_report = root / "local.json"
            local_markdown = root / "local.md"
            local_report.write_text(
                json.dumps({"status": "pass", "results": [{"status": "skipped"}]}),
                encoding="utf-8",
            )
            local_markdown.write_text("# Local\n\n- Status: pass\n", encoding="utf-8")
            self.assertEqual(
                release._normalize_local_gate_report(local_report, strict_local=False, markdown_path=local_markdown),
                "skip",
            )
            self.assertIn("- Status: skip", local_markdown.read_text(encoding="utf-8"))
            self.assertEqual(
                release._normalize_local_gate_report(local_report, strict_local=True, markdown_path=local_markdown),
                "fail",
            )

    def test_noxfile_wrappers_inject_current_root_and_patchable_dependencies(self):
        root = Path("F:/patched-release-root")
        with mock.patch.object(noxfile, "ROOT", root):
            with mock.patch.object(noxfile, "_common_release_gate_version_check") as version_check:
                noxfile._release_gate_version_check("1.2.3")
            version_check.assert_called_once_with(root, expected_version="1.2.3")

            with mock.patch.object(noxfile, "_common_write_release_gate_reports") as write_reports:
                noxfile._write_release_gate_reports([], quick=True)
            write_reports.assert_called_once_with(root, [], True)

            with mock.patch.object(noxfile, "_common_normalize_local_gate_report") as normalize:
                noxfile._normalize_local_gate_report(Path("local.json"), strict_local=True)
            normalize.assert_called_once_with(Path("local.json"), True, None)

            with mock.patch.object(noxfile, "_common_release_gate_mmd_anim_pin_check") as pin_check:
                with mock.patch.object(noxfile.subprocess, "run") as run_process:
                    noxfile._release_gate_mmd_anim_pin_check()
            pin_check.assert_called_once_with(root, run_process=run_process)

            with mock.patch.object(noxfile, "_common_run_release_gate_command") as run_command:
                noxfile._run_release_gate_command("tier:test", ["child"], [])
            command_kwargs = run_command.call_args.kwargs
            self.assertIs(command_kwargs["run_logged_subprocess"], noxfile._run_logged_subprocess)
            self.assertIs(command_kwargs["safe_log_name"], noxfile._safe_log_name)
            self.assertIs(
                command_kwargs["compact_failure_details_from_log"],
                noxfile._compact_failure_details_from_log,
            )
            self.assertIs(command_kwargs["format_test_summary"], noxfile._format_test_summary)
            self.assertEqual(command_kwargs["root"], root)

            with mock.patch.object(noxfile, "_common_run_release_gate_callable") as run_callable:
                noxfile._run_release_gate_callable("tier:test", lambda: None, [])
            self.assertIs(
                run_callable.call_args.kwargs["format_test_summary"],
                noxfile._format_test_summary,
            )

    def test_failure_label_prefers_first_failure_then_error_then_name(self):
        self.assertEqual(release._release_gate_failure_label({"first_failure": "broken"}), "broken")
        self.assertEqual(release._release_gate_failure_label({"error": "failed"}), "failed")
        self.assertEqual(release._release_gate_failure_label({"name": "tier:test"}), "tier:test")
        self.assertEqual(release._release_gate_failure_label({}), "unknown failure")
