"""IK enabled-state animation helpers for VMD conversion."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Union

import maya.cmds as cmds

from ..core.namespace_utils import NamespaceUtils
from .vmd_context import VmdIkEnabledAnimationContext
from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes


def node_namespace(node: str) -> str:
    """Return the namespace of a Maya node leaf name."""
    return NamespaceUtils.get_namespace_from_node(node) or ""


def collect_ik_nodes_by_bone_name(
    target_namespace: Optional[str] = None,
    namespace_for_node: Callable[[str], str] = node_namespace,
) -> Dict[str, str]:
    """Collect mmdCcdIk nodes keyed by PMX IK bone name."""
    nodes: Dict[str, str] = {}
    for node in _ls_mmd_ccd_ik_nodes():
        if target_namespace and namespace_for_node(node) != target_namespace:
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


def _resolve_ik_enabled_animation_context(
    converter_or_context: Union[Any, VmdIkEnabledAnimationContext],
) -> VmdIkEnabledAnimationContext:
    if isinstance(converter_or_context, VmdIkEnabledAnimationContext):
        return converter_or_context
    factory = getattr(converter_or_context, "_ik_enabled_animation_context", None)
    if callable(factory):
        return factory()
    return VmdIkEnabledAnimationContext(
        logger=converter_or_context.logger,
        collect_ik_nodes_by_bone_name=converter_or_context._collect_ik_nodes_by_bone_name,
        get_animation_frame_range=converter_or_context._get_animation_frame_range,
        vmd_frame_to_maya_time=converter_or_context.vmd_frame_to_maya_time,
    )


def apply_ik_enabled_animation(
    converter_or_context: Union[Any, VmdIkEnabledAnimationContext],
    vmd_data,
    target_namespace: Optional[str] = None,
) -> None:
    """Apply VMD IK show/hide property frames to mmdCcdIk.enabled."""
    context = _resolve_ik_enabled_animation_context(converter_or_context)
    ik_nodes = context.collect_ik_nodes_by_bone_name(target_namespace)
    if not ik_nodes:
        return

    property_frames = sorted(
        list(getattr(vmd_data, "ik_show_hide_frames", []) or []),
        key=lambda f: int(getattr(f, "frame_number", 0)),
    )
    default_nodes = set(ik_nodes.values()) if getattr(vmd_data, "bone_frames", None) else set()

    if property_frames or default_nodes:
        min_frame, _max_frame = context.get_animation_frame_range(vmd_data)
        min_time = context.vmd_frame_to_maya_time(min_frame)
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
                    time=context.vmd_frame_to_maya_time(frame_number),
                    value=int(value),
                )
                keyed += 1
        if keyed:
            context.logger.info(f"Applied {keyed} keys of VMD IK state to mmdCcdIk.enabled")
        return

    if default_nodes:
        context.logger.info(f"No VMD IK state found; set active mmdCcdIk.enabled default ON: {len(default_nodes)} nodes")
