"""Pure routing checks for MMD Control Rig motion transactions."""

import unittest

from mmd_tools.core.mmd_control_rig_motion import (
    _connect_ik_control_visibility,
    _classify_route,
    _consistent_rotation_group_basis,
    _dense_sample_times,
    ROUTE_SAMPLED,
    ROUTE_SAME_BASIS,
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


def _ik_link_rows():
    return [
        {
            "control": f"leg_CTRL.rotate{axis}",
            "target": f"solver.inputRotate[6].inputRotateElement{axis}",
            "routeClass": ROUTE_SAMPLED,
            "routeReasons": ["ik", "ik_link_input", "joint_orient"],
        }
        for axis in "XYZ"
    ]


class MmdControlRigMotionRoutingTest(unittest.TestCase):
    """Keep optional twist Append routes complete and fail closed when partial."""

    def test_dense_sample_times_clips_requested_range_but_manual_mode_is_unchanged(self):
        source_times = (-12.0, 0.0, 120.0, 132.0)

        self.assertEqual(
            _dense_sample_times(source_times, (0, 120)),
            [float(frame) for frame in range(121)],
        )
        self.assertEqual(
            _dense_sample_times(source_times),
            [float(frame) for frame in range(-12, 133)],
        )

    def test_dense_sample_times_keeps_fractional_requested_endpoints(self):
        self.assertEqual(
            _dense_sample_times((-2.0, 7.0), (0.25, 2.75)),
            [0.25, 1.0, 2.0, 2.75],
        )

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

    def test_legacy_ik_quaternion_xyz_is_grouped_only_for_bake_passthrough(self):
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

    def test_ik_link_xyz_supports_live_and_bake_basis_conversion(self):
        rows = _ik_link_rows()

        self.assertTrue(all(_supports_live_authoring_basis(row) for row in rows))
        self.assertTrue(all(_supports_bake_authoring_basis(row) for row in rows))
        groups = _rotation_channel_groups(rows, include_sampled_direct=True)
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

    def test_parent_basis_driver_is_sampled_even_when_current_value_is_identity(self):
        class FakeCmds:
            def __init__(self, *, incoming=None, keyed=None, scale_values=None):
                self.incoming = incoming or {}
                self.keyed = keyed or {}
                self.scale_values = scale_values or {}

            def objExists(self, node):
                return node in {"|joint", "|parent"}

            def attributeQuery(self, attribute, *, node, exists=False, **_kwargs):
                return exists and node == "|joint" and attribute.startswith("jointOrient")

            def getAttr(self, plug):
                if plug.endswith("rotateOrder"):
                    return 0
                if ".rotate" in plug:
                    return 0.0
                if ".scale" in plug:
                    return self.scale_values.get(plug[-1], 1.0)
                return 0.0

            def listRelatives(self, node, **kwargs):
                if kwargs.get("parent") and node == "|joint":
                    return ["|parent"]
                return []

            def listConnections(self, plug, **_kwargs):
                return list(self.incoming.get(plug, ()))

            def keyframe(self, target, **kwargs):
                attribute = kwargs.get("attribute")
                key = (target, attribute) if attribute else target
                return list(self.keyed.get(key, ()))

        binding = {"inputKind": "direct_channel", "joint": "|joint"}
        static_route = _classify_route(FakeCmds(), binding, "|joint.rotateX")
        self.assertEqual(static_route, (ROUTE_SAME_BASIS, ()))

        zero_scale_route = _classify_route(
            FakeCmds(scale_values={"Y": 0.0}),
            binding,
            "|joint.rotateX",
        )
        self.assertEqual(zero_scale_route[0], ROUTE_SAMPLED)
        self.assertIn("parent_basis", zero_scale_route[1])

        driven_route = _classify_route(
            FakeCmds(incoming={"|parent.rotateX": ["|animCurve.output"]}),
            binding,
            "|joint.rotateX",
        )
        self.assertEqual(driven_route[0], ROUTE_SAMPLED)
        self.assertIn("parent_basis", driven_route[1])

        keyed_route = _classify_route(
            FakeCmds(keyed={"|parent.scaleY": [1.0, 12.0]}),
            binding,
            "|joint.rotateX",
        )
        self.assertEqual(keyed_route[0], ROUTE_SAMPLED)
        self.assertIn("parent_basis", keyed_route[1])

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
