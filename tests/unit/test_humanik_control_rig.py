"""Unit tests for the transactional HumanIK Control Rig creation path."""

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from mmd_tools.core.humanik_control_rig import (
    HumanIkControlRigBakeResult,
    HumanIkControlRigTransaction,
    bake_humanik_control_rig,
    begin_humanik_control_rig,
    get_active_control_rig_transaction,
    new_cycle_plugs,
    stop_humanik_control_rig,
    unregister_control_rig_transaction,
)
from mmd_tools.core.humanik_transaction import HumanIkRestoreState


class FakeCmds:
    def __init__(self, extra_nodes=None):
        self.connections = {"|hips.rotateX": ["ik.outputRotateX"]}
        self.values = {"|hips.rotateX": 0.0}
        self.disconnects = []
        self.deleted = []
        # "Character" is tracked here too: every existing test exercises a
        # normal teardown where the HIK character node is still present.
        # Tests for HUMANIK-RESTORE-GAPS-1 (a manually deleted character)
        # remove it explicitly via ``existing_nodes.discard("Character")``.
        self.existing_nodes = {"|hips", "ik", "twist", "Character"} | set(extra_nodes or [])
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
        self.current_character = ""

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
        if command.startswith("hikGetCurrentCharacter"):
            return self.current_character
        if command.startswith("hikSetCurrentCharacter"):
            value = command[len("hikSetCurrentCharacter(") :].rstrip(");").strip()
            self.current_character = value[1:-1] if value.startswith('"') and value.endswith('"') else value
            return None
        if command.startswith("hikHasControlRig"):
            return int(self.has_control_rig)
        if command == "hikDeleteControlRig();":
            self.delete_calls += 1
            self.has_control_rig = False
            return 1
        return None


class FakeBakeCmds(FakeCmds):
    """Playback/current-time surface used by the native bake wrapper tests."""

    def __init__(self):
        super().__init__()
        self.playback = {
            "minTime": 1.5,
            "maxTime": 24.5,
            "animationStartTime": 0.0,
            "animationEndTime": 30.0,
        }
        self.current = 7.0
        self.playback_edits = []

    def playbackOptions(self, query=False, edit=False, **kwargs):
        if query:
            for key, value in kwargs.items():
                if value:
                    return self.playback[key]
            return None
        if edit:
            self.playback_edits.append(dict(kwargs))
            self.playback.update({key: value for key, value in kwargs.items() if value is not None})
            return None
        return None

    def currentTime(self, value=None, query=False, edit=False):
        if query:
            return self.current
        if edit:
            self.current = value
        return None


class FakeBakeMel(FakeMel):
    def __init__(self, fail=False, current_character=""):
        super().__init__()
        self.has_control_rig = True
        self.commands = []
        self.fail = fail
        self.current_character = current_character

    def eval(self, command):
        self.commands.append(command)
        if command == "hikBakeToControlRig(0);" and self.fail:
            raise RuntimeError("native bake failed")
        return super().eval(command)


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
        "mmd_tools.core.humanik_control_rig.collect_hik_ownership_report",
        side_effect=list(reports),
    )


def _patch_facts():
    """No-op context manager kept so call sites do not need to change.

    ``begin_humanik_control_rig`` now calls the consolidated
    ``collect_hik_ownership_report`` helper (see
    ``mmd_tools.core.humanik_constraints``) instead of calling
    ``collect_humanik_constraint_facts``/``classify_humanik_constraints``
    directly, so there is nothing left here to patch; ``_patch_classify``
    above covers the whole report.
    """
    return nullcontext()


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

    def test_supported_importer_foot_feedback_is_isolated_and_restored(self):
        cmds, mel = FakeCmds(extra_nodes={"left_leg_ik_mmdCcdIk"}), FakeMel()
        source = "left_leg_ik_mmdCcdIk.outputRotate[0]"
        cmds.connections["|hips.rotateX"] = [source]
        safe_row = {
            "node": "left_leg_ik_mmdCcdIk",
            "nodeType": "mmdCcdIk",
            "classification": "feedback_blocker",
            "reads": ["|left_leg.translate", "|left_leg_ik.translate"],
            "readHikJoints": ["|left_leg"],
            "readOutsideJoints": ["|left_leg_ik"],
            "writes": ["|hips.rotateX"],
        }
        clean_post = {
            "rows": [
                {
                    "node": "left_leg_ik_mmdCcdIk",
                    "classification": "manual",
                    "writes": [],
                }
            ]
        }

        def fake_create(character, mel_module=None):
            self.assertEqual(cmds.connections["|hips.rotateX"], [])
            mel.has_control_rig = True
            return True

        with _patch_classify({"rows": [safe_row]}, clean_post), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig",
            side_effect=fake_create,
        ):
            transaction = begin_humanik_control_rig(
                "owner:rig",
                "Character",
                {"|hips"},
                cmds,
                mel,
                assignments=[{"joint": "|hips", "hikBone": "LeftUpLeg"}],
            )

        self.assertEqual(cmds.connections["|hips.rotateX"], [])
        self.assertEqual(
            transaction.disconnected,
            [{"source": source, "destination": "|hips.rotateX"}],
        )
        self.assertEqual(transaction.isolated_feedback_nodes, ["left_leg_ik_mmdCcdIk"])
        stop_humanik_control_rig(transaction, cmds, mel)
        self.assertEqual(cmds.connections["|hips.rotateX"], [source])

    def test_importer_foot_feedback_without_assignments_remains_blocked(self):
        cmds, mel = FakeCmds(), FakeMel()
        safe_shape_without_context = {
            "node": "left_leg_ik_mmdCcdIk",
            "nodeType": "mmdCcdIk",
            "classification": "feedback_blocker",
            "reads": ["|left_leg.translate", "|left_leg_ik.translate"],
            "readHikJoints": ["|left_leg"],
            "readOutsideJoints": ["|left_leg_ik"],
            "writes": ["|hips.rotateX"],
        }

        with _patch_classify({"rows": [safe_shape_without_context]}), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig"
        ) as create:
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                begin_humanik_control_rig("owner:rig", "Character", {"|hips"}, cmds, mel)
            create.assert_not_called()

    def test_supported_foot_feedback_new_writer_after_create_rolls_back(self):
        cmds, mel = FakeCmds(extra_nodes={"left_leg_ik_mmdCcdIk", "|left_foot"}), FakeMel()
        source = "left_leg_ik_mmdCcdIk.outputRotate[0]"
        cmds.connections["|hips.rotateX"] = [source]
        safe_row = {
            "node": "left_leg_ik_mmdCcdIk",
            "nodeType": "mmdCcdIk",
            "classification": "feedback_blocker",
            "reads": ["|left_leg.translate", "|left_leg_ik.translate"],
            "readHikJoints": ["|left_leg"],
            "readOutsideJoints": ["|left_leg_ik"],
            "writes": ["|hips.rotateX"],
        }
        residual_row = {
            **safe_row,
            "writes": ["|left_foot.rotateX"],
            "writeHikJoints": ["|left_foot"],
        }

        def fake_create(character, mel_module=None):
            mel.has_control_rig = True
            cmds.connections["|left_foot.rotateX"] = [source]

        with _patch_classify({"rows": [safe_row]}, {"rows": [residual_row]}), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig",
            side_effect=fake_create,
        ), self.assertRaisesRegex(RuntimeError, "residual muted HIK writers"):
            begin_humanik_control_rig(
                "owner:rig",
                "Character",
                {"|hips", "|left_foot"},
                cmds,
                mel,
                assignments=[{"joint": "|hips", "hikBone": "LeftUpLeg"}],
            )

        self.assertEqual(cmds.connections["|hips.rotateX"], [source])
        self.assertEqual(cmds.connections["|left_foot.rotateX"], [])

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

        # restore_state-covered writer restored
        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])
        # residual writer explicitly disconnected before rollback (not captured)
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

    def test_stop_deletes_rig_and_restores_restore_state_idempotently(self):
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


class TestStopControlRigExceptionSafety(unittest.TestCase):
    """HUMANIK-RESTORE-GAPS-1 fix 1a: teardown must not get permanently stuck.

    Before this fix, deleting the HIK character node out from under an
    active transaction (the ``case_partial_delete`` probe scenario) made
    ``stop_humanik_control_rig``/``restore_mmd_rig`` raise the same
    "node not found" MEL error on every single call forever: the exception
    happened before ``transaction.active`` was ever set ``False`` or the
    transaction unregistered, so nothing about the stuck state ever changed
    between retries.
    """

    def tearDown(self):
        unregister_control_rig_transaction("Character")

    def _begin(self, cmds, mel):
        def fake_create(character, mel_module=None):
            mel.has_control_rig = True
            return True

        with _patch_facts(), _patch_classify(MUTE_REPORT, CLEAN_POST_REPORT), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig",
            side_effect=fake_create,
        ):
            return begin_humanik_control_rig("owner:rig", "Character", {"|hips"}, cmds, mel)

    def test_deleted_character_node_stops_cleanly_instead_of_raising_forever(self):
        cmds, mel = FakeCmds(), FakeMel()
        transaction = self._begin(cmds, mel)

        # Simulate the user manually deleting the HIK character node between
        # create_control_rig and restore_mmd_rig (the probe's partial_delete
        # case).
        cmds.existing_nodes.discard("Character")

        stop_humanik_control_rig(transaction, cmds, mel)  # must not raise

        self.assertFalse(transaction.active)
        self.assertIsNone(get_active_control_rig_transaction("Character"))
        # hikDeleteControlRig was skipped (no character to target)...
        self.assertEqual(mel.delete_calls, 0)
        # ...but the captured writer on the (still-existing) skeleton joint
        # was restored normally.
        self.assertEqual(cmds.connections["|hips.rotateX"], ["ik.outputRotateX"])

        # A retry after the transaction was already released is a pure no-op,
        # not another attempt at the same doomed teardown.
        stop_humanik_control_rig(transaction, cmds, mel)
        self.assertEqual(mel.delete_calls, 0)

    def test_restore_state_restore_failure_retains_transaction_for_safe_retry(self):
        cmds, mel = FakeCmds(), FakeMel()
        transaction = self._begin(cmds, mel)

        def failing_connect(source, destination, force=False):
            raise RuntimeError("boom-connect")

        original_connect = cmds.connectAttr
        cmds.connectAttr = failing_connect

        with self.assertRaisesRegex(RuntimeError, "boom-connect"):
            stop_humanik_control_rig(transaction, cmds, mel)

        # The rig was deleted, but restore_state failed.  Keep the
        # transaction registered so a retry can complete the writer restore.
        self.assertTrue(transaction.active)
        self.assertIs(get_active_control_rig_transaction("Character"), transaction)
        self.assertEqual(mel.delete_calls, 1)  # control rig delete step still ran

        cmds.connectAttr = original_connect
        stop_humanik_control_rig(transaction, cmds, mel)
        self.assertFalse(transaction.active)
        self.assertIsNone(get_active_control_rig_transaction("Character"))
        self.assertEqual(mel.delete_calls, 1)

    def test_control_rig_delete_failure_does_not_restore_writers_or_release_transaction(self):
        cmds, mel = FakeCmds(), FakeMel()
        transaction = self._begin(cmds, mel)

        with patch(
            "mmd_tools.core.humanik_control_rig._delete_control_rig",
            side_effect=RuntimeError("delete failed"),
        ) as delete:
            with self.assertRaisesRegex(RuntimeError, "deletion failed"):
                stop_humanik_control_rig(transaction, cmds, mel)

        delete.assert_called_once()
        self.assertTrue(transaction.active)
        self.assertIs(get_active_control_rig_transaction("Character"), transaction)
        self.assertEqual(cmds.connections["|hips.rotateX"], [])


class TestControlRigTransactionRegistry(unittest.TestCase):
    """The module-level registry ``begin_humanik_control_rig``/
    ``stop_humanik_control_rig`` maintain, which ``humanik_control_rig_watch``
    reads (read-only) to decide whether the plugin path already owns a
    character's Control Rig before warning about an out-of-band one."""

    def tearDown(self):
        unregister_control_rig_transaction("Character")

    def test_begin_registers_and_stop_unregisters(self):
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

        self.assertIs(get_active_control_rig_transaction("Character"), transaction)

        stop_humanik_control_rig(transaction, cmds, mel)
        self.assertIsNone(get_active_control_rig_transaction("Character"))

    def test_watch_sees_plugin_transaction_via_registry(self):
        cmds, mel = FakeCmds(), FakeMel()

        def fake_create(character, mel_module=None):
            mel.has_control_rig = True
            return True

        with _patch_facts(), _patch_classify(MUTE_REPORT, CLEAN_POST_REPORT), patch(
            "mmd_tools.core.humanik_control_rig.create_humanik_control_rig",
            side_effect=fake_create,
        ):
            plugin_transaction = begin_humanik_control_rig(
                "owner:rig", "Character", {"|hips"}, cmds, mel
            )

        # humanik_control_rig_watch checks this registry before warning about
        # an out-of-band Control Rig and must see the plugin path's own
        # transaction and stay silent.
        self.assertIs(get_active_control_rig_transaction("Character"), plugin_transaction)

        stop_humanik_control_rig(plugin_transaction, cmds, mel)
        self.assertIsNone(get_active_control_rig_transaction("Character"))


class TestBakeHumanIkControlRig(unittest.TestCase):
    def _transaction(self, active=True):
        return HumanIkControlRigTransaction(
            ownership_id="owner:rig",
            character="Character",
            restore_state=None,
            disconnected=[],
            retained_nodes=[],
            created_nodes=["HIKControlSetNode1"],
            active=active,
        )

    def test_baked_flag_scene_round_trip_and_legacy_default(self):
        restore_state = HumanIkRestoreState(
            "owner:rig",
            "Character",
            True,
            "",
            -1,
            [],
            [],
            "character-uuid",
        )
        transaction = HumanIkControlRigTransaction(
            ownership_id="owner:rig",
            character="Character",
            restore_state=restore_state,
            disconnected=[],
            retained_nodes=[],
            created_nodes=[],
            baked=True,
        )

        payload = transaction.to_scene_dict(
            "|model",
            model_root_uuid="model-root-uuid",
            character_uuid="character-uuid",
        )
        self.assertTrue(payload["baked"])
        restored = HumanIkControlRigTransaction.from_scene_dict(payload)
        self.assertTrue(restored.baked)

        legacy_payload = dict(payload)
        legacy_payload.pop("baked")
        self.assertFalse(HumanIkControlRigTransaction.from_scene_dict(legacy_payload).baked)

    def test_native_bake_uses_explicit_range_and_restores_playback_state(self):
        cmds, mel = FakeBakeCmds(), FakeBakeMel()
        with patch("mmd_tools.core.humanik_control_rig.ensure_humanik_mel_loaded"):
            result = bake_humanik_control_rig(self._transaction(), 2, 18, cmds, mel)

        self.assertIsInstance(result, HumanIkControlRigBakeResult)
        self.assertEqual(result.to_dict()["start"], 2)
        self.assertEqual(result.to_dict()["end"], 18)
        self.assertIn('hikSetCurrentCharacter("Character");', mel.commands)
        self.assertIn("hikBakeToControlRig(0);", mel.commands)
        self.assertEqual(cmds.playback_edits[0], {
            "minTime": 2,
            "maxTime": 18,
            "animationStartTime": 2,
            "animationEndTime": 18,
        })
        self.assertEqual(cmds.playback_edits[1], {
            "minTime": 1.5,
            "maxTime": 24.5,
            "animationStartTime": 0.0,
            "animationEndTime": 30.0,
        })
        self.assertEqual(cmds.current, 7.0)

    def test_native_bake_restores_previous_current_character(self):
        cmds, mel = FakeBakeCmds(), FakeBakeMel(current_character="OtherCharacter")
        with patch("mmd_tools.core.humanik_control_rig.ensure_humanik_mel_loaded"):
            bake_humanik_control_rig(self._transaction(), 2, 18, cmds, mel)

        self.assertEqual(mel.current_character, "OtherCharacter")
        self.assertEqual(
            [command for command in mel.commands if command.startswith("hikSetCurrentCharacter")],
            [
                'hikSetCurrentCharacter("Character");',
                'hikSetCurrentCharacter("OtherCharacter");',
            ],
        )

    def test_native_bake_failure_restores_playback_and_keeps_transaction_active(self):
        cmds, mel = FakeBakeCmds(), FakeBakeMel(fail=True)
        transaction = self._transaction()
        with patch("mmd_tools.core.humanik_control_rig.ensure_humanik_mel_loaded"):
            with self.assertRaisesRegex(RuntimeError, "native bake failed"):
                bake_humanik_control_rig(transaction, 2, 18, cmds, mel)

        self.assertTrue(transaction.active)
        self.assertEqual(len(cmds.playback_edits), 2)
        self.assertEqual(cmds.playback["minTime"], 1.5)

    def test_native_bake_failure_restores_previous_current_character(self):
        cmds, mel = FakeBakeCmds(), FakeBakeMel(fail=True, current_character="OtherCharacter")
        transaction = self._transaction()
        with patch("mmd_tools.core.humanik_control_rig.ensure_humanik_mel_loaded"):
            with self.assertRaisesRegex(RuntimeError, "native bake failed"):
                bake_humanik_control_rig(transaction, 2, 18, cmds, mel)

        self.assertTrue(transaction.active)
        self.assertEqual(mel.current_character, "OtherCharacter")

    def test_native_bake_failure_logs_concurrent_cleanup_failure(self):
        cmds, mel = FakeBakeCmds(), FakeBakeMel(fail=True)
        original_playback = cmds.playbackOptions

        def fail_playback_restore(query=False, edit=False, **kwargs):
            if edit and kwargs.get("minTime") == 1.5:
                raise RuntimeError("playback cleanup failed")
            return original_playback(query=query, edit=edit, **kwargs)

        cmds.playbackOptions = fail_playback_restore
        with patch("mmd_tools.core.humanik_control_rig.ensure_humanik_mel_loaded"), patch(
            "mmd_tools.core.humanik_control_rig.logger.error"
        ) as log_error:
            with self.assertRaisesRegex(RuntimeError, "native bake failed"):
                bake_humanik_control_rig(self._transaction(), 2, 18, cmds, mel)

        log_error.assert_called_once()
        self.assertIn("after an operation failure", log_error.call_args.args[1])

    def test_native_bake_cleanup_failure_keeps_success_and_attempts_current_restore(self):
        cmds, mel = FakeBakeCmds(), FakeBakeMel(current_character="OtherCharacter")
        original_playback = cmds.playbackOptions

        def fail_playback_restore(query=False, edit=False, **kwargs):
            if edit and kwargs.get("minTime") == 1.5:
                raise RuntimeError("playback cleanup failed")
            return original_playback(query=query, edit=edit, **kwargs)

        cmds.playbackOptions = fail_playback_restore
        with patch("mmd_tools.core.humanik_control_rig.ensure_humanik_mel_loaded"), patch(
            "mmd_tools.core.humanik_control_rig.logger.error"
        ) as log_error:
            result = bake_humanik_control_rig(self._transaction(), 2, 18, cmds, mel)

        self.assertIsInstance(result, HumanIkControlRigBakeResult)
        self.assertEqual(mel.current_character, "OtherCharacter")
        log_error.assert_called_once()

    def test_native_bake_with_no_previous_current_character_restores_empty_selection(self):
        cmds, mel = FakeBakeCmds(), FakeBakeMel(current_character="")
        with patch("mmd_tools.core.humanik_control_rig.ensure_humanik_mel_loaded"):
            bake_humanik_control_rig(self._transaction(), 2, 18, cmds, mel)

        self.assertEqual(mel.current_character, "")
        self.assertIn('hikSetCurrentCharacter("");', mel.commands)

    def test_native_bake_rejects_inactive_transaction_before_maya_queries(self):
        cmds, mel = FakeBakeCmds(), FakeBakeMel()
        with self.assertRaisesRegex(RuntimeError, "transaction is not active"):
            bake_humanik_control_rig(self._transaction(active=False), 2, 18, cmds, mel)
        self.assertEqual(mel.commands, [])
        self.assertEqual(cmds.playback_edits, [])


if __name__ == "__main__":
    unittest.main()
