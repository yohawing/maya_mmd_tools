"""Maya integration tests for non-destructive Rest Pose display."""

import os
import unittest

from maya import cmds

from mmd_tools.actions.rest_pose_action import RestPoseManager


class TestRestPoseActionMaya(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        self.manager = RestPoseManager(cmds)

    def tearDown(self):
        if self.manager.active:
            self.manager.return_to_motion()
        cmds.file(new=True, force=True)

    @staticmethod
    def _create_driven_model(namespace):
        cmds.namespace(add=namespace)
        root = cmds.createNode("transform", name=f"{namespace}:root")
        cmds.select(clear=True)
        joint = cmds.joint(name=f"{namespace}:joint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        pose = cmds.dagPose(joint, save=True, bindPose=True, name=f"{namespace}:bindPose")

        translate_driver = cmds.createNode("multiplyDivide", name=f"{namespace}:translateDriver")
        rotate_driver = cmds.createNode("multiplyDivide", name=f"{namespace}:rotateDriver")
        cmds.setAttr(f"{translate_driver}.input1", 4.0, 5.0, 6.0)
        cmds.setAttr(f"{rotate_driver}.input1", 10.0, 20.0, 30.0)
        cmds.connectAttr(f"{translate_driver}.output", f"{joint}.translate", force=True)
        cmds.connectAttr(f"{rotate_driver}.output", f"{joint}.rotate", force=True)
        return root, joint, pose, translate_driver, rotate_driver

    def test_compound_rig_writers_restore_losslessly_and_other_model_is_unchanged(self):
        root_a, joint_a, _pose_a, translate_a, rotate_a = self._create_driven_model("modelA")
        _root_b, joint_b, _pose_b, _translate_b, rotate_b = self._create_driven_model("modelB")
        before_translate_a = cmds.listConnections(
            f"{joint_a}.translate", source=True, destination=False, plugs=True
        )
        before_rotate_a = cmds.listConnections(
            f"{joint_a}.rotate", source=True, destination=False, plugs=True
        )
        before_b = cmds.listConnections(
            f"{joint_b}.rotate",
            source=True,
            destination=False,
            plugs=True,
        )

        entered = self.manager.enter_rest_pose(root_a)

        self.assertTrue(entered.succeeded, entered.error)
        self.assertFalse(cmds.listConnections(f"{joint_a}.translate", source=True, destination=False))
        self.assertFalse(cmds.listConnections(f"{joint_a}.rotate", source=True, destination=False))
        self.assertEqual(tuple(round(v, 6) for v in cmds.getAttr(f"{joint_a}.translate")[0]), (1.0, 2.0, 3.0))
        self.assertEqual(tuple(round(v, 6) for v in cmds.getAttr(f"{joint_a}.rotate")[0]), (0.0, 0.0, 0.0))
        self.assertEqual(
            cmds.listConnections(f"{joint_b}.rotate", source=True, destination=False, plugs=True),
            before_b,
        )
        self.assertIsNotNone(rotate_b)

        returned = self.manager.return_to_motion()

        self.assertTrue(returned.succeeded, returned.error)
        self.assertEqual(
            cmds.listConnections(f"{joint_a}.translate", source=True, destination=False, plugs=True),
            before_translate_a,
        )
        self.assertEqual(
            cmds.listConnections(f"{joint_a}.rotate", source=True, destination=False, plugs=True),
            before_rotate_a,
        )
        self.assertEqual(tuple(round(v, 6) for v in cmds.getAttr(f"{joint_a}.translate")[0]), (4.0, 5.0, 6.0))
        for actual, expected in zip(cmds.getAttr(f"{joint_a}.rotate")[0], (10.0, 20.0, 30.0)):
            self.assertAlmostEqual(actual, expected, places=5)

    def test_undo_enter_restores_topology_and_manager_state(self):
        root, joint, _pose, _translate, rotate = self._create_driven_model("undoModel")
        before_rotate = cmds.listConnections(
            f"{joint}.rotate", source=True, destination=False, plugs=True
        )

        entered = self.manager.enter_rest_pose(root)
        self.assertTrue(entered.succeeded, entered.error)
        self.assertFalse(cmds.listConnections(f"{joint}.rotate", source=True, destination=False))

        cmds.undo()
        self.manager._after_undo()

        self.assertEqual(
            cmds.listConnections(f"{joint}.rotate", source=True, destination=False, plugs=True),
            before_rotate,
        )
        self.assertIsNotNone(rotate)
        self.assertFalse(self.manager.active)

        cmds.redo()
        self.manager._after_redo()

        self.assertFalse(cmds.listConnections(f"{joint}.rotate", source=True, destination=False))
        self.assertTrue(self.manager.active)

        self.manager.return_to_motion()
        cmds.undo()
        self.manager._after_undo()
        self.assertFalse(cmds.listConnections(f"{joint}.rotate", source=True, destination=False))
        self.assertTrue(self.manager.active)

        cmds.redo()
        self.manager._after_redo()
        self.assertEqual(
            cmds.listConnections(f"{joint}.rotate", source=True, destination=False, plugs=True),
            before_rotate,
        )
        self.assertFalse(self.manager.active)

    def test_kokomi_ccdik_connections_restore_exactly(self):
        """Exercise the model that originally made dagPose restore fail."""
        pmx_path = os.environ.get("MMD_REST_POSE_KOKOMI_PMX")
        if not pmx_path or not os.path.isfile(pmx_path):
            self.skipTest("MMD_REST_POSE_KOKOMI_PMX is not configured")

        from mmd_tools.io.mmd_importer import import_mmd_file

        root = import_mmd_file(
            pmx_path,
            options={
                "use_namespace": True,
                "setup_rig": True,
                "import_physics": False,
                "create_mmd_shaders": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        self.assertTrue(root)
        driven_joints = []
        for leaf, driver_fragment in (
            ("er32_1", "left_leg_ik_mmdCcdIk"),
            ("er3Nub_1", "left_toe_ik_mmdCcdIk"),
        ):
            matches = cmds.ls(f"*:{leaf}", long=True, type="joint") or []
            self.assertEqual(len(matches), 1, matches)
            joint = matches[0]
            sources = cmds.listConnections(
                f"{joint}.rotate", source=True, destination=False, plugs=True
            ) or []
            self.assertTrue(any(driver_fragment in source for source in sources), sources)
            driven_joints.append((joint, sources))

        entered = self.manager.enter_rest_pose(str(root))

        self.assertTrue(entered.succeeded, entered.error)
        for joint, _sources in driven_joints:
            self.assertFalse(
                cmds.listConnections(
                    f"{joint}.rotate", source=True, destination=False, plugs=True
                )
            )

        returned = self.manager.return_to_motion()

        self.assertTrue(returned.succeeded, returned.error)
        for joint, sources in driven_joints:
            self.assertEqual(
                cmds.listConnections(
                    f"{joint}.rotate", source=True, destination=False, plugs=True
                ),
                sources,
            )


if __name__ == "__main__":
    unittest.main()
