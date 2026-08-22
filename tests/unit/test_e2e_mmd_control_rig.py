"""Regression tests for the Control Rig IK-move witness gate."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from mmd_tools.validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
)
from tests.viewport.e2e_mmd_control_rig import (
    _approve_one_shot_export_warnings,
    _focused_witnesses_pass,
    _ik_move_witness_pass,
    _nonzero_sentinel_value,
    _record_one_shot_terminal_evidence,
    _vmd_applicability_candidates,
)


class IkMoveWitnessTests(unittest.TestCase):
    @staticmethod
    def _bone_frame(frame, position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0)):
        return SimpleNamespace(
            bone_name="センター",
            frame_number=frame,
            position=position,
            rotation=rotation,
        )

    def test_sentinel_value_never_lands_on_zero(self):
        self.assertEqual(_nonzero_sentinel_value(-0.35, 0.35), -0.7)
        self.assertEqual(_nonzero_sentinel_value(0.0, 0.35), 0.35)

    def test_witness_requires_authored_route_target_and_link_response(self):
        self.assertTrue(
            _ik_move_witness_pass(
                control_route_pass=True,
                control_delta=0.35,
                target_delta=0.35,
                link_deltas={"0:|model|left_leg": 0.14},
            )
        )

    def test_zero_target_delta_is_fail_closed_even_with_control_delta(self):
        self.assertFalse(
            _ik_move_witness_pass(
                control_route_pass=True,
                control_delta=0.35,
                target_delta=0.0,
                link_deltas={"0:|model|left_leg": 0.14},
            )
        )

    def test_missing_link_response_is_fail_closed_even_with_target_delta(self):
        self.assertFalse(
            _ik_move_witness_pass(
                control_route_pass=True,
                control_delta=0.35,
                target_delta=0.35,
                link_deltas={},
            )
        )

    def test_one_unchanged_link_is_fail_closed(self):
        self.assertFalse(
            _ik_move_witness_pass(
                control_route_pass=True,
                control_delta=0.35,
                target_delta=0.35,
                link_deltas={"0:|model|knee": 0.14, "1:|model|leg": 0.0},
            )
        )

    def test_unwritable_control_route_is_fail_closed(self):
        self.assertFalse(
            _ik_move_witness_pass(
                control_route_pass=False,
                control_delta=0.35,
                target_delta=0.35,
                link_deltas={"0:|model|left_leg": 0.14},
            )
        )

    def test_export_parity_cannot_mask_failed_ik_move(self):
        self.assertFalse(
            _focused_witnesses_pass(
                {
                    "ikMove": {"pass": False},
                    "ikToggle": {"pass": True},
                    "autoBakeExport": {"pass": True},
                }
            )
        )

    def test_all_focused_witnesses_are_required(self):
        self.assertTrue(
            _focused_witnesses_pass(
                {
                    "ikMove": {"pass": True},
                    "ikToggle": {"pass": True},
                    "autoBakeExport": {"pass": True},
                }
            )
        )

    def test_applicability_uses_first_key_baseline_and_skips_identical_nonzero_frame(self):
        vmd_data = SimpleNamespace(
            bone_frames=[
                self._bone_frame(0, position=(1.0, 0.0, 0.0)),
                self._bone_frame(174, position=(1.0, 0.0, 0.0)),
                self._bone_frame(175, position=(1.0, 0.25, 0.0)),
            ]
        )

        candidates = _vmd_applicability_candidates(
            vmd_data, lambda name: "|model|center" if name == "センター" else None
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["baselineFrame"], 0)
        self.assertEqual(candidates[0]["candidateFrame"], 175)
        self.assertGreater(candidates[0]["sourceMaxAbsDelta"], 1.0e-5)

    def test_applicability_excludes_static_and_unmapped_bones(self):
        static = [
            self._bone_frame(0, position=(1.0, 0.0, 0.0)),
            self._bone_frame(175, position=(1.0, 0.0, 0.0)),
        ]
        moving_unmapped = [
            SimpleNamespace(
                bone_name="未マップ",
                frame_number=0,
                position=(0.0, 0.0, 0.0),
                rotation=(0.0, 0.0, 0.0, 1.0),
            ),
            SimpleNamespace(
                bone_name="未マップ",
                frame_number=1,
                position=(0.0, 0.25, 0.0),
                rotation=(0.0, 0.0, 0.0, 1.0),
            ),
        ]

        candidates = _vmd_applicability_candidates(
            SimpleNamespace(bone_frames=static + moving_unmapped),
            lambda name: "|model|center" if name == "センター" else None,
        )

        self.assertEqual(candidates, [])

    def test_one_shot_warning_callback_records_canonical_evidence_and_approves(self):
        auto_gate = {}
        report = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "VMD_FRAME_RANGE",
                    "warning",
                    False,
                    "frame_range",
                    "The selected range will be exported.",
                ),
            ),
            mode="bake_timeline",
        )

        self.assertTrue(_approve_one_shot_export_warnings(report, auto_gate))
        self.assertEqual(
            auto_gate["warningAcknowledgement"],
            {
                "invoked": True,
                "approved": True,
                "callbackCount": 1,
                "warnings": [
                    {
                        "code": "VMD_FRAME_RANGE",
                        "severity": "warning",
                        "path": "frame_range",
                        "message": "The selected range will be exported.",
                    }
                ],
            },
        )

    def test_one_shot_terminal_failure_is_recorded_before_output_parse(self):
        auto_gate = {
            "warningAcknowledgement": {
                "invoked": False,
                "approved": False,
                "callbackCount": 0,
                "warnings": [],
            }
        }
        report = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "OUTPUT_WRITE_FAILED",
                    "fatal",
                    True,
                    "output",
                    "The temporary output could not be published.",
                ),
            ),
            mode="bake_timeline",
        )
        published = SimpleNamespace(
            succeeded=False,
            state="Failed",
            error=OSError("disk full"),
            report=report,
            phase_timings={"collect": 0.1, "encode": 0.2},
            active_phase="cleanup",
            completed_phases=["collect", "encode"],
        )

        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "missing.vmd"
            with self.assertRaisesRegex(RuntimeError, "state='Failed'"):
                _record_one_shot_terminal_evidence(auto_gate, published, output_path)

        self.assertFalse(auto_gate["outputExists"])
        self.assertEqual(auto_gate["publishedState"], "Failed")
        self.assertEqual(auto_gate["activePhase"], "cleanup")
        self.assertEqual(auto_gate["completedPhases"], ["collect", "encode"])
        self.assertEqual(
            auto_gate["validationReport"]["warnings"],
            [
                {
                    "code": "OUTPUT_WRITE_FAILED",
                    "severity": "fatal",
                    "path": "output",
                    "message": "The temporary output could not be published.",
                }
            ],
        )
        self.assertFalse(auto_gate["warningAcknowledgement"]["invoked"])

    def test_one_shot_warning_callback_cannot_approve_a_fatal_report(self):
        auto_gate = {}
        report = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "SCENE_PREFLIGHT_FAILED",
                    "fatal",
                    True,
                    "scene",
                    "The scene is not exportable.",
                ),
            ),
            mode="bake_timeline",
        )

        self.assertFalse(_approve_one_shot_export_warnings(report, auto_gate))
        self.assertFalse(auto_gate["warningAcknowledgement"]["approved"])
        self.assertTrue(auto_gate["warningAcknowledgement"]["fatalRejected"])


if __name__ == "__main__":
    unittest.main()
