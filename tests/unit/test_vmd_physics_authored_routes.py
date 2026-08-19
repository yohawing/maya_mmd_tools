"""Focused ownership tests for physics pre-input VMD routes."""

from __future__ import annotations

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import vmd_scene_collector as collector_module  # noqa: E402
from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector  # noqa: E402


class _PhysicsCmds:
    def __init__(self):
        self.types = {
            "|model": "transform",
            "|model|bone": "joint",
            "|model|other": "joint",
            "|other_model": "transform",
            "|other_model|bone": "joint",
            "|solver": "mmdPhysicsSolver",
            "|other_solver": "mmdPhysicsSolver",
            "|driver": "mmdPhysicsBoneDriver",
            "|driver_duplicate": "mmdPhysicsBoneDriver",
            "|other_driver": "mmdPhysicsBoneDriver",
            "|curve": "animCurveTL",
            "|curve_rotate": "animCurveTA",
            "|blend_translate": "animBlendNodeAdditiveDL",
            "|curve_other": "animCurveTL",
        }
        self.attributes = {
            (driver, attr)
            for driver in ("|driver", "|driver_duplicate", "|other_driver")
            for attr in (
                "mmd_target_joint_message",
                "inBoneIndex",
                "inPreTranslateX",
                "inPreTranslateY",
                "inPreTranslateZ",
                "inPreRotateX",
                "inPreRotateY",
                "inPreRotateZ",
            )
        }
        self.values = {
            "|driver.inBoneIndex": 4,
            "|driver_duplicate.inBoneIndex": 4,
            "|other_driver.inBoneIndex": 5,
        }
        self.solver_roots = {"|solver": ["|model"], "|other_solver": ["|other_model"]}
        self.solver_drivers = {
            "|solver.outBoneMatrices": ["|driver"],
            "|other_solver.outBoneMatrices": ["|other_driver"],
        }
        self.driver_targets = {
            "|driver.mmd_target_joint_message": ["|model|bone"],
            "|driver_duplicate.mmd_target_joint_message": ["|model|bone"],
            "|other_driver.mmd_target_joint_message": ["|other_model|bone"],
        }
        self.driver_sources = {
            "|driver.inPreTranslateX": ["|curve.output"],
            "|driver.inPreRotateX": ["|curve_rotate.output"],
        }

    def ls(self, value=None, type=None, long=False, **_kwargs):
        if type:
            return [node for node, node_type in self.types.items() if node_type == type]
        if value in self.types:
            return [value]
        return []

    def listConnections(self, plug, source=False, destination=False, type=None, **_kwargs):
        if plug.endswith(".modelRoot") and source and not destination:
            return list(self.solver_roots.get(plug.split(".", 1)[0], []))
        if plug.endswith(".modelRegistry") and source and not destination:
            return []
        if destination and plug in self.solver_drivers:
            values = self.solver_drivers[plug]
        elif source and plug in self.driver_targets:
            values = self.driver_targets[plug]
        elif source and plug in self.driver_sources:
            values = self.driver_sources[plug]
        else:
            values = []
        if type:
            values = [value for value in values if self.types.get(value.split(".", 1)[0]) == type]
        if not destination and plug.endswith(".outBoneMatrices"):
            return []
        return list(values)

    def attributeQuery(self, attr, node, exists=False, **_kwargs):
        return bool(exists and (node, attr) in self.attributes)

    def getAttr(self, plug, **_kwargs):
        return self.values.get(plug, 0.0)

    def nodeType(self, node):
        return self.types.get(node)


class PhysicsAuthoredRouteTests(unittest.TestCase):
    def test_owned_unique_driver_routes_only_authored_pre_inputs(self):
        cmds = _PhysicsCmds()
        cmds.driver_sources["|model|bone.translateX"] = [
            "|blend_translate.output"
        ]
        collector = VmdSceneCollector()
        with mock.patch.object(collector_module, "cmds", cmds), mock.patch.object(
            collector_module, "collect_append_info", return_value={}
        ), mock.patch.object(
            collector_module, "collect_mmd_ik_passthrough_info", return_value={}
        ), mock.patch.object(
            collector_module, "read_mmd_control_rig_metadata", return_value=None
        ):
            routes = collector._scene_authored_input_routes(
                ["|model|bone"], "|model"
            )
        self.assertEqual(
            routes["|model|bone"]["translateX"],
            ("|driver", "inPreTranslateX"),
        )
        self.assertEqual(
            routes["|model|bone"]["rotateX"],
            ("|driver", "inPreRotateX"),
        )
        self.assertNotIn("translateY", routes["|model|bone"])
        # The route alone does not invent a required track when its source
        # graph has no keyed times.
        self.assertEqual(
            collector_module._routed_key_times(
                "|model|bone", routes["|model|bone"]
            ),
            [],
        )

    def test_joint_authored_source_fallback_is_unique_and_nonphysics(self):
        cmds = _PhysicsCmds()
        cmds.driver_sources.clear()
        cmds.driver_sources.update(
            {
                "|model|bone.translateX": ["|blend_translate.output"],
                "|model|bone.translateY": [
                    "|curve.output",
                    "|curve_other.output",
                ],
                "|model|bone.translateZ": ["|driver_duplicate.outTranslateZ"],
            }
        )
        collector = VmdSceneCollector()
        with mock.patch.object(collector_module, "cmds", cmds), mock.patch.object(
            collector_module, "collect_append_info", return_value={}
        ), mock.patch.object(
            collector_module, "collect_mmd_ik_passthrough_info", return_value={}
        ), mock.patch.object(
            collector_module, "read_mmd_control_rig_metadata", return_value=None
        ):
            routes = collector._scene_authored_input_routes(
                ["|model|bone"], "|model"
            )

        self.assertEqual(
            routes["|model|bone"]["translateX"],
            ("|blend_translate", "output"),
        )
        self.assertNotIn("translateY", routes["|model|bone"])
        self.assertNotIn("translateZ", routes["|model|bone"])

    def test_existing_append_route_wins_and_unowned_driver_is_ignored(self):
        cmds = _PhysicsCmds()
        collector = VmdSceneCollector()
        append = {
            "|model|bone": {
                "node": "|append",
                "attr_map": {"translateX": "inputTranslateX"},
            }
        }
        with mock.patch.object(collector_module, "cmds", cmds), mock.patch.object(
            collector_module, "collect_append_info", return_value=append
        ), mock.patch.object(
            collector_module, "collect_mmd_ik_passthrough_info", return_value={}
        ), mock.patch.object(
            collector_module, "read_mmd_control_rig_metadata", return_value=None
        ):
            routes = collector._scene_authored_input_routes(
                ["|model|bone", "|other_model|bone"], "|model"
            )
        self.assertEqual(
            routes["|model|bone"]["translateX"],
            ("|append", "inputTranslateX"),
        )
        self.assertEqual(
            routes["|model|bone"]["rotateX"],
            ("|driver", "inPreRotateX"),
        )
        self.assertNotIn("|other_model|bone", routes)

    def test_duplicate_driver_target_fails_closed(self):
        cmds = _PhysicsCmds()
        cmds.solver_drivers["|solver.outBoneMatrices"] = [
            "|driver",
            "|driver_duplicate",
        ]
        cmds.driver_sources["|driver_duplicate.inPreTranslateX"] = ["|curve.output"]
        collector = VmdSceneCollector()
        with mock.patch.object(collector_module, "cmds", cmds), mock.patch.object(
            collector_module, "collect_append_info", return_value={}
        ), mock.patch.object(
            collector_module, "collect_mmd_ik_passthrough_info", return_value={}
        ), mock.patch.object(
            collector_module, "read_mmd_control_rig_metadata", return_value=None
        ):
            routes = collector._scene_authored_input_routes(
                ["|model|bone"], "|model"
            )
        self.assertNotIn("|model|bone", routes)


if __name__ == "__main__":
    unittest.main()
