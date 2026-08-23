"""Focused tests for native-session command delegation."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tools.nox.common import _option, _without_option
from tools.nox.native_sessions import (
    run_cpp_build,
    run_cpp_cli_smoke,
    run_cpp_verify,
    run_maya_smoke,
    run_native_export_smoke,
    run_reduction_abi_probe,
)
from tools.nox.maya_sessions import (
    run_cpp_plugin_smoke,
    run_control_rig_gui_e2e,
    run_import_order_e2e,
    run_import_scale_drift_e2e,
    run_anim_layer_graph_compare,
    run_model_readme_dialog_e2e,
    run_maya_batch_import,
    run_native_physics_bake,
    run_pmx_roundtrip,
    run_user_roundtrip_smoke,
    run_physics_solver_cycle_probe,
    run_root_move_ik_target_probe,
    run_root_move_skin_parity_probe,
    run_runtime_bake_bench,
    run_viewport_capture,
    run_yw_test_model_fixture_gate,
)


class _FakeSession:
    def __init__(self, posargs=None):
        self.posargs = list(posargs or [])
        self.runs = []

    def run(self, *args, **kwargs):
        self.runs.append((args, kwargs))


class NativeSessionsTest(unittest.TestCase):
    def test_control_rig_gui_e2e_runs_external_oracle_after_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out_dir = root / "build/e2e"
            out_dir.mkdir(parents=True)
            (out_dir / "mmd_control_rig_e2e_maya2024.vmd").write_bytes(b"vmd")
            (root / "external/mmd-anim/target/release").mkdir(parents=True)
            session = _FakeSession(["--maya", "2024"])
            mayapy = mock.Mock()
            mayapy.exists.return_value = True

            def read_report(_session, report_path, label):
                if "GUI" in label:
                    return {"status": "pass"}
                return {"status": "passed", "comparison": {"max": 0.0}}

            run_control_rig_gui_e2e(
                session,
                posargs=session.posargs,
                option=_option,
                default_maya_version="2026",
                root=root,
                require_build_path=lambda _session, value, _name: root / value,
                read_probe_report=read_report,
                clear_probe_report=lambda *_args: None,
                mayapy=lambda _version: mayapy,
                mayapy_env=lambda _mayapy, **values: values,
                mayapy_arg_path=lambda _mayapy, path: str(path),
                mayapy_script=lambda _mayapy, script: script,
                python_executable="python.exe",
            )
            self.assertEqual(session.runs[0][0][1], str(root / "tests/viewport/e2e_mmd_control_rig.py"))
            self.assertEqual(session.runs[1][0][1], "tests/viewport/mmd_anim_mesh_oracle_compare.py")
            self.assertEqual(session.runs[1][1]["success_codes"], (0, 1, 2))

    def test_control_rig_gui_e2e_tracks_focused_report_and_auto_bake_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out_dir = root / "build/e2e"
            out_dir.mkdir(parents=True)
            suffix = "_create_on_import_auto_bake_only_frames_0_120_release"
            exported_vmd = out_dir / f"mmd_control_rig_e2e_maya2024{suffix}.auto_bake.vmd"
            exported_vmd.write_bytes(b"vmd")
            (root / "external/mmd-anim/target/release").mkdir(parents=True)
            session = _FakeSession(
                [
                    "--maya",
                    "2024",
                    "--create-on-import",
                    "--auto-bake-only",
                    "--auto-frame-range",
                    "0",
                    "120",
                    "--cpp-config",
                    "Release",
                ]
            )
            reports = []
            mayapy = mock.Mock()
            mayapy.exists.return_value = True

            def read_report(_session, report_path, label):
                reports.append((report_path, label))
                if "GUI" in label:
                    return {"status": "pass"}
                return {"status": "passed", "comparison": {"max": 0.0}}

            run_control_rig_gui_e2e(
                session,
                posargs=session.posargs,
                option=_option,
                default_maya_version="2026",
                root=root,
                require_build_path=lambda _session, value, _name: root / value,
                read_probe_report=read_report,
                clear_probe_report=lambda *_args: None,
                mayapy=lambda _version: mayapy,
                mayapy_env=lambda _mayapy, **values: values,
                mayapy_arg_path=lambda _mayapy, path: str(path),
                mayapy_script=lambda _mayapy, script: script,
                python_executable="python.exe",
            )

            self.assertEqual(
                reports[0][0],
                out_dir / f"mmd_control_rig_e2e_maya2024{suffix}.json",
            )
            oracle_args = session.runs[1][0]
            self.assertEqual(oracle_args[oracle_args.index("--vmd") + 1], str(exported_vmd))
            self.assertIn(
                str(root / "plug-ins/2024/Release/mmd_tools_cpp.mll"),
                session.runs[1][1]["env"]["MMD_TOOLS_CPP_PLUGIN"],
            )

    def test_remaining_mayapy_diagnostics_keep_scripts_and_filter_maya(self):
        runners = (
            (run_import_scale_drift_e2e, "tests/viewport/import_scale_drift_e2e.py"),
            (run_anim_layer_graph_compare, "tests/viewport/anim_layer_graph_compare.py"),
            (run_runtime_bake_bench, "tests/viewport/runtime_bake_benchmark.py"),
        )
        for runner, script in runners:
            session = _FakeSession(["--maya", "2026", "--case", "case"])
            mayapy = mock.Mock()
            runner(
                session,
                posargs=session.posargs,
                option=_option,
                mayapy=lambda _version: mayapy,
                mayapy_env=lambda _mayapy, **values: values,
                mayapy_script=lambda _mayapy, child: child,
                convert_mayapy_path_options=lambda _mayapy, args, _options: args,
            )
            args, _kwargs = session.runs[0]
            self.assertEqual(args[1], script)
            self.assertNotIn("--maya", args)

    def test_import_order_e2e_generates_manifest_and_profile_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _FakeSession(["--maya", "2024", "--require-zero-fallback"])
            mayapy = mock.Mock()
            run_import_order_e2e(
                session,
                posargs=session.posargs,
                option=_option,
                has_flag=lambda args, flag: flag in args,
                root=root,
                mayapy=lambda _version: mayapy,
                mayapy_env=lambda _mayapy, **values: values,
                mayapy_script=lambda _mayapy, child: child,
                convert_mayapy_path_options=lambda _mayapy, args, _options: args,
                write_local_manifest=lambda *_args: root / "build/generated-manifest.json",
            )
            args, kwargs = session.runs[0]
            self.assertEqual(args[1], "tests/viewport/import_order_e2e.py")
            self.assertIn("--manifest", args)
            self.assertIn("MMD_TOOLS_VMD_PROFILE_JSONL", kwargs["env"])

    def test_cpp_cli_smoke_requires_manifest_and_forwards_selection(self):
        session = _FakeSession(
            ["--maya", "2026", "--config", "Release", "--manifest", "manifest.json", "--case", "case", "--limit", "2"]
        )
        calls = []
        run_cpp_cli_smoke(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2024",
            default_config="Debug",
            run_cli_smoke=lambda *args: calls.append(args),
        )
        self.assertEqual(calls, [(session, "2026", "Release", "manifest.json", "case", "2")])

    def test_cpp_verify_keeps_native_cli_and_mayapy_order(self):
        session = _FakeSession(
            ["--maya", "2026", "--config", "Release", "--manifest", "manifest.json", "--case", "case"]
        )
        calls = []
        mayapy = mock.Mock()
        mayapy.exists.return_value = True
        run_cpp_verify(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2024",
            default_config="Debug",
            root=Path("F:/repo"),
            configure_bullet3_dir=lambda _session, _env: calls.append("bullet"),
            native_runtime_smoke_code=lambda: "runtime-smoke",
            configure=lambda *args: calls.append(("configure", args)),
            build=lambda *args, **kwargs: calls.append(("build", args, kwargs)),
            run_cli_smoke=lambda *args: calls.append(("cli", args)),
            mayapy=lambda _version: mayapy,
            mayapy_env=lambda _mayapy, **values: values,
            mayapy_script=lambda _mayapy, script: script,
            python_executable="python.exe",
        )
        self.assertEqual(calls[:4], [
            "bullet",
            ("configure", (session, "2026", "Release")),
            ("build", (session, "2026", "Release"), {"clean_first": True}),
            ("cli", (session, "2026", "Release", "manifest.json", "case", "")),
        ])
        self.assertEqual(session.runs[0][0][:5], ("cargo", "build", "-p", "mmd-anim-ffi", "--manifest-path"))
        self.assertEqual(session.runs[1][0][:2], ("python.exe", "-c"))
        self.assertEqual([run[0][1] for run in session.runs[2:]], [
            "tests/cpp/smoke_runtime_node.py",
            "tests/cpp/focused_physics_solver_world_toggle.py",
        ])

    def test_maya_batch_import_removes_nox_maya_option(self):
        session = _FakeSession(["--maya", "2024", "--manifest", "manifest.json", "--limit", "1"])
        mayapy = mock.Mock()
        mayapy.exists.return_value = True
        run_maya_batch_import(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2026",
            root=Path("F:/repo"),
            mayapy=lambda _version: mayapy,
            mayapy_env=lambda _mayapy, **values: values,
            mayapy_script=lambda _mayapy, script: script,
            convert_mayapy_path_options=lambda _mayapy, args, _options: args,
        )
        args, kwargs = session.runs[0]
        self.assertEqual(args[1:], ("tests/track6/track6_runner.py", "--manifest", "manifest.json", "--limit", "1"))
        self.assertEqual(kwargs["env"]["MAYA_VERSION"], "2024")

    def test_pmx_roundtrip_adds_default_manifest_and_shader_environment(self):
        session = _FakeSession(["--maya", "2025"])
        mayapy = mock.Mock()
        mayapy.exists.return_value = True
        run_pmx_roundtrip(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2024",
            root=Path("F:/repo"),
            mayapy=lambda _version: mayapy,
            mayapy_env=lambda _mayapy, **values: values,
            mayapy_script=lambda _mayapy, script: script,
            convert_mayapy_path_options=lambda _mayapy, args, _options: args,
        )
        args, kwargs = session.runs[0]
        self.assertEqual(args[1], "tests/roundtrip/pmx_roundtrip_runner.py")
        self.assertIn(str(Path("F:/repo/tests/roundtrip/manifest_template.json")), args)
        self.assertEqual(kwargs["env"]["MAYA_SKIP_USERSETUP_PY"], "1")
        self.assertEqual(kwargs["env"]["MMD_TOOLS_SKIP_SHADER_OVERRIDE"], "1")

    def test_user_roundtrip_smoke_host_python_forwards_runner_arguments(self):
        session = _FakeSession(["--maya", "2024", "--case", "miku"])
        run_user_roundtrip_smoke(
            session,
            posargs=session.posargs,
            root=Path("F:/repo"),
            python_executable="python-test",
        )
        args, kwargs = session.runs[0]
        self.assertEqual(args, ("python-test", str(Path("F:/repo/tools/local_asset_roundtrip.py")), "--maya", "2024", "--case", "miku"))
        self.assertTrue(kwargs["external"])

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
            "tests/cpp/focused_vmd_batch_sampler.py",
            "tools/maya_authoring_command_support_smoke.py",
            "tools/maya_morph_binding_query_smoke.py",
            "tools/maya_morph_weight_command_smoke.py",
            "tools/maya_material_value_command_smoke.py",
            "tools/maya_material_outline_command_smoke.py",
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

    def test_report_only_maya_probes_keep_their_script_and_default_options(self):
        cases = (
            (run_physics_solver_cycle_probe, "tests/viewport/physics_solver_cycle_probe.py", "--frames"),
            (run_root_move_skin_parity_probe, "tests/viewport/root_move_skin_parity_probe.py", "--delta"),
            (run_root_move_ik_target_probe, "tests/viewport/root_move_ik_target_probe.py", "--expect-root-parity"),
        )
        for runner, script, expected_option in cases:
            session = _FakeSession(["--maya", "2026"])
            probe_calls = []
            runner(
                session,
                posargs=session.posargs,
                option=_option,
                default_maya_version="2024",
                root=Path("F:/repo"),
                mayapy=lambda version: f"mayapy-{version}",
                clear_probe_report=lambda *_args: None,
                run_mayapy_probe=lambda *args, **kwargs: probe_calls.append((args, kwargs)),
                read_probe_report=lambda *_args: {"status": "pass"},
            )
            self.assertEqual(probe_calls[0][0][2], script)
            self.assertIn(expected_option, probe_calls[0][0][3])


if __name__ == "__main__":
    unittest.main()
