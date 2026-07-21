"""Unit tests for collecting HumanIK candidates from Maya-like scenes."""

import unittest

from mmd_tools.config.humanik_mapping import HIK_BONE_INDICES
from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME, ATTR_MMD_BONE_NAME_EN
from mmd_tools.core.humanik_builder import (
    build_humanik_definition_mel_commands,
    collect_humanik_joint_candidates,
    create_humanik_definition,
    create_humanik_definition_from_scene,
    create_humanik_control_rig,
    delete_humanik_character,
    ensure_humanik_mel_loaded,
    get_humanik_definition_lock_state,
    get_humanik_finger_solving_property_node,
    get_humanik_finger_solving_state,
    get_humanik_left_elbow_kill_pitch_state,
    HumanIkCharacterCreationError,
    HUMANIK_FINGER_SOLVING_DISABLED,
    HUMANIK_LEFT_ELBOW_KILL_PITCH_ENABLED,
    lock_humanik_definition,
    resolve_scene_humanik_assignments,
    set_humanik_finger_solving_state,
    set_humanik_left_elbow_kill_pitch_state,
)


class FakeCmds:
    """Small Maya cmds fake for HumanIK builder tests."""

    def __init__(self):
        self.types = {
            "|model": "transform",
            "|model|lower": "joint",
            "|model|spine": "joint",
            "|other|arm": "joint",
        }
        self.children = {
            "|model": ["|model|spine", "|model|lower"],
            "|other": ["|other|arm"],
        }
        self.attrs = {
            ("|model|lower", ATTR_MMD_BONE_NAME): "下半身",
            ("|model|lower", ATTR_MMD_BONE_INDEX): 1,
            ("|model|spine", ATTR_MMD_BONE_NAME_EN): "upper_body",
            ("|model|spine", ATTR_MMD_BONE_INDEX): 2,
            ("|other|arm", ATTR_MMD_BONE_NAME): "左腕",
            ("|other|arm", ATTR_MMD_BONE_INDEX): 3,
        }

    def objExists(self, node):
        return node in self.types

    def nodeType(self, node):
        return self.types[node]

    def listRelatives(self, node, allDescendents=False, fullPath=False, type=None):
        values = list(self.children.get(node, []))
        if type:
            values = [value for value in values if self.types.get(value) == type]
        return values

    def ls(self, *args, **kwargs):
        if args:
            node = args[0]
            return [node] if node in self.types else []
        node_type = kwargs.get("type")
        if node_type:
            return [node for node, value in self.types.items() if value == node_type]
        return list(self.types)

    def attributeQuery(self, attr, node, exists=False):
        return exists and (node, attr) in self.attrs

    def getAttr(self, plug):
        node, attr = plug.rsplit(".", 1)
        return self.attrs[(node, attr)]


class FakeMel:
    """Small Maya mel fake for HumanIK definition tests."""

    def __init__(self):
        self.commands = []
        self.loaded = False
        self.has_control_rig = False
        self.locked = False
        self.scene_characters = set()
        self.characterization_status = None
        self.hik_ui_initialized = False

    def eval(self, command):
        self.commands.append(command)
        if command.startswith("exists "):
            return int(self.loaded)
        if command.startswith("source "):
            self.loaded = True
            return None
        if command.startswith("hikCreateCharacter("):
            self.scene_characters.add("Character1")
            return "Character1"
        if command.startswith("hikDeleteCharacter("):
            self.scene_characters.discard("Character1")
            return None
        if command == "about -batch":
            return 0
        if command == "control -exists hikContextualTabs":
            return int(self.hik_ui_initialized)
        if command == "HIKCharacterControlsTool;":
            self.hik_ui_initialized = True
            return None
        if command == "hikOnSwitchContextualTabs;":
            return None
        if command.startswith("hikGetSceneCharacters"):
            return sorted(self.scene_characters)
        if command == "hikCreateControlRig();":
            self.has_control_rig = True
            return None
        if command.startswith("hikHasControlRig("):
            return int(self.has_control_rig)
        if command.startswith("hikGetSkNode("):
            return "mappedJoint"
        if command.startswith("hikCharacterLock("):
            self.locked = True
            return None
        if command.startswith("hikIsDefinitionLocked("):
            return int(self.locked)
        if command.startswith("characterizationToolUICmd -query -curcharstatus"):
            return self.characterization_status
        return None


class TestHumanIkBuilder(unittest.TestCase):
    """HumanIK builder scene collection tests."""

    def test_collect_humanik_joint_candidates_from_model_root(self):
        candidates = collect_humanik_joint_candidates("|model", FakeCmds())

        self.assertEqual([candidate.node for candidate in candidates], ["|model|lower", "|model|spine"])
        self.assertEqual(candidates[0].mmd_name, "下半身")
        self.assertEqual(candidates[1].english_name, "upper_body")

    def test_collect_humanik_joint_candidates_can_scan_all_joints(self):
        candidates = collect_humanik_joint_candidates(cmds_module=FakeCmds())

        self.assertEqual([candidate.node for candidate in candidates], ["|model|lower", "|model|spine", "|other|arm"])

    def test_resolve_scene_humanik_assignments_uses_collected_metadata(self):
        result = resolve_scene_humanik_assignments("|model", FakeCmds())

        assignments = result.assignments_by_hik_index
        self.assertEqual(assignments[HIK_BONE_INDICES["Hips"]].joint, "|model|lower")
        self.assertEqual(assignments[HIK_BONE_INDICES["Spine"]].source, "english_name")

    def test_collect_humanik_joint_candidates_rejects_missing_root(self):
        with self.assertRaisesRegex(ValueError, "Model root does not exist"):
            collect_humanik_joint_candidates("|missing", FakeCmds())

    def test_build_humanik_definition_mel_commands_assigns_hik_indices(self):
        result = resolve_scene_humanik_assignments("|model", FakeCmds())

        commands = build_humanik_definition_mel_commands("Character1", result.assignments)

        self.assertIn('hikSetCharacterObject("|model|lower", "Character1", 1, 0);', commands)
        self.assertIn('hikSetCharacterObject("|model|spine", "Character1", 8, 0);', commands)
        self.assertEqual(commands[-1], 'hikUpdateCharacterControlsUI(false);')

    def test_build_humanik_definition_mel_commands_can_create_control_rig(self):
        result = resolve_scene_humanik_assignments("|model", FakeCmds())

        commands = build_humanik_definition_mel_commands(
            'Character"One',
            result.assignments,
            create_control_rig=True,
        )

        self.assertIn('hikSetCurrentCharacter("Character\\"One");', commands)
        self.assertEqual(commands[-2:], ["hikCreateControlRig();", "hikUpdateCharacterControlsUI(false);"])

    def test_create_humanik_definition_executes_create_then_assignments(self):
        result = resolve_scene_humanik_assignments("|model", FakeCmds())
        mel = FakeMel()

        character = create_humanik_definition(result, name_hint="MMD Character", mel_module=mel)

        self.assertEqual(character, "Character1")
        self.assertIn("source hikSkeletonUtils.mel", mel.commands)
        self.assertIn("source hikGlobalUtils.mel", mel.commands)
        self.assertIn("source hikDefinitionUtils.mel", mel.commands)
        self.assertIn('hikCreateCharacter("MMD Character")', mel.commands)
        self.assertIn('hikSetCharacterObject("|model|lower", "Character1", 1, 0);', mel.commands)
        self.assertEqual(
            sum(command.startswith("hikGetSkNode(") for command in mel.commands),
            len(result.assignments),
        )
        self.assertEqual(mel.commands[-1], "hikUpdateCharacterControlsUI(false);")

    def test_create_humanik_definition_from_scene_uses_collector_and_resolver(self):
        mel = FakeMel()

        character = create_humanik_definition_from_scene("|model", cmds_module=FakeCmds(), mel_module=mel)

        self.assertEqual(character, "Character1")
        self.assertIn('hikSetCharacterObject("|model|spine", "Character1", 8, 0);', mel.commands)

    def test_create_humanik_definition_verifies_requested_control_rig(self):
        result = resolve_scene_humanik_assignments("|model", FakeCmds())
        mel = FakeMel()

        character = create_humanik_definition(result, mel_module=mel, create_control_rig=True)

        self.assertEqual(character, "Character1")
        self.assertIn("hikCreateControlRig();", mel.commands)
        self.assertIn('hikHasControlRig("Character1")', mel.commands)

    def test_create_humanik_definition_rejects_empty_assignments(self):
        with self.assertRaisesRegex(ValueError, "requires at least one"):
            create_humanik_definition(resolve_scene_humanik_assignments("|missing", _EmptySceneCmds()), mel_module=FakeMel())

    def test_create_humanik_definition_fails_when_control_rig_is_missing(self):
        result = resolve_scene_humanik_assignments("|model", FakeCmds())
        mel = _NoControlRigMel()

        with self.assertRaisesRegex(RuntimeError, "control rig was not created"):
            create_humanik_definition(result, mel_module=mel, create_control_rig=True)

    def test_create_humanik_definition_cleans_up_after_assignment_failure(self):
        result = resolve_scene_humanik_assignments("|model", FakeCmds())
        mel = _AssignmentFailureMel()

        with self.assertRaises(HumanIkCharacterCreationError) as context:
            create_humanik_definition(result, mel_module=mel)

        error = context.exception
        self.assertEqual(error.character, "Character1")
        self.assertIsInstance(error.creation_error, RuntimeError)
        self.assertIsNone(error.cleanup_error)
        self.assertNotIn("Character1", mel.scene_characters)
        self.assertIn("cleanup succeeded", str(error))

    def test_create_humanik_definition_cleans_up_after_silent_assignment_readback_failure(self):
        result = resolve_scene_humanik_assignments("|model", FakeCmds())
        mel = _SilentAssignmentReadbackMel(HIK_BONE_INDICES["Spine"])

        with self.assertRaises(HumanIkCharacterCreationError) as context:
            create_humanik_definition(result, mel_module=mel)

        error = context.exception
        self.assertIsInstance(error.creation_error, RuntimeError)
        self.assertIsNone(error.cleanup_error)
        self.assertNotIn("Character1", mel.scene_characters)
        self.assertEqual(
            sum(command.startswith("hikGetSkNode(") for command in mel.commands),
            len(result.assignments),
        )
        self.assertRegex(
            str(error),
            r"readback failed: character=Character1, requested=2, missing=1",
        )
        self.assertRegex(str(error), r"slot=Spine, hikIndex=8")
        self.assertIn("cleanup succeeded", str(error))

    def test_create_humanik_definition_retains_character_when_cleanup_fails(self):
        result = resolve_scene_humanik_assignments("|model", FakeCmds())
        mel = _AssignmentAndCleanupFailureMel()

        with self.assertRaises(HumanIkCharacterCreationError) as context:
            create_humanik_definition(result, mel_module=mel)

        error = context.exception
        self.assertEqual(error.character, "Character1")
        self.assertIsInstance(error.creation_error, RuntimeError)
        self.assertIsInstance(error.cleanup_error, RuntimeError)
        self.assertIn("cleanup failed", str(error))
        self.assertIn("Character1", mel.scene_characters)

    def test_create_humanik_control_rig_uses_current_character_and_verifies(self):
        mel = FakeMel()

        self.assertTrue(create_humanik_control_rig("Character1", mel_module=mel))
        self.assertLess(mel.commands.index("HIKCharacterControlsTool;"), mel.commands.index('hikSetCurrentCharacter("Character1");'))
        self.assertIn('hikSetCurrentCharacter("Character1");', mel.commands)
        self.assertIn("hikCreateControlRig();", mel.commands)
        self.assertIn("hikOnSwitchContextualTabs;", mel.commands)
        self.assertIn('hikHasControlRig("Character1")', mel.commands)

    def test_create_humanik_control_rig_preserves_existing_hik_ui_state(self):
        mel = FakeMel()
        mel.hik_ui_initialized = True

        self.assertTrue(create_humanik_control_rig("Character1", mel_module=mel))

        self.assertNotIn("about -batch", mel.commands)
        self.assertNotIn("HIKCharacterControlsTool;", mel.commands)

    def test_create_humanik_control_rig_rejects_batch_without_hik_ui(self):
        class HeadlessMel(FakeMel):
            def eval(self, command):
                if command == "about -batch":
                    self.commands.append(command)
                    return 1
                return super().eval(command)

        mel = HeadlessMel()

        with self.assertRaisesRegex(RuntimeError, "interactive Maya Character Controls UI"):
            create_humanik_control_rig("Character1", mel_module=mel)

        self.assertNotIn("HIKCharacterControlsTool;", mel.commands)
        self.assertNotIn("hikCreateControlRig();", mel.commands)

    def test_create_humanik_control_rig_rejects_ui_init_without_contextual_tabs(self):
        class NoOpHikUiMel(FakeMel):
            def eval(self, command):
                if command == "HIKCharacterControlsTool;":
                    self.commands.append(command)
                    return None
                return super().eval(command)

        mel = NoOpHikUiMel()

        with self.assertRaisesRegex(RuntimeError, "UI is unavailable"):
            create_humanik_control_rig("Character1", mel_module=mel)

        self.assertNotIn("hikCreateControlRig();", mel.commands)

    def test_delete_humanik_character_verifies_scene_readback(self):
        mel = FakeMel()
        mel.scene_characters.add("Character1")

        self.assertTrue(delete_humanik_character("Character1", mel_module=mel))
        self.assertIn('hikDeleteCharacter("Character1");', mel.commands)
        self.assertNotIn("Character1", mel.scene_characters)

    def test_delete_humanik_character_rejects_readback_failure(self):
        class BrokenDeleteMel(FakeMel):
            def eval(self, command):
                if command.startswith("hikDeleteCharacter("):
                    self.commands.append(command)
                    return None
                return super().eval(command)

        mel = BrokenDeleteMel()
        mel.scene_characters.add("Character1")

        with self.assertRaisesRegex(RuntimeError, "was not deleted"):
            delete_humanik_character("Character1", mel_module=mel)

    def test_ensure_humanik_mel_loaded_skips_source_when_available(self):
        mel = FakeMel()
        mel.loaded = True

        ensure_humanik_mel_loaded(mel)

        self.assertNotIn("source hikGlobalUtils.mel", mel.commands)

    def test_lock_humanik_definition_verifies_state(self):
        mel = FakeMel()

        self.assertTrue(lock_humanik_definition("Character1", mel_module=mel))
        self.assertTrue(get_humanik_definition_lock_state("Character1", mel_module=mel))
        self.assertIn('hikCharacterLock("Character1", 1, 1);', mel.commands)

    def test_lock_syncs_current_characterization_state_before_status_and_lock(self):
        mel = FakeMel()
        mel.characterization_status = 2

        self.assertTrue(lock_humanik_definition("Character1", mel_module=mel))

        commands = mel.commands
        sync_start = commands.index('hikSetCurrentCharacter("Character1");')
        status_index = commands.index("characterizationToolUICmd -query -curcharstatus")
        lock_index = commands.index('hikCharacterLock("Character1", 1, 1);')
        self.assertLess(sync_start, commands.index("hikDefinitionUpdateCharacterLists();"))
        self.assertLess(commands.index("hikDefinitionUpdateCharacterLists();"), commands.index("hikDefinitionUpdateBones();"))
        self.assertLess(commands.index("hikDefinitionUpdateBones();"), commands.index("hikUpdateCharacterControlsUI(false);"))
        self.assertLess(commands.index("hikUpdateCharacterControlsUI(false);"), status_index)
        self.assertLess(status_index, lock_index)

    def test_lock_rejects_invalid_characterization_status_before_lock(self):
        mel = FakeMel()
        mel.characterization_status = 3

        with self.assertRaisesRegex(RuntimeError, r"status=3"):
            lock_humanik_definition("Character1", mel_module=mel)
        self.assertFalse(any(command.startswith("hikCharacterLock(") for command in mel.commands))

    def test_lock_humanik_definition_rejects_unlocked_state(self):
        class BrokenLockMel(FakeMel):
            def eval(self, command):
                if command.startswith("hikCharacterLock("):
                    self.commands.append(command)
                    return None
                return super().eval(command)

        with self.assertRaisesRegex(RuntimeError, "failed to lock"):
            lock_humanik_definition("Character1", mel_module=BrokenLockMel())

    def test_lock_failure_diagnostic_includes_characterization_status(self):
        class StatusBrokenLockMel(FakeMel):
            def __init__(self):
                super().__init__()
                self.characterization_status = 4

            def eval(self, command):
                if command.startswith("hikCharacterLock("):
                    self.commands.append(command)
                    return None
                return super().eval(command)

        with self.assertRaisesRegex(RuntimeError, r"character=Character1, status=4"):
            lock_humanik_definition("Character1", mel_module=StatusBrokenLockMel())

    def test_lock_command_exception_includes_characterization_diagnostic(self):
        class CommandFailureMel(FakeMel):
            def __init__(self):
                super().__init__()
                self.characterization_status = 2

            def eval(self, command):
                if command.startswith("hikCharacterLock("):
                    self.commands.append(command)
                    raise RuntimeError("canLockCharacterization failed")
                return super().eval(command)

        with self.assertRaisesRegex(RuntimeError, r"command failed: character=Character1, status=2"):
            lock_humanik_definition("Character1", mel_module=CommandFailureMel())


class _EmptySceneCmds(FakeCmds):
    def __init__(self):
        super().__init__()
        self.types["|missing"] = "transform"
        self.children["|missing"] = []


class _AssignmentFailureMel(FakeMel):
    def eval(self, command):
        if command.startswith("hikSetCharacterObject("):
            self.commands.append(command)
            raise RuntimeError("assignment failed")
        return super().eval(command)


class _AssignmentAndCleanupFailureMel(_AssignmentFailureMel):
    def eval(self, command):
        if command.startswith("hikDeleteCharacter("):
            self.commands.append(command)
            raise RuntimeError("cleanup failed")
        return super().eval(command)


class _SilentAssignmentReadbackMel(FakeMel):
    def __init__(self, missing_index):
        super().__init__()
        self.missing_index = missing_index

    def eval(self, command):
        if command.startswith("hikGetSkNode("):
            self.commands.append(command)
            if f", {self.missing_index});" in command:
                return ""
            return "mappedJoint"
        return super().eval(command)


class _NoControlRigMel(FakeMel):
    def eval(self, command):
        if command == "hikCreateControlRig();":
            self.commands.append(command)
            return None
        return super().eval(command)


class _FingerSolvingCmds:
    """Fake ``cmds`` with a HIKProperty2State node holding ``FingerSolving``."""

    def __init__(self, has_attr=True, initial=1, left_elbow_initial=0):
        self.has_attr = has_attr
        self.values = (
            {
                "propNode.FingerSolving": initial,
                "propNode.LeftElbowKillPitch": left_elbow_initial,
            }
            if has_attr
            else {}
        )
        self.attribute_query_calls = []
        self.set_attr_calls = []

    def attributeQuery(self, attr, node=None, exists=False):
        self.attribute_query_calls.append((attr, node, exists))
        return bool(
            exists
            and self.has_attr
            and attr in {"FingerSolving", "LeftElbowKillPitch"}
            and node == "propNode"
        )

    def getAttr(self, plug):
        return self.values.get(plug)

    def setAttr(self, plug, value):
        self.set_attr_calls.append((plug, value))
        self.values[plug] = value


class _FingerSolvingMel:
    """Fake ``mel`` reporting whether ``hikGetProperty2StateFromCharacter`` exists."""

    def __init__(self, node="propNode", proc_exists=True):
        self.node = node
        self.proc_exists = proc_exists
        self.commands = []

    def eval(self, command):
        self.commands.append(command)
        if command.startswith("exists "):
            return int(self.proc_exists)
        if command.startswith("hikGetProperty2StateFromCharacter"):
            return self.node
        return None


class TestHumanIkFingerSolving(unittest.TestCase):
    """HUMANIK-RETARGET-S5: FingerSolving property lookup/mutation helpers."""

    def test_get_property_node_returns_connected_node(self):
        mel = _FingerSolvingMel(node="propNode")
        self.assertEqual(get_humanik_finger_solving_property_node("Target", mel), "propNode")

    def test_get_property_node_returns_empty_when_proc_missing(self):
        mel = _FingerSolvingMel(proc_exists=False)
        self.assertEqual(get_humanik_finger_solving_property_node("Target", mel), "")

    def test_get_state_reads_current_value(self):
        cmds = _FingerSolvingCmds(initial=1)
        mel = _FingerSolvingMel()
        self.assertEqual(get_humanik_finger_solving_state("Target", mel, cmds), 1)

    def test_set_state_disables_and_returns_previous_value(self):
        cmds = _FingerSolvingCmds(initial=1)
        mel = _FingerSolvingMel()
        previous = set_humanik_finger_solving_state(
            "Target", HUMANIK_FINGER_SOLVING_DISABLED, mel, cmds
        )
        self.assertEqual(previous, 1)
        self.assertEqual(cmds.values["propNode.FingerSolving"], 0)
        self.assertEqual(cmds.set_attr_calls, [("propNode.FingerSolving", 0)])

    def test_set_state_is_noop_when_proc_missing(self):
        """Older Maya/plugin variants without the proc must not hard-fail."""
        cmds = _FingerSolvingCmds(initial=1)
        mel = _FingerSolvingMel(proc_exists=False)
        previous = set_humanik_finger_solving_state("Target", 0, mel, cmds)
        self.assertIsNone(previous)
        self.assertEqual(cmds.set_attr_calls, [])

    def test_set_state_is_noop_when_no_property_node(self):
        """A character with no associated property node is left untouched."""
        cmds = _FingerSolvingCmds(initial=1)
        mel = _FingerSolvingMel(node="")
        previous = set_humanik_finger_solving_state("Target", 0, mel, cmds)
        self.assertIsNone(previous)
        self.assertEqual(cmds.set_attr_calls, [])

    def test_set_state_is_noop_when_attribute_missing(self):
        """Property node exists but lacks FingerSolving (older HIK schema)."""
        cmds = _FingerSolvingCmds(has_attr=False)
        mel = _FingerSolvingMel()
        previous = set_humanik_finger_solving_state("Target", 0, mel, cmds)
        self.assertIsNone(previous)
        self.assertEqual(cmds.set_attr_calls, [])


class TestHumanIkLeftElbowKillPitch(unittest.TestCase):
    """Aida-proven TARGET elbow-pitch property helper behavior."""

    def test_get_state_reads_current_value(self):
        cmds = _FingerSolvingCmds(left_elbow_initial=0)
        mel = _FingerSolvingMel()
        self.assertEqual(get_humanik_left_elbow_kill_pitch_state("Target", mel, cmds), 0)

    def test_set_state_enables_and_returns_previous_value(self):
        cmds = _FingerSolvingCmds(left_elbow_initial=0)
        mel = _FingerSolvingMel()
        previous = set_humanik_left_elbow_kill_pitch_state(
            "Target", HUMANIK_LEFT_ELBOW_KILL_PITCH_ENABLED, mel, cmds
        )
        self.assertEqual(previous, 0)
        self.assertEqual(cmds.values["propNode.LeftElbowKillPitch"], 1)
        self.assertEqual(cmds.set_attr_calls, [("propNode.LeftElbowKillPitch", 1)])

    def test_set_state_is_noop_when_property_missing(self):
        cmds = _FingerSolvingCmds(has_attr=False)
        mel = _FingerSolvingMel()
        self.assertIsNone(set_humanik_left_elbow_kill_pitch_state("Target", 1, mel, cmds))
        self.assertEqual(cmds.set_attr_calls, [])


if __name__ == "__main__":
    unittest.main()
