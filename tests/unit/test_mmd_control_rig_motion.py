"""Pure routing checks for MMD Control Rig motion transactions."""

import unittest

from mmd_tools.core.mmd_control_rig_motion import (
    ROUTE_SAMPLED,
    _rotation_channel_groups,
)


def _twist_rows(target_prefix="baseRotate"):
    return [
        {
            "control": f"twist_CTRL.rotate{axis}",
            "target": f"append.{target_prefix}{axis}",
            "routeClass": ROUTE_SAMPLED,
            "routeReasons": ["append_base"],
            "twistController": True,
        }
        for axis in "XYZ"
    ]


class MmdControlRigMotionRoutingTest(unittest.TestCase):
    """Keep optional twist Append routes complete and fail closed when partial."""

    def test_twist_append_complete_xyz_is_one_rotation_group(self):
        groups = _rotation_channel_groups(_twist_rows())

        self.assertEqual(len(groups), 1)
        self.assertEqual(
            [row["target"] for row in groups[0]],
            ["append.baseRotateX", "append.baseRotateY", "append.baseRotateZ"],
        )

    def test_twist_append_partial_xyz_is_not_grouped(self):
        rows = _twist_rows()
        rows[-1] = dict(rows[-1], target="append.baseRotateX")

        self.assertEqual(_rotation_channel_groups(rows), [])

    def test_non_twist_append_route_stays_scalar_fail_closed(self):
        rows = [dict(row, twistController=False) for row in _twist_rows()]

        self.assertEqual(_rotation_channel_groups(rows), [])

