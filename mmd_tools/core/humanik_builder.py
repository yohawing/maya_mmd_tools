"""Prepare Maya HumanIK assignments from imported MMD skeleton joints.

The functions here keep collection, definition creation, and lock verification
small and testable.  The Maya operations are driven through MEL without
requiring any HumanIK UI controls.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME, ATTR_MMD_BONE_NAME_EN
from mmd_tools.core.humanik_resolver import (
    HumanIkBoneAssignment,
    HumanIkJointCandidate,
    HumanIkResolveResult,
    resolve_humanik_assignments,
)


_HIK_MEL_SCRIPTS = (
    "hikSkeletonOperations.mel",
    "hikGlobalUtils.mel",
    "hikDefinitionUtils.mel",
    "hikDefinitionOperations.mel",
    "hikInputSourceUtils.mel",
    "hikCharacterControlsUI.mel",
    "hikControlRigOperations.mel",
)
_HIK_LOAD_PLUGIN_COMMANDS = (
    'if (!`pluginInfo -query -loaded "mayaHIK"`) loadPlugin "mayaHIK";',
    'if (!`pluginInfo -query -loaded "mayaCharacterization"`) loadPlugin "mayaCharacterization";',
)
_REQUIRED_HIK_PROCS = (
    "hikCreateCharacter",
    "hikSetCharacterObject",
    "hikSetCurrentCharacter",
    "hikCharacterLock",
    "hikIsDefinitionLocked",
    "hikSetCharacterInput",
    "hikGetInputType",
    "hikGetRetargetCharacterInput",
    "hikCreateControlRig",
    "hikHasControlRig",
    "hikDeleteCharacter",
    "hikGetSceneCharacters",
)


class HumanIkCharacterCreationError(RuntimeError):
    """Report a failed HumanIK character creation and cleanup outcome.

    ``character`` identifies the Maya character created before the failure.
    ``creation_error`` is the original assignment, UI, or control-rig error;
    ``cleanup_error`` is populated only when deleting that character also
    failed, allowing callers to retain the character for a later retry.
    """

    def __init__(self, character: str, creation_error: Exception, cleanup_error=None):
        self.character = str(character)
        self.creation_error = creation_error
        self.cleanup_error = cleanup_error
        if cleanup_error is None:
            message = (
                f"HumanIK character creation failed for {self.character}: "
                f"{creation_error}; cleanup succeeded"
            )
        else:
            message = (
                f"HumanIK character creation failed for {self.character}: "
                f"{creation_error}; cleanup failed: {cleanup_error}"
            )
        super().__init__(message)


def collect_humanik_joint_candidates(model_root: Optional[str] = None, cmds_module=None) -> List[HumanIkJointCandidate]:
    """Collect imported MMD joint metadata as HumanIK resolver candidates.

    Args:
        model_root: Optional model root or joint path.  When omitted, all scene
            joints are considered.
        cmds_module: Optional Maya cmds-compatible module, used by tests.

    Returns:
        Joint candidates sorted by imported MMD bone index when available.
    """
    cmds = cmds_module or _maya_cmds()
    joints = _list_candidate_joints(cmds, model_root)
    candidates = [
        HumanIkJointCandidate(
            node=joint,
            mmd_name=_get_string_attr(cmds, joint, ATTR_MMD_BONE_NAME),
            english_name=_get_string_attr(cmds, joint, ATTR_MMD_BONE_NAME_EN),
            bone_index=_get_int_attr(cmds, joint, ATTR_MMD_BONE_INDEX),
        )
        for joint in joints
    ]
    return sorted(candidates, key=lambda candidate: (_sort_bone_index(candidate.bone_index), candidate.node))


def resolve_scene_humanik_assignments(
    model_root: Optional[str] = None,
    cmds_module=None,
) -> HumanIkResolveResult:
    """Resolve HumanIK assignments from joints in a Maya scene."""
    return resolve_humanik_assignments(collect_humanik_joint_candidates(model_root, cmds_module))


def build_humanik_definition_mel_commands(
    character: str,
    assignments: Sequence[HumanIkBoneAssignment],
    create_control_rig: bool = False,
    update_ui: bool = True,
) -> List[str]:
    """Build MEL commands that assign resolved joints to a HIK character."""
    commands = [
        f'hikSetCharacterObject({_mel_string(assignment.joint)}, {_mel_string(character)}, {assignment.hik_index}, 0);'
        for assignment in assignments
    ]
    commands.append(f'hikSetCurrentCharacter({_mel_string(character)});')
    if create_control_rig:
        commands.append("hikCreateControlRig();")
    if update_ui:
        commands.append("hikUpdateCharacterControlsUI(false);")
    return commands


def create_humanik_definition(
    result: HumanIkResolveResult,
    name_hint: str = "MMDCharacter",
    mel_module=None,
    create_control_rig: bool = False,
    update_ui: bool = True,
) -> str:
    """Create a Maya HumanIK character definition from resolved assignments."""
    if not result.assignments:
        raise ValueError("HumanIK definition requires at least one resolved assignment")

    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)
    character = str(mel.eval(f'hikCreateCharacter({_mel_string(name_hint)})'))
    try:
        for command in build_humanik_definition_mel_commands(
            character,
            result.assignments,
            create_control_rig=create_control_rig,
            update_ui=update_ui,
        ):
            mel.eval(command)
        if create_control_rig and not bool(mel.eval(f"hikHasControlRig({_mel_string(character)})")):
            raise RuntimeError(f"HumanIK control rig was not created for character: {character}")
    except Exception as creation_error:
        try:
            delete_humanik_character(character, mel_module=mel)
        except Exception as cleanup_error:
            raise HumanIkCharacterCreationError(
                character,
                creation_error,
                cleanup_error=cleanup_error,
            ) from creation_error
        raise HumanIkCharacterCreationError(character, creation_error) from creation_error
    return character


def ensure_humanik_mel_loaded(mel_module=None) -> None:
    """Source Maya HumanIK MEL scripts when standalone has not loaded them."""
    mel = mel_module or _maya_mel()
    if _has_hik_procs(mel):
        return
    for command in _HIK_LOAD_PLUGIN_COMMANDS:
        mel.eval(command)
    for script in _HIK_MEL_SCRIPTS:
        mel.eval(f"source {script}")
    if not _has_hik_procs(mel):
        missing = [proc for proc in _REQUIRED_HIK_PROCS if not _mel_exists(mel, proc)]
        raise RuntimeError(f"Failed to load Maya HumanIK MEL procedures: {', '.join(missing)}")


def create_humanik_definition_from_scene(
    model_root: Optional[str] = None,
    name_hint: str = "MMDCharacter",
    cmds_module=None,
    mel_module=None,
    create_control_rig: bool = False,
    update_ui: bool = True,
) -> str:
    """Resolve scene joints and create a Maya HumanIK character definition."""
    result = resolve_scene_humanik_assignments(model_root, cmds_module)
    return create_humanik_definition(
        result,
        name_hint=name_hint,
        mel_module=mel_module,
        create_control_rig=create_control_rig,
        update_ui=update_ui,
    )


def get_humanik_definition_lock_state(character: str, mel_module=None) -> bool:
    """Return Maya's authoritative lock state for a HIK definition.

    Args:
        character: HIK character node/name.
        mel_module: Optional Maya ``mel`` compatible module for tests.

    Returns:
        ``True`` when Maya reports the definition as locked.
    """
    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)
    return bool(mel.eval(f"hikIsDefinitionLocked({_mel_string(character)})"))


def lock_humanik_definition(
    character: str,
    mel_module=None,
    validate_and_save_stance: bool = True,
) -> bool:
    """Lock a HumanIK definition and verify the resulting state.

    This is the non-UI equivalent of pressing the Characterize/Lock button.
    Maya's MEL procedure takes an explicit integer lock flag and validation
    flag; passing both keeps behaviour deterministic in mayapy and GUI runs.

    Args:
        character: HIK character node/name.
        mel_module: Optional Maya ``mel`` compatible module for tests.
        validate_and_save_stance: Ask Maya to validate and save the stance pose.

    Returns:
        The verified lock state (``True`` on success).

    Raises:
        RuntimeError: If Maya did not report the definition as locked.
    """
    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)
    mel.eval(
        f"hikCharacterLock({_mel_string(character)}, 1, "
        f"{1 if validate_and_save_stance else 0});"
    )
    locked = bool(mel.eval(f"hikIsDefinitionLocked({_mel_string(character)})"))
    if not locked:
        raise RuntimeError(f"HumanIK definition failed to lock: {character}")
    return locked


def create_humanik_control_rig(character: str, mel_module=None) -> bool:
    """Create and verify a HumanIK control rig for an existing character.

    Args:
        character: Existing characterized HumanIK character name.
        mel_module: Optional Maya ``mel`` compatible module for tests.

    Returns:
        ``True`` when Maya reports that the control rig exists.

    Raises:
        ValueError: If ``character`` is empty.
        RuntimeError: If Maya does not report a control rig after creation.
    """
    if not character:
        raise ValueError("character is required")
    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)
    mel.eval(f"hikSetCurrentCharacter({_mel_string(character)});")
    mel.eval("hikCreateControlRig();")
    if not bool(mel.eval(f"hikHasControlRig({_mel_string(character)})")):
        raise RuntimeError(f"HumanIK control rig was not created: {character}")
    return True


def delete_humanik_character(character: str, mel_module=None) -> bool:
    """Delete a HumanIK character and verify it left the Maya scene.

    Args:
        character: Existing HumanIK character name.
        mel_module: Optional Maya ``mel`` compatible module for tests.

    Returns:
        ``True`` when the character is absent after deletion.

    Raises:
        ValueError: If ``character`` is empty.
        RuntimeError: If Maya readback still contains the character.
    """
    if not character:
        raise ValueError("character is required")
    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)
    mel.eval(f"hikDeleteCharacter({_mel_string(character)});")
    remaining = _scene_humanik_characters(mel)
    if remaining is None:
        raise RuntimeError("HumanIK character scene readback is unavailable")
    if str(character) in remaining:
        raise RuntimeError(f"HumanIK character was not deleted: {character}")
    return True


def _maya_cmds():
    from maya import cmds

    return cmds


def _maya_mel():
    from maya import mel

    return mel


def _list_candidate_joints(cmds, model_root: Optional[str]) -> List[str]:
    if model_root:
        if not cmds.objExists(model_root):
            raise ValueError(f"Model root does not exist: {model_root}")
        joints = []
        if _node_type(cmds, model_root) == "joint":
            joints.append(_long_name(cmds, model_root))
        joints.extend(cmds.listRelatives(model_root, allDescendents=True, fullPath=True, type="joint") or [])
        return _dedupe(joints)
    return _dedupe(cmds.ls(type="joint", long=True) or [])


def _get_string_attr(cmds, node: str, attr: str) -> str:
    if not _has_attr(cmds, node, attr):
        return ""
    value = cmds.getAttr(f"{node}.{attr}")
    return "" if value is None else str(value)


def _get_int_attr(cmds, node: str, attr: str) -> Optional[int]:
    if not _has_attr(cmds, node, attr):
        return None
    value = cmds.getAttr(f"{node}.{attr}")
    if value is None:
        return None
    return int(value)


def _has_attr(cmds, node: str, attr: str) -> bool:
    return bool(cmds.attributeQuery(attr, node=node, exists=True))


def _node_type(cmds, node: str) -> str:
    node_type = getattr(cmds, "nodeType", None)
    if node_type is None:
        return ""
    return str(node_type(node))


def _long_name(cmds, node: str) -> str:
    matches = cmds.ls(node, long=True) or []
    return matches[0] if matches else node


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _sort_bone_index(index: Optional[int]) -> int:
    return index if index is not None else 1_000_000_000


def _mel_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _has_hik_procs(mel) -> bool:
    return all(_mel_exists(mel, proc) for proc in _REQUIRED_HIK_PROCS)


def _scene_humanik_characters(mel):
    raw = mel.eval("hikGetSceneCharacters()")
    if raw is None:
        return None
    if isinstance(raw, (tuple, list)):
        return {str(value) for value in raw}
    text = str(raw).strip()
    if not text:
        return set()
    return {value.strip().strip('"') for value in text.replace(";", " ").split() if value.strip()}


def _mel_exists(mel, proc: str) -> bool:
    return bool(mel.eval(f"exists {proc}"))
