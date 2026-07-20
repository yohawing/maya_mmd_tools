"""Unit tests for exclusive HumanIK TARGET preview transitions."""

import unittest

from mmd_tools.core.humanik_preview import (
    begin_humanik_target_preview,
    stop_humanik_target_preview,
)


class FakeCmds:
    def __init__(self):
        self.connections = {"|hips.rotateX": ["ik.outputRotateX"]}
        self.values = {"|hips.rotateX": 0.0}

    def listConnections(self, plug, source=True, destination=False, plugs=True, connections=False):
        if connections:
            return []
        return list(self.connections.get(plug, []))

    def disconnectAttr(self, source, destination):
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
        return False

    def ls(self, type=None, long=False):
        return []

    def nodeType(self, node):
        return "joint"


class FakeMel:
    def __init__(self):
        self.source = ""
        self.locked = True

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
        return None


class TestHumanIkPreview(unittest.TestCase):
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

    def test_blocker_stops_before_mutation(self):
        cmds, mel = FakeCmds(), FakeMel()
        report = {"rows": [{"node": "unknown", "classification": "manual", "writes": []}]}

        with self.assertRaisesRegex(RuntimeError, "blocked"):
            begin_humanik_target_preview(
                "owner:target", "Target", "Source", report, {"|hips"}, cmds, mel
            )

        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])
        self.assertEqual(mel.source, "")


if __name__ == "__main__":
    unittest.main()
