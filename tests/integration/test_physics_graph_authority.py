"""Integration coverage for the model-root physics DAG contract."""

from pathlib import Path
from types import SimpleNamespace
import unittest

from maya import cmds

from mmd_tools.converters.physics_scene_builder import (
    _find_drivers_for_solver,
    _find_solver_for_model,
    _find_target_joint_for_driver,
    build_physics_live_graph,
    recover_physics_driver_connections,
)
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

    def _build_graph(self):
        cmds.namespace(add="graphModel")
        cmds.namespace(set=":graphModel")
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


if __name__ == "__main__":
    unittest.main()
