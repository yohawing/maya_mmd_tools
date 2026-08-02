"""Focused tests for Maya render-session command construction."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from noxlib.common import _has_flag, _option, _options
from noxlib.maya_sessions import run_static_render, run_visual_regression


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


if __name__ == "__main__":
    unittest.main()
