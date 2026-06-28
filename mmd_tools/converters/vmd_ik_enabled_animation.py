"""IK enabled-state animation helpers for VMD conversion."""

from __future__ import annotations

from typing import Dict, Optional

import maya.cmds as cmds

from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes


def node_namespace(node: str) -> str:
    """Return the namespace of a Maya node leaf name."""
    leaf = node.split("|")[-1]
    if ":" not in leaf:
        return ""
    return leaf.rsplit(":", 1)[0].lstrip(":")


def collect_ik_nodes_by_bone_name(converter, target_namespace: Optional[str] = None) -> Dict[str, str]:
    """Collect mmdCcdIk nodes keyed by PMX IK bone name."""
    nodes: Dict[str, str] = {}
    for node in _ls_mmd_ccd_ik_nodes():
        if target_namespace and converter._node_namespace(node) != target_namespace:
            continue
        name = ""
        if cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
            try:
                name = cmds.getAttr(f"{node}.mmd_ik_bone_name") or ""
            except Exception:
                name = ""
        if name:
            nodes[name] = node
    return nodes


def apply_ik_enabled_animation(converter, vmd_data, target_namespace: Optional[str] = None) -> None:
    """Apply VMD IK show/hide property frames to mmdCcdIk.enabled."""
    ik_nodes = converter._collect_ik_nodes_by_bone_name(target_namespace)
    if not ik_nodes:
        return

    property_frames = sorted(
        list(getattr(vmd_data, "ik_show_hide_frames", []) or []),
        key=lambda f: int(getattr(f, "frame_number", 0)),
    )
    default_nodes = set(ik_nodes.values()) if getattr(vmd_data, "bone_frames", None) else set()

    if property_frames or default_nodes:
        min_frame, _max_frame = converter._get_animation_frame_range(vmd_data)
        min_time = converter.vmd_frame_to_maya_time(min_frame)
        for node in (ik_nodes.values() if property_frames else default_nodes):
            cmds.setAttr(f"{node}.enabled", True)
            cmds.setKeyframe(node, attribute="enabled", time=min_time, value=1)

    if property_frames:
        keyed = 0
        for frame in property_frames:
            frame_number = int(getattr(frame, "frame_number", 0))
            for ik_name, show_flag in getattr(frame, "ik_states", []) or []:
                node = ik_nodes.get(ik_name)
                if not node:
                    continue
                value = bool(show_flag)
                cmds.setAttr(f"{node}.enabled", value)
                cmds.setKeyframe(
                    node,
                    attribute="enabled",
                    time=converter.vmd_frame_to_maya_time(frame_number),
                    value=int(value),
                )
                keyed += 1
        if keyed:
            converter.logger.info(f"Applied {keyed} keys of VMD IK state to mmdCcdIk.enabled")
        return

    if default_nodes:
        converter.logger.info(f"No VMD IK state found; set active mmdCcdIk.enabled default ON: {len(default_nodes)} nodes")
