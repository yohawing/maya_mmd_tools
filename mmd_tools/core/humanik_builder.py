"""Prepare Maya HumanIK assignments from imported MMD skeleton joints.

The functions here keep collection, definition creation, and lock verification
small and testable.  Definition-only operations are UI independent; Control
Rig creation follows Maya's supported Character Controls runtime sequence and
therefore requires an interactive HumanIK UI.
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
    "hikSkeletonUtils.mel",
    "hikSkeletonOperations.mel",
    "hikGlobalUtils.mel",
    "hikDefinitionUtils.mel",
    "hikDefinitionOperations.mel",
    "hikInputSourceUtils.mel",
    "hikCharacterControlsUI.mel",
    "hikControlRigOperations.mel",
    # hikGetProperty2StateFromCharacter (finger-solving lookup) lives here.
    "hikCharacterControlsUtils.mel",
)
_HIK_LOAD_PLUGIN_COMMANDS = (
    'if (!`pluginInfo -query -loaded "mayaHIK"`) loadPlugin "mayaHIK";',
    'if (!`pluginInfo -query -loaded "mayaCharacterization"`) loadPlugin "mayaCharacterization";',
)
_REQUIRED_HIK_PROCS = (
    "hikCreateCharacter",
    "hikSetCharacterObject",
    "hikGetSkNode",
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
_VALID_CHARACTERIZATION_STATES = frozenset({0, 2, 4})
_HIK_CONTROL_RIG_UI_INIT_COMMAND = "HIKCharacterControlsTool;"


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
        # This lower-level definition path remains UI independent for existing
        # mayapy/batch callers.  The user-facing Control Rig action uses
        # create_humanik_control_rig(), which initializes Character Controls.
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
        commands = build_humanik_definition_mel_commands(
            character,
            result.assignments,
            create_control_rig=create_control_rig,
            update_ui=update_ui,
        )
        assignment_count = len(result.assignments)
        for command in commands[:assignment_count]:
            mel.eval(command)
        _verify_humanik_assignment_readback(character, result.assignments, mel)
        for command in commands[assignment_count:]:
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


HUMANIK_FINGER_SOLVING_DISABLED = 0
"""Value that disables HumanIK's internal finger-rotation reconstruction.

HUMANIK-RETARGET-S5 root-caused the self-retarget finger residual
(``matrixMax`` 0.217693, see ``build/reports/hik_fingersolving_full_on.json``)
to the TARGET character's ``HIKProperty2State.FingerSolving`` attribute
defaulting to 1: with it set, HumanIK reconstructs every finger slot's
rotation through an internal IK solve instead of copying the source's
rotation directly, even though the source and target skeletons are
identical. Forcing this attribute to 0 on the TARGET character's property
node (see :func:`set_humanik_finger_solving_state`) drops every finger slot
residual to ~1e-6 degrees and the S5 gate ``matrixMax`` to 0.029879 --
exactly matching the body-only (no finger slots characterized) reference
(``build/reports/hik_property_experiment_fingersolving0.json``,
``build/reports/hik_fingersolving_full_off.json``). The setting is a no-op
when the character has no finger slots characterized at all (there is
nothing for HIK to reconstruct), so it is safe to apply unconditionally.
"""


def get_humanik_finger_solving_property_node(character: str, mel_module=None) -> str:
    """Return the ``HIKProperty2State`` node feeding ``character``.

    Args:
        character: HIK character node/name.
        mel_module: Optional Maya ``mel`` compatible module for tests.

    Returns:
        The property-state node name, or ``""`` when Maya's
        ``hikGetProperty2StateFromCharacter`` procedure is unavailable (older
        Maya/plugin variants) or the character has no associated property
        node.
    """
    mel = mel_module or _maya_mel()
    if not _mel_exists(mel, "hikGetProperty2StateFromCharacter"):
        return ""
    node = mel.eval(f"hikGetProperty2StateFromCharacter({_mel_string(character)})")
    return str(node) if node else ""


def get_humanik_finger_solving_state(
    character: str,
    mel_module=None,
    cmds_module=None,
) -> Optional[int]:
    """Return the current ``FingerSolving`` value for ``character``.

    Args:
        character: HIK character node/name.
        mel_module: Optional Maya ``mel`` compatible module for tests.
        cmds_module: Optional Maya ``cmds`` compatible module for tests.

    Returns:
        The current integer attribute value, or ``None`` when the property
        node or the ``FingerSolving`` attribute is unavailable.
    """
    mel = mel_module or _maya_mel()
    cmds = cmds_module or _maya_cmds()
    node = get_humanik_finger_solving_property_node(character, mel_module=mel)
    if not node or not cmds.attributeQuery("FingerSolving", node=node, exists=True):
        return None
    value = cmds.getAttr(f"{node}.FingerSolving")
    return None if value is None else int(value)


def set_humanik_finger_solving_state(
    character: str,
    value: int,
    mel_module=None,
    cmds_module=None,
) -> Optional[int]:
    """Set ``FingerSolving`` on ``character``'s property node and return the prior value.

    See :data:`HUMANIK_FINGER_SOLVING_DISABLED` for why mmd_tools forces this
    to 0 around TARGET self-retarget preview.

    Args:
        character: HIK character node/name.
        value: New integer attribute value (0 disables finger IK solving).
        mel_module: Optional Maya ``mel`` compatible module for tests.
        cmds_module: Optional Maya ``cmds`` compatible module for tests.

    Returns:
        The previous attribute value so callers can restore it later, or
        ``None`` when the property node/attribute is unavailable. The scene
        is left untouched in the ``None`` case -- older Maya/plugin variants
        without ``HIKProperty2State.FingerSolving`` must not hard-fail here.
    """
    mel = mel_module or _maya_mel()
    cmds = cmds_module or _maya_cmds()
    node = get_humanik_finger_solving_property_node(character, mel_module=mel)
    if not node or not cmds.attributeQuery("FingerSolving", node=node, exists=True):
        return None
    previous = cmds.getAttr(f"{node}.FingerSolving")
    cmds.setAttr(f"{node}.FingerSolving", int(value))
    return None if previous is None else int(previous)


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
    characterization = _sync_humanik_characterization_state(character, mel)
    status = characterization["status"]
    if status is not None and status not in _VALID_CHARACTERIZATION_STATES:
        raise RuntimeError(
            "HumanIK characterization status is invalid before lock: "
            f"character={character}, status={status}, diagnostics={characterization}"
        )
    try:
        mel.eval(
            f"hikCharacterLock({_mel_string(character)}, 1, "
            f"{1 if validate_and_save_stance else 0});"
        )
    except Exception as exc:
        raise RuntimeError(
            "HumanIK definition lock command failed: "
            f"character={character}, status={status}, diagnostics={characterization}: {exc}"
        ) from exc
    locked = bool(mel.eval(f"hikIsDefinitionLocked({_mel_string(character)})"))
    if not locked:
        raise RuntimeError(
            "HumanIK definition failed to lock: "
            f"character={character}, status={status}, diagnostics={characterization}"
        )
    return locked


def _sync_humanik_characterization_state(character: str, mel) -> dict:
    """Synchronize Maya's current-character/UI state before lock validation.

    Maya 2024's ``canLockCharacterization`` reads the characterization UI
    plugin's current status rather than only the HIK character node.  The
    explicit list/bone/UI refresh sequence mirrors Autodesk's local
    ``hikDefinitionOperations.mel`` flow and keeps ``update_ui=False`` scene
    setup deterministic in both GUI and mayapy.
    """
    mel.eval(f"hikSetCurrentCharacter({_mel_string(character)});")
    warnings = []
    for command in (
        "hikDefinitionUpdateCharacterLists();",
        "hikDefinitionUpdateBones();",
        "hikUpdateCharacterControlsUI(false);",
    ):
        try:
            mel.eval(command)
        except Exception as exc:
            warnings.append(f"{command}: {exc}")
    status = None
    try:
        raw_status = mel.eval("characterizationToolUICmd -query -curcharstatus")
        if raw_status not in (None, ""):
            status = int(raw_status)
    except Exception as exc:
        warnings.append(f"characterizationToolUICmd status readback: {exc}")
    return {
        "character": str(character),
        "status": status,
        "warnings": warnings,
    }


def _verify_humanik_assignment_readback(
    character: str,
    assignments: Sequence[HumanIkBoneAssignment],
    mel,
) -> None:
    """Verify every requested HumanIK slot has a connected skeleton node.

    ``hikSetCharacterObject`` can return without an error when Maya rejects a
    slot (for example, because the character is in an unexpected state).  The
    authoritative ``hikGetSkNode`` readback catches that semantic failure while
    intentionally avoiding a strict path comparison: Maya may return a short
    DAG name even when the requested joint was a long path.
    """
    missing = []
    for assignment in assignments:
        label = (
            f"slot={assignment.hik_bone}, hikIndex={assignment.hik_index}, "
            f"joint={assignment.joint}"
        )
        try:
            raw_node = mel.eval(
                f"hikGetSkNode({_mel_string(character)}, {assignment.hik_index});"
            )
        except Exception as exc:
            missing.append(f"{label}, readbackError={exc}")
            continue
        if raw_node is None or not str(raw_node).strip():
            missing.append(f"{label}, actual=<empty>")

    if missing:
        raise RuntimeError(
            "HumanIK assignment readback failed: "
            f"character={character}, requested={len(assignments)}, "
            f"missing={len(missing)}: "
            + "; ".join(missing)
        )


def create_humanik_control_rig(character: str, mel_module=None) -> bool:
    """Create and verify a HumanIK control rig for an existing character.

    Args:
        character: Existing characterized HumanIK character name.
        mel_module: Optional Maya ``mel`` compatible module for tests.

    Returns:
        ``True`` when Maya reports that the control rig exists.

    Raises:
        ValueError: If ``character`` is empty.
        RuntimeError: If the interactive Character Controls UI is unavailable
            or Maya does not report a control rig after creation.
    """
    if not character:
        raise ValueError("character is required")
    mel = mel_module or _maya_mel()
    ensure_humanik_mel_loaded(mel)
    _initialize_humanik_control_rig_ui(mel)
    mel.eval(f"hikSetCurrentCharacter({_mel_string(character)});")
    mel.eval("hikCreateControlRig();")
    mel.eval("hikOnSwitchContextualTabs;")
    if not bool(mel.eval(f"hikHasControlRig({_mel_string(character)})")):
        raise RuntimeError(f"HumanIK control rig was not created: {character}")
    return True


def _initialize_humanik_control_rig_ui(mel) -> None:
    """Initialize the Character Controls UI required by ``hikCreateControlRig``.

    Maya 2026's ``hikCreateControlRig`` unconditionally updates the
    ``hikContextualTabs`` tab layout.  The supported runtime command creates
    that UI first, but it is a no-op in mayapy/batch mode.  Detect batch mode
    before invoking it and verify the tab layout afterwards so callers never
    reach a misleading missing-control MEL error.
    """
    if _humanik_control_rig_ui_exists(mel):
        return
    if _mel_truthy(_mel_eval_optional(mel, "about -batch")):
        raise RuntimeError(
            "HumanIK Control Rig requires an interactive Maya Character Controls UI; "
            "run this operation from the Maya GUI."
        )
    try:
        mel.eval(_HIK_CONTROL_RIG_UI_INIT_COMMAND)
    except Exception as exc:
        raise RuntimeError(
            "HumanIK Character Controls UI could not be initialized; "
            "open HIKCharacterControlsTool in the Maya GUI and retry."
        ) from exc
    if not _humanik_control_rig_ui_exists(mel):
        raise RuntimeError(
            "HumanIK Character Controls UI is unavailable; "
            "open HIKCharacterControlsTool in the Maya GUI and retry."
        )


def _humanik_control_rig_ui_exists(mel) -> bool:
    """Return whether Maya's contextual tab layout has been built."""
    try:
        return _mel_truthy(mel.eval("control -exists hikContextualTabs"))
    except Exception:
        return False


def _mel_eval_optional(mel, command):
    """Evaluate a best-effort MEL query without making test doubles fragile."""
    try:
        return mel.eval(command)
    except Exception:
        return None


def _mel_truthy(value) -> bool:
    """Interpret Maya MEL booleans and integer query results consistently."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


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
