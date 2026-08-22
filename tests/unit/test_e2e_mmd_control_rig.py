"""Regression tests for the Control Rig IK-move witness gate."""

import unittest
from types import SimpleNamespace

from tests.viewport.e2e_mmd_control_rig import (
    _focused_witnesses_pass,
    _ik_move_witness_pass,
    _nonzero_sentinel_value,
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


if __name__ == "__main__":
    unittest.main()
