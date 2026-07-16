"""IK enabled-state animation helpers for VMD conversion."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Set, Union

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core.namespace_utils import NamespaceUtils
from .vmd_context import VmdIkEnabledAnimationContext
from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes


def node_namespace(node: str) -> str:
    """Return the namespace of a Maya node leaf name."""
    return NamespaceUtils.get_namespace_from_node(node) or ""


def _long_names(nodes) -> Set[str]:
    result = set()
    for node in nodes or []:
        try:
            selection = om.MGlobal.getSelectionListByName(node)
        except RuntimeError:
            try:
                selection = om.MGlobal.getSelectionListByName(f"*|{node}")
            except RuntimeError:
                continue

        for index in range(selection.length()):
            try:
                result.add(selection.getDagPath(index).fullPathName())
            except TypeError:
                continue
    return result


def root_owned_joints(target_model: str) -> Set[str]:
    """Return the explicit root's joint descendants as stable DAG paths."""
    if not target_model or not cmds.objExists(target_model):
        return set()
    joints = cmds.listRelatives(
        target_model,
        allDescendents=True,
        type="joint",
        fullPath=True,
    ) or []
    if cmds.nodeType(target_model) == "joint":
        joints.append(target_model)
    return _long_names(joints)


def ik_node_is_owned_by_root(
    node: str,
    target_model: str,
    owned_joints: Optional[Set[str]] = None,
) -> bool:
    """Prove IK ownership through its direct connections to root-owned joints."""
    root_joints = owned_joints if owned_joints is not None else root_owned_joints(target_model)
    if not root_joints:
        return False
    connected_joints = _long_names(
        cmds.listConnections(node, source=True, destination=True, type="joint") or []
    )
    return bool(connected_joints) and connected_joints.issubset(root_joints)


def collect_ik_nodes_by_bone_name(
    target_namespace: Optional[str] = None,
    namespace_for_node: Callable[[str], str] = node_namespace,
    target_model: Optional[str] = None,
) -> Dict[str, str]:
    """Collect mmdCcdIk nodes keyed by PMX IK bone name."""
    nodes: Dict[str, str] = {}
    owned_joints = root_owned_joints(target_model) if target_model else None
    for node in _ls_mmd_ccd_ik_nodes():
        if target_model:
            if not ik_node_is_owned_by_root(node, target_model, owned_joints):
                continue
        elif target_namespace and namespace_for_node(node) != target_namespace:
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
    target_model: Optional[str] = None,
) -> None:
    """Apply VMD IK show/hide property frames to mmdCcdIk.enabled."""
    context = _resolve_ik_enabled_animation_context(converter_or_context)
    if target_model:
        ik_nodes = context.collect_ik_nodes_by_bone_name(
            target_namespace,
            target_model=target_model,
        )
    else:
        # Preserve compatibility with direct contexts whose callback predates
        # explicit root scoping and accepts only target_namespace.
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
