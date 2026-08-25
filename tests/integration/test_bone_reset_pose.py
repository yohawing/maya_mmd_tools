"""Maya integration tests for keyless Bone Reset Pose."""

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

    def test_reset_pose_temporarily_overrides_anim_curve_without_keying(self):
        root = cmds.createNode("transform", name="model")
        cmds.select(clear=True)
        joint = cmds.joint(name="joint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        root = (cmds.ls(root, long=True) or [root])[0]

        cmds.setKeyframe(joint, attribute="rotateX", time=1.0, value=25.0)
        cmds.setKeyframe(joint, attribute="rotateX", time=36.0, value=35.0)
        cmds.setAttr(f"{joint}.rotateY", 10.0)
        cmds.currentTime(24.0, edit=True)
        curve_plug = cmds.listConnections(
            f"{joint}.rotateX", source=True, destination=False, plugs=True
        )
        self.assertEqual(len(curve_plug or []), 1)
        curve = curve_plug[0].rsplit(".", 1)[0]
        motion_value = float(cmds.getAttr(f"{joint}.rotateX"))
        self.assertNotEqual(motion_value, 0.0)
        key_times = cmds.keyframe(curve, query=True, timeChange=True) or []
        key_values = cmds.keyframe(curve, query=True, valueChange=True) or []
        cmds.currentTime(25.0, edit=True)
        motion_value_25 = float(cmds.getAttr(f"{joint}.rotateX"))
        cmds.currentTime(24.0, edit=True)

        model_uuid = (cmds.ls(root, uuid=True) or [None])[0]
        transaction = ResetPoseTransaction(
            MayaCmdsAdapter(cmds),
            model_root=root,
            model_uuid=str(model_uuid),
            targets=[joint],
            bind_translations={joint: (1.0, 2.0, 3.0)},
        )

        self.assertEqual(transaction.apply(), 1)
        self.assertEqual(cmds.getAttr(f"{joint}.rotateX"), 0.0)
        self.assertEqual(
            cmds.listConnections(
                f"{joint}.rotateX", source=True, destination=False, plugs=True
            ),
            curve_plug,
        )
        self.assertEqual(
            cmds.keyframe(curve, query=True, timeChange=True) or [],
            key_times,
        )
        self.assertEqual(
            cmds.keyframe(curve, query=True, valueChange=True) or [],
            key_values,
        )
        undo_was_enabled = bool(cmds.undoInfo(query=True, state=True))
        if undo_was_enabled:
            cmds.undoInfo(stateWithoutFlush=False)
        try:
            cmds.currentTime(25.0, edit=True)
        finally:
            if undo_was_enabled:
                cmds.undoInfo(stateWithoutFlush=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), motion_value_25)
        cmds.undo()
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), motion_value_25)
        self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True) or [], key_times)
        self.assertEqual(cmds.keyframe(curve, query=True, valueChange=True) or [], key_values)
        cmds.redo()
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), motion_value_25)
        self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True) or [], key_times)
        self.assertEqual(cmds.keyframe(curve, query=True, valueChange=True) or [], key_values)

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

    def test_reset_pose_writes_external_authored_compounds_not_evaluated_joint(self):
        root = cmds.createNode("transform", name="semanticModel")
        cmds.select(clear=True)
        joint = cmds.joint(name="semanticJoint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        root = (cmds.ls(root, long=True) or [root])[0]
        authoring = cmds.createNode("network", name="appendAuthoring")
        for compound in ("baseTranslate", "baseRotate"):
            cmds.addAttr(authoring, longName=compound, attributeType="double3")
            for axis in "XYZ":
                cmds.addAttr(
                    authoring,
                    longName=f"{compound}{axis}",
                    attributeType="double",
                    parent=compound,
                    keyable=True,
                )
        cmds.setAttr(f"{authoring}.baseTranslate", 7.0, 8.0, 9.0, type="double3")
        cmds.setAttr(f"{authoring}.baseRotate", 10.0, 20.0, 30.0, type="double3")
        cmds.setKeyframe(authoring, attribute="baseRotateX", time=1.0, value=15.0)
        cmds.setKeyframe(authoring, attribute="baseRotateX", time=36.0, value=35.0)
        cmds.currentTime(24.0, edit=True)
        curve = (cmds.listConnections(
            f"{authoring}.baseRotateX", source=True, destination=False
        ) or [None])[0]
        key_times = cmds.keyframe(curve, query=True, timeChange=True) or []
        key_values = cmds.keyframe(curve, query=True, valueChange=True) or []
        motion_value = float(cmds.getAttr(f"{authoring}.baseRotateX"))
        joint_before = (
            tuple(cmds.getAttr(f"{joint}.translate")[0]),
            tuple(cmds.getAttr(f"{joint}.rotate")[0]),
        )
        transaction = ResetPoseTransaction(
            MayaCmdsAdapter(cmds),
            model_root=root,
            model_uuid=str((cmds.ls(root, uuid=True) or [None])[0]),
            targets=[joint],
            bind_translations={joint: (1.0, 2.0, 3.0)},
            authored_plugs_by_target={
                joint: (
                    f"{authoring}.baseTranslate",
                    f"{authoring}.baseRotate",
                )
            },
        )

        self.assertEqual(transaction.apply(), 1)

        self.assertEqual(
            tuple(cmds.getAttr(f"{authoring}.baseTranslate")[0]),
            (1.0, 2.0, 3.0),
        )
        self.assertEqual(
            tuple(cmds.getAttr(f"{authoring}.baseRotate")[0]),
            (0.0, 0.0, 0.0),
        )
        self.assertEqual(
            (
                tuple(cmds.getAttr(f"{joint}.translate")[0]),
                tuple(cmds.getAttr(f"{joint}.rotate")[0]),
            ),
            joint_before,
        )
        self.assertEqual(
            cmds.keyframe(curve, query=True, timeChange=True) or [],
            key_times,
        )
        self.assertEqual(
            cmds.keyframe(curve, query=True, valueChange=True) or [],
            key_values,
        )
        cmds.currentTime(25.0, edit=True)
        cmds.currentTime(24.0, edit=True)
        self.assertAlmostEqual(
            cmds.getAttr(f"{authoring}.baseRotateX"),
            motion_value,
        )

    def test_reset_pose_temporarily_overrides_partially_keyed_additive_layer(self):
        root = cmds.createNode("transform", name="layerModel")
        cmds.select(clear=True)
        joint = cmds.joint(name="layerJoint", position=(1.0, 2.0, 3.0))
        cmds.parent(joint, root)
        joint = (cmds.ls(joint, long=True) or [joint])[0]
        root = (cmds.ls(root, long=True) or [root])[0]

        cmds.setAttr(f"{joint}.translate", 1.0, 2.0, 3.0)
        cmds.setAttr(f"{joint}.rotate", 12.0, -8.0, 5.0)
        base_values = {
            channel: float(cmds.getAttr(f"{joint}.{channel}"))
            for channel in (
                "translateX",
                "translateY",
                "translateZ",
                "rotateX",
                "rotateY",
                "rotateZ",
            )
        }
        for channel, value in base_values.items():
            if not channel.startswith("rotate"):
                continue
            cmds.setKeyframe(joint, attribute=channel, time=1.0, value=value)
            cmds.setKeyframe(joint, attribute=channel, time=36.0, value=value)
        cmds.currentTime(24.0, edit=True)
        layer = cmds.animLayer("ResetPoseAdditive", override=False, weight=1.0)
        for channel in ("rotateX", "rotateY", "rotateZ"):
            cmds.animLayer(layer, edit=True, attribute=f"{joint}.{channel}")
            if channel != "rotateX":
                continue
            cmds.setKeyframe(
                joint,
                attribute=channel,
                time=24.0,
                value=base_values[channel] + 3.0,
                animLayer=layer,
            )
        cmds.animLayer(layer, edit=True, selected=True, preferred=True)
        cmds.currentTime(24.0, edit=True)
        motion_values = {
            channel: float(cmds.getAttr(f"{joint}.{channel}"))
            for channel in base_values
        }
        curves = tuple(
            node
            for node in (cmds.listHistory(joint) or [])
            if str(cmds.nodeType(node)).startswith("animCurve")
        )
        curve_payloads = {
            curve: (
                cmds.keyframe(curve, query=True, timeChange=True) or [],
                cmds.keyframe(curve, query=True, valueChange=True) or [],
            )
            for curve in curves
        }

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
        self.assertEqual(
            {
                curve: (
                    cmds.keyframe(curve, query=True, timeChange=True) or [],
                    cmds.keyframe(curve, query=True, valueChange=True) or [],
                )
                for curve in curves
            },
            curve_payloads,
        )
        cmds.currentTime(25.0, edit=True)
        cmds.currentTime(24.0, edit=True)
        for channel, value in motion_values.items():
            if not channel.startswith("rotate"):
                continue
            self.assertAlmostEqual(cmds.getAttr(f"{joint}.{channel}"), value)


if __name__ == "__main__":
    unittest.main()
