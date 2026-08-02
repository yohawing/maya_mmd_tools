"""Focused tests for native-session command delegation."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from noxlib.common import _option, _without_option
from noxlib.native_sessions import (
    run_cpp_build,
    run_maya_smoke,
    run_native_export_smoke,
    run_reduction_abi_probe,
)
from noxlib.maya_sessions import run_cpp_plugin_smoke
from noxlib.maya_sessions import (
    run_model_readme_dialog_e2e,
    run_viewport_capture,
    run_yw_test_model_fixture_gate,
    run_native_physics_bake,
)


class _FakeSession:
    def __init__(self, posargs=None):
        self.posargs = list(posargs or [])
        self.runs = []

    def run(self, *args, **kwargs):
        self.runs.append((args, kwargs))


class NativeSessionsTest(unittest.TestCase):
    def test_native_export_smoke_removes_ffi_path_from_child_arguments(self):
        session = _FakeSession(["--strict", "--ffi-path", "build/custom-ffi"])
        run_native_export_smoke(
            session,
            posargs=session.posargs,
            option=_option,
            without_option=_without_option,
            resolve_existing_or_repo_path=lambda value: Path("F:/resolved") / value,
        )
        args, kwargs = session.runs[0]
        self.assertEqual(args, (sys.executable, "tests/native_export_smoke.py", "--strict"))
        self.assertEqual(kwargs["env"]["MMD_ANIM_FFI_PATH"], str(Path("F:/resolved") / "build/custom-ffi"))
        self.assertTrue(kwargs["external"])

    def test_reduction_probe_resolves_paths_and_build_reports(self):
        session = _FakeSession(["--ffi-path", "ffi", "--out-json", "build/probe.json"])

        def require_build_path(_session, value, _option_name):
            return Path("F:/root") / value

        run_reduction_abi_probe(
            session,
            posargs=session.posargs,
            option=_option,
            resolve_existing_or_repo_path=lambda value: Path("F:/root") / value,
            require_build_path=require_build_path,
        )
        args, kwargs = session.runs[0]
        self.assertEqual(args[0], sys.executable)
        self.assertEqual(args[1], "tests/release/reduction_abi_probe.py")
        self.assertEqual(args[2], "--ffi-path")
        self.assertEqual(Path(args[3]), Path("F:/root/ffi"))
        self.assertIn(str(Path("F:/root/build/probe.json")), args)
        self.assertIn(str(Path("F:/root/build/reports/reduction_abi_probe.md")), args)
        self.assertTrue(kwargs["external"])

    def test_cpp_build_keeps_configure_before_build(self):
        session = _FakeSession(["--maya", "2026", "--config", "Release"])
        calls = []
        run_cpp_build(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2024",
            default_config="Debug",
            configure=lambda *args: calls.append(("configure", args)),
            build=lambda *args: calls.append(("build", args)),
        )
        self.assertEqual(calls, [("configure", (session, "2026", "Release")), ("build", (session, "2026", "Release"))])

    def test_maya_smoke_runs_all_runtime_scripts_with_one_environment(self):
        session = _FakeSession(["--maya", "2024", "--config", "Debug"])
        mayapy = types.SimpleNamespace(exists=lambda: True)
        env = {"MAYA_VERSION": "2024"}
        run_maya_smoke(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2026",
            default_config="Release",
            mayapy=lambda _version: mayapy,
            mayapy_env=lambda _mayapy, **_values: env,
            mayapy_script=lambda _mayapy, script: script,
        )
        self.assertEqual([args[1] for args, _kwargs in session.runs], [
            "tests/cpp/smoke_python_rig_fallback.py",
            "tests/cpp/smoke_runtime_node.py",
            "tests/cpp/focused_physics_solver_world_toggle.py",
        ])
        self.assertTrue(all(kwargs["env"] is env for _args, kwargs in session.runs))

    def test_cpp_plugin_smoke_constructs_plugin_environment_for_each_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "plug-ins" / "2024" / "Debug" / "mmd_tools_cpp.mll"
            plugin.parent.mkdir(parents=True)
            plugin.write_bytes(b"plugin")
            session = _FakeSession(["--maya", "2024", "--config", "Debug"])
            mayapy = mock.Mock()
            mayapy.exists.return_value = True
            env = {"MAYA_VERSION": "2024"}
            run_cpp_plugin_smoke(
                session,
                posargs=session.posargs,
                option=_option,
                default_maya_version="2026",
                default_config="Release",
                root=root,
                mayapy=lambda _version: mayapy,
                mayapy_env=lambda _mayapy, **values: (env.update(values) or env),
                mayapy_arg_path=lambda _mayapy, path: str(path),
                mayapy_script=lambda _mayapy, script: script,
                scripts=("tests/cpp/a.py", "tests/cpp/b.py"),
                require_plugin=True,
            )
        self.assertEqual(len(session.runs), 2)
        self.assertEqual(session.runs[0][0][1], "tests/cpp/a.py")
        self.assertEqual(session.runs[1][0][1], "tests/cpp/b.py")
        self.assertEqual(env["MMD_TOOLS_CPP_CONFIG"], "Debug")
        self.assertEqual(Path(env["MMD_TOOLS_CPP_PLUGIN"]), plugin)

    def test_viewport_capture_forwards_dimensions_without_plugin_environment(self):
        session = _FakeSession(["--maya", "2024", "--out", "build/capture.png", "--width", "320"])
        mayapy = mock.Mock()
        mayapy.exists.return_value = True
        env = {"MAYA_VERSION": "2024"}
        run_viewport_capture(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2026",
            root=Path("F:/repo"),
            mayapy=lambda _version: mayapy,
            mayapy_env=lambda _mayapy, **_values: env,
            mayapy_arg_path=lambda _mayapy, path: str(path),
            mayapy_script=lambda _mayapy, script: script,
        )
        args, kwargs = session.runs[0]
        self.assertEqual(args[1], "tests/viewport/smoke_viewport_capture.py")
        self.assertEqual(args[-6:], ("--frame", "1", "--width", "320", "--height", "480"))
        self.assertNotIn("MMD_TOOLS_CPP_PLUGIN", kwargs["env"])

    def test_model_readme_gate_validates_each_child_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out_dir = root / "reports"
            out_dir.mkdir()
            for version in ("2024", "2026"):
                (out_dir / f"maya-{version}.json").write_text('{"status": "pass"}\n', encoding="utf-8")
            session = _FakeSession(["--out-dir", "reports"])
            run_model_readme_dialog_e2e(
                session,
                posargs=session.posargs,
                options=lambda _args, _name: ["2024", "2026"],
                option=_option,
                root=root,
                require_build_path=lambda _session, value, _name: root / value,
                python_executable="python.exe",
            )
        self.assertEqual(len(session.runs), 2)
        self.assertEqual(session.runs[0][0][-1], "7731")
        self.assertEqual(session.runs[1][0][-1], "7732")

    def test_yw_fixture_gate_preserves_manifest_and_report_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "fixture.json"
            manifest.write_text("{}\n", encoding="utf-8")
            out_dir = root / "out"
            out_dir.mkdir()
            (out_dir / "maya-2024.json").write_text("{}\n", encoding="utf-8")
            session = _FakeSession(["--maya", "2024", "--manifest", str(manifest), "--out-dir", "out"])
            mayapy = mock.Mock()
            mayapy.exists.return_value = True
            run_yw_test_model_fixture_gate(
                session,
                posargs=session.posargs,
                options=lambda _args, _name: ["2024"],
                option=_option,
                default_maya_versions=("2024", "2026"),
                root=root,
                require_build_path=lambda _session, value, _name: root / value,
                mayapy=lambda _version: mayapy,
                mayapy_env=lambda _mayapy, **_values: {},
                mayapy_arg_path=lambda _mayapy, path: str(path),
                mayapy_script=lambda _mayapy, script: script,
            )
        args, _kwargs = session.runs[0]
        self.assertEqual(args[1], "tests/viewport/yw_test_model_fixture_gate.py")
        self.assertIn(str(manifest), args)
        self.assertIn(str(out_dir / "maya-2024.json"), args)

    def test_native_physics_bake_keeps_capture_and_route_argument_shapes(self):
        mayapy = mock.Mock()
        mayapy.exists.return_value = True
        for verify_bake_route in (False, True):
            session = _FakeSession(["--maya", "2024", "--ffi-path", "build/ffi"])
            run_native_physics_bake(
                session,
                posargs=session.posargs,
                option=_option,
                default_maya_version="2026",
                root=Path("F:/repo"),
                resolve_existing_or_repo_path=lambda value: Path("F:/repo") / value,
                mayapy=lambda _version: mayapy,
                mayapy_env=lambda _mayapy, **values: values,
                mayapy_arg_path=lambda _mayapy, path: str(path),
                mayapy_script=lambda _mayapy, script: script,
                verify_bake_route=verify_bake_route,
            )
            args, kwargs = session.runs[0]
            if verify_bake_route:
                self.assertIn("--verify-bake-route", args)
                self.assertIn("--eval-frames", args)
                self.assertNotIn("--out", args)
            else:
                self.assertNotIn("--verify-bake-route", args)
                self.assertIn("--out", args)
                self.assertIn("--width", args)
            self.assertEqual(kwargs["env"]["MMD_ANIM_FFI_PATH"], str(Path("F:/repo/build/ffi")))


if __name__ == "__main__":
    unittest.main()
