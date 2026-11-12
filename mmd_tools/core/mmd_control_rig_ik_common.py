"""Shared node identity, ownership snapshots and pose matching for Control Rig IK."""

import json
import math
from contextlib import contextmanager
from uuid import uuid4

from .mmd_control_rig_builder import MmdControlRigBuildError


def _node(cmds, uuid):
    nodes = cmds.ls(uuid, long=True) or []
    if len(nodes) != 1:
        raise MmdControlRigBuildError("IK owned node is missing or ambiguous: " + uuid)
    return nodes[0]


def _uuid(cmds, node):
    return cmds.ls(node, uuid=True)[0]


def _ref(cmds, plug):
    node, attribute = plug.split(".", 1)
    return {"nodeUuid": _uuid(cmds, node), "attribute": attribute}


def resolve_ik_plug(cmds, ref):
    """Resolve a saved IK plug without relying on names or namespaces."""
    return _node(cmds, ref["nodeUuid"]) + "." + ref["attribute"]


def create_fk_storage(cmds, control, parent, name, static):
    """Keep detached quaternion XYZ curves grouped so Maya preserves their W."""
    storage = cmds.createNode("transform", name=name, parent=parent)
    order = cmds.getAttr(control + ".rotateOrder")
    cmds.setAttr(storage + ".rotateOrder", order)
    static.append({"uuid": _uuid(cmds, storage), "values": {"rotateOrder": order}})
    return storage


def release_fk_storage(cmds, row):
    """Release FK curve taps after the original control ownership is restored."""
    for item in row["inputs"]:
        if item.get("storageRef"):
            plug = resolve_ik_plug(cmds, item["storageRef"])
            source = cmds.connectionInfo(plug, sourceFromDestination=True)
            if source:
                cmds.disconnectAttr(source, plug)


@contextmanager
def _transaction(cmds, label):
    if not cmds.undoInfo(query=True, state=True):
        raise MmdControlRigBuildError("IK requires Maya Undo to be enabled")
    chunk_name = label + "_" + uuid4().hex
    cmds.undoInfo(openChunk=True, chunkName=chunk_name)
    try:
        yield
    except Exception:
        cmds.undoInfo(closeChunk=True)
        if cmds.undoInfo(query=True, undoName=True) == chunk_name:
            cmds.undo()
        raise
    else:
        cmds.undoInfo(closeChunk=True)


def snapshot_ik_graph(cmds, row, editable_uuids):
    """Record exact types, DAG parents and solver inputs; targets remain animatable."""
    result = {}
    editable = set(editable_uuids)
    for uuid in row["nodes"]:
        node = _node(cmds, uuid)
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or [] if cmds.objectType(node, isAType="dagNode") else []
        incoming = []
        pairs = cmds.listConnections(node, source=True, destination=False, plugs=True, connections=True) or []
        for destination, source in zip(pairs[::2], pairs[1::2]):
            attribute = cmds.attributeName(destination, long=True)
            if uuid in editable and attribute.startswith(("translate", "rotate")):
                continue
            incoming.append([_ref(cmds, source), _ref(cmds, destination)])
        outgoing = []
        pairs = cmds.listConnections(node, source=False, destination=True, plugs=True, connections=True) or []
        for source, destination in zip(pairs[::2], pairs[1::2]):
            edge = [_ref(cmds, source), _ref(cmds, destination)]
            if any(edge == [item["outputRef"], item["controlRef"]] for item in row["inputs"]):
                continue
            outgoing.append(edge)
        result[uuid] = {
            "outgoing": sorted(outgoing, key=lambda edge: json.dumps(edge, sort_keys=True)),
            "type": cmds.nodeType(node),
            "parents": [_uuid(cmds, parent) for parent in parents],
            "incoming": sorted(incoming, key=lambda edge: json.dumps(edge, sort_keys=True)),
        }
    return result


def _numeric_equal(left, right):
    if isinstance(left, (tuple, list)) and isinstance(right, (tuple, list)):
        return len(left) == len(right) and all(_numeric_equal(a, b) for a, b in zip(left, right))
    return abs(float(left) - float(right)) < 1e-7


def _match_world_pose(cmds, node, matrix, *, rotate_only=False):
    """Key only the current frame on existing curves; preserve all other keys."""
    from maya.api import OpenMaya as om

    parent = cmds.listRelatives(node, parent=True, fullPath=True)
    local = om.MMatrix(matrix)
    if parent:
        local = local * om.MMatrix(cmds.xform(parent[0], q=True, ws=True, matrix=True)).inverse()
    transform = om.MTransformationMatrix(local)
    rotation = transform.rotation()
    rotation.reorderIt(cmds.getAttr(node + ".rotateOrder"))
    translation = transform.translation(om.MSpace.kTransform)
    for channel, values in (("translate", translation), ("rotate", [math.degrees(value) for value in rotation])):
        if rotate_only and channel == "translate":
            continue
        for axis, value in zip("XYZ", values):
            plug = node + "." + channel + axis
            if cmds.connectionInfo(plug, sourceFromDestination=True):
                cmds.setKeyframe(plug, time=cmds.currentTime(q=True), value=value)
                cmds.dgdirty(plug)
            else:
                cmds.setAttr(plug, value)
    cmds.currentTime(cmds.currentTime(q=True), update=True)

