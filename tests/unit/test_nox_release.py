"""Focused tests for extracted release helpers and noxfile dependency wiring."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
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
from tools.nox.release_sessions import (
    _run_release_gate_tier2_parallel,
    run_flip_report,
    run_golden_oracle,
    run_release_camera_motion_oracle,
    run_release_gate,
)


class NoxReleaseTest(unittest.TestCase):
    def _run_minimal_release_gate(self, posargs, tier2_commands, run_command):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            visual_manifest = root / "render.json"
            visual_manifest.write_text("{}", encoding="utf-8")
            report_calls = []

            class Session:
                def __init__(self):
                    self.logs = []

                def log(self, message):
                    self.logs.append(message)

                def error(self, message):
                    raise RuntimeError(message)

            session = Session()

            def record_callable(name, _func, results):
                results.append(
                    {
                        "name": name,
                        "command": [],
                        "status": "pass",
                        "returncode": 0,
                        "duration_sec": 0.0,
                    }
                )

            def record_report(results, quick, **kwargs):
                report_calls.append((list(results), quick, kwargs))
                return root / "release_gate.md", root / "release_gate.json"

            run_release_gate(
                session,
                posargs=posargs,
                option=noxfile._option,
                options=noxfile._options,
                has_flag=noxfile._has_flag,
                root=root,
                default_maya_version="2024",
                default_cpp_config="Debug",
                default_cpp_versions=("2024",),
                release_maya_versions=("2024", "2025"),
                viewport_matrix=(),
                default_visual_manifest=str(visual_manifest),
                release_visual_ports={},
                release_visual_cases=lambda _backend: (),
                new_release_gate_run=lambda: ("run", "timestamp"),
                release_gate_pin_check=lambda: None,
                release_gate_version_check=lambda: None,
                release_gate_tier0_commands=lambda: (),
                release_gate_tier1_commands=lambda **_kwargs: (),
                release_gate_tier2_commands=lambda **_kwargs: tier2_commands,
                release_gate_tier3_commands=lambda **_kwargs: (),
                run_release_gate_callable=record_callable,
                run_release_gate_command=run_command,
                write_release_gate_reports=record_report,
                release_gate_failure_label=lambda result: str(result["name"]),
                format_test_summary=lambda *_args, **_kwargs: "summary",
                environment={},
            )
            return report_calls

    def test_release_gate_default_and_jobs_one_are_sequential(self):
        commands = [
            (f"tier2:{step}-{version}", [step, version])
            for version in ("2024", "2025")
            for step in ("cpp-debug-prerequisite", "mayapy-unit", "mayapy-integration")
        ]
        for posargs in ([], ["--jobs", "1"]):
            calls = []

            def run_command(name, command, local_results, **_kwargs):
                calls.append(name)
                local_results.append(
                    {"name": name, "status": "pass", "duration_sec": 0.0, "command": command}
                )

            report_calls = self._run_minimal_release_gate(posargs, commands, run_command)
            self.assertEqual(calls, [name for name, _command in commands])
            self.assertEqual(
                [result["name"] for result in report_calls[0][0]],
                ["tier0:mmd-anim-pin", "tier0:version-markers", *calls],
            )
            self.assertIn("duration_sec", report_calls[0][2])

    def test_tier2_parallel_lanes_overlap_and_merge_in_declaration_order(self):
        versions = ("2024", "2025", "2026")
        commands = [
            (f"tier2:{step}-{version}", [step, version])
            for version in versions
            for step in ("cpp-debug-prerequisite", "mayapy-unit", "mayapy-integration")
        ]
        commands.append(("tier2:serial", ["serial"]))
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active = 0
        max_active = 0
        calls_by_version = {version: [] for version in versions}

        def run_command(name, command, local_results, **_kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
                for version in versions:
                    if name.endswith(f"-{version}"):
                        calls_by_version[version].append(name)
                        break
            if name.endswith("cpp-debug-prerequisite-2024") or name.endswith("cpp-debug-prerequisite-2025"):
                barrier.wait(timeout=2)
            time.sleep(0.005)
            local_results.append(
                {"name": name, "status": "pass", "duration_sec": 0.005, "command": command}
            )
            with lock:
                active -= 1

        results = _run_release_gate_tier2_parallel(
            commands,
            versions,
            run_command,
            verbose=False,
        )

        self.assertGreaterEqual(max_active, 2)
        for version in versions:
            self.assertEqual(
                calls_by_version[version],
                [
                    f"tier2:cpp-debug-prerequisite-{version}",
                    f"tier2:mayapy-unit-{version}",
                    f"tier2:mayapy-integration-{version}",
                ],
            )
        self.assertEqual([result["name"] for result in results], [name for name, _ in commands])

    def test_tier2_parallel_keeps_running_after_lane_failure(self):
        versions = ("2024", "2025")
        commands = [
            (f"tier2:{step}-{version}", [step, version])
            for version in versions
            for step in ("cpp-debug-prerequisite", "mayapy-unit", "mayapy-integration")
        ]
        seen = []

        def run_command(name, command, local_results, **_kwargs):
            seen.append(name)
            local_results.append(
                {
                    "name": name,
                    "status": "fail" if name == "tier2:mayapy-unit-2024" else "pass",
                    "duration_sec": 0.0,
                    "command": command,
                }
            )

        results = _run_release_gate_tier2_parallel(commands, versions, run_command, verbose=False)

        self.assertEqual({result["name"] for result in results}, set(seen))
        self.assertEqual(len(results), len(commands))
        self.assertEqual(results[1]["status"], "fail")
        self.assertIn("tier2:mayapy-integration-2024", seen)
        self.assertIn("tier2:mayapy-integration-2025", seen)

    def test_tier2_parallel_groups_respect_viewport_visual_barriers(self):
        versions = ("2024", "2025")
        commands = [
            (f"tier2:{step}-{version}", [step, version])
            for version in versions
            for step in ("cpp-debug-prerequisite", "mayapy-unit", "mayapy-integration")
        ]
        commands.extend(
            [
                ("tier2:viewport-glsl-2025", ["viewport", "2025"]),
                ("tier2:viewport-dx11-2026", ["viewport", "2026"]),
                ("tier2:generated-pmx-visual-glsl-2025", ["visual", "2025"]),
                ("tier2:generated-pmx-visual-dx11-2026", ["visual", "2026"]),
                ("tier2:generated-pmx-glsl-dx11-diff", ["diff"]),
                ("tier2:serial-first", ["serial", "first"]),
                ("tier2:serial-second", ["serial", "second"]),
            ]
        )
        viewport_barrier = threading.Barrier(2)
        visual_barrier = threading.Barrier(2)
        viewport_done = threading.Event()
        visual_done = threading.Event()
        lock = threading.Lock()
        active = 0
        peak_active = 0
        active_by_version = {}
        completed_viewports = 0
        completed_visuals = 0
        serial_calls = []

        def run_command(name, command, local_results, **_kwargs):
            nonlocal active, peak_active, completed_viewports, completed_visuals
            version = name.rsplit("-", 1)[-1] if name.startswith("tier2:viewport-") or name.startswith("tier2:generated-pmx-visual-") else None
            with lock:
                active += 1
                peak_active = max(peak_active, active)
                if version is not None:
                    active_by_version[version] = active_by_version.get(version, 0) + 1
                    self.assertLessEqual(active_by_version[version], 1)
            try:
                if name.startswith("tier2:viewport-"):
                    self.assertFalse(visual_done.is_set())
                    viewport_barrier.wait(timeout=2)
                    with lock:
                        completed_viewports += 1
                        if completed_viewports == 2:
                            viewport_done.set()
                elif name.startswith("tier2:generated-pmx-visual-"):
                    self.assertTrue(viewport_done.is_set())
                    visual_barrier.wait(timeout=2)
                    with lock:
                        completed_visuals += 1
                        if completed_visuals == 2:
                            visual_done.set()
                elif name == "tier2:generated-pmx-glsl-dx11-diff":
                    self.assertTrue(visual_done.is_set())
                    local_results.append(
                        {"name": name, "status": "pass", "duration_sec": 0.0, "command": command}
                    )
                    return
                elif name.startswith("tier2:serial-"):
                    self.assertEqual(active, 1)
                    serial_calls.append(name)
                local_results.append(
                    {
                        "name": name,
                        "status": "fail" if name == "tier2:generated-pmx-visual-glsl-2025" else "pass",
                        "duration_sec": 0.0,
                        "command": command,
                    }
                )
            finally:
                with lock:
                    if version is not None:
                        active_by_version[version] -= 1
                    active -= 1

        results = _run_release_gate_tier2_parallel(commands, versions, run_command, verbose=False)

        self.assertEqual([result["name"] for result in results], [name for name, _ in commands])
        self.assertEqual(peak_active, 2)
        self.assertEqual(serial_calls, ["tier2:serial-first", "tier2:serial-second"])
        self.assertEqual(results[8]["status"], "fail")
        self.assertTrue(visual_done.is_set())
        self.assertEqual(completed_visuals, 2)

    def test_release_gate_rejects_invalid_jobs(self):
        session = types.SimpleNamespace(posargs=["--quick", "--jobs", "3"])
        with self.assertRaisesRegex(ValueError, "--jobs must be 1 or 2"):
            run_release_gate(
                session,
                posargs=session.posargs,
                option=noxfile._option,
                options=noxfile._options,
                has_flag=noxfile._has_flag,
                root=Path("F:/repo"),
                default_maya_version="2024",
                default_cpp_config="Debug",
                default_cpp_versions=("2024",),
                release_maya_versions=("2024",),
                viewport_matrix=(),
                default_visual_manifest="missing.json",
                release_visual_ports={},
                release_visual_cases=lambda _backend: (),
                new_release_gate_run=lambda: ("run", "timestamp"),
                release_gate_pin_check=lambda: None,
                release_gate_version_check=lambda: None,
                release_gate_tier0_commands=lambda: (),
                release_gate_tier1_commands=lambda **_kwargs: (),
                release_gate_tier2_commands=lambda **_kwargs: (),
                release_gate_tier3_commands=lambda **_kwargs: (),
                run_release_gate_callable=lambda *_args, **_kwargs: None,
                run_release_gate_command=lambda *_args, **_kwargs: None,
                write_release_gate_reports=lambda *_args, **_kwargs: (Path("md"), Path("json")),
                release_gate_failure_label=lambda _result: "failure",
                format_test_summary=lambda *_args, **_kwargs: "summary",
            )

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
            markdown_path, json_path = release._write_release_gate_reports(
                root, results, quick=False, duration_sec=12.3456
            )
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"], {"pass": 1, "fail": 0, "skip": 1})
            self.assertRegex(payload["run_id"], r"^\d{8}T\d{12}Z-[0-9a-f]{8}$")
            self.assertTrue(payload["timestamp"].endswith("+00:00"))
            self.assertEqual(payload["log_dir"], str(root / "build" / "reports" / "release_gate"))
            self.assertEqual(payload["duration_sec"], 12.346)
            self.assertIn("Duration (seconds): 12.346", markdown_path.read_text(encoding="utf-8"))
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
