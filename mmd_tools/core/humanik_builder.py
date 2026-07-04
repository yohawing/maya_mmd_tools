"""Prepare Maya HumanIK assignments from imported MMD skeleton joints.

The functions here stop at data collection and resolution.  Actual HumanIK
character creation is kept for a later slice so tests can cover the resolver
without requiring Maya's HIK UI commands.
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
    character = str(mel.eval(f'hikCreateCharacter({_mel_string(name_hint)})'))
    for command in build_humanik_definition_mel_commands(
        character,
        result.assignments,
        create_control_rig=create_control_rig,
        update_ui=update_ui,
    ):
        mel.eval(command)
    return character


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
