"""Focused tests for the native physics release determinism contract."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from tests.release import native_physics_determinism as determinism


def _passing_report() -> dict:
    return {
        "status": "passed",
        "feature_flags": 3,
        "native_physics_available": True,
        "runtime_library_path": "F:/repo/mmd_tools/native/win64/mmd_runtime_ffi.dll",
        "eval_frames": [0, 1],
        "delta_epsilon": 0.001,
        "static_pmx_extent": 10.0,
        "import_scale": 1.0,
        "scaled_static_pmx_extent": 10.0,
        "extent_source": "pmx_static_vertices_x_import_scale",
        "baseline": {"physics_routing": {}, "physics_bones": ["hair"], "samples": {"hair": {"0": {"tx": 0.0}}}},
        "native": {
            "physics_routing": {"used": True},
            "physics_bones": ["hair"],
            "samples": {"hair": {"0": {"tx": 1.0}}},
        },
        "delta": {"passed": True, "comparedChannels": 1, "maxAbsDelta": 1.0},
        "assertions": [{"name": name, "pass": True} for name in sorted(determinism.REQUIRED_ASSERTIONS)],
    }


class NativePhysicsDeterminismTest(unittest.TestCase):
    def test_identical_meaningful_outputs_pass(self):
        report = _passing_report()
        result = determinism.compare_reports(report, copy.deepcopy(report), Path(report["runtime_library_path"]))
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["deterministic"])

    def test_channel_output_difference_fails(self):
        first = _passing_report()
        second = copy.deepcopy(first)
        second["native"]["samples"]["hair"]["0"]["tx"] = 1.5
        result = determinism.compare_reports(first, second, Path(first["runtime_library_path"]))
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["deterministic"])

    def test_silent_native_fallback_fails(self):
        report = _passing_report()
        report["native"]["physics_routing"]["used"] = False
        result = determinism.compare_reports(report, copy.deepcopy(report), Path(report["runtime_library_path"]))
        self.assertEqual(result["status"], "fail")
        self.assertIn("native physics routing was not used", result["errors"][0])

    def test_wrong_runtime_path_fails(self):
        report = _passing_report()
        result = determinism.compare_reports(report, copy.deepcopy(report), Path("F:/expected/runtime.dll"))
        self.assertEqual(result["status"], "fail")


if __name__ == "__main__":
    unittest.main()
