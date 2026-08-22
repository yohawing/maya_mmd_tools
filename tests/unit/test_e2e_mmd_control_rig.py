"""Regression tests for the Control Rig IK-move witness gate."""

import unittest

from tests.viewport.e2e_mmd_control_rig import (
    _focused_witnesses_pass,
    _ik_move_witness_pass,
    _nonzero_sentinel_value,
)


class IkMoveWitnessTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
