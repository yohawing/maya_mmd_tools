"""Unit tests for collecting HumanIK candidates from Maya-like scenes."""

import unittest

from mmd_tools.config.humanik_mapping import HIK_BONE_INDICES
from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME, ATTR_MMD_BONE_NAME_EN
from mmd_tools.core.humanik_builder import (
    build_humanik_definition_mel_commands,
    collect_humanik_joint_candidates,
    create_humanik_definition,
    create_humanik_definition_from_scene,
    ensure_humanik_mel_loaded,
    get_humanik_definition_lock_state,
    lock_humanik_definition,
    resolve_scene_humanik_assignments,
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

    def eval(self, command):
        self.commands.append(command)
        if command.startswith("exists "):
            return int(self.loaded)
        if command.startswith("source "):
            self.loaded = True
            return None
        if command.startswith("hikCreateCharacter("):
            return "Character1"
        if command == "hikCreateControlRig();":
            self.has_control_rig = True
            return None
        if command.startswith("hikHasControlRig("):
            return int(self.has_control_rig)
        if command.startswith("hikCharacterLock("):
            self.locked = True
            return None
        if command.startswith("hikIsDefinitionLocked("):
            return int(self.locked)
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
        self.assertIn("source hikGlobalUtils.mel", mel.commands)
        self.assertIn("source hikDefinitionUtils.mel", mel.commands)
        self.assertIn('hikCreateCharacter("MMD Character")', mel.commands)
        self.assertIn('hikSetCharacterObject("|model|lower", "Character1", 1, 0);', mel.commands)
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

    def test_lock_humanik_definition_rejects_unlocked_state(self):
        class BrokenLockMel(FakeMel):
            def eval(self, command):
                if command.startswith("hikCharacterLock("):
                    self.commands.append(command)
                    return None
                return super().eval(command)

        with self.assertRaisesRegex(RuntimeError, "failed to lock"):
            lock_humanik_definition("Character1", mel_module=BrokenLockMel())


class _EmptySceneCmds(FakeCmds):
    def __init__(self):
        super().__init__()
        self.types["|missing"] = "transform"
        self.children["|missing"] = []


class _NoControlRigMel(FakeMel):
    def eval(self, command):
        if command == "hikCreateControlRig();":
            self.commands.append(command)
            return None
        return super().eval(command)


if __name__ == "__main__":
    unittest.main()
