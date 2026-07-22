"""Unit tests for exclusive HumanIK TARGET preview transitions."""

import unittest
from unittest.mock import patch

from mmd_tools.core.humanik_preview import (
    begin_humanik_target_preview,
    stop_humanik_target_preview,
)


class FakeCmds:
    def __init__(self, finger_solving_node="propNode", finger_solving_initial=1, left_elbow_kill_pitch_initial=0):
        self.connections = {"|hips.rotateX": ["ik.outputRotateX"]}
        self.values = {"|hips.rotateX": 0.0}
        self.disconnects = []
        self.finger_solving_node = finger_solving_node
        if finger_solving_node:
            self.values[f"{finger_solving_node}.FingerSolving"] = finger_solving_initial
            self.values[f"{finger_solving_node}.LeftElbowKillPitch"] = left_elbow_kill_pitch_initial

    def listConnections(self, plug, source=True, destination=False, plugs=True, connections=False):
        if connections:
            return []
        return list(self.connections.get(plug, []))

    def disconnectAttr(self, source, destination):
        self.disconnects.append((source, destination))
        self.connections[destination].remove(source)

    def connectAttr(self, source, destination, force=False):
        self.connections[destination] = [source]

    def isConnected(self, source, destination):
        return source in self.connections.get(destination, [])

    def getAttr(self, plug, type=False):
        return "double" if type else self.values.get(plug, 0.0)

    def setAttr(self, plug, *values, **kwargs):
        self.values[plug] = values[0]

    def attributeQuery(self, attr, node=None, exists=False):
        return bool(
            exists
            and attr in {"FingerSolving", "LeftElbowKillPitch"}
            and self.finger_solving_node
            and node == self.finger_solving_node
        )

    def ls(self, type=None, long=False):
        return []

    def nodeType(self, node):
        return "joint"

    def objExists(self, node):
        return True


class FakeMel:
    def __init__(self, finger_solving_node="propNode"):
        self.source = ""
        self.locked = True
        self.finger_solving_node = finger_solving_node

    def eval(self, command):
        if command.startswith("exists "):
            return 1
        if command.startswith("hikGetRetargetCharacterInput"):
            return self.source
        if command.startswith("hikGetInputType"):
            return 3 if self.source else -1
        if command.startswith("hikIsDefinitionLocked"):
            return int(self.locked)
        if command.startswith("hikSetCharacterInput"):
            self.source = "Source" if '"Source"' in command else ""
        if command.startswith("hikGetProperty2StateFromCharacter"):
            return self.finger_solving_node
        return None


class ReconnectingMel(FakeMel):
    def __init__(self, cmds):
        super().__init__()
        self.cmds = cmds

    def eval(self, command):
        if command.startswith("hikSetCharacterInput"):
            self.cmds.connections["|hips.rotateX"] = ["ik.outputRotateX"]
        return super().eval(command)


class ResidualReconnectingMel(ReconnectingMel):
    def eval(self, command):
        if command.startswith("hikSetCharacterInput") and '"Source"' in command:
            self.cmds.connections["|left_foot.rotateX"] = [
                "ik.outputRotateX",
                "third_party.outputRotateX",
            ]
        return super().eval(command)


class TestHumanIkPreview(unittest.TestCase):
    def test_supported_importer_foot_feedback_isolated_for_preview_and_restored(self):
        cmds, mel = FakeCmds(), FakeMel()
        node = "|left_leg_ik_mmdCcdIk"
        left_leg = "|model|left_leg.rotate"
        left_knee = "|model|left_knee.rotate"
        cmds.connections[left_leg] = [f"{node}.outputRotate[0]"]
        cmds.connections[left_knee] = [f"{node}.outputRotate[1]"]
        report = {
            "rows": [{
                "node": node,
                "nodeType": "mmdCcdIk",
                "classification": "feedback_blocker",
                "reads": ["|model|left_leg.translate", "|model|left_leg_ik.translate"],
                "readHikJoints": ["|model|left_leg"],
                "readOutsideJoints": ["|model|left_leg_ik"],
                "writes": [left_leg, left_knee],
            }],
        }

        with patch(
            "mmd_tools.core.humanik_preview.classify_humanik_constraints",
            return_value={"rows": [], "counts": {}},
        ):
            preview = begin_humanik_target_preview(
                "owner:target",
                "Target",
                "Source",
                report,
                {"|model|left_leg", "|model|left_knee"},
                cmds,
                mel,
                assignments=(
                    {"joint": "|model|left_leg", "hikBone": "LeftUpLeg"},
                    {"joint": "|model|left_knee", "hikBone": "LeftLeg"},
                ),
            )

        self.assertTrue(preview.active)
        self.assertEqual(cmds.connections[left_leg], [])
        self.assertEqual(cmds.connections[left_knee], [])
        self.assertEqual([row["node"] for row in preview.isolated_feedback_rows], [node])
        stop_humanik_target_preview(preview, cmds, mel)
        self.assertEqual(cmds.connections[left_leg], [f"{node}.outputRotate[0]"])
        self.assertEqual(cmds.connections[left_knee], [f"{node}.outputRotate[1]"])

    def test_supported_foot_feedback_without_assignments_remains_blocked(self):
        cmds, mel = FakeCmds(), FakeMel()
        node = "|left_leg_ik_mmdCcdIk"
        destination = "|model|left_leg.rotate"
        cmds.connections[destination] = [f"{node}.outputRotate[0]"]
        report = {
            "rows": [{
                "node": node,
                "nodeType": "mmdCcdIk",
                "classification": "feedback_blocker",
                "reads": ["|model|left_leg.translate", "|model|left_leg_ik.translate"],
                "readHikJoints": ["|model|left_leg"],
                "readOutsideJoints": ["|model|left_leg_ik"],
                "writes": [destination],
            }],
        }

        with self.assertRaisesRegex(RuntimeError, "blocked"):
            begin_humanik_target_preview(
                "owner:target", "Target", "Source", report, {"|model|left_leg"}, cmds, mel
            )
        self.assertEqual(cmds.connections[destination], [f"{node}.outputRotate[0]"])

    def test_begin_mutes_writer_then_stop_restores_neutral(self):
        cmds, mel = FakeCmds(), FakeMel()
        report = {
            "rows": [
                {
                    "node": "ik",
                    "classification": "mute_for_hik",
                    "writes": ["|hips.rotateX"],
                },
                {
                    "node": "twist",
                    "classification": "keep_post",
                    "writes": ["|twist.rotateX"],
                },
            ]
        }

        preview = begin_humanik_target_preview(
            "owner:target",
            "Target",
            "Source",
            report,
            {"|hips"},
            cmds,
            mel,
        )

        self.assertEqual(cmds.connections["|hips.rotateX"], [])
        self.assertEqual(mel.source, "Source")
        self.assertEqual(preview.retained_nodes, ["twist"])
        stop_humanik_target_preview(preview, cmds, mel)
        stop_humanik_target_preview(preview, cmds, mel)
        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])
        self.assertEqual(mel.source, "")
        self.assertFalse(preview.active)

    def test_begin_reisolates_reviewed_edge_reconnected_by_source_connection(self):
        cmds = FakeCmds()
        mel = ReconnectingMel(cmds)
        report = {
            "rows": [{
                "node": "ik",
                "classification": "mute_for_hik",
                "writes": ["|hips.rotateX"],
            }]
        }

        with patch(
            "mmd_tools.core.humanik_preview.classify_humanik_constraints",
            return_value={"rows": [{"node": "ik", "classification": "manual", "writes": []}]},
        ):
            preview = begin_humanik_target_preview(
                "owner:target", "Target", "Source", report, {"|hips"}, cmds, mel
            )

        self.assertEqual(cmds.connections["|hips.rotateX"], [])
        self.assertEqual(
            cmds.disconnects,
            [("ik.outputRotateX", "|hips.rotateX"), ("ik.outputRotateX", "|hips.rotateX")],
        )
        self.assertEqual(preview.disconnected, [{"source": "ik.outputRotateX", "destination": "|hips.rotateX"}])
        stop_humanik_target_preview(preview, cmds, mel)
        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])

    def test_residual_muted_writer_fails_closed_and_rolls_back(self):
        cmds = FakeCmds()
        mel = ResidualReconnectingMel(cmds)
        report = {
            "rows": [{
                "node": "ik",
                "classification": "mute_for_hik",
                "writes": ["|hips.rotateX"],
            }]
        }
        post_report = {
            "rows": [{
                "node": "ik",
                "classification": "mute_for_hik",
                "writes": ["|left_foot.rotateX"],
                "writeHikJoints": ["|left_foot"],
            }]
        }

        with patch(
            "mmd_tools.core.humanik_preview.classify_humanik_constraints",
            return_value=post_report,
        ), self.assertRaisesRegex(RuntimeError, "residual muted HIK writers: ik->\\|left_foot"):
            begin_humanik_target_preview(
                "owner:target", "Target", "Source", report, {"|hips", "|left_foot"}, cmds, mel
            )

        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])
        self.assertEqual(cmds.connections["|left_foot.rotateX"], ["third_party.outputRotateX"])
        self.assertEqual(mel.source, "")
        self.assertEqual(cmds.values["propNode.LeftElbowKillPitch"], 0)

    def test_blocker_stops_before_mutation(self):
        cmds, mel = FakeCmds(), FakeMel()
        report = {"rows": [{"node": "unknown", "classification": "manual", "writes": []}]}

        with self.assertRaisesRegex(RuntimeError, "blocked"):
            begin_humanik_target_preview(
                "owner:target", "Target", "Source", report, {"|hips"}, cmds, mel
            )

        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])
        self.assertEqual(mel.source, "")


class TestHumanIkPreviewFingerSolving(unittest.TestCase):
    """HUMANIK-RETARGET-S5: FingerSolving is disabled for the preview lifetime."""

    REPORT = {
        "rows": [{
            "node": "ik",
            "classification": "mute_for_hik",
            "writes": ["|hips.rotateX"],
        }]
    }

    def test_begin_disables_finger_solving_and_stop_restores_it(self):
        cmds, mel = FakeCmds(finger_solving_initial=1), FakeMel()

        preview = begin_humanik_target_preview(
            "owner:target", "Target", "Source", self.REPORT, {"|hips"}, cmds, mel
        )

        self.assertEqual(cmds.values["propNode.FingerSolving"], 0)
        self.assertEqual(preview.finger_solving_previous, 1)
        self.assertEqual(cmds.values["propNode.LeftElbowKillPitch"], 1)
        self.assertEqual(preview.left_elbow_kill_pitch_previous, 0)

        stop_humanik_target_preview(preview, cmds, mel)
        self.assertEqual(cmds.values["propNode.FingerSolving"], 1)
        self.assertEqual(cmds.values["propNode.LeftElbowKillPitch"], 0)

        # Repeated stop calls stay idempotent/safe.
        stop_humanik_target_preview(preview, cmds, mel)
        self.assertEqual(cmds.values["propNode.FingerSolving"], 1)
        self.assertEqual(cmds.values["propNode.LeftElbowKillPitch"], 0)

    def test_rollback_restores_finger_solving_before_reraising(self):
        cmds, mel = FakeCmds(finger_solving_initial=1), FakeMel()
        report = {"rows": [{"node": "unknown", "classification": "manual", "writes": []}]}

        with self.assertRaisesRegex(RuntimeError, "blocked"):
            begin_humanik_target_preview(
                "owner:target", "Target", "Source", report, {"|hips"}, cmds, mel
            )

        # Blocked before any mutation: FingerSolving was never touched.
        self.assertEqual(cmds.values["propNode.FingerSolving"], 1)

    def test_stop_restores_properties_when_restore_state_raises(self):
        cmds, mel = FakeCmds(finger_solving_initial=1), FakeMel()
        preview = begin_humanik_target_preview(
            "owner:target", "Target", "Source", self.REPORT, {"|hips"}, cmds, mel
        )
        self.assertEqual(cmds.values["propNode.FingerSolving"], 0)
        self.assertEqual(cmds.values["propNode.LeftElbowKillPitch"], 1)

        restore_error = RuntimeError("restore-state failure")
        with patch(
            "mmd_tools.core.humanik_preview.apply_humanik_restore_state",
            side_effect=restore_error,
        ), self.assertRaisesRegex(RuntimeError, "restore-state failure"):
            stop_humanik_target_preview(preview, cmds, mel)

        self.assertEqual(cmds.values["propNode.FingerSolving"], 1)
        self.assertEqual(cmds.values["propNode.LeftElbowKillPitch"], 0)
        self.assertTrue(preview.active)

    def test_missing_property_node_does_not_hard_fail(self):
        """Older Maya/plugin variants without a property node must not break preview."""
        cmds = FakeCmds(finger_solving_node="")
        mel = FakeMel(finger_solving_node="")

        preview = begin_humanik_target_preview(
            "owner:target", "Target", "Source", self.REPORT, {"|hips"}, cmds, mel
        )

        self.assertIsNone(preview.finger_solving_previous)
        stop_humanik_target_preview(preview, cmds, mel)
        self.assertFalse(preview.active)


if __name__ == "__main__":
    unittest.main()
