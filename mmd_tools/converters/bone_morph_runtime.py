"""PMX bone morph metadata から runtime DG グラフを構築する。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


def build_bone_morph_graph(root_group: str) -> Dict[str, Any]:
    """Build or refresh PMX bone morph accumulator nodes under ``root_group``.

    Args:
        root_group: Imported MMD model root group. Joints are resolved from its
            descendants via ``mmd_bone_index``.

    Returns:
        Summary dict containing created/reused accumulator nodes and skipped
        morph metadata records.
    """
    result = {
        "success": True,
        "accumulator_nodes": [],
        "created": 0,
        "reused": 0,
        "contributions": 0,
        "skipped": [],
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
    _append_group_morph_contributions(
        contributions_by_joint,
        list(_iter_group_morph_nodes(root_group)),
        bone_morph_nodes,
        joints_by_index,
        result["skipped"],
    )
    if not contributions_by_joint:
        result["skipped"].append("no_bone_morph_contributions")
        return result

    for contributions in contributions_by_joint.values():
        contributions.sort(
            key=lambda item: (
                item["morph_order"],
                item.get("group_morph_node") or "",
                item["morph_node"],
            )
        )

    existing_by_joint = _collect_existing_accumulators()
    for joint, contributions in contributions_by_joint.items():
        node = existing_by_joint.get(joint)
        if node and cmds.objExists(node):
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


def _iter_group_morph_nodes(root_group: str) -> Iterable[str]:
    yield from _iter_morph_nodes(root_group, "group", "mmd_group_morph_offsets_json")


def _iter_morph_nodes(root_group: str, morph_type: str, required_attr: str) -> Iterable[str]:
    for metadata in iter_morph_network_metadata(
        root_group=root_group,
        morph_types={morph_type},
        required_attrs=(required_attr,),
        enforce_model_root_scope=False,
    ):
        yield metadata.node


def _collect_contributions_by_joint(
    morph_nodes: Iterable[str],
    joints_by_index: Dict[int, str],
    skipped: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    contributions_by_joint: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
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
            contributions_by_joint[joint].append(contribution)

    return dict(contributions_by_joint)


def _append_group_morph_contributions(
    contributions_by_joint: Dict[str, List[Dict[str, Any]]],
    group_morph_nodes: Iterable[str],
    bone_morph_nodes: Iterable[str],
    joints_by_index: Dict[int, str],
    skipped: List[str],
) -> None:
    bone_nodes_by_morph_index = _collect_morph_nodes_by_index(bone_morph_nodes)
    for group_node in group_morph_nodes:
        group_offsets = _parse_group_offsets_json(group_node)
        if group_offsets is None:
            skipped.append(f"invalid_group_offsets:{group_node}")
            continue

        group_order = _get_morph_order(group_node)
        for group_offset in group_offsets:
            if not isinstance(group_offset, dict) or "morph_index" not in group_offset:
                skipped.append(f"invalid_group_offset:{group_node}")
                continue
            try:
                target_morph_index = int(group_offset["morph_index"])
                group_rate = float(group_offset.get("morph_rate", 0.0))
            except Exception:
                skipped.append(f"invalid_group_offset:{group_node}")
                continue

            target_node = bone_nodes_by_morph_index.get(target_morph_index)
            if target_node is None:
                skipped.append(f"group_reference_not_bone:{group_node}:{target_morph_index}")
                continue

            offsets = _parse_offsets_json(target_node)
            if offsets is None:
                skipped.append(f"invalid_offsets:{target_node}")
                continue
            for offset in offsets:
                contribution = _offset_to_contribution(target_node, group_order, offset)
                if contribution is None:
                    skipped.append(f"invalid_offset:{target_node}")
                    continue
                joint = joints_by_index.get(contribution["bone_index"])
                if joint is None:
                    skipped.append(f"missing_joint:{target_node}:{contribution['bone_index']}")
                    continue
                contribution["group_morph_node"] = group_node
                contribution["group_morph_rate"] = group_rate
                contributions_by_joint.setdefault(joint, []).append(contribution)


def _collect_morph_nodes_by_index(morph_nodes: Iterable[str]) -> Dict[int, str]:
    nodes_by_index: Dict[int, str] = {}
    for morph_node in morph_nodes:
        if not cmds.attributeQuery("mmd_morph_index", node=morph_node, exists=True):
            continue
        try:
            morph_index = int(cmds.getAttr(f"{morph_node}.mmd_morph_index"))
        except Exception:
            continue
        nodes_by_index.setdefault(morph_index, morph_node)
    return nodes_by_index


def _parse_offsets_json(morph_node: str) -> Optional[List[Dict[str, Any]]]:
    return parse_morph_offsets_json(morph_node, "mmd_bone_morph_offsets_json")


def _parse_group_offsets_json(morph_node: str) -> Optional[List[Dict[str, Any]]]:
    return parse_morph_offsets_json(morph_node, "mmd_group_morph_offsets_json")


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


def _collect_existing_accumulators() -> Dict[str, str]:
    accumulators: Dict[str, str] = {}
    for node in cmds.ls(type=ACCUM_NODE_TYPE) or []:
        if not cmds.attributeQuery("mmd_target_joint", node=node, exists=True):
            continue
        try:
            joint = cmds.getAttr(f"{node}.mmd_target_joint") or ""
        except Exception:
            continue
        if joint and cmds.objExists(joint):
            accumulators[joint] = node
    return accumulators


def _create_accumulator(joint: str) -> Optional[str]:
    node_name = f"{joint.split('|')[-1]}_boneMorphAccum"
    try:
        return cmds.createNode(ACCUM_NODE_TYPE, name=node_name)
    except Exception as exc:
        logger.warning("Failed to create %s for %s: %s", ACCUM_NODE_TYPE, joint, exc)
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
        _connect_contribution_weight(node, slot, contribution, f"{prefix}.weight")


def _connect_contribution_weight(
    accumulator_node: str,
    slot: int,
    contribution: Dict[str, Any],
    destination: str,
) -> None:
    group_node = contribution.get("group_morph_node")
    if not group_node:
        _connect_if_needed(str(contribution["weight_source"]), destination, force=True)
        return

    multiplier = _group_weight_multiplier_node(accumulator_node, slot)
    if not cmds.objExists(multiplier):
        multiplier = cmds.createNode("multiplyDivide", name=multiplier)
    cmds.setAttr(f"{multiplier}.operation", 1)
    cmds.setAttr(f"{multiplier}.input2X", float(contribution.get("group_morph_rate", 0.0)))
    _connect_if_needed(f"{group_node}.weight", f"{multiplier}.input1X", force=True)
    _connect_if_needed(f"{multiplier}.outputX", destination, force=True)


def _group_weight_multiplier_node(accumulator_node: str, slot: int) -> str:
    return f"{_safe_node_token(accumulator_node)}_contribution{slot}_groupWeight"


def _safe_node_token(node: str) -> str:
    token = node.split("|")[-1]
    for char in (":", ".", "[", "]"):
        token = token.replace(char, "_")
    return token


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
    joint_attr = f"{joint}.{attr_kind}"
    output_name = f"output{attr_kind.capitalize()}"
    base_name = f"base{attr_kind.capitalize()}"
    for source in cmds.listConnections(joint_attr, s=True, d=False, p=True) or []:
        source_node, source_attr = _split_plug(source)
        if source_node and cmds.nodeType(source_node) == "mmdAppend" and source_attr.startswith(output_name):
            return f"{source_node}.{base_name}"
    return joint_attr


def _reroute_compound(destination: str, base_attr: str, output_attr: str, attr_kind: str) -> None:
    if not _is_connected(output_attr, destination):
        _copy_current_compound_value(destination, base_attr)

    for source in cmds.listConnections(destination, s=True, d=False, p=True) or []:
        if _same_source(source, output_attr):
            continue
        _disconnect_and_reconnect(source, destination, base_attr)

    for axis in ("X", "Y", "Z"):
        dst_axis = f"{destination}{axis}"
        base_axis = f"{base_attr}{axis}"
        output_axis = f"{output_attr}{axis}"
        for source in cmds.listConnections(dst_axis, s=True, d=False, p=True) or []:
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
