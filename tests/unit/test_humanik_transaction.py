"""Unit tests for reversible HumanIK transaction journals."""

import unittest

from mmd_tools.core.humanik_transaction import (
    capture_humanik_journal,
    humanik_transaction,
    restore_humanik_journal,
)


class FakeCmds:
    def __init__(self):
        self.values = {"dst.tx": 3.0, "node.nodeState": 0, "node.enabled": True}
        self.types = {"dst.tx": "double"}
        self.connections = {"dst.tx": ["src.tx"]}

    def listConnections(self, plug, source=True, destination=False, plugs=True):
        return list(self.connections.get(plug, []))

    def getAttr(self, plug, type=False):
        return self.types.get(plug, "double") if type else self.values.get(plug)

    def setAttr(self, plug, *values, **kwargs):
        self.values[plug] = values[0] if len(values) == 1 else tuple(values)

    def attributeQuery(self, attr, node=None, exists=False):
        return f"{node}.{attr}" in self.values

    def disconnectAttr(self, source, destination):
        if source in self.connections.get(destination, []):
            self.connections[destination].remove(source)

    def connectAttr(self, source, destination, force=False):
        self.connections[destination] = [source]

    def isConnected(self, source, destination):
        return source in self.connections.get(destination, [])


class FakeMel:
    def __init__(self):
        self.source = "Source"
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
        if command.startswith("hikCharacterLock"):
            self.locked = ", 1," in command
        return None


class TestHumanIkTransaction(unittest.TestCase):
    def test_restore_is_exact_and_idempotent(self):
        cmds, mel = FakeCmds(), FakeMel()
        journal = capture_humanik_journal("owner:A", "Target", ["dst.tx"], ["node"], cmds, mel)
        cmds.connections["dst.tx"] = []
        cmds.values["dst.tx"] = 42.0
        cmds.values["node.nodeState"] = 2
        mel.source = ""

        restore_humanik_journal(journal, "owner:A", cmds, mel)
        restore_humanik_journal(journal, "owner:A", cmds, mel)

        self.assertEqual(cmds.connections["dst.tx"], ["src.tx"])
        self.assertEqual(cmds.values["node.nodeState"], 0)
        self.assertEqual(mel.source, "Source")
        self.assertEqual(journal.to_dict()["ownership_id"], "owner:A")

    def test_context_rolls_back_on_exception(self):
        cmds, mel = FakeCmds(), FakeMel()
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with humanik_transaction("owner:A", "Target", ["dst.tx"], ["node"], cmds, mel):
                cmds.connections["dst.tx"] = []
                cmds.values["node.enabled"] = False
                raise RuntimeError("boom")

        self.assertEqual(cmds.connections["dst.tx"], ["src.tx"])
        self.assertTrue(cmds.values["node.enabled"])

    def test_owner_mismatch_is_rejected(self):
        cmds, mel = FakeCmds(), FakeMel()
        journal = capture_humanik_journal("owner:A", "Target", [], [], cmds, mel)
        with self.assertRaisesRegex(ValueError, "ownership mismatch"):
            restore_humanik_journal(journal, "owner:B", cmds, mel)

    def test_stance_input_type_zero_is_preserved(self):
        class StanceMel(FakeMel):
            def eval(self, command):
                if command.startswith("hikGetInputType"):
                    return 0
                return super().eval(command)

        journal = capture_humanik_journal(
            "owner:A",
            "Target",
            [],
            [],
            FakeCmds(),
            StanceMel(),
        )
        self.assertEqual(journal.input_type, 0)


if __name__ == "__main__":
    unittest.main()
