"""Runtime-rig scene helpers for VMD runtime bake conversion."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

import maya.cmds as cmds

from .vmd_context import VmdRuntimeRigContext


MMD_APPEND_NODE_TYPES = ("mmdAppend",)
MMD_CCD_IK_NODE_TYPES = ("mmdCcdIk",)


def _ls_nodes_of_types(node_types: Tuple[str, ...]) -> List[str]:
    """List Maya nodes for all available type names, ignoring unavailable types."""
    nodes: List[str] = []
    seen = set()
    try:
        available_types = set(cmds.allNodeTypes() or [])
    except Exception:
        available_types = set()
    for node_type in node_types:
        if available_types and node_type not in available_types:
            continue
        try:
            typed_nodes = cmds.ls(type=node_type) or []
        except Exception:
            typed_nodes = []
        for node in typed_nodes:
            if node not in seen:
                nodes.append(node)
                seen.add(node)
    return nodes


def _ls_mmd_append_nodes() -> List[str]:
    return _ls_nodes_of_types(MMD_APPEND_NODE_TYPES)


def _ls_mmd_ccd_ik_nodes() -> List[str]:
    return _ls_nodes_of_types(MMD_CCD_IK_NODE_TYPES)


def runtime_bake_mapped_joint_names(bone_name_mapping: Dict[str, str]) -> set[str]:
    """Return short and long Maya names for joints targeted by runtime bake."""
    joints: set[str] = set()
    for joint in bone_name_mapping.values():
        if not joint or not cmds.objExists(joint):
            continue
        joints.add(joint)
        for long_name in cmds.ls(joint, long=True) or []:
            joints.add(long_name)
    return joints


def node_name_in_set(node: str, names: set[str]) -> bool:
    """Return True if a Maya node matches any short or long name in the set."""
    if node in names:
        return True
    return any(long_name in names for long_name in cmds.ls(node, long=True) or [])


def node_has_mapped_destination(
    node: str,
    attrs: Optional[Tuple[str, ...]],
    mapped_joints: set[str],
) -> bool:
    """Return True when node output connections target one of the mapped joints."""
    plugs = [f"{node}.{attr}" for attr in attrs] if attrs else [node]
    for plug in plugs:
        for destination in cmds.listConnections(plug, s=False, d=True, p=True) or []:
            destination_node = destination.split(".", 1)[0]
            if node_name_in_set(destination_node, mapped_joints):
                return True
    return False


def disconnect_node_output_connections(node: str, attrs: Tuple[str, ...]) -> int:
    """Disconnect output connections from the given attrs and return the count."""
    disconnected = 0
    for attr in attrs:
        output_plug = f"{node}.{attr}"
        destinations = cmds.listConnections(output_plug, s=False, d=True, p=True) or []
        for destination in destinations:
            sources = cmds.listConnections(destination, s=True, d=False, p=True) or []
            for source in sources:
                if not source.startswith(output_plug):
                    continue
                try:
                    cmds.disconnectAttr(source, destination)
                    disconnected += 1
                except Exception:
                    pass
    return disconnected


def disable_mmd_rig_constraints_for_runtime_bake(context: VmdRuntimeRigContext) -> None:
    """Disable live MMD rig outputs that would double-evaluate runtime bake."""
    mapped_joints = runtime_bake_mapped_joint_names(dict(context.bone_name_mapping))
    disconnected = 0
    ik_nodes_for_runtime = set()
    for node in _ls_mmd_append_nodes():
        if not node_has_mapped_destination(
            node,
            ("outputRotate", "outputTranslate"),
            mapped_joints,
        ):
            continue
        disconnected += disconnect_node_output_connections(
            node,
            ("outputRotate", "outputTranslate"),
        )
    for node in _ls_mmd_ccd_ik_nodes():
        if not node_has_mapped_destination(node, ("outputRotate",), mapped_joints):
            continue
        ik_nodes_for_runtime.add(node)
        disconnected += disconnect_node_output_connections(node, ("outputRotate",))
    if disconnected:
        context.logger.info(f"Disconnected {disconnected} live rig output connections for runtime bake")

    constraints = cmds.ls("*.mmd_grant_constraint", objectsOnly=True) or []
    disabled = 0
    for constraint in constraints:
        if not node_has_mapped_destination(constraint, None, mapped_joints):
            continue
        try:
            if cmds.attributeQuery("nodeState", node=constraint, exists=True):
                cmds.setAttr(f"{constraint}.nodeState", 2)
                disabled += 1
            elif cmds.attributeQuery("envelope", node=constraint, exists=True):
                cmds.setAttr(f"{constraint}.envelope", 0)
                disabled += 1
        except Exception as e:
            context.logger.debug(f"failed to disable MMD grant constraint {constraint}: {e}")

    if disabled:
        context.logger.info(f"Disabled {disabled} MMD append constraints for runtime bake")

    ik_disabled = 0
    for node in ik_nodes_for_runtime:
        try:
            for plug in cmds.listConnections(f"{node}.enabled", s=True, d=False, p=True) or []:
                cmds.disconnectAttr(plug, f"{node}.enabled")
            cmds.setAttr(f"{node}.enabled", False)
            ik_disabled += 1
        except Exception as e:
            context.logger.debug(f"failed to disable mmdCcdIk solver {node}: {e}")

    if ik_disabled:
        context.logger.info(f"Turned off {ik_disabled} mmdCcdIk solvers for runtime bake")


def has_live_mmd_rig_for_runtime_target(logger) -> bool:
    """Return whether live MMD rig outputs are connected to any joint."""

    def _output_rotate_connected_to_joint(node: str) -> bool:
        try:
            destinations = cmds.listConnections(f"{node}.outputRotate", s=False, d=True, p=True) or []
        except Exception as e:
            logger.debug(f"failed to inspect {node}.outputRotate connections: {e}")
            return False

        for destination in destinations:
            destination_node, _, destination_attr = destination.rpartition(".")
            if not destination_node or destination_attr not in {
                "rotate",
                "rotateX",
                "rotateY",
                "rotateZ",
            }:
                continue
            try:
                if cmds.nodeType(destination_node) == "joint":
                    return True
            except Exception as e:
                logger.debug(f"failed to inspect destination node type {destination_node}: {e}")
        return False

    for node in _ls_mmd_ccd_ik_nodes() + _ls_mmd_append_nodes():
        if _output_rotate_connected_to_joint(node):
            return True

    return False


def native_ik_handle_targets_mapped_joint(
    handle: str,
    mapped_joints: set[str],
    link_joint_resolver: Callable[[str], List[str]],
) -> bool:
    """Return whether a native Maya IK handle targets any mapped runtime joint."""
    if node_has_mapped_destination(handle, None, mapped_joints):
        return True
    for joint in link_joint_resolver(handle):
        if node_name_in_set(joint, mapped_joints):
            return True
    return False


def restore_joints_to_bind_pose_for_runtime_bake(context: VmdRuntimeRigContext) -> None:
    """Clear live-rig values and restore mapped joints to bind pose before runtime bake."""
    restored = 0
    for vmd_bone_name, joint in context.bone_name_mapping.items():
        if not cmds.objExists(joint):
            continue

        for attr in context.runtime_joint_attrs():
            plug = f"{joint}.{attr}"
            for source in cmds.listConnections(plug, s=True, d=False, p=True) or []:
                try:
                    cmds.disconnectAttr(source, plug)
                except Exception:
                    pass

        bind_translate = context.bone_bind_poses.get(vmd_bone_name)
        try:
            if bind_translate is not None:
                cmds.setAttr(
                    f"{joint}.translate",
                    float(bind_translate[0]),
                    float(bind_translate[1]),
                    float(bind_translate[2]),
                )
            cmds.setAttr(f"{joint}.rotate", 0.0, 0.0, 0.0)
            restored += 1
        except Exception as e:
            context.logger.debug(f"failed to restore bind pose for runtime bake {joint}: {e}")

    if restored:
        context.logger.info(f"Restored {restored} joints to bind pose for runtime bake")
