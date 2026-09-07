"""Ensure the GUI parity oracle rejects missing and invalid geometry evidence."""

import unittest

from tools.smoke.maya_fast_import_authoring import (
    _call_native,
    _max_error,
    _motion_delta,
    _phase_gate,
    _require_native_route,
    _require_import_success,
    _require_vmd_success,
    _validate_multi_import_witness,
)


class TestAuthoringSamples(unittest.TestCase):
    def test_failed_native_call_followed_by_python_fallback_is_rejected(self):
        calls = []

        def unavailable(**kwargs):
            raise RuntimeError("native unavailable")

        with self.assertRaises(RuntimeError):
            _call_native(unavailable, calls, f="fixture.pmx")
        with self.assertRaises(RuntimeError):
            _require_native_route("cpp", calls)
        with self.assertRaises(RuntimeError):
            _require_native_route("cpp", [])

    def test_native_result_contract_is_required(self):
        for result in (None, [], ["root"], "root"):
            calls = []
            _call_native(lambda: result, calls)
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                _require_native_route("cpp", calls)
        calls = []
        _call_native(lambda: ["root", "mesh"], calls)
        _require_native_route("cpp", calls)
        with self.assertRaises(RuntimeError):
            _require_native_route("python", calls)

    def test_looping_motion_is_not_mistaken_for_a_static_import(self):
        self.assertEqual(_motion_delta({"0": {"mesh": [[0, 0, 0]]},
                                        "15": {"mesh": [[1, 0, 0]]},
                                        "60": {"mesh": [[0, 0, 0]]}}), 1.0)

    def test_mismatched_meshes_and_topology_fail(self):
        for left, right in (({}, {}), ({"a": [[0, 0, 0]]}, {"b": [[0, 0, 0]]}),
                            ({"a": [[0, 0, 0]]}, {"a": []})):
            with self.subTest(left=left, right=right), self.assertRaises(ValueError):
                _max_error(left, right)

    def test_nonfinite_positions_cannot_hide_in_matching_samples(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _max_error({"mesh": [[value, 0, 0]]}, {"mesh": [[value, 0, 0]]})

    def test_reports_largest_vertex_displacement_across_frames(self):
        self.assertAlmostEqual(
            _max_error({"0": {"mesh": [[0, 0, 0]]}, "60": {"mesh": [[1, 2, 3]]}},
                       {"0": {"mesh": [[0, 0, 0]]}, "60": {"mesh": [[1, 2.25, 3]]}}),
            0.25,
        )

    def test_required_phase_gate_rejects_missing_multi_import_witness(self):
        report = {
            "phases": {
                name: {"status": "pass"}
                for name in (
                    "ui_binding_python",
                    "ui_binding_cpp",
                    "multi_import_identity",
                    "failure_cleanup",
                    "vmd_playback",
                    "save_reopen",
                    "scene_contract",
                )
            },
            "checks": [],
        }
        report["phases"]["multi_import_identity"] = {"status": "not_run"}
        _phase_gate(report)
        check = next(item for item in report["checks"] if item["name"] == "phase-multi_import_identity")
        self.assertFalse(check["pass"])
        self.assertEqual(check["status"], "not_run")

    def test_required_phase_gate_rejects_cleanup_failure_even_with_geometry_pass(self):
        report = {
            "phases": {
                name: {"status": "pass"}
                for name in (
                    "ui_binding_python",
                    "ui_binding_cpp",
                    "multi_import_identity",
                    "failure_cleanup",
                    "vmd_playback",
                    "save_reopen",
                    "scene_contract",
                )
            },
            "checks": [{"name": "python-cpp-positions", "pass": True}],
        }
        report["phases"]["failure_cleanup"] = {"status": "fail"}
        _phase_gate(report)
        check = next(item for item in report["checks"] if item["name"] == "phase-failure_cleanup")
        self.assertFalse(check["pass"])
        self.assertEqual(check["status"], "fail")

    def test_partial_action_result_cannot_be_reported_as_clean_ui_success(self):
        observations = [{
            "request": {"filePath": "fixture.pmx"},
            "result": {"outcome": "partial", "succeeded": True, "warnings": [{"code": "warning"}]},
        }]
        with self.assertRaises(RuntimeError):
            _require_import_success(observations, "fixture.pmx")

    def test_missing_vmd_action_observation_cannot_be_reported_as_success(self):
        with self.assertRaises(RuntimeError):
            _require_vmd_success([], "fixture.vmd")

    def test_empty_critical_dg_witness_cannot_pass_multi_import_contract(self):
        witness = {
            "roots": ["|model", "|model1"],
            "criticalRequirements": {
                "|model": {"skinCluster": True},
                "|model1": {"skinCluster": True},
            },
            "criticalPresence": {
                "|model": {"skinCluster": False},
                "|model1": {"skinCluster": False},
            },
            "crossConnections": [],
        }
        with self.assertRaises(RuntimeError):
            _validate_multi_import_witness(witness)

    def test_shared_critical_dg_witness_cannot_pass_multi_import_contract(self):
        witness = {
            "roots": ["|model", "|model1"],
            "criticalIdentityOwners": {"skinCluster1": ["|model", "|model1"]},
            "criticalPresence": {
                "|model": {"skinCluster": True},
                "|model1": {"skinCluster": True},
            },
            "crossConnections": [],
        }
        with self.assertRaises(RuntimeError):
            _validate_multi_import_witness(witness)

    def test_phase_gate_requires_both_routes_for_shared_evidence(self):
        report = {
            "phases": {
                "multi_import_identity": {
                    "status": "pass",
                    "routes": {"python": {"status": "pass"}},
                }
            },
            "checks": [],
        }
        _phase_gate(report)
        check = next(item for item in report["checks"] if item["name"] == "phase-multi_import_identity")
        self.assertFalse(check["pass"])
        self.assertEqual(check["missingRoutes"], ["cpp"])
