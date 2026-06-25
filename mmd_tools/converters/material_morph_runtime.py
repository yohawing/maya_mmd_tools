"""PMX material morph metadata から runtime DG グラフを構築する。"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from maya import cmds

from mmd_tools.core.constants import ATTR_MMD_MATERIAL_INDEX
from mmd_tools.core.logger import get_logger


logger = get_logger(__name__)

EVAL_NODE_TYPE = "mmdMaterialMorphEval"


def build_material_morph_graph(root_group: str) -> Dict[str, Any]:
    """Build or refresh PMX material morph evaluator nodes for shaders under *root_group*.

    Args:
        root_group: Imported MMD model root group.

    Returns:
        Summary dict.
    """
    result: Dict[str, Any] = {
        "success": True,
        "evaluator_nodes": [],
        "created": 0,
        "reused": 0,
        "contributions": 0,
        "skipped": [],
    }
    if not root_group or not cmds.objExists(root_group):
        result["success"] = False
        result["skipped"].append("root_group_missing")
        return result

    shaders_by_index = _collect_shaders_by_material_index(root_group)
    if not shaders_by_index:
        result["skipped"].append("no_indexed_shaders")
        return result

    contributions_by_shader = _collect_contributions_by_shader(
        _iter_material_morph_nodes(root_group),
        shaders_by_index,
        result["skipped"],
    )
    if not contributions_by_shader:
        result["skipped"].append("no_material_morph_contributions")
        return result

    existing_by_shader = _collect_existing_evaluators()
    for shader, contributions in contributions_by_shader.items():
        node = existing_by_shader.get(shader)
        if node and cmds.objExists(node):
            result["reused"] += 1
        else:
            node = _create_evaluator(shader)
            if not node:
                result["success"] = False
                result["skipped"].append(f"create_failed:{shader}")
                continue
            result["created"] += 1

        _mark_evaluator(node, shader)
        _refresh_contributions(node, contributions)
        _reroute_shader_color(shader, node)
        result["evaluator_nodes"].append(node)
        result["contributions"] += len(contributions)

    return result


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def _collect_shaders_by_material_index(root_group: str) -> Dict[int, str]:
    """Find shader nodes with mmd_material_index under root_group's meshes."""
    shapes = cmds.listRelatives(root_group, allDescendents=True, type="mesh", fullPath=True) or []
    if not shapes:
        return {}

    shading_groups = set()
    for shape in shapes:
        sgs = cmds.listConnections(shape, type="shadingEngine") or []
        shading_groups.update(sgs)

    shaders_by_index: Dict[int, str] = {}
    for sg in shading_groups:
        shader_list = cmds.ls(cmds.listConnections(sg) or [], materials=True) or []
        for shader in shader_list:
            if not cmds.attributeQuery(ATTR_MMD_MATERIAL_INDEX, node=shader, exists=True):
                continue
            try:
                mat_index = int(cmds.getAttr(f"{shader}.{ATTR_MMD_MATERIAL_INDEX}"))
            except Exception:
                continue
            shaders_by_index[mat_index] = shader
    return shaders_by_index


def _iter_material_morph_nodes(root_group: str) -> Iterable[str]:
    namespace = _namespace_from_node(root_group)
    for node in cmds.ls(type="network") or []:
        if namespace is not None and _namespace_from_node(node) != namespace:
            continue
        if not cmds.attributeQuery("mmd_morph_type", node=node, exists=True):
            continue
        try:
            if cmds.getAttr(f"{node}.mmd_morph_type") != "material":
                continue
        except Exception:
            continue
        if not cmds.attributeQuery("mmd_material_morph_offsets_json", node=node, exists=True):
            continue
        yield node


def _namespace_from_node(node: str) -> Optional[str]:
    short_name = node.split("|")[-1]
    if ":" not in short_name:
        return None
    return short_name.rsplit(":", 1)[0]


def _collect_contributions_by_shader(
    morph_nodes: Iterable[str],
    shaders_by_index: Dict[int, str],
    skipped: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    contributions_by_shader: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
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
            shader = shaders_by_index.get(contribution["material_index"])
            if shader is None:
                # material_index == -1 means "all materials"
                if contribution["material_index"] == -1:
                    for shader in shaders_by_index.values():
                        contributions_by_shader[shader].append(contribution)
                else:
                    skipped.append(f"missing_shader:{morph_node}:{contribution['material_index']}")
                continue
            contributions_by_shader[shader].append(contribution)

    for contributions in contributions_by_shader.values():
        contributions.sort(key=lambda c: (c["morph_order"], c["morph_node"]))
    return dict(contributions_by_shader)


def _parse_offsets_json(morph_node: str) -> Optional[List[Dict[str, Any]]]:
    try:
        raw = cmds.getAttr(f"{morph_node}.mmd_material_morph_offsets_json") or "[]"
        offsets = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(offsets, list):
        return None
    return offsets


def _get_morph_order(morph_node: str) -> int:
    if cmds.attributeQuery("mmd_morph_index", node=morph_node, exists=True):
        try:
            return int(cmds.getAttr(f"{morph_node}.mmd_morph_index"))
        except Exception:
            pass
    return 0


def _offset_to_contribution(
    morph_node: str,
    morph_order: int,
    offset: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(offset, dict) or "material_index" not in offset:
        return None
    try:
        diffuse = offset.get("diffuse", [0.0, 0.0, 0.0, 0.0])
        if len(diffuse) < 3:
            return None
        return {
            "morph_node": morph_node,
            "morph_order": int(morph_order),
            "material_index": int(offset["material_index"]),
            "operation_type": int(offset.get("operation_type", 1)),
            "diffuse_rgb": (float(diffuse[0]), float(diffuse[1]), float(diffuse[2])),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Evaluator node management
# ---------------------------------------------------------------------------

def _collect_existing_evaluators() -> Dict[str, str]:
    evaluators: Dict[str, str] = {}
    for node in cmds.ls(type=EVAL_NODE_TYPE) or []:
        if not cmds.attributeQuery("mmd_target_shader", node=node, exists=True):
            continue
        try:
            shader = cmds.getAttr(f"{node}.mmd_target_shader") or ""
        except Exception:
            continue
        if shader and cmds.objExists(shader):
            evaluators[shader] = node
    return evaluators


def _create_evaluator(shader: str) -> Optional[str]:
    node_name = f"{shader.split('|')[-1]}_materialMorphEval"
    try:
        return cmds.createNode(EVAL_NODE_TYPE, name=node_name)
    except Exception as exc:
        logger.warning("Failed to create %s for %s: %s", EVAL_NODE_TYPE, shader, exc)
        return None


def _mark_evaluator(node: str, shader: str) -> None:
    if not cmds.attributeQuery("mmd_material_morph_eval", node=node, exists=True):
        cmds.addAttr(node, longName="mmd_material_morph_eval", attributeType="bool")
    cmds.setAttr(f"{node}.mmd_material_morph_eval", True)
    if not cmds.attributeQuery("mmd_target_shader", node=node, exists=True):
        cmds.addAttr(node, longName="mmd_target_shader", dataType="string")
    cmds.setAttr(f"{node}.mmd_target_shader", shader, type="string")


def _refresh_contributions(node: str, contributions: List[Dict[str, Any]]) -> None:
    for index in cmds.getAttr(f"{node}.contribution", multiIndices=True) or []:
        try:
            cmds.removeMultiInstance(f"{node}.contribution[{index}]", b=True)
        except Exception:
            pass

    for slot, contribution in enumerate(contributions):
        prefix = f"{node}.contribution[{slot}]"
        cmds.setAttr(f"{prefix}.morphOrder", int(contribution["morph_order"]))
        cmds.setAttr(f"{prefix}.operationType", int(contribution["operation_type"]))
        dr, dg, db = contribution["diffuse_rgb"]
        cmds.setAttr(f"{prefix}.diffuseOffset", dr, dg, db, type="double3")
        _connect_if_needed(f"{contribution['morph_node']}.weight", f"{prefix}.weight", force=True)


def _reroute_shader_color(shader: str, node: str) -> None:
    """Intercept shader.color through the evaluator node."""
    output_attr = f"{node}.outputDiffuse"
    base_attr = f"{node}.baseDiffuse"
    shader_attr = f"{shader}.color"

    if _is_connected(output_attr, shader_attr):
        return

    # Copy current color to baseDiffuse
    try:
        color = cmds.getAttr(shader_attr)[0]
        cmds.setAttr(base_attr, float(color[0]), float(color[1]), float(color[2]), type="double3")
    except Exception:
        logger.debug("Failed to read %s", shader_attr, exc_info=True)

    # Reroute any existing connections to baseDiffuse
    for source in cmds.listConnections(shader_attr, s=True, d=False, p=True) or []:
        if _same_source(source, output_attr):
            continue
        try:
            cmds.disconnectAttr(source, shader_attr)
        except Exception:
            pass
        _connect_if_needed(source, base_attr, force=True)

    for axis in ("R", "G", "B"):
        shader_axis = f"{shader}.color{axis}"
        base_axis = f"{base_attr}{axis[-1].lower()}"
        output_axis = f"{output_attr}{axis[-1].lower()}"
        for source in cmds.listConnections(shader_axis, s=True, d=False, p=True) or []:
            if _same_source(source, output_axis):
                continue
            try:
                cmds.disconnectAttr(source, shader_axis)
            except Exception:
                pass
            _connect_if_needed(source, base_axis, force=True)

    _connect_if_needed(output_attr, shader_attr, force=True)


# ---------------------------------------------------------------------------
# Connection utilities (shared pattern with bone_morph_runtime)
# ---------------------------------------------------------------------------

def _connect_if_needed(source: str, destination: str, force: bool = False) -> None:
    if _is_connected(source, destination):
        return
    cmds.connectAttr(source, destination, force=force)


def _is_connected(source: str, destination: str) -> bool:
    return any(
        _same_source(conn, source)
        for conn in cmds.listConnections(destination, s=True, d=False, p=True) or []
    )


def _same_source(left: str, right: str) -> bool:
    if left == right:
        return True
    left_long = cmds.ls(left, long=True) or []
    right_long = cmds.ls(right, long=True) or []
    return bool(left_long and right_long and left_long == right_long)
