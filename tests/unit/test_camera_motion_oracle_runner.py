"""Unit coverage for the local camera-motion oracle runner policy helpers."""

from __future__ import annotations

import importlib.util
import json
import math
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
    def test_repo_camera_motion_manifest_points_to_runnable_generated_fixtures(self):
        manifest = Path(__file__).resolve().parents[1] / "data" / "camera_motion" / "manifest.json"
        manifest_dir, cases = runner._load_manifest(manifest)

        self.assertGreaterEqual(len(cases), 2)
        case_names = {case["name"] for case in cases}
        self.assertIn("camera-edge-generated-vmd", case_names)
        self.assertIn("camera-interpolation-isolated-vmd", case_names)
        for case in cases:
            assets = case.get("assets") or {}
            vmd_path = runner._resolve_path(manifest_dir, assets.get("cameraMotion") or assets.get("motion"))
            oracle_path = runner._resolve_path(manifest_dir, ((case.get("oracle") or {}).get("path")))

            self.assertIsNotNone(vmd_path)
            self.assertTrue(vmd_path.exists(), vmd_path)
            self.assertIsNotNone(oracle_path)
            self.assertTrue(oracle_path.exists(), oracle_path)
            self.assertFalse(runner._skip_current_frame_zero(case, "auto"))

            records = [json.loads(line) for line in oracle_path.read_text(encoding="utf-8").splitlines() if line]
            record_frames = {int(record["frame"]) for record in records}
            if case.get("frames"):
                self.assertLessEqual({int(frame) for frame in case["frames"]}, record_frames)
            selected_records = runner._select_records(case, records, max_frames=240, all_frames=False)
            self.assertGreater(len(selected_records), 0)

            first_record = records[0]
            self.assertIn("current", first_record["camera"])
            self.assertTrue(first_record["camera"].get("keyframes"))

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

    def test_select_frame_numbers_sorts_deduplicates_and_keeps_last(self):
        frames = runner._select_frame_numbers([75, 0, 50, 75, 5780, 74, 5722], max_frames=3, all_frames=False)

        self.assertEqual(frames, [0, 2890, 5780])

    def test_select_frame_numbers_can_select_all_timeline_frames(self):
        frames = runner._select_frame_numbers([3, 1, 3, 2], max_frames=1, all_frames=True)

        self.assertEqual(frames, [1, 2, 3])

    def test_select_frame_numbers_uses_timeline_range_not_key_count(self):
        frames = runner._select_frame_numbers([0, 10], max_frames=4, all_frames=False)

        self.assertEqual(frames, [0, 3, 7, 10])

    def test_parity_drift_summary_excludes_keyframes_for_interpolation_budget(self):
        frames = [0, 5, 10]
        base_state = {
            "position": [0.0, 0.0, 0.0],
            "distance": -10.0,
            "rotation": [0.0, 0.0, 0.0],
            "fov": 45.0,
            "transformForward": [0.0, 0.0, -1.0],
            "transformUp": [0.0, 1.0, 0.0],
            "worldMatrix": [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 10.0, 1.0],
        }
        bake_states = {frame: dict(base_state) for frame in frames}
        sparse_states = {frame: dict(base_state) for frame in frames}
        sparse_states[5] = {
            **base_state,
            "rotation": [math.radians(12.0), 0.0, 0.0],
            "transformForward": [0.0, 0.5, -0.8660254038],
            "worldMatrix": [1.0, 0.0, 0.0, 0.0, 0.0, 0.8660254038, 0.5, 0.0, 0.0, -0.5, 0.8660254038, 0.0, 0.0, 5.0, 10.0, 1.0],
        }

        rows = runner._parity_drift_rows(frames, {0, 10}, sparse_states, bake_states)
        summary = runner._summarize_parity_drift(rows)

        self.assertEqual(summary["keyframes"], 2)
        self.assertEqual(summary["inbetweenFrames"], 1)
        self.assertAlmostEqual(summary["keyframesOnly"]["eyeEuclidean"]["max"], 0.0)
        self.assertGreater(summary["inbetweenOnly"]["eyeEuclidean"]["max"], 4.9)
        self.assertGreater(summary["inbetweenOnly"]["forwardAngleDeg"]["max"], 29.0)

    def test_interpolation_drift_mismatches_gate_thresholds(self):
        summary = {
            "inbetweenOnly": {
                "eyeEuclidean": {"max": 5.0, "maxFrame": 5},
                "forwardAngleDeg": {"max": 12.0, "maxFrame": 5},
                "upAngleDeg": {"max": 2.0, "maxFrame": 5},
                "rotationMaxDeg": {"max": 9.0, "maxFrame": 5},
            }
        }

        mismatches = runner._interpolation_drift_mismatches(
            summary,
            {
                "parity-interpolation-eye-max": 1.0,
                "parity-interpolation-forward-max-deg": 5.0,
                "parity-interpolation-up-max-deg": 5.0,
                "parity-interpolation-rotation-max-deg": 10.0,
            },
        )

        self.assertEqual([item["field"] for item in mismatches], ["eyeEuclidean", "forwardAngleDeg"])
