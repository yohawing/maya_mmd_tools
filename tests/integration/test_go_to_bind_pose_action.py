"""Maya integration tests for one-shot Go to Bind Pose."""

import unittest

from maya import cmds

from mmd_tools.actions.go_to_bind_pose_action import GoToBindPoseAction


class TestGoToBindPoseActionMaya(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_unconnected_skeleton_enters_bind_pose_and_returns_to_motion(self):
        root = cmds.createNode("transform", name="model")
        cmds.select(clear=True)
        joint = cmds.joint(name="joint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        cmds.dagPose(joint, save=True, bindPose=True, name="model_bindPose")
        cmds.setAttr(f"{joint}.translate", 4.0, 5.0, 6.0)
        cmds.setAttr(f"{joint}.rotate", 10.0, 20.0, 30.0)

        action = GoToBindPoseAction(cmds)
        result = action.execute(root)

        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr(f"{joint}.translate")[0]),
            (1.0, 2.0, 3.0),
        )
        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr(f"{joint}.rotate")[0]),
            (0.0, 0.0, 0.0),
        )
        self.assertTrue(action.active)

        restored = action.return_to_motion()

        self.assertTrue(restored.succeeded, restored.error)
        self.assertFalse(action.active)
        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr(f"{joint}.translate")[0]),
            (4.0, 5.0, 6.0),
        )
        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr(f"{joint}.rotate")[0]),
            (10.0, 20.0, 30.0),
        )

    def test_driven_restore_isolates_then_reconnects_existing_driver(self):
        root = cmds.createNode("transform", name="drivenModel")
        cmds.select(clear=True)
        joint = cmds.joint(name="drivenJoint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        cmds.dagPose(joint, save=True, bindPose=True, name="driven_bindPose")
        driver = cmds.createNode("multiplyDivide", name="rotateDriver")
        cmds.connectAttr(f"{driver}.output", f"{joint}.rotate", force=True)
        before = cmds.listConnections(
            f"{joint}.rotate", source=True, destination=False, plugs=True
        )

        action = GoToBindPoseAction(cmds)
        entered = action.execute(root)

        self.assertTrue(entered.succeeded, entered.error)
        self.assertIsNone(
            cmds.listConnections(
                f"{joint}.rotate", source=True, destination=False, plugs=True
            )
        )

        restored = action.return_to_motion()

        self.assertTrue(restored.succeeded, restored.error)
        self.assertEqual(
            cmds.listConnections(
                f"{joint}.rotate", source=True, destination=False, plugs=True
            ),
            before,
        )

    def test_animation_curve_payload_stays_exact_across_timeline_and_return(self):
        root = cmds.createNode("transform", name="animatedModel")
        cmds.select(clear=True)
        joint = cmds.joint(name="animatedJoint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        cmds.dagPose(joint, save=True, bindPose=True, name="animated_bindPose")
        cmds.setKeyframe(joint, attribute="rotateX", time=1, value=10.0)
        cmds.setKeyframe(joint, attribute="rotateX", time=20, value=40.0)
        cmds.currentTime(1)
        curve = (cmds.listConnections(f"{joint}.rotateX", source=True, destination=False) or [None])[0]
        before_times = cmds.keyframe(curve, query=True, timeChange=True)
        before_values = cmds.keyframe(curve, query=True, valueChange=True)
        cmds.select(joint, replace=True)

        action = GoToBindPoseAction(cmds)
        entered = action.execute(root)
        self.assertTrue(entered.succeeded, entered.error)
        cmds.currentTime(20)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), 0.0, places=6)

        restored = action.return_to_motion()

        self.assertTrue(restored.succeeded, restored.error)
        self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True), before_times)
        self.assertEqual(cmds.keyframe(curve, query=True, valueChange=True), before_values)
        self.assertAlmostEqual(cmds.currentTime(query=True), 1.0, places=6)
        self.assertEqual(cmds.ls(selection=True, long=True), [joint])
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), 10.0, places=6)

    def test_offset_parent_matrix_driver_is_restored_exactly(self):
        root = cmds.createNode("transform", name="opmModel")
        cmds.select(clear=True)
        joint = cmds.joint(name="opmJoint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        cmds.dagPose(joint, save=True, bindPose=True, name="opm_bindPose")
        matrix = cmds.createNode("composeMatrix", name="opmDriver")
        cmds.setAttr(f"{matrix}.inputTranslateX", 7.0)
        cmds.connectAttr(f"{matrix}.outputMatrix", f"{joint}.offsetParentMatrix")
        before_source = cmds.connectionInfo(
            f"{joint}.offsetParentMatrix", sourceFromDestination=True
        )
        before_matrix = cmds.getAttr(f"{joint}.offsetParentMatrix")

        action = GoToBindPoseAction(cmds)
        entered = action.execute(root)

        self.assertTrue(entered.succeeded, entered.error)
        self.assertFalse(
            cmds.connectionInfo(f"{joint}.offsetParentMatrix", isDestination=True)
        )
        restored = action.return_to_motion()
        self.assertTrue(restored.succeeded, restored.error)
        self.assertEqual(
            cmds.connectionInfo(
                f"{joint}.offsetParentMatrix", sourceFromDestination=True
            ),
            before_source,
        )
        self.assertEqual(cmds.getAttr(f"{joint}.offsetParentMatrix"), before_matrix)

    def test_compound_lock_and_renamed_nodes_restore_by_uuid(self):
        root = cmds.createNode("transform", name="renameModel")
        cmds.select(clear=True)
        joint = cmds.joint(name="renameJoint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        cmds.dagPose(joint, save=True, bindPose=True, name="rename_bindPose")
        driver = cmds.createNode("multiplyDivide", name="renameDriver")
        cmds.connectAttr(f"{driver}.output", f"{joint}.rotate")
        cmds.setAttr(f"{joint}.rotate", lock=True)
        cmds.select(joint, replace=True)
        before_source = cmds.connectionInfo(f"{joint}.rotate", sourceFromDestination=True)
        joint_uuid = (cmds.ls(joint, uuid=True) or [None])[0]

        action = GoToBindPoseAction(cmds)
        entered = action.execute(root)
        self.assertTrue(entered.succeeded, entered.error)
        renamed_root = cmds.rename(root, "renamedModel")
        current_joint = (cmds.ls(joint_uuid, long=True) or [None])[0]
        renamed_joint = cmds.rename(current_joint, "renamedJoint")
        renamed_joint = (cmds.ls(renamed_joint, long=True) or [renamed_joint])[0]

        restored = action.return_to_motion()

        self.assertTrue(restored.succeeded, restored.error)
        self.assertTrue(cmds.getAttr(f"{renamed_joint}.rotate", lock=True))
        self.assertEqual(
            cmds.connectionInfo(f"{renamed_joint}.rotate", sourceFromDestination=True),
            before_source,
        )
        self.assertEqual(cmds.ls(selection=True, long=True), [renamed_joint])
        self.assertTrue(cmds.objExists(renamed_root))


if __name__ == "__main__":
    unittest.main()
