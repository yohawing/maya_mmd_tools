"""Unit tests for non-UI HumanIK source and connection diagnostics."""

import unittest

from mmd_tools.core.humanik_resolver import HumanIkBoneAssignment
from mmd_tools.core.humanik_retarget import (
    collect_humanik_incoming_writer_census,
    connect_humanik_source,
    diff_humanik_connections,
    snapshot_humanik_connections,
    verify_root_locomotion,
)


class FakeMel:
    def __init__(self):
        self.commands = []
        self.loaded = True

    def eval(self, command):
        self.commands.append(command)
        if command.startswith("exists "):
            return 1
        if command.startswith("hikGetInputType"):
            return 3
        if command.startswith("hikGetRetargetCharacterInput"):
            return "Source"
        return None


class FakeCmds:
    def __init__(self):
        self.writers = {
            "|hips.rotateY": ["mmdCcdIk1.outputRotate"],
            "|hips.translateX": ["animCurve1.output"],
        }
        self.transforms = {
            "|hips": [0.0, 10.0, 0.0],
            "|spine": [0.0, 20.0, 0.0],
            "|leftLeg": [1.0, 5.0, 0.0],
            "|targetHips": [0.0, 10.0, 0.0],
            "|targetSpine": [0.0, 20.0, 0.0],
            "|targetLeftLeg": [1.0, 5.0, 0.0],
        }

    def listConnections(self, plug, source=True, destination=False, plugs=True):
        return self.writers.get(plug, [])

    def xform(self, joint, query=False, worldSpace=False, matrix=False):
        if not matrix:
            raise AssertionError("world matrix query required")
        x, y, z = self.transforms[joint]
        return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, x, y, z, 1.0]

    def getAttr(self, plug):
        node, attr = plug.rsplit(".", 1)
        if attr == "translate":
            return [tuple(self.transforms[node])]
        raise AssertionError(plug)

    def setAttr(self, plug, x, y, z, type="double3"):
        node, attr = plug.rsplit(".", 1)
        if attr != "translate":
            raise AssertionError(plug)
        delta = [float(x) - self.transforms[node][0], float(y) - self.transforms[node][1], float(z) - self.transforms[node][2]]
        for joint in self.transforms:
            self.transforms[joint] = [value + offset for value, offset in zip(self.transforms[joint], delta)]

    def refresh(self, force=False):
        return None


class TestHumanIkRetarget(unittest.TestCase):
    def setUp(self):
        self.assignments = [
            HumanIkBoneAssignment("|hips", "下半身", "Hips", 1, "mmd_name", 1),
            HumanIkBoneAssignment("|spine", "上半身", "Spine", 8, "mmd_name", 2),
            HumanIkBoneAssignment("|leftLeg", "左足", "LeftUpLeg", 2, "mmd_name", 3),
        ]

    def test_connect_humanik_source_reports_direct_input(self):
        mel = FakeMel()

        report = connect_humanik_source("Target", "Source", mel_module=mel)

        self.assertTrue(report["retargetConnected"])
        self.assertEqual(report["inputType"], 3)
        self.assertEqual(report["inputTypeName"], "direct")
        self.assertIn('hikSetCharacterInput("Target", "Source");', mel.commands)

    def test_connect_humanik_source_rejects_missing_source_readback(self):
        class MissingSourceMel(FakeMel):
            def eval(self, command):
                if command.startswith("hikGetRetargetCharacterInput"):
                    return ""
                return super().eval(command)

        with self.assertRaisesRegex(RuntimeError, "source connection failed"):
            connect_humanik_source("Target", "Source", mel_module=MissingSourceMel())

        report = connect_humanik_source(
            "Target",
            "Source",
            mel_module=MissingSourceMel(),
            require_connected=False,
        )
        self.assertFalse(report["retargetConnected"])

    def test_writer_census_is_flat_and_deterministic(self):
        rows = collect_humanik_incoming_writer_census(self.assignments, cmds_module=FakeCmds(), channels=("translateX", "rotateY"))

        self.assertEqual(rows[0]["hikBone"], "Hips")
        self.assertEqual(rows[0]["writers"], ["animCurve1.output"])
        self.assertEqual(rows[1]["writers"], ["mmdCcdIk1.outputRotate"])

    def test_connection_diff_lists_removed_writer(self):
        changes = diff_humanik_connections(
            {"|hips.rotateY": ["mmdCcdIk1.outputRotate"]},
            {"|hips.rotateY": ["HIK.outputRotate"]},
        )

        self.assertEqual(changes[0]["disconnected"], ["mmdCcdIk1.outputRotate"])
        self.assertEqual(changes[0]["connected"], ["HIK.outputRotate"])
        self.assertTrue(changes[0]["replaced"])

    def test_root_locomotion_reports_group_motion(self):
        cmds = FakeCmds()
        report = verify_root_locomotion(
            "|hips",
            {"upperBody": ["|spine"], "legs": ["|leftLeg"]},
            translation=(2.0, 0.0, 0.0),
            cmds_module=cmds,
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["groups"]["legs"]["deltas"]["|leftLeg"], [2.0, 0.0, 0.0])
        self.assertEqual(len(report["beforeWorldMatrix"]["|hips"]), 16)

    def test_root_locomotion_can_drive_source_and_observe_target(self):
        cmds = FakeCmds()
        report = verify_root_locomotion(
            "|hips",
            {"upperBody": ["|targetSpine"], "legs": ["|targetLeftLeg"]},
            translation=(2.0, 0.0, 0.0),
            cmds_module=cmds,
            observed_root_joint="|targetHips",
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["driverJoint"], "|hips")
        self.assertEqual(report["rootJoint"], "|targetHips")

    def test_root_locomotion_allows_uniform_target_retarget_scale(self):
        class ScaledTargetCmds(FakeCmds):
            def setAttr(self, plug, x, y, z, type="double3"):
                node, attr = plug.rsplit(".", 1)
                if attr != "translate":
                    raise AssertionError(plug)
                delta = [
                    float(x) - self.transforms[node][0],
                    float(y) - self.transforms[node][1],
                    float(z) - self.transforms[node][2],
                ]
                for joint in self.transforms:
                    scale = 1.5 if joint.startswith("|target") else 1.0
                    self.transforms[joint] = [
                        value + offset * scale
                        for value, offset in zip(self.transforms[joint], delta)
                    ]

        report = verify_root_locomotion(
            "|hips",
            {"upperBody": ["|targetSpine"], "legs": ["|targetLeftLeg"]},
            translation=(2.0, 0.0, 0.0),
            cmds_module=ScaledTargetCmds(),
            observed_root_joint="|targetHips",
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["rootDelta"], [3.0, 0.0, 0.0])

    def test_snapshot_contains_all_requested_channels(self):
        snapshot = snapshot_humanik_connections(self.assignments[:1], cmds_module=FakeCmds())

        self.assertEqual(len(snapshot), 6)
        self.assertEqual(snapshot["|hips.translateX"], ["animCurve1.output"])


if __name__ == "__main__":
    unittest.main()
