"""Focused CR061-05 Maya checks for lossless curve payload copying."""

import unittest

from maya import cmds

from mmd_tools.core.mmd_control_rig_motion import (
    _copy_animation_curve,
    _sample_control_input_to_mmd,
)
from tests.common.maya_test_base import MayaTestBase


class TestMmdControlRigRoundtrip(MayaTestBase):
    """Verify native animCurve payload metadata survives a bake copy."""

    def test_animcurve_copy_preserves_times_tangents_weights_and_infinity(self):
        driver = cmds.createNode("transform", name="cr061_payload_driver")
        cmds.setKeyframe(driver, attribute="translateX", time=3, value=1.0)
        cmds.setKeyframe(driver, attribute="translateX", time=17, value=5.0)
        source = (cmds.listConnections(f"{driver}.translateX", source=True, destination=False, plugs=True) or [None])[0]
        self.assertTrue(source)
        source_node = source.split(".", 1)[0]
        cmds.keyTangent(source_node, edit=True, weightedTangents=True)
        cmds.keyTangent(
            source_node,
            edit=True,
            time=(3, 3),
            inTangentType="fixed",
            outTangentType="fixed",
            outAngle=25.0,
            outWeight=0.75,
        )
        cmds.keyTangent(
            source_node,
            edit=True,
            time=(17, 17),
            inTangentType="fixed",
            outTangentType="fixed",
            inAngle=-15.0,
            inWeight=0.55,
        )
        cmds.setInfinity(source_node, edit=True, preInfinite="cycle", postInfinite="oscillate")

        destination_node = cmds.createNode(cmds.nodeType(source_node), name="cr061_payload_copy")
        destination = f"{destination_node}.output"
        _copy_animation_curve(cmds, source, destination)

        self.assertEqual(
            cmds.keyframe(source_node, query=True, timeChange=True),
            cmds.keyframe(destination_node, query=True, timeChange=True),
        )
        self.assertEqual(
            cmds.keyframe(source_node, query=True, valueChange=True),
            cmds.keyframe(destination_node, query=True, valueChange=True),
        )
        self.assertEqual(
            cmds.keyTangent(source_node, query=True, inTangentType=True),
            cmds.keyTangent(destination_node, query=True, inTangentType=True),
        )
        self.assertEqual(
            cmds.keyTangent(source_node, query=True, outTangentType=True),
            cmds.keyTangent(destination_node, query=True, outTangentType=True),
        )
        for option in ("inAngle", "outAngle", "inWeight", "outWeight"):
            source_values = cmds.keyTangent(source_node, query=True, **{option: True}) or []
            destination_values = cmds.keyTangent(destination_node, query=True, **{option: True}) or []
            self.assertEqual(len(source_values), len(destination_values))
            for source_value, destination_value in zip(source_values, destination_values):
                self.assertAlmostEqual(source_value, destination_value, places=5)
        self.assertEqual(
            cmds.keyTangent(source_node, query=True, weightedTangents=True),
            cmds.keyTangent(destination_node, query=True, weightedTangents=True),
        )
        self.assertEqual(
            cmds.setInfinity(source_node, query=True, preInfinite=True),
            cmds.setInfinity(destination_node, query=True, preInfinite=True),
        )
        self.assertEqual(
            cmds.setInfinity(source_node, query=True, postInfinite=True),
            cmds.setInfinity(destination_node, query=True, postInfinite=True),
        )

    def test_sampled_route_writes_control_values_at_source_times(self):
        control = cmds.createNode("transform", name="cr061_sampled_control")
        target = cmds.createNode("transform", name="cr061_sampled_target")
        cmds.setKeyframe(control, attribute="translateX", time=11, value=2.0)
        cmds.setKeyframe(control, attribute="translateX", time=23, value=7.0)
        source = (cmds.listConnections(f"{control}.translateX", source=True, destination=False, plugs=True) or [None])[0]
        self.assertTrue(source)
        cmds.connectAttr(source, f"{target}.translateX", force=True)
        cmds.currentTime(77, edit=True)

        _sample_control_input_to_mmd(
            cmds,
            {
                "control": f"{control}.translateX",
                "target": f"{target}.translateX",
                "source": source,
                "controlSource": source,
                "routeClass": "sampled",
            },
            source,
            source,
        )

        self.assertEqual(
            cmds.keyframe(source.split(".", 1)[0], query=True, timeChange=True),
            list(range(11, 24)),
        )
        sampled_values = cmds.keyframe(source.split(".", 1)[0], query=True, valueChange=True)
        self.assertAlmostEqual(sampled_values[0], 2.0)
        self.assertAlmostEqual(sampled_values[6], 4.5)
        self.assertAlmostEqual(sampled_values[-1], 7.0)
        self.assertAlmostEqual(cmds.getAttr(f"{target}.translateX", time=23), 7.0)
        self.assertEqual(float(cmds.currentTime(query=True)), 77.0)


if __name__ == "__main__":
    unittest.main()
