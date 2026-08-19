"""Integration coverage for the model-root physics DAG contract."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from maya import cmds

from mmd_tools.converters.physics_scene_builder import (
    _find_drivers_for_solver,
    _find_solver_for_model,
    _find_target_joint_for_driver,
    _prune_solver_kinematic_cycles,
    build_physics_live_graph,
    recover_physics_driver_connections,
)
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from tests.common.maya_test_base import MayaTestBase


class TestPhysicsGraphAuthority(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = str(
            Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        )
        try:
            cmds.loadPlugin(plugin_path)
        except Exception:
            pass

    def _build_graph(self, namespace="graphModel"):
        cmds.namespace(add=namespace)
        cmds.namespace(set=f":{namespace}")
        try:
            root = cmds.group(empty=True, name="root")
            joints = []
            for index in range(3):
                cmds.select(clear=True)
                joint = cmds.joint(name=f"bone{index}")
                cmds.parent(joint, root)
                joints.append(joint)
            graph = build_physics_live_graph(
                rigid_bodies=[
                    SimpleNamespace(physics_mode=1, related_bone_index=index)
                    for index in range(len(joints))
                ],
                bones=[
                    SimpleNamespace(parent_bone_index=-1)
                    for _ in joints
                ],
                maya_joints=joints,
                root_group=root,
            )
        finally:
            cmds.namespace(set=":")
        return root, joints, graph

    def test_world_toggle_is_explicitly_dirty_and_off_passthroughs_pre_pose(self):
        _root, joints, graph = self._build_graph()
        solver = graph["solver"]
        driver = graph["drivers"][0]
        source = cmds.connectionInfo(
            f"{solver}.inWorldSettingsVersion", sourceFromDestination=True
        )
        self.assertTrue(source.endswith(".outSettingsVersion"), source)
        world = source.rsplit(".", 1)[0]

        expected_t = (1.25, -2.5, 3.75)
        expected_r = (10.0, -20.0, 30.0)
        cmds.setAttr(f"{driver}.inPreTranslate", *expected_t)
        cmds.setAttr(f"{driver}.inPreRotate", *expected_r)
        for enabled in (False, True, False):
            cmds.setAttr(f"{world}.enable", enabled)
            _ = cmds.getAttr(f"{solver}.outStatus")
            actual_t = cmds.getAttr(f"{joints[0]}.translate")[0]
            actual_r = cmds.getAttr(f"{joints[0]}.rotate")[0]
            for actual, expected in zip(actual_t, expected_t):
                self.assertAlmostEqual(actual, expected, places=5)
            for actual, expected in zip(actual_r, expected_r):
                self.assertAlmostEqual(actual, expected, places=5)

        updated_t = (-4.5, 5.25, 6.75)
        updated_r = (-15.0, 25.0, -35.0)
        cmds.setAttr(f"{driver}.inPreTranslate", *updated_t)
        cmds.setAttr(f"{driver}.inPreRotate", *updated_r)
        for actual, expected in zip(cmds.getAttr(f"{joints[0]}.translate")[0], updated_t):
            self.assertAlmostEqual(actual, expected, places=5)
        for actual, expected in zip(cmds.getAttr(f"{joints[0]}.rotate")[0], updated_r):
            self.assertAlmostEqual(actual, expected, places=5)

        cmds.setKeyframe(driver, attribute="inPreTranslateX", time=1, value=-1.0)
        cmds.setKeyframe(driver, attribute="inPreTranslateX", time=10, value=9.0)
        cmds.setKeyframe(driver, attribute="inPreRotateY", time=1, value=-5.0)
        cmds.setKeyframe(driver, attribute="inPreRotateY", time=10, value=45.0)
        cmds.currentTime(10)
        self.assertAlmostEqual(cmds.getAttr(f"{joints[0]}.translateX"), 9.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{joints[0]}.rotateY"), 45.0, places=5)

    def test_sparse_vmd_keys_route_to_physics_pre_inputs(self):
        _root, joints, graph = self._build_graph("vmdRouteModel")
        joint = joints[0]
        driver = graph["drivers"][0]
        converter = VmdConverter()
        converter.use_animation_layers = False
        converter.bone_name_mapping = {"bone0": joint}
        converter._bone_bind_poses["bone0"] = (0.0, 0.0, 0.0)

        route = converter._build_legacy_bone_key_routes()[joint]
        expected_targets = {
            "translateX": "inPreTranslateX",
            "translateY": "inPreTranslateY",
            "translateZ": "inPreTranslateZ",
            "rotateX": "inPreRotateX",
            "rotateY": "inPreRotateY",
            "rotateZ": "inPreRotateZ",
        }
        self.assertEqual(
            route["attr_targets"],
            {
                channel: (driver, target)
                for channel, target in expected_targets.items()
            },
        )

        frame = VmdBoneFrame()
        frame.bone_name = "bone0"
        frame.frame_number = 7
        frame.position = (1.25, -2.5, 3.75)
        frame.rotation = (0.0, 0.0, 0.0, 1.0)
        converter._set_bone_keyframes(joint, [frame], "bone0", route)
        cmds.currentTime(converter.vmd_frame_to_maya_time(7), edit=True)

        expected_translate = (1.25, -2.5, -3.75)
        pre_translate = cmds.getAttr(f"{driver}.inPreTranslate")[0]
        visible_translate = cmds.getAttr(f"{joint}.translate")[0]
        for actual, expected in zip(pre_translate, expected_translate):
            self.assertAlmostEqual(actual, expected, places=5)
        for actual, expected in zip(visible_translate, expected_translate):
            self.assertAlmostEqual(actual, expected, places=5)
        for source_channel, target_channel in expected_targets.items():
            self.assertTrue(
                cmds.keyframe(
                    f"{driver}.{target_channel}",
                    query=True,
                    keyframeCount=True,
                )
            )
            compound = "outTranslate" if source_channel.startswith("translate") else "outRotate"
            self.assertEqual(
                cmds.connectionInfo(
                    f"{joint}.{source_channel}", sourceFromDestination=True
                ),
                f"{driver}.{compound}",
            )

        pre_rotate = cmds.getAttr(f"{driver}.inPreRotate")[0]
        visible_rotate = cmds.getAttr(f"{joint}.rotate")[0]
        for actual, expected in zip(visible_rotate, pre_rotate):
            self.assertAlmostEqual(actual, expected, places=5)

        self.assertTrue(
            cmds.isConnected(f"{driver}.outTranslate", f"{joint}.translate")
        )
        self.assertTrue(cmds.isConnected(f"{driver}.outRotate", f"{joint}.rotate"))

    def test_unattached_dynamic_body_silently_omits_joint_driver(self):
        cmds.namespace(add="unattachedModel")
        cmds.namespace(set=":unattachedModel")
        logger = Mock()
        try:
            root = cmds.group(empty=True, name="root")
            cmds.select(clear=True)
            joint = cmds.joint(name="bone0")
            cmds.parent(joint, root)
            graph = build_physics_live_graph(
                rigid_bodies=[
                    SimpleNamespace(physics_mode=1, related_bone_index=-1),
                    SimpleNamespace(physics_mode=1, related_bone_index=0),
                ],
                bones=[SimpleNamespace(parent_bone_index=-1)],
                maya_joints=[joint],
                root_group=root,
                logger=logger,
            )
        finally:
            cmds.namespace(set=":")

        self.assertEqual(len(graph["drivers"]), 1)
        logger.warning.assert_not_called()

    def test_kinematic_cycle_filter_disconnects_only_reported_inputs(self):
        cycle_plug = "cycleModel_solver.inKinematicWorldMatrix[7]"
        cycle_check = Mock(
            side_effect=[
                [cycle_plug, "cycleModel_solver.outSolved"],
                [],
                [],
            ]
        )
        with patch.object(cmds, "cycleCheck", cycle_check), patch.object(
            cmds, "ls", return_value=["cycleModel_solver"]
        ), patch.object(
            cmds,
            "listConnections",
            return_value=["cycleModel_bone.worldMatrix[0]"],
        ), patch.object(cmds, "disconnectAttr") as disconnect:
            summary = _prune_solver_kinematic_cycles(
                "cycleModel_solver",
                [cycle_plug, "cycleModel_solver.inKinematicWorldMatrix[9]"],
            )

        self.assertEqual(summary["candidate_count"], 2)
        self.assertEqual(summary["pruned_count"], 1)
        self.assertEqual(summary["pruned_bone_indices"], [7])
        self.assertEqual(summary["remaining_count"], 0)
        disconnect.assert_called_once_with(
            "cycleModel_bone.worldMatrix[0]", cycle_plug
        )

    def test_two_namespaced_models_share_one_world(self):
        _root_a, _joints_a, graph_a = self._build_graph("modelA")
        _root_b, _joints_b, graph_b = self._build_graph("modelB")
        worlds = cmds.ls(type="mmdPhysicsWorldShape", long=True) or []
        self.assertEqual(len(worlds), 1)
        world = worlds[0]
        for graph in (graph_a, graph_b):
            source = cmds.connectionInfo(
                f"{graph['solver']}.inWorldSettingsVersion",
                sourceFromDestination=True,
            )
            source_world = (cmds.ls(source.rsplit(".", 1)[0], long=True) or [None])[0]
            self.assertEqual(source_world, world)

    def _assert_graph_contract(self, root):
        solver = _find_solver_for_model(root)
        self.assertIsNotNone(solver)
        drivers = _find_drivers_for_solver(solver)
        self.assertEqual(len(drivers), 3)

        physics_destinations = [
            node
            for node in (
                cmds.listConnections(
                    f"{root}.message", source=False, destination=True
                ) or []
            )
            if cmds.nodeType(node) in {"mmdPhysicsSolver", "mmdPhysicsBoneDriver"}
        ]
        self.assertEqual(physics_destinations, [solver])
        for driver in drivers:
            self.assertFalse(
                cmds.attributeQuery("mmd_model_root", node=driver, exists=True)
            )
            target = _find_target_joint_for_driver(driver)
            self.assertTrue(target)
            self.assertTrue(cmds.objExists(target))
        return solver, drivers

    def test_solver_is_traversal_root_across_scene_lifecycle(self):
        root, _joints, graph = self._build_graph()
        solver, drivers = self._assert_graph_contract(root)
        self.assertEqual(set(drivers), set(graph["drivers"]))

        target = _find_target_joint_for_driver(drivers[0])
        cmds.disconnectAttr(f"{drivers[0]}.outTranslate", f"{target}.translate")
        cmds.disconnectAttr(f"{drivers[0]}.outRotate", f"{target}.rotate")
        recovery = recover_physics_driver_connections(root)
        self.assertEqual(recovery["recovered"], 1)
        self.assertTrue(
            cmds.isConnected(f"{drivers[0]}.outTranslate", f"{target}.translate")
        )
        self.assertTrue(
            cmds.isConnected(f"{drivers[0]}.outRotate", f"{target}.rotate")
        )

        scene_path = self.get_temp_filename("physics_graph_authority.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(scene_path, open=True, force=True)
        self._assert_graph_contract("graphModel:root")

        original_solvers = set(cmds.ls(type="mmdPhysicsSolver") or [])
        duplicated_root = cmds.duplicate(
            "graphModel:root", upstreamNodes=True, returnRootsOnly=True
        )[0]
        duplicated_solver = _find_solver_for_model(duplicated_root)
        self.assertIsNotNone(duplicated_solver)
        self.assertNotIn(duplicated_solver, original_solvers)
        duplicated_drivers = _find_drivers_for_solver(duplicated_solver)
        self.assertEqual(len(duplicated_drivers), 3)
        for driver in duplicated_drivers:
            self.assertTrue(cmds.objExists(_find_target_joint_for_driver(driver)))

        cmds.file(new=True, force=True)
        cmds.file(scene_path, reference=True, namespace="refGraph")
        self._assert_graph_contract("refGraph:graphModel:root")

    def test_world_exposes_only_enable_and_serializes_it(self):
        _root, _joints, graph = self._build_graph()
        solver = graph["solver"]
        source_plug = cmds.connectionInfo(
            f"{solver}.inWorldSettings", sourceFromDestination=True
        )
        world = source_plug.rsplit(".", 1)[0]
        self.assertEqual(cmds.nodeType(world), "mmdPhysicsWorldShape")
        self.assertFalse(cmds.getAttr(f"{world}.enable"))
        self.assertTrue(cmds.getAttr(f"{world}.enable", keyable=True))
        for attr in (
            "gravity", "gravityX", "gravityY", "gravityZ", "fixedTimestep",
            "maxSubsteps", "timeScale", "startFrame", "resetGeneration",
            "physicsMode", "outSettingsVersion",
        ):
            self.assertTrue(cmds.attributeQuery(attr, node=world, hidden=True), attr)
            self.assertFalse(cmds.getAttr(f"{world}.{attr}", keyable=True), attr)

        self.assertTrue(cmds.getAttr(f"{world}.hiddenInOutliner"))
        cmds.setAttr(f"{world}.enable", True)
        scene_path = self.get_temp_filename("physics_world_enable.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(scene_path, open=True, force=True)
        reopened_world = (cmds.ls(type="mmdPhysicsWorldShape") or [None])[0]
        self.assertIsNotNone(reopened_world)
        self.assertTrue(cmds.getAttr(f"{reopened_world}.enable"))


if __name__ == "__main__":
    unittest.main()
