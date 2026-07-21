"""Unit tests for reversible HumanIK transaction journals."""

import unittest

from mmd_tools.core.humanik_transaction import (
    capture_humanik_journal,
    humanik_transaction,
    restore_humanik_journal,
)


class FakeCmds:
    def __init__(self, missing_nodes=None):
        self.values = {"dst.tx": 3.0, "node.nodeState": 0, "node.enabled": True}
        self.types = {"dst.tx": "double"}
        self.connections = {"dst.tx": ["src.tx"]}
        # Every node this fake knows about is treated as existing by default
        # (this fake does not model node deletion elsewhere); tests exercising
        # the missing-node skip path pass explicit names here or mutate this
        # set directly before calling restore_humanik_journal.
        self.missing_nodes = set(missing_nodes or ())

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

    def objExists(self, node):
        return node not in self.missing_nodes


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

    def test_missing_character_node_is_skipped_with_warning_not_error(self):
        """HUMANIK-RESTORE-GAPS-1 fix 1a: a deleted character must not raise.

        Character-level restore (input source / lock) has nothing to act on
        once the character node is gone, so it is skipped with a warning
        instead of hitting Maya's "node not found" MEL error. Journaled
        entries on other, still-existing nodes are restored normally.
        """
        cmds, mel = FakeCmds(), FakeMel()
        journal = capture_humanik_journal("owner:A", "Target", ["dst.tx"], ["node"], cmds, mel)
        cmds.connections["dst.tx"] = []
        mel.source = ""
        cmds.missing_nodes.add("Target")

        warnings = restore_humanik_journal(journal, "owner:A", cmds, mel)

        self.assertTrue(any("Target" in message for message in warnings))
        self.assertEqual(mel.source, "")  # character-level restore was skipped
        self.assertEqual(cmds.connections["dst.tx"], ["src.tx"])  # unaffected node restored

    def test_missing_plug_node_is_skipped_with_warning_not_error(self):
        cmds, mel = FakeCmds(), FakeMel()
        journal = capture_humanik_journal("owner:A", "Target", ["dst.tx"], ["node"], cmds, mel)
        cmds.connections["dst.tx"] = []
        cmds.missing_nodes.add("dst")

        warnings = restore_humanik_journal(journal, "owner:A", cmds, mel)

        self.assertTrue(any("dst.tx" in message for message in warnings))
        self.assertEqual(cmds.connections["dst.tx"], [])  # not restored: node is gone
        self.assertEqual(mel.source, "Source")  # character-level restore still ran

    def test_restore_aggregates_failures_for_existing_nodes_and_raises(self):
        """A restore failure against a node that still exists must still
        surface as an error (the pre-existing "incomplete rollback is an
        error" guarantee), but every other journaled entry is still
        attempted first."""
        cmds, mel = FakeCmds(), FakeMel()
        journal = capture_humanik_journal("owner:A", "Target", ["dst.tx"], ["node"], cmds, mel)
        cmds.connections["dst.tx"] = []
        cmds.values["node.nodeState"] = 2
        mel.source = ""

        def failing_connect(source, destination, force=False):
            raise RuntimeError("boom-connect")

        cmds.connectAttr = failing_connect

        with self.assertRaisesRegex(RuntimeError, "boom-connect"):
            restore_humanik_journal(journal, "owner:A", cmds, mel)

        # The plug reconnect failed, but unrelated entries were still restored.
        self.assertEqual(mel.source, "Source")
        self.assertEqual(cmds.values["node.nodeState"], 0)


if __name__ == "__main__":
    unittest.main()
