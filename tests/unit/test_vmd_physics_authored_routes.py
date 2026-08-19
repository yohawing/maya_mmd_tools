"""Focused ownership tests for physics pre-input VMD routes."""

from __future__ import annotations

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import vmd_scene_collector as collector_module  # noqa: E402
from mmd_tools.converters.bone_morph_runtime import (  # noqa: E402
    BoneMorphBaseRouteResolution,
)
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
        self.solver_registries = {"|solver": [], "|other_solver": []}
        self.solver_drivers = {
            "|solver.outBoneMatrices": ["|driver"],
            "|other_solver.outBoneMatrices": ["|other_driver"],
        }
        self.solver_output_query_counts = {}
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
        if destination and plug.endswith((".outBoneMatrices", ".outBoneCount", ".outSolved")):
            self.solver_output_query_counts[plug] = (
                self.solver_output_query_counts.get(plug, 0) + 1
            )
        if plug.endswith(".modelRoot") and source and not destination:
            return list(self.solver_roots.get(plug.split(".", 1)[0], []))
        if plug.endswith(".modelRegistry") and source and not destination:
            return list(self.solver_registries.get(plug.split(".", 1)[0], []))
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
    def _collect_routes(
        self,
        cmds,
        joints=None,
        *,
        append=None,
        standard_mode_c=False,
    ):
        collector = VmdSceneCollector()
        with mock.patch.object(collector_module, "cmds", cmds), mock.patch.object(
            collector_module, "collect_append_info", return_value=append or {}
        ), mock.patch.object(
            collector_module, "collect_mmd_ik_passthrough_info", return_value={}
        ), mock.patch.object(
            collector_module, "read_mmd_control_rig_metadata", return_value=None
        ), mock.patch.object(
            collector_module,
            "resolve_owned_bone_morph_base_routes",
            return_value=BoneMorphBaseRouteResolution(routes={}, blocked={}),
        ):
            routes = collector._scene_authored_input_routes(
                joints or ["|model|bone"],
                "|model",
                standard_mode_c=standard_mode_c,
            )
        return collector, routes

    def test_standard_mode_c_records_owned_output_exclusion_and_ignores_unowned(self):
        cmds = _PhysicsCmds()
        cmds.driver_sources["|model|bone.translateY"] = ["|blend_translate.output"]
        collector, routes = self._collect_routes(cmds, standard_mode_c=True)

        self.assertEqual(
            routes["|model|bone"]["translateX"],
            ("|driver", "inPreTranslateX"),
        )
        self.assertEqual(
            routes["|model|bone"]["rotateX"],
            ("|driver", "inPreRotateX"),
        )
        self.assertEqual(
            routes["|model|bone"]["translateY"],
            ("|blend_translate", "output"),
        )
        self.assertNotIn("|other_model|bone", routes)
        self.assertFalse(
            any(
                attr.startswith("outTranslate") or attr.startswith("outRotate")
                for route in routes.values()
                for _node, attr in route.values()
            )
        )

        selection = collector.diagnostics["track_selection"]
        self.assertEqual(selection["counts"]["physics_output_excluded"], 1)
        self.assertEqual(len(selection["evidence"]), 1)
        self.assertEqual(
            selection["evidence"][0],
            {
                "section": "bone",
                "name": "bone",
                "decision": "physics_output_excluded",
                "reason": "standard_mode_c_owned_physics_final_output",
                "source_key_count": 0,
                "planned_key_count": 0,
            },
        )

    def test_standard_mode_c_duplicate_target_raises_but_legacy_skips(self):
        cmds = _PhysicsCmds()
        cmds.solver_drivers["|solver.outBoneMatrices"] = [
            "|driver",
            "|driver_duplicate",
        ]
        cmds.driver_sources["|driver_duplicate.inPreTranslateX"] = ["|curve.output"]

        _collector, routes = self._collect_routes(cmds)
        self.assertNotIn("|model|bone", routes)
        with self.assertRaisesRegex(ValueError, "duplicate drivers for target"):
            self._collect_routes(cmds, standard_mode_c=True)

    def test_standard_mode_c_selected_ownership_ambiguity_raises_but_legacy_skips(self):
        cases = (
            ("roots", ["|model", "|other_model"], []),
            ("registries", [], ["|registry", "|other_registry"]),
        )
        for label, roots, registries in cases:
            with self.subTest(source=label):
                cmds = _PhysicsCmds()
                cmds.solver_roots["|solver"] = roots
                cmds.solver_registries["|solver"] = registries
                ownership_patch = mock.patch(
                    "mmd_tools.core.model_registry.get_model_registry",
                    return_value="|registry",
                )
                with ownership_patch:
                    _collector, routes = self._collect_routes(cmds)
                    self.assertNotIn("|model|bone", routes)
                    with self.assertRaisesRegex(ValueError, "ownership is ambiguous"):
                        self._collect_routes(cmds, standard_mode_c=True)

    def test_standard_mode_c_unrelated_ambiguous_solver_is_ignored(self):
        cmds = _PhysicsCmds()
        cmds.types["|other_model2"] = "transform"
        cmds.solver_roots["|other_solver"] = ["|other_model", "|other_model2"]

        collector, routes = self._collect_routes(cmds, standard_mode_c=True)
        self.assertIn("|model|bone", routes)
        self.assertEqual(
            collector.diagnostics["track_selection"]["counts"][
                "physics_output_excluded"
            ],
            1,
        )

    def test_standard_mode_c_shared_driver_across_solvers_raises(self):
        cmds = _PhysicsCmds()
        cmds.solver_drivers["|other_solver.outBoneMatrices"] = ["|driver"]

        with self.assertRaisesRegex(ValueError, "exactly one selected solver"):
            self._collect_routes(cmds, standard_mode_c=True)

    def test_physics_solver_driver_inventory_queries_each_output_once(self):
        cmds = _PhysicsCmds()

        self._collect_routes(cmds, standard_mode_c=True)

        expected = {
            f"{solver}.{output}": 1
            for solver in ("|solver", "|other_solver")
            for output in ("outBoneMatrices", "outBoneCount", "outSolved")
        }
        self.assertEqual(cmds.solver_output_query_counts, expected)

    def test_standard_mode_c_subset_omits_owned_target_without_evidence(self):
        cmds = _PhysicsCmds()
        cmds.driver_targets["|driver.mmd_target_joint_message"] = ["|model|other"]

        collector, routes = self._collect_routes(
            cmds,
            joints=["|model|bone"],
            standard_mode_c=True,
        )
        self.assertNotIn("|model|other", routes)
        self.assertNotIn("track_selection", collector.diagnostics)

    def test_standard_mode_c_target_outside_root_raises(self):
        cmds = _PhysicsCmds()
        cmds.driver_targets["|driver.mmd_target_joint_message"] = [
            "|other_model|bone"
        ]

        with self.assertRaisesRegex(ValueError, "outside the selected model"):
            self._collect_routes(cmds, standard_mode_c=True)

    def test_collect_mode_c_raw_preservation_skips_strict_physics_ownership(self):
        raw_provenance = {
            "raw_bone_interpolation_complete": True,
            "raw_bone_transform_complete": True,
            "raw_bone_key_count": 1,
            "raw_bone_interpolation": [
                {
                    "bone_name": "bone",
                    "frame_number": 0,
                    "position": [0.0, 0.0, 0.0],
                    "rotation": [0.0, 0.0, 0.0, 1.0],
                    "interpolation": [0] * 64,
                }
            ],
        }

        def collect_with_mode(preserve_raw):
            cmds = _PhysicsCmds()
            cmds.solver_drivers["|solver.outBoneMatrices"] = [
                "|driver",
                "|driver_duplicate",
            ]
            cmds.driver_sources["|driver_duplicate.inPreTranslateX"] = [
                "|curve.output"
            ]
            collector = VmdSceneCollector()
            collector._find_blend_shapes = lambda _target: []
            collector._resolve_tagged_track = lambda *_args: []
            collector._control_rig_dense_export = lambda _target: False
            collector._rotation_time_curve_interpolation = lambda _target: {}
            collector._mode_c_dense_frame_samples = lambda *_args: []
            collector.collect_bone_frames = mock.Mock(return_value=[])
            collector.collect_morph_frames = mock.Mock(return_value=[])
            collector.collect_camera_frames = mock.Mock(return_value=[])
            collector.collect_light_frames = mock.Mock(return_value=[])
            collector.collect_ik_show_hide_frames = mock.Mock(return_value=[])
            collector._model_name = lambda _target: "model"
            with mock.patch.object(collector_module, "cmds", cmds), mock.patch.object(
                collector_module,
                "collect_append_info",
                return_value={},
            ), mock.patch.object(
                collector_module,
                "collect_mmd_ik_passthrough_info",
                return_value={},
            ), mock.patch.object(
                collector_module,
                "read_mmd_control_rig_metadata",
                return_value=None,
            ), mock.patch.object(
                collector_module,
                "resolve_owned_bone_morph_base_routes",
                return_value=BoneMorphBaseRouteResolution(routes={}, blocked={}),
            ), mock.patch.object(
                collector_module,
                "_read_vmd_import_provenance",
                return_value=raw_provenance,
            ), mock.patch.object(
                collector_module,
                "_scene_maya_time_to_vmd_frame",
                return_value=lambda value: value,
            ):
                return collector.collect(
                    {
                        "target_model": "|model",
                        "joints": ["|model|bone"],
                        "vmd_mode": "C",
                        "preserve_raw_bone_transforms": preserve_raw,
                    }
                )

        with self.assertRaisesRegex(ValueError, "duplicate drivers for target"):
            collect_with_mode(False)
        result = collect_with_mode(True)
        self.assertEqual(result["bone_frames"], [])

    def test_standard_mode_c_multiple_target_connections_raise(self):
        cmds = _PhysicsCmds()
        cmds.driver_targets["|driver.mmd_target_joint_message"] = [
            "|model|bone",
            "|model|other",
        ]

        with self.assertRaisesRegex(ValueError, "exactly one target connection"):
            self._collect_routes(cmds, standard_mode_c=True)

    def test_standard_mode_c_duplicate_bone_index_across_targets_raise(self):
        cmds = _PhysicsCmds()
        cmds.solver_drivers["|solver.outBoneMatrices"] = ["|driver", "|other_driver"]
        cmds.solver_drivers["|other_solver.outBoneMatrices"] = []
        cmds.values["|other_driver.inBoneIndex"] = 4
        cmds.driver_targets["|other_driver.mmd_target_joint_message"] = [
            "|model|other"
        ]

        with self.assertRaisesRegex(ValueError, "duplicate bone index 4"):
            self._collect_routes(
                cmds,
                joints=["|model|bone", "|model|other"],
                standard_mode_c=True,
            )

    def test_standard_mode_c_missing_fractional_and_negative_index_raise(self):
        cases = (
            ("missing", None),
            ("fractional", 4.5),
            ("negative", -1),
        )
        for label, value in cases:
            with self.subTest(index=label):
                cmds = _PhysicsCmds()
                if label == "missing":
                    cmds.attributes.remove(("|driver", "inBoneIndex"))
                else:
                    cmds.values["|driver.inBoneIndex"] = value
                with self.assertRaisesRegex(ValueError, "valid non-negative bone index"):
                    self._collect_routes(cmds, standard_mode_c=True)

    def test_standard_mode_c_append_route_keeps_priority(self):
        cmds = _PhysicsCmds()
        append = {
            "|model|bone": {
                "node": "|append",
                "attr_map": {"translateX": "inputTranslateX"},
            }
        }

        collector, routes = self._collect_routes(
            cmds,
            append=append,
            standard_mode_c=True,
        )
        self.assertEqual(
            routes["|model|bone"]["translateX"],
            ("|append", "inputTranslateX"),
        )
        self.assertEqual(
            routes["|model|bone"]["rotateX"],
            ("|driver", "inPreRotateX"),
        )
        self.assertEqual(
            collector.diagnostics["track_selection"]["counts"][
                "physics_output_excluded"
            ],
            1,
        )

    def test_standard_mode_c_static_physics_pre_inputs_route_all_channels(self):
        cmds = _PhysicsCmds()
        cmds.driver_sources.clear()

        _collector, routes = self._collect_routes(cmds, standard_mode_c=True)

        self.assertEqual(
            routes["|model|bone"],
            {
                "translateX": ("|driver", "inPreTranslateX"),
                "translateY": ("|driver", "inPreTranslateY"),
                "translateZ": ("|driver", "inPreTranslateZ"),
                "rotateX": ("|driver", "inPreRotateX"),
                "rotateY": ("|driver", "inPreRotateY"),
                "rotateZ": ("|driver", "inPreRotateZ"),
            },
        )

    def test_standard_mode_c_existing_route_covers_missing_pre_input(self):
        cmds = _PhysicsCmds()
        cmds.attributes.remove(("|driver", "inPreRotateZ"))
        append = {
            "|model|bone": {
                "node": "|append",
                "attr_map": {"rotateZ": "inputRotateZ"},
            }
        }

        collector, routes = self._collect_routes(
            cmds,
            append=append,
            standard_mode_c=True,
        )

        self.assertNotIn(
            "|model|bone",
            collector._mode_c_physics_output_excluded_targets,
        )
        self.assertEqual(routes["|model|bone"]["rotateZ"], ("|append", "inputRotateZ"))
        self.assertEqual(len(routes["|model|bone"]), 6)

    def test_standard_mode_c_joint_authored_source_covers_missing_pre_input(self):
        cmds = _PhysicsCmds()
        cmds.attributes.remove(("|driver", "inPreTranslateZ"))
        cmds.driver_sources["|model|bone.translateZ"] = ["|curve.output"]

        collector, routes = self._collect_routes(cmds, standard_mode_c=True)

        self.assertNotIn(
            "|model|bone",
            collector._mode_c_physics_output_excluded_targets,
        )
        self.assertEqual(routes["|model|bone"]["translateZ"], ("|curve", "output"))
        self.assertEqual(len(routes["|model|bone"]), 6)

    def test_standard_mode_c_incomplete_physics_pre_inputs_are_excluded(self):
        cmds = _PhysicsCmds()
        cmds.attributes.remove(("|driver", "inPreRotateZ"))

        collector, routes = self._collect_routes(cmds, standard_mode_c=True)

        self.assertNotIn("|model|bone", routes)
        self.assertEqual(collector._mode_c_physics_output_excluded_targets, {"|model|bone"})
        evidence = collector.diagnostics["track_selection"]["evidence"]
        self.assertEqual(evidence[0]["decision"], "physics_output_excluded")
        self.assertEqual(evidence[0]["reason"], "incomplete_pre_physics_route")
        self.assertEqual(
            collector.collect_bone_frames(
                ["|model|bone"],
                0,
                2,
                input_routes=routes,
                dense_sample=True,
                force_dense_sample=True,
                dense_frame_samples=[0, 1, 2],
                time_converter=lambda value: value,
            ),
            [],
        )

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
        ), mock.patch.object(
            collector_module,
            "resolve_owned_bone_morph_base_routes",
            return_value=BoneMorphBaseRouteResolution(routes={}, blocked={}),
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
        ), mock.patch.object(
            collector_module,
            "resolve_owned_bone_morph_base_routes",
            return_value=BoneMorphBaseRouteResolution(routes={}, blocked={}),
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
        ), mock.patch.object(
            collector_module,
            "resolve_owned_bone_morph_base_routes",
            return_value=BoneMorphBaseRouteResolution(routes={}, blocked={}),
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
        ), mock.patch.object(
            collector_module,
            "resolve_owned_bone_morph_base_routes",
            return_value=BoneMorphBaseRouteResolution(routes={}, blocked={}),
        ):
            routes = collector._scene_authored_input_routes(
                ["|model|bone"], "|model"
            )
        self.assertNotIn("|model|bone", routes)


if __name__ == "__main__":
    unittest.main()
