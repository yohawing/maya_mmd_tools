"""Unit coverage for the local camera-motion oracle runner policy helpers."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

from tests.common.maya_stub import install_maya_stub


install_maya_stub(profile="headless")
maya_module = sys.modules["maya"]
maya_module.__path__ = []
standalone_module = ModuleType("maya.standalone")
standalone_module.initialize = MagicMock(name="maya.standalone.initialize")
maya_module.standalone = standalone_module
sys.modules["maya.standalone"] = standalone_module


def _load_runner():
    path = Path(__file__).resolve().parents[1] / "local" / "camera_motion_oracle_runner.py"
    spec = importlib.util.spec_from_file_location("camera_motion_oracle_runner_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


class _Vector:
    def __init__(self, x: float, y: float, z: float):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __sub__(self, other: "_Vector") -> "_Vector":
        return _Vector(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, other):
        if isinstance(other, _Vector):
            return self.x * other.x + self.y * other.y + self.z * other.z
        return _Vector(self.x * other, self.y * other, self.z * other)

    def length(self) -> float:
        return (self.x * self.x + self.y * self.y + self.z * self.z) ** 0.5

    def normalize(self) -> None:
        length = self.length()
        if length > 0.0:
            self.x /= length
            self.y /= length
            self.z /= length


class TestCameraMotionOracleRunner(unittest.TestCase):
    def test_current_frame_zero_policy_auto_skips_real_dump_cases(self):
        self.assertTrue(runner._skip_current_frame_zero({"name": "camera-shake-it"}, "auto"))
        self.assertTrue(runner._skip_current_frame_zero({"name": "camera-weekender-girl"}, "auto"))


    def test_current_frame_zero_policy_auto_keeps_generated_cases(self):
        self.assertFalse(runner._skip_current_frame_zero({"name": "camera-edge-generated-vmd"}, "auto"))
        self.assertFalse(runner._skip_current_frame_zero({"name": "camera-interpolation-isolated-vmd"}, "auto"))


    def test_current_frame_zero_policy_explicit_override(self):
        self.assertFalse(runner._skip_current_frame_zero({"name": "camera-shake-it"}, "include"))
        self.assertTrue(runner._skip_current_frame_zero({"name": "camera-edge-generated-vmd"}, "skip"))


    def test_signed_camera_distance_keeps_negative_when_camera_aims_at_target(self):
        original_mvector = runner.om.MVector
        runner.om.MVector = _Vector
        try:
            distance = runner._signed_camera_distance(
                _Vector(0.0, 0.0, 10.0),
                _Vector(0.0, 0.0, 0.0),
                _Vector(0.0, 0.0, -1.0),
            )
        finally:
            runner.om.MVector = original_mvector

        self.assertEqual(distance, -10.0)

    def test_signed_camera_distance_restores_positive_when_target_is_behind_camera(self):
        original_mvector = runner.om.MVector
        runner.om.MVector = _Vector
        try:
            distance = runner._signed_camera_distance(
                _Vector(0.0, 0.0, 10.0),
                _Vector(0.0, 0.0, 0.0),
                _Vector(0.0, 0.0, 1.0),
            )
        finally:
            runner.om.MVector = original_mvector

        self.assertEqual(distance, 10.0)

    def test_rotation_diff_wraps_two_pi(self):
        delta = runner._diff_rotation_vector([0.0, 0.0, 0.0], [0.0, 2.0 * 3.141592653589793, 0.0])

        self.assertAlmostEqual(delta, 0.0, places=12)
