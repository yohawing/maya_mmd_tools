"""Focused tests for the aggregate release-gate result contract."""

from __future__ import annotations

import json
import inspect
import re
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

try:
    import tomllib  # noqa: F401
except ModuleNotFoundError:
    tomllib_stub = types.ModuleType("tomllib")

    def _loads_pyproject(text):
        project = text.split("[project]", 1)[1].split("[", 1)[0]
        version = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE)
        if version is None:
            raise ValueError("project.version not found")
        return {"project": {"version": version.group(1)}}

    tomllib_stub.loads = _loads_pyproject
    sys.modules["tomllib"] = tomllib_stub

import noxfile
from noxlib.release_matrix import tier2_commands


class ReleaseGateContractTest(unittest.TestCase):
    def test_release_visual_ports_do_not_reuse_development_command_port(self):
        ports = noxfile.DEFAULT_RELEASE_VISUAL_PORTS

        self.assertEqual(set(ports), {"2025", "2026"})
        self.assertEqual(len(set(ports.values())), len(ports))
        self.assertNotIn("7721", ports.values())

    def test_release_visual_matrix_excludes_unreachable_outline_case(self):
        outline = "fixture-render-generated-visual-mmd-outline-normal-silhouette"
        self.assertNotIn(outline, noxfile._release_visual_cases("dx11"))
        self.assertNotIn(outline, noxfile._release_visual_cases("glsl"))
        self.assertEqual(
            set(noxfile._release_visual_cases("dx11")),
            set(noxfile._release_visual_cases("glsl")),
        )

    def test_cpp_verify_mayapy_processes_skip_user_setup(self):
        class FakeSession:
            posargs = ["--maya", "2024", "--config", "Release"]

            def __init__(self):
                self.runs = []

            def run(self, *args, **kwargs):
                self.runs.append((args, kwargs))

        session = FakeSession()
        mayapy = Path("C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe")
        mayapy_env = {"MAYA_SKIP_USERSETUP_PY": "1"}
        with mock.patch("noxfile._configure_bullet3_dir"):
            with mock.patch("noxfile._cmake_configure"):
                with mock.patch("noxfile._cmake_build") as cmake_build:
                    with mock.patch("noxfile._run_cli_smoke"):
                        with mock.patch("noxfile._mayapy", return_value=mayapy):
                            with mock.patch("pathlib.Path.exists", return_value=True):
                                with mock.patch("noxfile._mayapy_env", return_value=mayapy_env) as env_mock:
                                    noxfile.cpp_verify(session)

        cmake_build.assert_called_once_with(
            session,
            "2024",
            "Release",
            clean_first=True,
        )
        self.assertEqual(env_mock.call_args.kwargs["MAYA_SKIP_USERSETUP_PY"], "1")
        mayapy_runs = [
            kwargs
            for args, kwargs in session.runs
            if len(args) >= 2
            and args[0] == str(mayapy)
            and str(args[1]).endswith(("smoke_runtime_node.py", "focused_physics_solver_world_toggle.py"))
        ]
        self.assertEqual(len(mayapy_runs), 2)
        self.assertTrue(all(run["env"] is mayapy_env for run in mayapy_runs))

    def test_mmd_anim_pin_check_rejects_checkout_head_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submodule = root / "external" / "mmd-anim"
            (submodule / ".git").mkdir(parents=True)
            parent_head = "1" * 40
            checkout_head = "2" * 40
            completed = [
                types.SimpleNamespace(
                    returncode=0,
                    stdout=f"160000 commit {parent_head}\texternal/mmd-anim\n",
                    stderr="",
                ),
                types.SimpleNamespace(returncode=0, stdout=f"{checkout_head}\n", stderr=""),
            ]

            with mock.patch("noxfile.subprocess.run", side_effect=completed):
                with self.assertRaisesRegex(
                    RuntimeError,
                    rf"parent gitlink={parent_head}.*checkout HEAD={checkout_head}",
                ):
                    noxfile._release_gate_mmd_anim_pin_check(root)

    def test_mmd_anim_pin_check_rejects_uninitialized_submodule(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "external" / "mmd-anim").mkdir(parents=True)

            with mock.patch("noxfile.subprocess.run") as run_mock:
                with self.assertRaisesRegex(RuntimeError, "not initialized"):
                    noxfile._release_gate_mmd_anim_pin_check(root)

            run_mock.assert_not_called()

    def test_mmd_anim_pin_check_rejects_unavailable_git(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submodule = root / "external" / "mmd-anim"
            (submodule / ".git").mkdir(parents=True)

            with mock.patch("noxfile.subprocess.run", side_effect=FileNotFoundError):
                with self.assertRaisesRegex(RuntimeError, "Git executable is unavailable"):
                    noxfile._release_gate_mmd_anim_pin_check(root)

    def test_mmd_anim_pin_check_accepts_matching_heads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submodule = root / "external" / "mmd-anim"
            (submodule / ".git").mkdir(parents=True)
            head = "a" * 40
            completed = [
                types.SimpleNamespace(
                    returncode=0,
                    stdout=f"160000 commit {head}\texternal/mmd-anim\n",
                    stderr="",
                ),
                types.SimpleNamespace(returncode=0, stdout=f"{head}\n", stderr=""),
                types.SimpleNamespace(returncode=0, stdout="", stderr=""),
            ]

            with mock.patch("noxfile.subprocess.run", side_effect=completed):
                noxfile._release_gate_mmd_anim_pin_check(root)

    def test_mmd_anim_pin_check_rejects_matching_head_with_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            submodule = root / "external" / "mmd-anim"
            (submodule / ".git").mkdir(parents=True)
            head = "a" * 40
            dirty_status = " M crates/runtime/src/lib.rs\n?? local-source.rs\n"
            completed = [
                types.SimpleNamespace(
                    returncode=0,
                    stdout=f"160000 commit {head}\texternal/mmd-anim\n",
                    stderr="",
                ),
                types.SimpleNamespace(returncode=0, stdout=f"{head}\n", stderr=""),
                types.SimpleNamespace(returncode=0, stdout=dirty_status, stderr=""),
            ]

            with mock.patch("noxfile.subprocess.run", side_effect=completed):
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"external/mmd-anim worktree is dirty.*crates/runtime/src/lib.rs.*local-source.rs",
                ):
                    noxfile._release_gate_mmd_anim_pin_check(root)

    def test_full_release_gate_pin_preflight_fails_before_commands(self):
        class FakeSession:
            def __init__(self):
                self.posargs = []
                self.logs = []

            def log(self, message):
                self.logs.append(message)

            def error(self, message):
                raise RuntimeError(message)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = FakeSession()
            with mock.patch.object(noxfile, "ROOT", root):
                with mock.patch(
                    "noxfile._release_gate_mmd_anim_pin_check",
                    side_effect=RuntimeError("pin mismatch"),
                ):
                    with mock.patch("noxfile._run_release_gate_command") as command_mock:
                        with mock.patch("builtins.print"):
                            with self.assertRaisesRegex(RuntimeError, "preflight failed"):
                                noxfile.release_gate(session)

            command_mock.assert_not_called()
            payload = json.loads(
                (root / "build" / "reports" / "release_gate.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["results"][0]["name"], "tier0:mmd-anim-pin")
            self.assertEqual(payload["results"][0]["error"], "pin mismatch")

    def test_quick_release_gate_skips_pin_preflight_and_runs_commands(self):
        class FakeSession:
            posargs = ["--quick"]

            def log(self, _message):
                pass

            def error(self, message):
                raise RuntimeError(message)

        callable_names = []

        def record_callable(name, _func, results):
            callable_names.append(name)
            results.append(
                {
                    "name": name,
                    "command": [],
                    "status": "pass",
                    "returncode": 0,
                    "duration_sec": 0.0,
                }
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(noxfile, "ROOT", root):
                with mock.patch("noxfile._release_gate_mmd_anim_pin_check") as pin_mock:
                    with mock.patch("noxfile._run_release_gate_command") as command_mock:
                        with mock.patch(
                            "noxfile._run_release_gate_callable",
                            side_effect=record_callable,
                        ):
                            with mock.patch("builtins.print"):
                                noxfile.release_gate(FakeSession())

        pin_mock.assert_not_called()
        self.assertNotIn("tier0:mmd-anim-pin", callable_names)
        self.assertIn("tier0:version-markers", callable_names)
        self.assertGreater(command_mock.call_count, 0)
        self.assertEqual(command_mock.call_args_list[0].args[0], "tier0:ruff")

    def test_callable_failure_error_is_used_by_aggregate_summary(self):
        result = {
            "name": "tier0:version-markers",
            "status": "fail",
            "error": "pluginMain.cpp version does not match",
        }

        self.assertEqual(
            noxfile._release_gate_failure_label(result),
            "pluginMain.cpp version does not match",
        )

    def test_version_check_rejects_cpp_plugin_mismatch(self):
        real_read_text = Path.read_text

        def mismatched_read_text(path, *args, **kwargs):
            if path == noxfile.ROOT / "cpp" / "src" / "pluginMain.cpp":
                return 'MFnPlugin plugin(obj, "yohawing", "9.9.9", "Any");'
            return real_read_text(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", mismatched_read_text):
            with self.assertRaisesRegex(RuntimeError, "pluginMain.cpp version"):
                noxfile._release_gate_version_check()

    def test_child_skip_is_not_treated_as_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "child.json"
            log = Path(directory) / "child.log"
            log.write_text("child output\n", encoding="utf-8")
            report.write_text('{"status":"skip"}', encoding="utf-8")
            results = []
            with mock.patch("noxfile._run_logged_subprocess", return_value=(0, log, (0, 0))):
                with mock.patch.object(Path, "unlink"):
                    with mock.patch("builtins.print"):
                        noxfile._run_release_gate_command("local", ["child"], results, result_report=report)
            self.assertEqual(results[0]["status"], "skip")

    def test_command_output_is_logged_and_repeated_warnings_are_summarized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "child.log"
            log_path.write_text(
                "Warning: repeated\nWarning: repeated\nUNKNOWN_DIAGNOSTIC payload\n",
                encoding="utf-8",
            )
            results = []
            with mock.patch.object(noxfile, "ROOT", root):
                with mock.patch(
                    "noxfile._run_logged_subprocess",
                    return_value=(0, log_path, (2, 1)),
                ):
                    with mock.patch("builtins.print") as print_mock:
                        noxfile._run_release_gate_command("tier:test", ["child"], results)

            transcript = log_path.read_text(encoding="utf-8")

        self.assertEqual(results[0]["repeated_warnings_suppressed"], 1)
        self.assertEqual(transcript.count("Warning: repeated"), 2)
        self.assertIn("UNKNOWN_DIAGNOSTIC payload", transcript)
        terminal = "\n".join(str(call.args[0]) for call in print_mock.call_args_list)
        self.assertIn("tests=1 pass=1 skip=0 fail=0", terminal)
        self.assertIn("repeated warnings suppressed from terminal: 1", terminal)

    def test_strict_local_promotes_required_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "child.json"
            log = Path(directory) / "child.log"
            log.write_text("child output\n", encoding="utf-8")
            report.write_text('{"status":"skipped"}', encoding="utf-8")
            results = []
            with mock.patch("noxfile._run_logged_subprocess", return_value=(0, log, (0, 0))):
                with mock.patch.object(Path, "unlink"):
                    with mock.patch("builtins.print"):
                        noxfile._run_release_gate_command(
                            "local", ["child"], results, result_report=report,
                            required_local=True, strict_local=True,
                        )
            self.assertEqual(results[0]["status"], "fail")

    def test_report_summary_keeps_optional_skip_and_passes_aggregate(self):
        results = [
            {"name": "unit", "status": "pass", "duration_sec": 1.0, "command": ["unit"]},
            {"name": "optional", "status": "skip", "duration_sec": 0.0, "command": ["optional"]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(noxfile, "ROOT", root):
                md_path, json_path = noxfile._write_release_gate_reports(results, quick=False)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"], {"pass": 1, "fail": 0, "skip": 1})
            self.assertIn("pass=1, fail=0, skip=1", md_path.read_text(encoding="utf-8"))

    def test_local_child_report_all_skip_and_strict_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "local-assets.json"
            markdown = Path(directory) / "local-assets.md"
            report.write_text(
                json.dumps({"status": "pass", "results": [{"status": "skip"}, {"status": "skipped"}]}),
                encoding="utf-8",
            )
            markdown.write_text(
                "# Local Assets Check\n\n- Status: pass\n\n| Asset | Status |\n| --- | --- |\n| fixture | skip |\n",
                encoding="utf-8",
            )
            self.assertEqual(
                noxfile._normalize_local_gate_report(report, strict_local=False, markdown_path=markdown),
                "skip",
            )
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"], {"pass": 0, "fail": 0, "skip": 2})
            markdown_text = markdown.read_text(encoding="utf-8")
            self.assertIn("- Status: skip", markdown_text)
            self.assertIn("- Summary: pass=0, fail=0, skip=2", markdown_text)
            self.assertIn("| fixture | skip |", markdown_text)
            self.assertEqual(
                noxfile._normalize_local_gate_report(report, strict_local=True, markdown_path=markdown),
                "fail",
            )
            strict_markdown = markdown.read_text(encoding="utf-8")
            self.assertIn("- Status: fail", strict_markdown)
            self.assertEqual(strict_markdown.count("- Summary:"), 1)
            self.assertIn("| fixture | skip |", strict_markdown)

    def test_local_child_report_failure_wins_and_pass_with_skip_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "local-assets.json"
            report.write_text(
                json.dumps({"results": [{"status": "pass"}, {"status": "skip"}]}),
                encoding="utf-8",
            )
            self.assertEqual(noxfile._normalize_local_gate_report(report, strict_local=True), "pass")
            report.write_text(
                json.dumps({"results": [{"status": "pass"}, {"status": "fail"}]}),
                encoding="utf-8",
            )
            self.assertEqual(noxfile._normalize_local_gate_report(report, strict_local=False), "fail")

    def test_full_release_gate_includes_bundled_native_smoke(self):
        commands = tier2_commands(
            version="2024",
            cpp_versions=[],
            cpp_config="Debug",
            release_maya_versions=(),
            viewport_matrix=(),
            visual_manifest=Path("missing-render-manifest.json"),
            visual_ports={},
            visual_cases=lambda _shader_backend: (),
            include_cpp=False,
            verbose=False,
        )
        self.assertIn(
            ("tier2:bundled-native-smoke", ["uvx", "nox", "-s", "bundled_native_smoke"]),
            commands,
        )

    def test_full_release_gate_includes_native_physics_release_gate(self):
        commands = tier2_commands(
            version="2024",
            cpp_versions=[],
            cpp_config="Debug",
            release_maya_versions=(),
            viewport_matrix=(),
            visual_manifest=Path("missing-render-manifest.json"),
            visual_ports={},
            visual_cases=lambda _shader_backend: (),
            include_cpp=False,
            verbose=False,
        )
        self.assertIn(
            (
                "tier2:native-physics-release-gate",
                ["uvx", "nox", "-s", "native_physics_release_gate"],
            ),
            commands,
        )
        gate_source = inspect.getsource(noxfile.native_physics_release_gate)
        self.assertIn('tests/data/physics/test_hair_physics.pmx', gate_source)
        self.assertIn('tests/data/mmt_test_model_test_motion.vmd', gate_source)
        self.assertIn("_bundled_physics_runtime()", gate_source)

    def test_bundled_physics_runtime_selects_supported_platform(self):
        windows = noxfile._bundled_physics_runtime("Windows")
        macos = noxfile._bundled_physics_runtime("Darwin")
        self.assertTrue(str(windows).replace("\\", "/").endswith("mmd_tools/native/win64/mmd_runtime_ffi.dll"))
        self.assertTrue(str(macos).replace("\\", "/").endswith("mmd_tools/native/macos/libmmd_runtime_ffi.dylib"))
        with self.assertRaisesRegex(RuntimeError, "unsupported on Linux"):
            noxfile._bundled_physics_runtime("Linux")

    def test_native_physics_release_gate_clears_all_reports_before_inputs(self):
        source = inspect.getsource(noxfile.native_physics_release_gate)
        cleanup = source.index("for stale_report in")
        input_check = source.index("for required in")
        self.assertLess(cleanup, input_check)
        self.assertIn("(*run_reports, comparison_json, comparison_md)", source)


if __name__ == "__main__":
    unittest.main()
