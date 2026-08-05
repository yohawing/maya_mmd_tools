"""Focused tests for Maya render-session command construction."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from tools.nox.common import _has_flag, _option, _options
from tools.nox.maya_sessions import (
    run_render_override_e2e,
    run_render_override_smoke,
    run_static_render,
    run_visual_regression,
)


class _FakeSession:
    def __init__(self, posargs):
        self.posargs = list(posargs)
        self.runs = []

    def run(self, *args, **kwargs):
        self.runs.append((args, kwargs))

    def error(self, message):
        raise AssertionError(message)


class NoxRenderSessionsTest(unittest.TestCase):
    def test_static_render_preserves_shader_backend_and_viewport_override(self):
        session = _FakeSession([
            "--maya", "2024", "--shader", "--shader-backend", "glsl", "--vp2-device", "glcore",
            "--diagnostics-out", "build/render.json",
        ])
        mayapy = mock.Mock()
        mayapy.exists.return_value = True
        env = {}
        run_static_render(
            session,
            posargs=session.posargs,
            option=_option,
            has_flag=_has_flag,
            default_maya_version="2026",
            root=Path("F:/repo"),
            require_build_path=lambda _session, value, _name: Path("F:/repo") / value,
            mayapy=lambda _version: mayapy,
            mayapy_env=lambda _mayapy, **_values: env,
            mayapy_arg_path=lambda _mayapy, path: str(path),
            mayapy_script=lambda _mayapy, script: script,
        )
        args, kwargs = session.runs[0]
        self.assertIn("--shader", args)
        self.assertIn("--shader-backend", args)
        self.assertIn("glsl", args)
        self.assertIn("--diagnostics-out", args)
        self.assertEqual(env["MAYA_VP2_DEVICE_OVERRIDE"], "VirtualDeviceGLCore")
        self.assertTrue(kwargs["external"])

    def test_visual_regression_forwards_case_threshold_and_comparison(self):
        session = _FakeSession([
            "--maya", "2026", "--manifest", "render.json", "--shader-backend", "dx11",
            "--case", "case-a", "--threshold", "0.12",
        ])
        run_visual_regression(
            session,
            posargs=session.posargs,
            option=_option,
            options=_options,
            has_flag=_has_flag,
            default_maya_version="2024",
            require_build_path=lambda _session, value, _name: Path("F:/repo") / value,
            python_executable="python.exe",
        )
        self.assertEqual(len(session.runs), 2)
        capture_args, capture_kwargs = session.runs[0]
        compare_args, compare_kwargs = session.runs[1]
        self.assertIn("--case", capture_args)
        self.assertIn("case-a", capture_args)
        self.assertIn("--threshold", compare_args)
        self.assertIn("0.12", compare_args)
        self.assertTrue(capture_kwargs["external"])
        self.assertTrue(compare_kwargs["external"])

    def test_render_override_smoke_enables_only_opt_in_override(self):
        session = _FakeSession(["--maya", "2024", "--out", "build/captures/r1.png"])
        mayapy = mock.Mock()
        mayapy.exists.return_value = True
        env = {}
        run_render_override_smoke(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2026",
            root=Path("F:/repo"),
            mayapy=lambda _version: mayapy,
            mayapy_env=lambda _mayapy, **values: env.update(values) or env,
            mayapy_arg_path=lambda _mayapy, path: str(path),
            mayapy_script=lambda _mayapy, script: script,
        )
        args, kwargs = session.runs[0]
        self.assertIn("tests/viewport/smoke_render_override.py", args)
        self.assertEqual(env["MMD_TOOLS_ENABLE_RENDER_OVERRIDE"], "1")
        self.assertEqual(env["MMD_TOOLS_SKIP_SHADER_OVERRIDE"], "1")
        self.assertTrue(kwargs["external"])

    def test_render_override_e2e_forwards_requested_vp2_device(self):
        session = _FakeSession(
            [
                "--maya",
                "2026",
                "--vp2-device",
                "dx11",
                "--target-probe",
                "--r32f-binding-probe",
                "--r32f-caster-pass",
                "--r32f-receiver-probe",
                "--r32f-light-space-caster",
                "--native-shadow-request",
                "--native-shadow-binding-probe",
                "--model",
                "F:/fixtures/self-shadow.pmx",
            ]
        )
        run_render_override_e2e(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2026",
            root=Path("F:/repo"),
        )
        args, kwargs = session.runs[0]
        self.assertIn("--vp2-device", args)
        self.assertEqual(args[args.index("--vp2-device") + 1], "dx11")
        self.assertIn("--target-probe", args)
        self.assertIn("--r32f-binding-probe", args)
        self.assertIn("--r32f-caster-pass", args)
        self.assertIn("--r32f-receiver-probe", args)
        self.assertIn("--r32f-light-space-caster", args)
        self.assertIn("--native-shadow-request", args)
        self.assertIn("--native-shadow-binding-probe", args)
        self.assertIn("--model", args)
        self.assertEqual(args[args.index("--model") + 1], "F:/fixtures/self-shadow.pmx")
        self.assertTrue(kwargs["external"])

    def test_render_override_e2e_rejects_model_without_target_probe(self):
        session = _FakeSession(["--model", "F:/fixtures/self-shadow.pmx"])
        with self.assertRaises(AssertionError):
            run_render_override_e2e(
                session,
                posargs=session.posargs,
                option=_option,
                default_maya_version="2026",
                root=Path("F:/repo"),
            )

    def test_render_override_e2e_rejects_binding_probe_without_target_probe(self):
        session = _FakeSession(["--r32f-binding-probe"])
        with self.assertRaises(AssertionError):
            run_render_override_e2e(
                session,
                posargs=session.posargs,
                option=_option,
                default_maya_version="2026",
                root=Path("F:/repo"),
            )

    def test_render_override_e2e_rejects_caster_pass_without_target_probe(self):
        session = _FakeSession(["--r32f-caster-pass"])
        with self.assertRaises(AssertionError):
            run_render_override_e2e(
                session,
                posargs=session.posargs,
                option=_option,
                default_maya_version="2026",
                root=Path("F:/repo"),
            )

    def test_render_override_e2e_rejects_receiver_probe_without_caster_pass(self):
        session = _FakeSession(["--r32f-receiver-probe"])
        with self.assertRaises(AssertionError):
            run_render_override_e2e(
                session,
                posargs=session.posargs,
                option=_option,
                default_maya_version="2026",
                root=Path("F:/repo"),
            )

    def test_render_override_e2e_rejects_light_space_caster_without_caster_pass(self):
        session = _FakeSession(["--r32f-light-space-caster"])
        with self.assertRaises(AssertionError):
            run_render_override_e2e(
                session,
                posargs=session.posargs,
                option=_option,
                default_maya_version="2026",
                root=Path("F:/repo"),
            )

    def test_render_override_e2e_accepts_native_shadow_binding_without_request(self):
        session = _FakeSession(["--native-shadow-binding-probe"])
        run_render_override_e2e(
            session,
            posargs=session.posargs,
            option=_option,
            default_maya_version="2026",
            root=Path("F:/repo"),
        )
        self.assertIn("--native-shadow-binding-probe", session.runs[0][0])


if __name__ == "__main__":
    unittest.main()
