"""Maya integration test for Bone Reset Pose animCurve keying."""

import unittest

from maya import cmds

from mmd_tools.adapters import MayaCmdsAdapter
from mmd_tools.ui.rest_pose_transaction import ResetPoseTransaction


class TestBoneResetPoseMaya(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True, infinity=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def test_reset_pose_keys_direct_curve_and_undo_restores_previous_value(self):
        root = cmds.createNode("transform", name="model")
        cmds.select(clear=True)
        joint = cmds.joint(name="joint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        root = (cmds.ls(root, long=True) or [root])[0]

        cmds.setKeyframe(joint, attribute="rotateX", time=1.0, value=25.0)
        cmds.currentTime(24.0, edit=True)
        curve_plug = cmds.listConnections(
            f"{joint}.rotateX", source=True, destination=False, plugs=True
        )
        self.assertEqual(len(curve_plug or []), 1)
        curve = curve_plug[0].rsplit(".", 1)[0]
        self.assertEqual(cmds.getAttr(f"{joint}.rotateX"), 25.0)

        model_uuid = (cmds.ls(root, uuid=True) or [None])[0]
        transaction = ResetPoseTransaction(
            MayaCmdsAdapter(cmds),
            model_root=root,
            model_uuid=str(model_uuid),
            targets=[joint],
            bind_translations={joint: (1.0, 2.0, 3.0)},
        )

        self.assertEqual(transaction.apply(), 1)
        cmds.dgdirty(a=True)
        self.assertEqual(cmds.getAttr(f"{joint}.rotateX"), 0.0)
        self.assertEqual(
            cmds.listConnections(
                f"{joint}.rotateX", source=True, destination=False, plugs=True
            ),
            curve_plug,
        )
        self.assertIn(24.0, cmds.keyframe(curve, query=True, timeChange=True) or [])
        self.assertEqual(
            cmds.keyframe(curve, query=True, time=(24.0, 24.0), valueChange=True),
            [0.0],
        )

        cmds.undo()

        cmds.dgdirty(a=True)
        self.assertEqual(cmds.getAttr(f"{joint}.rotateX"), 25.0)
        self.assertEqual(
            cmds.listConnections(
                f"{joint}.rotateX", source=True, destination=False, plugs=True
            ),
            curve_plug,
        )
        self.assertNotIn(24.0, cmds.keyframe(curve, query=True, timeChange=True) or [])
        self.assertEqual(
            cmds.keyframe(curve, query=True, time=(1.0, 1.0), valueChange=True),
            [25.0],
        )

    def test_reset_pose_restores_stored_translation_and_zero_rotation(self):
        root = cmds.createNode("transform", name="staticModel")
        cmds.select(clear=True)
        joint = cmds.joint(name="staticJoint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        root = (cmds.ls(root, long=True) or [root])[0]
        cmds.setAttr(f"{joint}.translate", 4.0, 5.0, 6.0)
        cmds.setAttr(f"{joint}.rotate", 10.0, 20.0, 30.0)

        transaction = ResetPoseTransaction(
            MayaCmdsAdapter(cmds),
            model_root=root,
            model_uuid=str((cmds.ls(root, uuid=True) or [None])[0]),
            targets=[joint],
            bind_translations={joint: (1.0, 2.0, 3.0)},
        )

        self.assertEqual(transaction.apply(), 1)
        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr(f"{joint}.translate")[0]),
            (1.0, 2.0, 3.0),
        )
        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr(f"{joint}.rotate")[0]),
            (0.0, 0.0, 0.0),
        )

        cmds.undo()

        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr(f"{joint}.translate")[0]),
            (4.0, 5.0, 6.0),
        )
        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr(f"{joint}.rotate")[0]),
            (10.0, 20.0, 30.0),
        )


if __name__ == "__main__":
    unittest.main()
