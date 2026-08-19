"""Focused tests for legacy VMD import routes into physics pre-inputs."""

from __future__ import annotations

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import vmd_legacy_bone_routes as routes_module  # noqa: E402
from mmd_tools.converters.vmd_bone_animation import set_bone_keyframes  # noqa: E402
from mmd_tools.converters.vmd_scene_keying import VmdKeyingError  # noqa: E402


class _PhysicsRouteCmds:
    def __init__(self):
        self.types = {
            "|model": "transform",
            "|model|bone": "joint",
            "|model|other": "joint",
            "|solver": "mmdPhysicsSolver",
            "|driver": "mmdPhysicsBoneDriver",
            "|other_driver": "mmdPhysicsBoneDriver",
        }
        self.roots = {"|solver.modelRoot": ["|model"]}
        self.drivers = {"|solver.outBoneMatrices": ["|driver"]}
        self.targets = {"|driver.mmd_target_joint_message": ["|model|bone"]}
        self.indices = {"|driver.inBoneIndex": 4}

    def ls(self, value=None, type=None, long=False, **_kwargs):
        if type:
            return [node for node, node_type in self.types.items() if node_type == type]
        return [value] if value in self.types else []

    def listConnections(self, plug, source=False, destination=False, **_kwargs):
        if source and not destination and plug.endswith(".modelRoot"):
            return list(self.roots.get(plug, []))
        if destination and not source and plug.endswith(
            (".outBoneMatrices", ".outBoneCount", ".outSolved")
        ):
            return list(self.drivers.get(plug, []))
        if source and not destination and plug.endswith(".mmd_target_joint_message"):
            return list(self.targets.get(plug, []))
        return []

    def attributeQuery(self, attr, node, exists=False, **_kwargs):
        return bool(
            exists
            and (
                attr in routes_module._PHYSICS_PRE_INPUT_ATTRS.values()
                or attr in {"mmd_target_joint_message", "inBoneIndex"}
            )
        )

    def getAttr(self, plug, **_kwargs):
        return self.indices[plug]


class LegacyPhysicsRouteTests(unittest.TestCase):
    def _build(self, cmds, joints=None, *, append=None, ik=None, controls=None):
        joints = joints or {"bone": "|model|bone"}
        converter = mock.MagicMock()
        converter.bone_name_mapping = joints
        converter._collect_append_info.return_value = append or {}
        converter._collect_ik_link_joints.return_value = ik or {}
        with (
            mock.patch.object(routes_module, "cmds", cmds),
            mock.patch.object(
                routes_module,
                "control_rig_edit_routes_for_joints",
                return_value=controls or {},
            ),
            mock.patch.object(
                routes_module,
                "control_rig_edit_authoring_bases_for_joints",
                return_value={},
            ),
            mock.patch.object(
                routes_module,
                "control_rig_fixed_axis_twist_joints",
                return_value=set(),
            ),
        ):
            return routes_module.build_legacy_bone_key_routes(converter)

    def test_unique_owned_driver_routes_all_six_channels(self):
        route = self._build(_PhysicsRouteCmds())["|model|bone"]
        self.assertEqual(
            route["attr_targets"],
            {
                channel: ("|driver", target)
                for channel, target in routes_module._PHYSICS_PRE_INPUT_ATTRS.items()
            },
        )

    def test_append_control_and_ik_decisions_keep_priority(self):
        cmds = _PhysicsRouteCmds()
        append = {
            "|model|bone": {
                "node": "|append",
                "attr_map": {"translateX": "inputTranslateX"},
            }
        }
        ik = {"|model|bone": {"solver": "|ik", "slot": 2}}
        controls = {"|model|bone": {"rotateX": ("|control", "rotateX")}}
        route = self._build(cmds, append=append, ik=ik, controls=controls)["|model|bone"]
        self.assertEqual(route["attr_targets"]["translateX"], ("|append", "inputTranslateX"))
        self.assertEqual(route["attr_targets"]["rotateX"], ("|control", "rotateX"))
        self.assertTrue(route["skip_rotate"])
        self.assertEqual(route["ik_solver_rotate"], ik["|model|bone"])

    def test_duplicate_target_or_index_fails_closed(self):
        cmds = _PhysicsRouteCmds()
        cmds.drivers["|solver.outBoneMatrices"] = ["|driver", "|other_driver"]
        cmds.targets["|other_driver.mmd_target_joint_message"] = ["|model|bone"]
        cmds.indices["|other_driver.inBoneIndex"] = 5
        route = self._build(cmds)["|model|bone"]
        self.assertEqual(route["block_reason"], "duplicate_physics_target_or_bone_index")

        cmds.targets["|other_driver.mmd_target_joint_message"] = ["|model|other"]
        cmds.indices["|other_driver.inBoneIndex"] = 4
        routes = self._build(
            cmds,
            joints={"bone": "|model|bone", "other": "|model|other"},
        )
        self.assertIn("blocked_channels", routes["|model|bone"])
        self.assertIn("blocked_channels", routes["|model|other"])

    def test_ambiguous_solver_ownership_fails_closed(self):
        cmds = _PhysicsRouteCmds()
        cmds.roots["|solver.modelRoot"] = ["|model", "|other_model"]
        route = self._build(cmds)["|model|bone"]
        self.assertEqual(route["block_reason"], "ambiguous_or_unowned_physics_driver")

    def test_complete_existing_route_does_not_block_on_ambiguous_physics(self):
        cmds = _PhysicsRouteCmds()
        cmds.roots["|solver.modelRoot"] = ["|model", "|other_model"]
        controls = {
            "|model|bone": {
                channel: ("|control", channel)
                for channel in routes_module._PHYSICS_PRE_INPUT_ATTRS
            }
        }
        route = self._build(cmds, controls=controls)["|model|bone"]
        self.assertNotIn("blocked_channels", route)
        self.assertEqual(route["attr_targets"], controls["|model|bone"])

    def test_blocked_physics_route_raises_before_any_keying(self):
        context = mock.MagicMock()
        with self.assertRaisesRegex(VmdKeyingError, "physics pre-input owner"):
            set_bone_keyframes(
                context,
                "|model|bone",
                [],
                "bone",
                {
                    "blocked_channels": ("translateX", "rotateX"),
                    "block_reason": "duplicate_physics_target_or_bone_index",
                },
            )
        context.set_bone_keyframes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
