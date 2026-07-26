"""Maya integration tests for one-shot Go to Bind Pose."""

import unittest

from maya import cmds

from mmd_tools.actions.go_to_bind_pose_action import GoToBindPoseAction


class TestGoToBindPoseActionMaya(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_unconnected_skeleton_restores_bind_pose_without_session_state(self):
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
        self.assertFalse(hasattr(action, "return_to_motion"))

    def test_failed_restore_never_disconnects_existing_driver(self):
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

        GoToBindPoseAction(cmds).execute(root)

        self.assertEqual(
            cmds.listConnections(
                f"{joint}.rotate", source=True, destination=False, plugs=True
            ),
            before,
        )


if __name__ == "__main__":
    unittest.main()
