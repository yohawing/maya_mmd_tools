"""Pure routing checks for MMD Control Rig motion transactions."""

import unittest

from mmd_tools.core.mmd_control_rig_motion import (
    _connect_ik_control_visibility,
    ROUTE_SAMPLED,
    _rotation_channel_groups,
)
from mmd_tools.core.mmd_control_rig_builder import MmdControlRigBuildError


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

    def test_ik_visibility_follows_enabled_and_reuses_canonical_connection(self):
        class FakeCmds:
            def __init__(self):
                self.incoming = {}
                self.connections = []

            def objExists(self, plug):
                return plug.endswith(".visibility")

            def listRelatives(self, node, **kwargs):
                return [f"|model|{node}Shape"]

            def listConnections(self, plug, **kwargs):
                return list(self.incoming.get(plug, ()))

            def connectAttr(self, source, target, **kwargs):
                self.connections.append((source, target))
                self.incoming[target] = [source]

            def ls(self, node, **kwargs):
                return ["|model|" + str(node).rsplit("|", 1)[-1]]

        cmds = FakeCmds()
        operations = []
        _connect_ik_control_visibility(
            cmds,
            ("left", "pole"),
            "|model|left.ikEnabled",
            operations,
        )
        self.assertEqual(len(cmds.connections), 2)
        self.assertTrue(all(target.endswith("visibility") for _, target in cmds.connections))
        self.assertEqual(len(operations), 2)

        # Maya may return a short source on the second query; UUID/DAG
        # normalization must treat it as the same owner route.
        cmds.incoming["|model|leftShape.visibility"] = ["left.ikEnabled"]
        cmds.incoming["|model|poleShape.visibility"] = ["left.ikEnabled"]
        _connect_ik_control_visibility(
            cmds,
            ("left", "pole"),
            "|model|left.ikEnabled",
            operations,
        )
        self.assertEqual(len(cmds.connections), 2)

    def test_ik_visibility_rejects_foreign_writer(self):
        class FakeCmds:
            def objExists(self, plug):
                return True

            def listRelatives(self, node, **kwargs):
                return [f"{node}Shape"]

            def listConnections(self, plug, **kwargs):
                return ["foreign.output"]

        with self.assertRaises(MmdControlRigBuildError):
            _connect_ik_control_visibility(
                FakeCmds(),
                ("left",),
                "left.ikEnabled",
                [],
            )
