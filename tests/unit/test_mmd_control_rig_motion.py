"""Pure routing checks for MMD Control Rig motion transactions."""

import unittest

from mmd_tools.core.mmd_control_rig_motion import (
    _connect_ik_control_visibility,
    _consistent_rotation_group_basis,
    ROUTE_SAMPLED,
    _rotation_channel_groups,
    _supports_bake_authoring_basis,
    _supports_live_authoring_basis,
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


def _bone_morph_rows():
    return [
        {
            "control": f"arm_CTRL.rotate{axis}",
            "target": f"accumulator.baseRotate{axis}",
            "routeClass": ROUTE_SAMPLED,
            "routeReasons": ["bone_morph_base", "joint_orient"],
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

    def test_ik_quaternion_xyz_is_grouped_only_for_bake_passthrough(self):
        rows = [
            {
                "control": f"foot_ik_CTRL.rotate{axis}",
                "target": f"solver.inputRotate[6].inputRotateElement{axis}",
                "routeClass": ROUTE_SAMPLED,
                "routeReasons": ["ik"],
            }
            for axis in "XYZ"
        ]

        self.assertEqual(_rotation_channel_groups(rows), [])
        self.assertEqual(
            _rotation_channel_groups(rows, include_sampled_direct=True),
            [],
        )
        groups = _rotation_channel_groups(
            rows,
            include_sampled_passthrough=True,
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            [row["target"] for row in groups[0]],
            [
                "solver.inputRotate[6].inputRotateElementX",
                "solver.inputRotate[6].inputRotateElementY",
                "solver.inputRotate[6].inputRotateElementZ",
            ],
        )

    def test_non_xyz_standard_joint_uses_bake_basis_but_not_live_converter(self):
        row = {
            "target": "joint.rotateX",
            "routeClass": ROUTE_SAMPLED,
            "routeReasons": ["joint_orient", "rotate_order"],
        }

        self.assertFalse(_supports_live_authoring_basis(row))
        self.assertTrue(_supports_bake_authoring_basis(row))

    def test_bone_morph_base_xyz_supports_live_and_bake_basis_conversion(self):
        rows = _bone_morph_rows()

        self.assertTrue(all(_supports_live_authoring_basis(row) for row in rows))
        self.assertTrue(all(_supports_bake_authoring_basis(row) for row in rows))
        groups = _rotation_channel_groups(rows, include_sampled_direct=True)
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            [row["target"] for row in groups[0]],
            [
                "accumulator.baseRotateX",
                "accumulator.baseRotateY",
                "accumulator.baseRotateZ",
            ],
        )

    def test_rotation_group_basis_accepts_json_roundtrip_normalization_noise(self):
        rows = [
            {
                "authoringBasis": {
                    "quaternion": [
                        0.4751920029162135,
                        0.5188175015723054,
                        0.0,
                        0.7106482677293658,
                    ],
                    "source": "pmx_tail",
                }
            }
        ]
        rows.extend(
            {
                "authoringBasis": {
                    "quaternion": [
                        0.4751920029162135 * scale,
                        0.5188175015723054 * scale,
                        0.0,
                        0.7106482677293658 * scale,
                    ],
                    "source": "pmx_tail",
                }
            }
            for scale in (10.0, 0.1)
        )

        basis = _consistent_rotation_group_basis(rows)

        self.assertAlmostEqual(basis.quaternion[0], 0.4751920029162134)
        self.assertAlmostEqual(basis.quaternion[3], 0.7106482677293657)

    def test_rotation_group_basis_rejects_genuinely_mixed_axes(self):
        rows = [
            {
                "authoringBasis": {
                    "quaternion": [0.0, 0.0, 0.0, 1.0],
                    "source": "identity",
                }
            }
            for _axis in "XY"
        ]
        rows.append(
            {
                "authoringBasis": {
                    "quaternion": [0.0, 0.0, 0.70710678, 0.70710678],
                    "source": "pmx_tail",
                }
            }
        )

        with self.assertRaisesRegex(MmdControlRigBuildError, "basis is inconsistent"):
            _consistent_rotation_group_basis(rows)

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
