"""Unit tests for the transactional HumanIK Control Rig creation path."""

import unittest
from unittest.mock import patch

from mmd_tools.core.humanik_control_rig import (
    begin_humanik_control_rig,
    new_cycle_plugs,
    stop_humanik_control_rig,
)


class FakeCmds:
    def __init__(self, extra_nodes=None):
        self.connections = {"|hips.rotateX": ["ik.outputRotateX"]}
        self.values = {"|hips.rotateX": 0.0}
        self.disconnects = []
        self.deleted = []
        self.existing_nodes = {"|hips", "ik", "twist"} | set(extra_nodes or [])
        self.cycle_responses = []

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
        return False

    def ls(self, type=None, long=False):
        if long:
            return sorted(self.existing_nodes)
        return []

    def nodeType(self, node):
        return "joint"

    def objExists(self, node):
        return node in self.existing_nodes

    def delete(self, nodes):
        for node in nodes:
            self.existing_nodes.discard(node)
            self.deleted.append(node)

    def cycleCheck(self, **kwargs):
        if self.cycle_responses:
            return list(self.cycle_responses.pop(0))
        return []


class FakeMel:
    def __init__(self):
        self.source = ""
        self.locked = True
        self.has_control_rig = False
        self.delete_calls = 0

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
        if command.startswith("hikCharacterLock"):
            return None
        if command.startswith("hikSetCurrentCharacter"):
            return None
        if command.startswith("hikHasControlRig"):
            return int(self.has_control_rig)
        if command == "hikDeleteControlRig();":
            self.delete_calls += 1
            self.has_control_rig = False
            return 1
        return None


MUTE_REPORT = {
    "rows": [
        {"node": "ik", "classification": "mute_for_hik", "writes": ["|hips.rotateX"]},
        {"node": "twist", "classification": "keep_post", "writes": ["|twist.rotateX"]},
    ]
}
CLEAN_POST_REPORT = {
    "rows": [
        {"node": "ik", "classification": "manual", "writes": []},
        {"node": "twist", "classification": "keep_post", "writes": ["|twist.rotateX"]},
    ]
}


def _patch_classify(*reports):
    return patch(
        "mmd_tools.core.humanik_control_rig.classify_humanik_constraints",
        side_effect=list(reports),
    )


def _patch_facts():
    return patch(
        "mmd_tools.core.humanik_control_rig.collect_humanik_constraint_facts",
        return_value=[],
    )


class TestBeginHumanIkControlRig(unittest.TestCase):
    def test_blocker_rejects_before_any_mutation(self):
        cmds, mel = FakeCmds(), FakeMel()
        report = {"rows": [{"node": "physics", "classification": "physics_blocker", "writes": []}]}

        with _patch_facts(), _patch_classify(report), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig"
        ) as create:
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                begin_humanik_control_rig("owner:rig", "Character", {"|hips"}, cmds, mel)
            create.assert_not_called()

        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])
        self.assertEqual(cmds.disconnects, [])

    def test_isolates_writer_before_create_and_keeps_it_disconnected(self):
        cmds, mel = FakeCmds(), FakeMel()
        observed = {}

        def fake_create(character, mel_module=None):
            observed["connectionsAtCreate"] = list(cmds.connections["|hips.rotateX"])
            mel.has_control_rig = True
            return True

        with _patch_facts(), _patch_classify(MUTE_REPORT, CLEAN_POST_REPORT), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig",
            side_effect=fake_create,
        ):
            transaction = begin_humanik_control_rig(
                "owner:rig", "Character", {"|hips"}, cmds, mel
            )

        self.assertEqual(observed["connectionsAtCreate"], [])
        self.assertEqual(cmds.connections["|hips.rotateX"], [])
        self.assertEqual(transaction.retained_nodes, ["twist"])
        self.assertEqual(
            transaction.disconnected,
            [{"source": "ik.outputRotateX", "destination": "|hips.rotateX"}],
        )
        self.assertTrue(transaction.active)

    def test_residual_muted_writer_after_create_rolls_back_and_raises(self):
        cmds, mel = FakeCmds(extra_nodes=set()), FakeMel()
        residual_report = {
            "rows": [
                {
                    "node": "ik",
                    "classification": "mute_for_hik",
                    "writes": ["|left_foot.rotateX"],
                    "writeHikJoints": ["|left_foot"],
                },
                {"node": "twist", "classification": "keep_post", "writes": ["|twist.rotateX"]},
            ]
        }
        cmds.connections["|left_foot.rotateX"] = ["ik.outputRotateX", "third_party.outputRotateX"]

        def fake_create(character, mel_module=None):
            cmds.existing_nodes.add("newCtrlNode")
            mel.has_control_rig = True
            return True

        with _patch_facts(), _patch_classify(MUTE_REPORT, residual_report), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig",
            side_effect=fake_create,
        ):
            with self.assertRaisesRegex(RuntimeError, "residual muted HIK writers: ik->\\|left_foot"):
                begin_humanik_control_rig("owner:rig", "Character", {"|hips", "|left_foot"}, cmds, mel)

        # journal-covered writer restored
        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])
        # residual writer explicitly disconnected before rollback (not journaled)
        self.assertEqual(cmds.connections["|left_foot.rotateX"], ["third_party.outputRotateX"])
        # node created by hikCreateControlRig was removed
        self.assertNotIn("newCtrlNode", cmds.existing_nodes)
        self.assertIn("newCtrlNode", cmds.deleted)
        self.assertEqual(mel.delete_calls, 1)

    def test_new_dg_cycle_rolls_back_and_raises(self):
        cmds, mel = FakeCmds(), FakeMel()
        cmds.cycle_responses = [
            ["mmdPhysicsSolver1.outSolved"],
            ["mmdPhysicsSolver1.outSolved", "HIKState2SK1.LeftLegT"],
        ]

        def fake_create(character, mel_module=None):
            cmds.existing_nodes.add("newCtrlNode")
            mel.has_control_rig = True
            return True

        with _patch_facts(), _patch_classify(MUTE_REPORT, CLEAN_POST_REPORT), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig",
            side_effect=fake_create,
        ):
            with self.assertRaisesRegex(RuntimeError, "new DG cycles"):
                begin_humanik_control_rig("owner:rig", "Character", {"|hips"}, cmds, mel)

        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])
        self.assertNotIn("newCtrlNode", cmds.existing_nodes)
        self.assertEqual(mel.delete_calls, 1)

    def test_pre_existing_cycle_does_not_block_success(self):
        cmds, mel = FakeCmds(), FakeMel()
        cmds.cycle_responses = [
            ["mmdPhysicsSolver1.outSolved"],
            ["mmdPhysicsSolver1.outSolved"],
        ]

        def fake_create(character, mel_module=None):
            mel.has_control_rig = True
            return True

        with _patch_facts(), _patch_classify(MUTE_REPORT, CLEAN_POST_REPORT), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig",
            side_effect=fake_create,
        ):
            transaction = begin_humanik_control_rig(
                "owner:rig", "Character", {"|hips"}, cmds, mel
            )

        self.assertTrue(transaction.active)
        self.assertEqual(
            new_cycle_plugs(transaction.pre_cycle_baseline, transaction.post_cycle_plugs),
            [],
        )

    def test_stop_deletes_rig_and_restores_journal_idempotently(self):
        cmds, mel = FakeCmds(), FakeMel()

        def fake_create(character, mel_module=None):
            mel.has_control_rig = True
            return True

        with _patch_facts(), _patch_classify(MUTE_REPORT, CLEAN_POST_REPORT), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig",
            side_effect=fake_create,
        ):
            transaction = begin_humanik_control_rig(
                "owner:rig", "Character", {"|hips"}, cmds, mel
            )

        stop_humanik_control_rig(transaction, cmds, mel)
        stop_humanik_control_rig(transaction, cmds, mel)

        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])
        self.assertEqual(mel.delete_calls, 1)
        self.assertFalse(transaction.active)


if __name__ == "__main__":
    unittest.main()
