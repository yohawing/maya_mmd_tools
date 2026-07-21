"""Unit tests for reversible HumanIK transaction restore states."""

import unittest

from mmd_tools.core.humanik_transaction import (
    HumanIkNodeSnapshot,
    HumanIkPlugSnapshot,
    HumanIkRestoreState,
    HUMANIK_RESTORE_STATE_NODE,
    capture_humanik_restore_state,
    deserialize_humanik_restore_state,
    humanik_transaction,
    load_humanik_restore_state,
    persist_humanik_restore_state,
    serialize_humanik_restore_state,
    apply_humanik_restore_state,
)


class FakeCmds:
    def __init__(self, missing_nodes=None):
        self.values = {"dst.tx": 3.0, "node.nodeState": 0, "node.enabled": True}
        self.types = {"dst.tx": "double"}
        self.connections = {"dst.tx": ["src.tx"]}
        # Every node this fake knows about is treated as existing by default
        # (this fake does not model node deletion elsewhere); tests exercising
        # the missing-node skip path pass explicit names here or mutate this
        # set directly before calling apply_humanik_restore_state.
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


class FakeRestoreStateStorageCmds:
    """Minimal non-DAG node store for restore-state persistence tests."""

    def __init__(self):
        self.nodes = set()
        self.attrs = {}
        self.created = []

    def ls(self, type=None):
        return sorted(self.nodes) if type == "network" else []

    def objExists(self, node):
        return node in self.nodes

    def createNode(self, node_type, name):
        self.assertEqual(node_type, "network")
        self.nodes.add(name)
        self.created.append((node_type, name))
        return name

    def attributeQuery(self, attr, node=None, exists=False):
        return (node, attr) in self.attrs

    def addAttr(self, node, longName, dataType):
        self.assertEqual(dataType, "string")
        self.attrs[(node, longName)] = ""

    def setAttr(self, plug, value, type=None):
        node, attr = plug.rsplit(".", 1)
        self.attrs[(node, attr)] = value

    def getAttr(self, plug):
        node, attr = plug.rsplit(".", 1)
        return self.attrs[(node, attr)]

    def assertEqual(self, left, right):
        if left != right:
            raise AssertionError(f"{left!r} != {right!r}")


class TestHumanIkTransaction(unittest.TestCase):
    def test_restore_state_uses_one_non_dag_network_node(self):
        restore_state = HumanIkRestoreState("owner:A", "Target", True, "", -1, [], [])
        record = {
            "modelRoot": "|model_root",
            "ownershipId": "owner:A",
            "character": "Target",
            "restore_state": restore_state.to_dict(),
            "active": True,
        }
        cmds = FakeRestoreStateStorageCmds()

        self.assertTrue(persist_humanik_restore_state([record], cmds_module=cmds))
        self.assertEqual(cmds.created, [("network", HUMANIK_RESTORE_STATE_NODE)])
        self.assertEqual(load_humanik_restore_state(cmds_module=cmds), [record])

    def test_restore_state_serializes_and_reconstructs_with_schema_validation(self):
        restore_state = HumanIkRestoreState(
            ownership_id="owner:A",
            character="Target",
            lock_state=True,
            input_source="Source",
            input_type=3,
            plugs=[HumanIkPlugSnapshot("joint.tx", ["writer.out"], [1.0, 2.0], "double3")],
            nodes=[HumanIkNodeSnapshot("writer", {"mute": True})],
        )
        payload = serialize_humanik_restore_state(
            [{
                "modelRoot": "|model_root",
                "ownershipId": "owner:A",
                "character": "Target",
                "restore_state": restore_state.to_dict(),
                "createdNodes": ["HIKControlSetNode1"],
                "active": True,
            }]
        )
        rows = deserialize_humanik_restore_state(payload)
        restored = HumanIkRestoreState.from_dict(rows[0]["restore_state"])
        self.assertEqual(restored.to_dict(), restore_state.to_dict())
        self.assertEqual(rows[0]["modelRoot"], "|model_root")

    def test_malformed_or_foreign_scene_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            deserialize_humanik_restore_state({
                "schema": "foreign",
                "version": 1,
                "transactions": [],
            })
        with self.assertRaisesRegex(ValueError, "character mismatch"):
            deserialize_humanik_restore_state({
                "schema": "mmd_tools.humanik_restore_state",
                "version": 1,
                "transactions": [{
                    "modelRoot": "|model_root",
                    "ownershipId": "owner:A",
                    "character": "Other",
                    "restore_state": HumanIkRestoreState(
                        "owner:A", "Target", True, "Source", 3, [], []
                    ).to_dict(),
                }],
            })
        with self.assertRaisesRegex(ValueError, "invalid active flag"):
            deserialize_humanik_restore_state({
                "schema": "mmd_tools.humanik_restore_state",
                "version": 1,
                "transactions": [{
                    "modelRoot": "|model_root",
                    "ownershipId": "owner:A",
                    "character": "Target",
                    "restore_state": HumanIkRestoreState(
                        "owner:A", "Target", True, "Source", 3, [], []
                    ).to_dict(),
                    "active": "false",
                }],
            })

    def test_restore_is_exact_and_idempotent(self):
        cmds, mel = FakeCmds(), FakeMel()
        restore_state = capture_humanik_restore_state("owner:A", "Target", ["dst.tx"], ["node"], cmds, mel)
        cmds.connections["dst.tx"] = []
        cmds.values["dst.tx"] = 42.0
        cmds.values["node.nodeState"] = 2
        mel.source = ""

        apply_humanik_restore_state(restore_state, "owner:A", cmds, mel)
        apply_humanik_restore_state(restore_state, "owner:A", cmds, mel)

        self.assertEqual(cmds.connections["dst.tx"], ["src.tx"])
        self.assertEqual(cmds.values["node.nodeState"], 0)
        self.assertEqual(mel.source, "Source")
        self.assertEqual(restore_state.to_dict()["ownership_id"], "owner:A")

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
        restore_state = capture_humanik_restore_state("owner:A", "Target", [], [], cmds, mel)
        with self.assertRaisesRegex(ValueError, "ownership mismatch"):
            apply_humanik_restore_state(restore_state, "owner:B", cmds, mel)

    def test_stance_input_type_zero_is_preserved(self):
        class StanceMel(FakeMel):
            def eval(self, command):
                if command.startswith("hikGetInputType"):
                    return 0
                return super().eval(command)

        restore_state = capture_humanik_restore_state(
            "owner:A",
            "Target",
            [],
            [],
            FakeCmds(),
            StanceMel(),
        )
        self.assertEqual(restore_state.input_type, 0)

    def test_missing_character_node_is_skipped_with_warning_not_error(self):
        """HUMANIK-RESTORE-GAPS-1 fix 1a: a deleted character must not raise.

        Character-level restore (input source / lock) has nothing to act on
        once the character node is gone, so it is skipped with a warning
        instead of hitting Maya's "node not found" MEL error. Captured
        entries on other, still-existing nodes are restored normally.
        """
        cmds, mel = FakeCmds(), FakeMel()
        restore_state = capture_humanik_restore_state("owner:A", "Target", ["dst.tx"], ["node"], cmds, mel)
        cmds.connections["dst.tx"] = []
        mel.source = ""
        cmds.missing_nodes.add("Target")

        warnings = apply_humanik_restore_state(restore_state, "owner:A", cmds, mel)

        self.assertTrue(any("Target" in message for message in warnings))
        self.assertEqual(mel.source, "")  # character-level restore was skipped
        self.assertEqual(cmds.connections["dst.tx"], ["src.tx"])  # unaffected node restored

    def test_missing_plug_node_is_skipped_with_warning_not_error(self):
        cmds, mel = FakeCmds(), FakeMel()
        restore_state = capture_humanik_restore_state("owner:A", "Target", ["dst.tx"], ["node"], cmds, mel)
        cmds.connections["dst.tx"] = []
        cmds.missing_nodes.add("dst")

        warnings = apply_humanik_restore_state(restore_state, "owner:A", cmds, mel)

        self.assertTrue(any("dst.tx" in message for message in warnings))
        self.assertEqual(cmds.connections["dst.tx"], [])  # not restored: node is gone
        self.assertEqual(mel.source, "Source")  # character-level restore still ran

    def test_restore_aggregates_failures_for_existing_nodes_and_raises(self):
        """A restore failure against a node that still exists must still
        surface as an error (the pre-existing "incomplete rollback is an
        error" guarantee), but every other captured entry is still
        attempted first."""
        cmds, mel = FakeCmds(), FakeMel()
        restore_state = capture_humanik_restore_state("owner:A", "Target", ["dst.tx"], ["node"], cmds, mel)
        cmds.connections["dst.tx"] = []
        cmds.values["node.nodeState"] = 2
        mel.source = ""

        def failing_connect(source, destination, force=False):
            raise RuntimeError("boom-connect")

        cmds.connectAttr = failing_connect

        with self.assertRaisesRegex(RuntimeError, "boom-connect"):
            apply_humanik_restore_state(restore_state, "owner:A", cmds, mel)

        # The plug reconnect failed, but unrelated entries were still restored.
        self.assertEqual(mel.source, "Source")
        self.assertEqual(cmds.values["node.nodeState"], 0)


if __name__ == "__main__":
    unittest.main()
