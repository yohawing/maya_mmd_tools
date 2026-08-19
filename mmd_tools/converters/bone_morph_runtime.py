"""PMX bone morph metadata から runtime DG グラフを構築する。"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import maya.api.OpenMaya as om
from maya import cmds

from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX
from mmd_tools.core.logger import get_logger
from mmd_tools.converters.morph_runtime_common import (
    connect_if_needed as _connect_if_needed,
    get_morph_order,
    is_connected as _is_connected,
    parse_morph_offsets_json,
    same_source as _same_source,
)
from mmd_tools.converters.morph_scene_metadata import iter_morph_network_metadata


logger = get_logger(__name__)

ACCUM_NODE_TYPE = "mmdBoneMorphAccum"
CCD_IK_NODE_TYPE = "mmdCcdIk"
APPEND_NODE_TYPE = "mmdAppend"
REQUIRED_ACCUM_ATTRS = (
    "contribution",
    "morphOrder",
    "weight",
    "translateOffset",
    "rotateOffsetQuat",
    "baseTranslate",
    "baseRotate",
    "rotateOrder",
    "outputTranslate",
    "outputRotate",
)
_NODE_TYPE_UNAVAILABLE = "node_type_unavailable"
_PROBE_NODE_NAME = "__mmdBoneMorphAccum_availability_probe__"
_ARRAY_INDEX_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\](?:\.|$)")


@dataclass(frozen=True)
class BoneMorphBaseRouteResolution:
    """Owned accumulator routes plus fail-closed claims that could not resolve."""

    routes: Dict[str, Dict[str, Tuple[str, str]]]
    blocked: Dict[str, Tuple[Tuple[str, ...], str]]


def build_bone_morph_graph(root_group: str) -> Dict[str, Any]:
    """Build or refresh PMX bone morph accumulator nodes under ``root_group``.

    Args:
        root_group: Imported MMD model root group. Joints are resolved from its
            descendants via ``mmd_bone_index``.

    Returns:
        Summary dict containing created/reused accumulator nodes and skipped
        morph metadata records. When ``mmdBoneMorphAccum`` is unavailable the
        graph is skipped, morph metadata is preserved, and a structured
        ``node_type_unavailable`` warning is returned.
    """
    result = {
        "success": True,
        "accumulator_nodes": [],
        "created": 0,
        "reused": 0,
        "contributions": 0,
        "skipped": [],
        "warnings": [],
    }
    if not root_group or not cmds.objExists(root_group):
        result["success"] = False
        result["skipped"].append("root_group_missing")
        return result

    joints_by_index = _collect_joints_by_bone_index(root_group)
    if not joints_by_index:
        result["skipped"].append("no_indexed_joints")
        return result

    bone_morph_nodes = list(_iter_bone_morph_nodes(root_group))
    contributions_by_joint = _collect_contributions_by_joint(
        bone_morph_nodes,
        joints_by_index,
        result["skipped"],
    )
    existing_by_joint = {
        joint: node
        for joint, node in _collect_existing_accumulators().items()
        if joint in set(joints_by_index.values())
    }
    for joint in sorted(set(existing_by_joint) - set(contributions_by_joint)):
        _remove_accumulator(joint, existing_by_joint[joint])
    if not contributions_by_joint:
        result["skipped"].append("no_bone_morph_contributions")
        return result

    availability = probe_bone_morph_accum_availability()
    if not availability.get("available"):
        warning = _node_type_unavailable_warning(availability)
        result["success"] = False
        result["warnings"].append(warning)
        result["skipped"].append(_NODE_TYPE_UNAVAILABLE)
        logger.warning(
            "Skipping bone morph runtime graph: %s unavailable (%s)",
            ACCUM_NODE_TYPE,
            availability.get("detail") or _NODE_TYPE_UNAVAILABLE,
        )
        return result

    for contributions in contributions_by_joint.values():
        contributions.sort(
            key=lambda item: (
                item["morph_order"],
                item.get("group_morph_node") or "",
                item["morph_node"],
            )
        )

    for joint, contributions in contributions_by_joint.items():
        node = existing_by_joint.get(joint)
        if node and _is_valid_accumulator(node):
            result["reused"] += 1
        else:
            node = _create_accumulator(joint)
            if not node:
                result["success"] = False
                result["skipped"].append(f"create_failed:{joint}")
                continue
            result["created"] += 1

        _mark_accumulator(node, joint)
        _sync_rotate_order(node, joint)
        _refresh_contributions(node, contributions)
        _reroute_joint_inputs_through_accumulator(joint, node)
        result["accumulator_nodes"].append(node)
        result["contributions"] += len(contributions)

    return result


def probe_bone_morph_accum_availability() -> Dict[str, Any]:
    """Probe whether ``mmdBoneMorphAccum`` can be created with its attribute contract.

    Creates a temporary probe node, validates type and required attributes, then
    deletes the probe. Never leaves a scene artifact on success or failure paths
    that this function owns.

    Returns:
        Dict with ``available`` bool and diagnostic fields. When unavailable,
        ``code`` / ``reason`` are ``node_type_unavailable``.
    """
    result = {
        "available": False,
        "code": _NODE_TYPE_UNAVAILABLE,
        "reason": _NODE_TYPE_UNAVAILABLE,
        "node_type": ACCUM_NODE_TYPE,
        "detail": "",
        "missing_attributes": [],
        "actual_type": "",
    }
    node = None
    scene_was_modified = None
    undo_was_enabled = None
    try:
        try:
            scene_was_modified = bool(cmds.file(query=True, modified=True))
        except Exception:
            pass
        try:
            undo_was_enabled = bool(cmds.undoInfo(query=True, state=True))
            if undo_was_enabled:
                cmds.undoInfo(stateWithoutFlush=False)
        except Exception:
            undo_was_enabled = None
        try:
            node = cmds.createNode(ACCUM_NODE_TYPE, name=_PROBE_NODE_NAME)
        except Exception as exc:
            result["detail"] = "create_failed: {0}".format(exc)
            return result

        try:
            actual_type = cmds.nodeType(node) or ""
        except Exception as exc:
            result["detail"] = "node_type_query_failed: {0}".format(exc)
            return result
        result["actual_type"] = actual_type

        if actual_type != ACCUM_NODE_TYPE:
            result["detail"] = "unknown_or_wrong_type: {0}".format(actual_type)
            return result

        missing = []
        for attr in REQUIRED_ACCUM_ATTRS:
            try:
                exists = cmds.attributeQuery(attr, node=node, exists=True)
            except Exception:
                exists = False
            if not exists:
                missing.append(attr)
        if missing:
            result["missing_attributes"] = missing
            result["detail"] = "missing_attributes: {0}".format(",".join(missing))
            return result

        result["available"] = True
        result["code"] = ""
        result["reason"] = ""
        result["detail"] = ""
        return result
    finally:
        # A createNode exception may still have mutated Maya before failing.
        # Cleanup is only provable after Maya returned a concrete node name.
        cleanup_succeeded = node is not None and _delete_node_quiet(node)
        if undo_was_enabled:
            try:
                cmds.undoInfo(stateWithoutFlush=True)
            except Exception:
                pass
        if scene_was_modified is not None and cleanup_succeeded:
            try:
                cmds.file(modified=scene_was_modified)
            except Exception:
                pass


def log_bone_morph_accum_availability_postcondition() -> Dict[str, Any]:
    """Soft plugin-init postcondition: probe contract without failing load.

    Returns the probe result. Callers should treat exceptions / unavailable
    results as non-fatal diagnostics only.
    """
    availability = probe_bone_morph_accum_availability()
    if not availability.get("available"):
        logger.warning(
            "Plugin postcondition: %s unavailable (%s); bone morph runtime will fail soft",
            ACCUM_NODE_TYPE,
            availability.get("detail") or _NODE_TYPE_UNAVAILABLE,
        )
    return availability


def _node_type_unavailable_warning(availability: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": _NODE_TYPE_UNAVAILABLE,
        "reason": _NODE_TYPE_UNAVAILABLE,
        "node_type": ACCUM_NODE_TYPE,
        "detail": availability.get("detail") or _NODE_TYPE_UNAVAILABLE,
        "missing_attributes": list(availability.get("missing_attributes") or []),
        "actual_type": availability.get("actual_type") or "",
    }


def _delete_node_quiet(node: Optional[str]) -> bool:
    if not node:
        return True
    try:
        if cmds.objExists(node):
            cmds.delete(node)
            return not cmds.objExists(node)
        return True
    except Exception:
        logger.debug("Failed to delete temporary node %s", node, exc_info=True)
        return False


def _is_valid_accumulator(node: str) -> bool:
    """Return whether *node* is a usable ``mmdBoneMorphAccum`` instance."""
    if not node or not cmds.objExists(node):
        return False
    try:
        if cmds.nodeType(node) != ACCUM_NODE_TYPE:
            return False
        return all(
            cmds.attributeQuery(attr, node=node, exists=True) for attr in REQUIRED_ACCUM_ATTRS
        )
    except Exception:
        return False


def resolve_owned_bone_morph_base_routes(
    joints: Iterable[str],
) -> BoneMorphBaseRouteResolution:
    """Resolve unambiguous authored base channels for owned accumulators.

    The marker and stored target alone are insufficient: a stale or copied
    accumulator must not capture VMD keys.  Each accepted node must also own
    both live output connections at the destination selected by the runtime
    graph (joint, append base, or IK input).  Duplicate candidates and any
    incomplete ownership proof are skipped fail-closed.
    """
    requested: Dict[str, str] = {}
    for joint in joints:
        canonical = _canonical_dag_path(str(joint))
        if canonical:
            requested[canonical] = str(joint)
    if not requested:
        return BoneMorphBaseRouteResolution(routes={}, blocked={})

    candidates: Dict[str, List[str]] = defaultdict(list)
    invalid_candidates: Dict[str, List[str]] = defaultdict(list)
    try:
        accumulators = cmds.ls(type=ACCUM_NODE_TYPE) or []
    except Exception:
        return BoneMorphBaseRouteResolution(routes={}, blocked={})
    for node_value in accumulators:
        node = str(node_value)
        try:
            if not cmds.attributeQuery("mmd_bone_morph_accum", node=node, exists=True):
                continue
            if not bool(cmds.getAttr(f"{node}.mmd_bone_morph_accum")):
                continue
        except Exception:
            continue
        try:
            has_target = cmds.attributeQuery(
                "mmd_target_joint", node=node, exists=True
            )
            target = (
                str(cmds.getAttr(f"{node}.mmd_target_joint") or "")
                if has_target
                else ""
            )
        except Exception:
            target = ""
        target_matches = _canonical_dag_paths(target)
        requested_targets = [match for match in target_matches if match in requested]
        claimed_targets = set(requested_targets)
        # A malformed/stale target marker can still be associated safely with
        # the mapped joint whose live input it partially owns. This slow scan
        # runs only on malformed candidates, never on the normal import path.
        if len(target_matches) != 1 or len(requested_targets) != 1:
            for canonical_joint in requested:
                if any(
                    _is_connected(
                        f"{node}.output{attr_kind.capitalize()}",
                        _destination_upstream_of_append(canonical_joint, attr_kind),
                    )
                    for attr_kind in ("translate", "rotate")
                ):
                    claimed_targets.add(canonical_joint)
        if not claimed_targets:
            continue
        if (
            len(target_matches) != 1
            or len(requested_targets) != 1
            or not _is_valid_accumulator(node)
        ):
            for claimed_target in claimed_targets:
                invalid_candidates[claimed_target].append(node)
            continue
        candidates[requested_targets[0]].append(node)

    routes: Dict[str, Dict[str, Tuple[str, str]]] = {}
    blocked: Dict[str, Tuple[Tuple[str, ...], str]] = {}
    all_channels = tuple(
        f"{attr_kind}{axis}"
        for attr_kind in ("translate", "rotate")
        for axis in ("X", "Y", "Z")
    )
    claimed_joints = set(candidates) | set(invalid_candidates)
    for canonical_joint in claimed_joints:
        nodes = candidates.get(canonical_joint, [])
        invalid_nodes = invalid_candidates.get(canonical_joint, [])
        if invalid_nodes:
            blocked[requested[canonical_joint]] = (
                all_channels,
                "invalid_or_ambiguous_bone_morph_accumulator",
            )
            continue
        if len(set(nodes)) != 1:
            blocked[requested[canonical_joint]] = (
                all_channels,
                "duplicate_bone_morph_accumulator",
            )
            continue
        node = nodes[0]
        ownership = (
            (
                "translate",
                "baseTranslate",
                "outputTranslate",
            ),
            ("rotate", "baseRotate", "outputRotate"),
        )
        if not all(
            _is_connected(
                f"{node}.{output_name}",
                _destination_upstream_of_append(canonical_joint, attr_kind),
            )
            for attr_kind, _base_name, output_name in ownership
        ):
            blocked[requested[canonical_joint]] = (
                all_channels,
                "bone_morph_accumulator_output_unowned",
            )
            continue
        route: Dict[str, Tuple[str, str]] = {}
        for attr_kind, base_name, _output_name in ownership:
            for axis in ("X", "Y", "Z"):
                route[f"{attr_kind}{axis}"] = (node, f"{base_name}{axis}")
        routes[requested[canonical_joint]] = route
    return BoneMorphBaseRouteResolution(routes=routes, blocked=blocked)


def _canonical_dag_path(node: str) -> Optional[str]:
    """Return one unambiguous long DAG path, or ``None``."""
    try:
        matches = cmds.ls(node, long=True) or []
    except Exception:
        return None
    return str(matches[0]) if len(matches) == 1 else None


def _canonical_dag_paths(node: str) -> Tuple[str, ...]:
    """Return every long DAG match for ambiguity-aware ownership checks."""
    try:
        return tuple(str(match) for match in (cmds.ls(node, long=True) or []))
    except Exception:
        return ()


def _collect_joints_by_bone_index(root_group: str) -> Dict[int, str]:
    joints = cmds.listRelatives(root_group, allDescendents=True, type="joint", fullPath=True) or []
    if cmds.nodeType(root_group) == "joint":
        joints.append(root_group)

    joints_by_index: Dict[int, str] = {}
    for joint in joints:
        if not cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
            continue
        try:
            bone_index = int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))
        except Exception:
            continue
        if bone_index in joints_by_index:
            logger.warning(
                "Duplicate %s=%s for bone morph runtime: %s and %s",
                ATTR_MMD_BONE_INDEX,
                bone_index,
                joints_by_index[bone_index],
                joint,
            )
            continue
        joints_by_index[bone_index] = joint
    return joints_by_index


def _iter_bone_morph_nodes(root_group: str) -> Iterable[str]:
    yield from _iter_morph_nodes(root_group, "bone", "mmd_bone_morph_offsets_json")


def _iter_morph_nodes(root_group: str, morph_type: str, required_attr: str) -> Iterable[str]:
    for metadata in iter_morph_network_metadata(
        root_group=root_group,
        morph_types={morph_type},
        required_attrs=(required_attr,),
    ):
        yield metadata.node


def _collect_contributions_by_joint(
    morph_nodes: Iterable[str],
    joints_by_index: Dict[int, str],
    skipped: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    contributions_by_joint: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    bind_orientations: Dict[str, om.MQuaternion] = {}
    for morph_node in morph_nodes:
        offsets = _parse_offsets_json(morph_node)
        if offsets is None:
            skipped.append(f"invalid_offsets:{morph_node}")
            continue
        morph_order = _get_morph_order(morph_node)
        for offset in offsets:
            contribution = _offset_to_contribution(morph_node, morph_order, offset)
            if contribution is None:
                skipped.append(f"invalid_offset:{morph_node}")
                continue
            joint = joints_by_index.get(contribution["bone_index"])
            if joint is None:
                skipped.append(f"missing_joint:{morph_node}:{contribution['bone_index']}")
                continue
            contribution["rotate_quat"] = _pmx_quat_to_joint_rotate(
                contribution["rotate_quat"],
                joint,
                bind_orientations,
            )
            contributions_by_joint[joint].append(contribution)

    return dict(contributions_by_joint)


def _parse_offsets_json(morph_node: str) -> Optional[List[Dict[str, Any]]]:
    return parse_morph_offsets_json(morph_node, "mmd_bone_morph_offsets_json")


def _get_morph_order(morph_node: str) -> int:
    return get_morph_order(morph_node)


def _offset_to_contribution(
    morph_node: str,
    morph_order: int,
    offset: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(offset, dict) or "bone_index" not in offset:
        return None
    try:
        translation = offset.get("translation", (0.0, 0.0, 0.0))
        rotation = offset.get("rotation", (0.0, 0.0, 0.0, 1.0))
        if len(translation) != 3 or len(rotation) != 4:
            return None
        return {
            "morph_node": morph_node,
            "morph_order": int(morph_order),
            "bone_index": int(offset["bone_index"]),
            "translate": _pmx_translate_to_maya(translation),
            "rotate_quat": _pmx_quat_to_maya(rotation),
            "weight_source": f"{morph_node}.weight",
        }
    except Exception:
        return None


def _pmx_translate_to_maya(values) -> Tuple[float, float, float]:
    return (float(values[0]), float(values[1]), -float(values[2]))


def _pmx_quat_to_maya(values) -> Tuple[float, float, float, float]:
    return (-float(values[0]), -float(values[1]), float(values[2]), float(values[3]))


def pmx_bone_offset_to_runtime_values(
    translation: Tuple[float, float, float],
    rotation: Tuple[float, float, float, float],
    joint: str,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float]]:
    """Convert one PMX bone offset to the accumulator's runtime basis.

    This small public helper is shared by the selected-morph patch path so a
    direct contribution update uses exactly the same handedness reflection and
    bind-orientation conjugation as the full runtime graph builder.
    """
    translate = (float(translation[0]), float(translation[1]), -float(translation[2]))
    rotate = _pmx_quat_to_joint_rotate(_pmx_quat_to_maya(rotation), joint, {})
    return translate, rotate


def _pmx_quat_to_joint_rotate(
    maya_quat: Tuple[float, float, float, float],
    joint: str,
    bind_orientations: Optional[Dict[str, om.MQuaternion]] = None,
) -> Tuple[float, float, float, float]:
    """Map a reflected PMX offset into the joint's bind-oriented rotate basis.

    PMX bone rotations are expressed in the model basis. Imported Maya joints
    use ``jointOrient`` to represent their bind axes, so an offset must be
    conjugated by the joint's bind-world orientation before it can be composed
    with the existing ``joint.rotate`` animation. This is the same basis change
    used by the VMD runtime path and remains a homomorphism, preserving PMX
    morph-index multiplication order.
    """
    q_offset = om.MQuaternion(*maya_quat)
    try:
        q_offset.normalizeIt()
        q_bind = bind_orientations.get(joint) if bind_orientations is not None else None
        if q_bind is None:
            q_bind = _joint_bind_world_orientation(joint)
            if bind_orientations is not None:
                bind_orientations[joint] = q_bind
        q_rotate = q_bind * q_offset * q_bind.inverse()
        q_rotate.normalizeIt()
        return (q_rotate.x, q_rotate.y, q_rotate.z, q_rotate.w)
    except Exception:
        logger.warning(
            "Failed to map PMX bone morph rotation into bind space for %s; "
            "using reflected model-space quaternion",
            joint,
            exc_info=True,
        )
        return maya_quat


def _joint_bind_world_orientation(joint: str) -> om.MQuaternion:
    """Return the static bind-world orientation from the jointOrient chain."""
    chain = []
    current = joint
    while current and cmds.objExists(current) and cmds.nodeType(current) == "joint":
        chain.append(current)
        parents = cmds.listRelatives(current, parent=True, type="joint", fullPath=True) or []
        current = parents[0] if parents else ""

    q_world = om.MQuaternion()
    for current in reversed(chain):
        values = cmds.getAttr(f"{current}.jointOrient")[0]
        q_local = om.MEulerRotation(
            math.radians(float(values[0])),
            math.radians(float(values[1])),
            math.radians(float(values[2])),
        ).asQuaternion()
        q_world = q_local * q_world
    q_world.normalizeIt()
    return q_world


def _collect_existing_accumulators() -> Dict[str, str]:
    accumulators: Dict[str, str] = {}
    for node in cmds.ls(type=ACCUM_NODE_TYPE) or []:
        if not _is_valid_accumulator(node):
            continue
        if not cmds.attributeQuery("mmd_target_joint", node=node, exists=True):
            continue
        if not cmds.attributeQuery("mmd_bone_morph_accum", node=node, exists=True):
            continue
        try:
            if not cmds.getAttr(f"{node}.mmd_bone_morph_accum"):
                continue
            joint = cmds.getAttr(f"{node}.mmd_target_joint") or ""
        except Exception:
            continue
        if joint and cmds.objExists(joint):
            accumulators[joint] = node
    return accumulators


def _remove_accumulator(joint: str, node: str) -> None:
    """Restore pre-morph inputs and delete one owned accumulator."""
    for attr_kind, base_name, output_name in (
        ("rotate", "baseRotate", "outputRotate"),
        ("translate", "baseTranslate", "outputTranslate"),
    ):
        destination = _destination_upstream_of_append(joint, attr_kind)
        base_attr = f"{node}.{base_name}"
        output_attr = f"{node}.{output_name}"
        if _is_connected(output_attr, destination):
            cmds.disconnectAttr(output_attr, destination)
            _copy_current_compound_value(base_attr, destination)
        for axis in ("X", "Y", "Z"):
            base_axis = _compound_axis_plug(base_attr, axis)
            destination_axis = _compound_axis_plug(destination, axis)
            if not base_axis or not destination_axis:
                continue
            for source in cmds.listConnections(base_axis, s=True, d=False, p=True) or []:
                cmds.disconnectAttr(source, base_axis)
                _connect_if_needed(source, destination_axis, force=True)
        for source in cmds.listConnections(base_attr, s=True, d=False, p=True) or []:
            cmds.disconnectAttr(source, base_attr)
            _connect_if_needed(source, destination, force=True)
    cmds.delete(node)


def _create_accumulator(joint: str) -> Optional[str]:
    node_name = f"{joint.split('|')[-1]}_boneMorphAccum"
    try:
        node = cmds.createNode(ACCUM_NODE_TYPE, name=node_name)
    except Exception as exc:
        logger.warning("Failed to create %s for %s: %s", ACCUM_NODE_TYPE, joint, exc)
        return None
    if _is_valid_accumulator(node):
        return node
    logger.warning(
        "Created %s for %s, but type/attribute contract is unavailable; deleting probe node",
        ACCUM_NODE_TYPE,
        joint,
    )
    _delete_node_quiet(node)
    return None


def _mark_accumulator(node: str, joint: str) -> None:
    if not cmds.attributeQuery("mmd_bone_morph_accum", node=node, exists=True):
        cmds.addAttr(node, longName="mmd_bone_morph_accum", attributeType="bool")
    cmds.setAttr(f"{node}.mmd_bone_morph_accum", True)
    if not cmds.attributeQuery("mmd_target_joint", node=node, exists=True):
        cmds.addAttr(node, longName="mmd_target_joint", dataType="string")
    cmds.setAttr(f"{node}.mmd_target_joint", joint, type="string")


def _sync_rotate_order(node: str, joint: str) -> None:
    try:
        value = int(cmds.getAttr(f"{joint}.rotateOrder"))
        cmds.setAttr(f"{node}.rotateOrder", value)
        _connect_if_needed(f"{joint}.rotateOrder", f"{node}.rotateOrder", force=True)
    except Exception:
        logger.debug("Failed to sync rotateOrder for %s", joint, exc_info=True)


def _refresh_contributions(node: str, contributions: List[Dict[str, Any]]) -> None:
    for index in cmds.getAttr(f"{node}.contribution", multiIndices=True) or []:
        try:
            cmds.removeMultiInstance(f"{node}.contribution[{index}]", b=True)
        except Exception:
            pass

    for slot, contribution in enumerate(contributions):
        prefix = f"{node}.contribution[{slot}]"
        cmds.setAttr(f"{prefix}.morphOrder", int(contribution["morph_order"]))
        tx, ty, tz = contribution["translate"]
        cmds.setAttr(f"{prefix}.translateOffset", tx, ty, tz, type="double3")
        qx, qy, qz, qw = contribution["rotate_quat"]
        cmds.setAttr(f"{prefix}.rotateOffsetQuat", qx, qy, qz, qw, type="double4")
        _connect_if_needed(str(contribution["weight_source"]), f"{prefix}.weight", force=True)


def _reroute_joint_inputs_through_accumulator(joint: str, node: str) -> None:
    rotate_destination = _destination_upstream_of_append(joint, "rotate")
    translate_destination = _destination_upstream_of_append(joint, "translate")
    _reroute_compound(
        destination=rotate_destination,
        base_attr=f"{node}.baseRotate",
        output_attr=f"{node}.outputRotate",
        attr_kind="rotate",
    )
    _reroute_compound(
        destination=translate_destination,
        base_attr=f"{node}.baseTranslate",
        output_attr=f"{node}.outputTranslate",
        attr_kind="translate",
    )


def _destination_upstream_of_append(joint: str, attr_kind: str) -> str:
    """Resolve where a bone morph accumulator should feed for *joint*.*attr_kind*.

    Priority:
    1. ``mmdAppend.output*`` driving the joint → feed ``mmdAppend.base*``
    2. ``mmdCcdIk.outputRotate[link_i]`` driving ``joint.rotate`` → feed
       ``mmdCcdIk.inputRotate[chainJson.links[link_i].bone_slot]``
    3. Otherwise feed the joint attribute itself
    """
    joint_attr = f"{joint}.{attr_kind}"
    output_name = f"output{attr_kind.capitalize()}"
    base_name = f"base{attr_kind.capitalize()}"
    for source in cmds.listConnections(joint_attr, s=True, d=False, p=True) or []:
        source_node, source_attr = _split_plug(source)
        if not source_node:
            continue
        try:
            source_type = cmds.nodeType(source_node)
        except Exception:
            continue
        if source_type == APPEND_NODE_TYPE and source_attr.startswith(output_name):
            return f"{source_node}.{base_name}"
        if attr_kind == "rotate" and source_type == CCD_IK_NODE_TYPE:
            ik_dest = _ccd_ik_input_rotate_destination(source_node, source_attr)
            if ik_dest:
                return ik_dest
    return joint_attr


def _ccd_ik_input_rotate_destination(ik_node: str, source_attr: str) -> Optional[str]:
    """Map ``mmdCcdIk.outputRotate[link_i]`` to its pre-solver ``inputRotate`` plug."""
    link_index = _array_attr_index(source_attr, "outputRotate")
    if link_index is None:
        return None
    bone_slot = _ccd_ik_link_bone_slot(ik_node, link_index)
    if bone_slot is None or bone_slot < 0:
        return None
    return f"{ik_node}.inputRotate[{bone_slot}]"


def _ccd_ik_link_bone_slot(ik_node: str, link_index: int) -> Optional[int]:
    """Return ``chainJson.links[link_index].bone_slot`` (fallback: *link_index*)."""
    try:
        raw = cmds.getAttr(f"{ik_node}.chainJson") or "{}"
        cfg = json.loads(raw)
    except Exception:
        return link_index
    links = cfg.get("links") if isinstance(cfg, dict) else None
    if not isinstance(links, list) or link_index < 0 or link_index >= len(links):
        return link_index
    link = links[link_index]
    if not isinstance(link, dict):
        return link_index
    try:
        return int(link.get("bone_slot", link_index))
    except (TypeError, ValueError):
        return link_index


def _array_attr_index(source_attr: str, array_name: str) -> Optional[int]:
    """Parse multi-index from ``outputRotate[2]`` or ``outputRotate[2].child``."""
    if not source_attr.startswith(array_name):
        return None
    match = _ARRAY_INDEX_RE.match(source_attr)
    if not match or match.group(1) != array_name:
        return None
    try:
        return int(match.group(2))
    except (TypeError, ValueError):
        return None


def _compound_axis_plug(compound_attr: str, axis: str) -> Optional[str]:
    """Resolve the X/Y/Z child plug for a compound attribute.

    Handles standard children (``rotateX``) and mmdCcdIk array elements
    (``inputRotate[n].inputRotateElementX``).
    """
    candidates = [f"{compound_attr}{axis}"]
    if "[" in compound_attr:
        leaf = compound_attr.rsplit(".", 1)[-1]
        array_name = leaf.split("[", 1)[0]
        if array_name:
            candidates.append(f"{compound_attr}.{array_name}Element{axis}")
    for plug in candidates:
        try:
            if cmds.objExists(plug):
                return plug
        except Exception:
            continue
    return candidates[0] if candidates else None


def _reroute_compound(destination: str, base_attr: str, output_attr: str, attr_kind: str) -> None:
    if not _is_connected(output_attr, destination):
        _copy_current_compound_value(destination, base_attr)

    for source in cmds.listConnections(destination, s=True, d=False, p=True) or []:
        if _same_source(source, output_attr):
            continue
        _disconnect_and_reconnect(source, destination, base_attr)

    for axis in ("X", "Y", "Z"):
        dst_axis = _compound_axis_plug(destination, axis)
        base_axis = _compound_axis_plug(base_attr, axis)
        output_axis = _compound_axis_plug(output_attr, axis)
        if not dst_axis or not base_axis or not output_axis:
            continue
        try:
            axis_sources = cmds.listConnections(dst_axis, s=True, d=False, p=True) or []
        except Exception:
            axis_sources = []
        for source in axis_sources:
            if _same_source(source, output_axis):
                continue
            _disconnect_and_reconnect(source, dst_axis, base_axis)

    _connect_if_needed(output_attr, destination, force=True)


def _copy_current_compound_value(source_attr: str, destination_attr: str) -> None:
    try:
        value = _get_compound_value(source_attr)
        if not value or len(value) != 3:
            return
        cmds.setAttr(destination_attr, float(value[0]), float(value[1]), float(value[2]), type="double3")
    except Exception:
        logger.debug("Failed to copy %s to %s", source_attr, destination_attr, exc_info=True)


def _get_compound_value(attr: str) -> Optional[Tuple[float, float, float]]:
    value = cmds.getAttr(attr)
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, tuple) and len(value) == 1 and isinstance(value[0], tuple):
        value = value[0]
    if isinstance(value, tuple) and len(value) == 3:
        return (float(value[0]), float(value[1]), float(value[2]))

    try:
        return (
            float(cmds.getAttr(f"{attr}X")),
            float(cmds.getAttr(f"{attr}Y")),
            float(cmds.getAttr(f"{attr}Z")),
        )
    except Exception:
        return None


def _disconnect_and_reconnect(source: str, destination: str, new_destination: str) -> None:
    try:
        cmds.disconnectAttr(source, destination)
    except Exception:
        pass
    _connect_if_needed(source, new_destination, force=True)


def _split_plug(plug: str) -> Tuple[str, str]:
    if "." not in plug:
        return "", ""
    return plug.rsplit(".", 1)
