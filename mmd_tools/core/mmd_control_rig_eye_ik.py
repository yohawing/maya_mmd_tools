"""Owned, head-space parallel gaze target for the combined-eye FK control."""
import json
import math

from .mmd_control_rig_builder import (
    MmdControlRigBuildError, read_mmd_control_rig_metadata, _write_metadata,
    _create_control_curve, _color_control,
)
from .mmd_control_rig_basis import matrix_from_quaternion
from .mmd_control_rig_arm_ik import (
    _node, _uuid, _ref, _transaction, _numeric_equal, _match_world_pose,
)


def _plug(cmds, ref):
    return _node(cmds, ref["nodeUuid"]) + "." + ref["attribute"]


def _graph(cmds, row):
    """Inventory exact helper edges, permitting only target TR animation."""
    result = {}
    for uuid in row["nodes"]:
        node = _node(cmds, uuid)
        incoming, outgoing = [], []
        pairs = cmds.listConnections(node, source=True, destination=False, plugs=True, connections=True) or []
        for destination, source in zip(pairs[::2], pairs[1::2]):
            attr = cmds.attributeName(destination, long=True)
            if uuid == row["targetUuid"] and attr.startswith(("translate", "rotate")):
                continue
            incoming.append([_ref(cmds, source), _ref(cmds, destination)])
        pairs = cmds.listConnections(node, source=False, destination=True, plugs=True, connections=True) or []
        for source, destination in zip(pairs[::2], pairs[1::2]):
            edge = [_ref(cmds, source), _ref(cmds, destination)]
            if not any(edge == [item["outputRef"], item["controlRef"]] for item in row["inputs"]):
                outgoing.append(edge)
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or [] if cmds.objectType(node, isAType="dagNode") else []
        result[uuid] = {
            "type": cmds.nodeType(node), "parents": [_uuid(cmds, n) for n in parents],
            "incoming": sorted(incoming, key=lambda edge: json.dumps(edge, sort_keys=True)),
            "outgoing": sorted(outgoing, key=lambda edge: json.dumps(edge, sort_keys=True)),
        }
    return result


def _curve_input(cmds, plug):
    writer = cmds.connectionInfo(plug, sourceFromDestination=True)
    if writer:
        node = writer.split(".", 1)[0]
        if cmds.nodeType(node) not in {"animCurveTA", "animCurveTL"}:
            raise MmdControlRigBuildError("Eye IK requires direct time animation: " + plug)
    return writer


def validate_eye_ik(cmds, metadata):
    """Validate the saved gaze graph and its original eye append ownership."""
    row = (metadata or {}).get("eyeIk")
    if not row:
        raise MmdControlRigBuildError("Eye IK is not built")
    owned = {entry["uuid"] for entry in metadata["nodes"]}
    if any(uuid not in owned for uuid in row["nodes"]) or _graph(cmds, row) != row["graph"]:
        raise MmdControlRigBuildError("Eye IK helper ownership or connections changed")
    for item in row["inputs"]:
        source, control = _plug(cmds, item["outputRef"]), _plug(cmds, item["controlRef"])
        if bool(cmds.isConnected(source, control)) != bool(row["active"]):
            raise MmdControlRigBuildError("Eye IK control writer changed")
        if item.get("sourceRef"):
            _plug(cmds, item["sourceRef"])
    target = _node(cmds, row["targetUuid"])
    for channel in ("translate", "rotate"):
        for axis in "XYZ":
            _curve_input(cmds, target + "." + channel + axis)
    for item in row["static"]:
        node = _node(cmds, item["uuid"])
        for attr, value in item["values"].items():
            if cmds.listConnections(node + "." + attr, source=True, destination=False) or not _numeric_equal(cmds.getAttr(node + "." + attr), value):
                raise MmdControlRigBuildError("Eye IK static basis or append configuration changed")
    parent = cmds.listRelatives(_node(cmds, row["jointUuid"]), parent=True, fullPath=True) or []
    if len(parent) != 1 or _uuid(cmds, parent[0]) != row["parentUuid"]:
        raise MmdControlRigBuildError("Eye IK combined-eye parent changed")
    for source, destination in row["appendEdges"]:
        if not cmds.isConnected(_plug(cmds, source), _plug(cmds, destination)):
            raise MmdControlRigBuildError("Eye IK eye append source changed")
    return row


def _require_edit(metadata):
    if not metadata or metadata.get("state") != "EDIT" or metadata.get("owner") != "CONTROL_OWNED":
        raise MmdControlRigBuildError("Eye IK requires Control Rig EDIT ownership")


def _match_target(cmds, row):
    """Match gaze direction and roll from the current combined-eye FK pose."""
    from maya.api import OpenMaya as om
    joint = _node(cmds, row["jointUuid"])
    parent = _node(cmds, row["parentUuid"])
    jo = om.MEulerRotation(*[math.radians(v) for v in cmds.getAttr(joint + ".jointOrient")[0]]).asMatrix()
    rotate = om.MEulerRotation(*[math.radians(v) for v in cmds.getAttr(joint + ".rotate")[0]],
                              cmds.getAttr(joint + ".rotateOrder")).asMatrix()
    gaze = jo.inverse() * rotate * jo
    origin = om.MVector(cmds.getAttr(joint + ".translate")[0])
    point = origin + (om.MVector(0, 0, 1) * gaze) * row["distance"]
    matrix = list(gaze)
    matrix[12:15] = list(point)
    world = om.MMatrix(matrix) * om.MMatrix(cmds.xform(parent, query=True, worldSpace=True, matrix=True))
    _match_world_pose(cmds, _node(cmds, row["targetUuid"]), list(world))


def _connect(cmds, row):
    fresh = []
    for item in row["inputs"]:
        control = _plug(cmds, item["controlRef"])
        writer = _curve_input(cmds, control)
        fresh.append(dict(item, sourceRef=_ref(cmds, writer) if writer else None, value=cmds.getAttr(control)))
    _match_target(cmds, row)
    for item in fresh:
        control = _plug(cmds, item["controlRef"])
        if item["sourceRef"]:
            cmds.disconnectAttr(_plug(cmds, item["sourceRef"]), control)
        cmds.connectAttr(_plug(cmds, item["outputRef"]), control)
    row["inputs"], row["active"] = fresh, True


def enable_eye_ik(model_root, *, cmds_module=None):
    """Enable a matched gaze target without changing either eye append route."""
    from maya import cmds as maya_cmds
    from maya.api import OpenMaya as om
    from . import mmd_control_rig_motion as motion
    from .mmd_control_rig_analyzer import analyze_mmd_control_rig
    cmds = cmds_module or maya_cmds
    metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds)
    _require_edit(metadata)
    if metadata.get("eyeIk"):
        row = validate_eye_ik(cmds, metadata)
        if not row["active"]:
            with _transaction(cmds, "Enable Eye IK"):
                _connect(cmds, row)
                _write_metadata(cmds, model_root, metadata)
        return inspect_eye_ik(model_root, cmds_module=cmds)
    binding = metadata.get("bindings", {}).get("both_eyes")
    if not binding or binding["inputKind"] != "direct_channel":
        raise MmdControlRigBuildError("Eye IK requires a direct combined-eye FK control")
    joint = _node(cmds, binding["jointUuid"])
    control = _node(cmds, metadata["controls"]["both_eyes"])
    parent = cmds.listRelatives(joint, parent=True, fullPath=True)[0]
    _, journal, _ = motion._resolve_edit_journal(cmds, metadata)
    rows = [r for r in journal if r["control"].split(".", 1)[0] == control and r["control"].endswith(tuple("rotate"+a for a in "XYZ"))]
    if len(rows) != 3 or any(r.get("layerRoute") for r in rows):
        raise MmdControlRigBuildError("Eye IK requires ordinary combined-eye XYZ routes")
    for axis in "XYZ":
        _curve_input(cmds, control + ".rotate" + axis)
    if cmds.listConnections(control + ".rotateOrder", source=True, destination=False):
        raise MmdControlRigBuildError("Eye IK requires static control rotation order")
    static = []
    ancestors = [joint]
    while cmds.listRelatives(ancestors[-1], parent=True, fullPath=True):
        ancestors.append(cmds.listRelatives(ancestors[-1], parent=True, fullPath=True)[0])
    for node in ancestors:
        if cmds.listConnections(node + ".scale", source=True, destination=False) or not _numeric_equal(cmds.getAttr(node + ".scale")[0], (1,1,1)):
            raise MmdControlRigBuildError("Eye IK requires static unit scale")
        static.append({"uuid": _uuid(cmds, node), "values": {"scale": cmds.getAttr(node + ".scale")}})
    values = {}
    for attr in ("jointOrient", "rotateAxis", "rotateOrder", "translate"):
        if cmds.listConnections(joint + "." + attr, source=True, destination=False):
            raise MmdControlRigBuildError("Eye IK requires static joint orientation and position")
        values[attr] = cmds.getAttr(joint + "." + attr)
    if not _numeric_equal(values["rotateAxis"][0], (0,0,0)):
        raise MmdControlRigBuildError("Eye IK currently requires zero rotateAxis")
    static.append({"uuid": _uuid(cmds, joint), "values": values})
    static.append({"uuid": _uuid(cmds, control), "values": {"rotateOrder": cmds.getAttr(control + ".rotateOrder")}})
    append_edges = []
    eye_bindings = {b.mmd_name: b for b in analyze_mmd_control_rig(model_root, cmds_module=cmds).bones}
    for name in ("左目", "右目"):
        eye = eye_bindings.get(name)
        if not eye or eye.input_kind != "append_base":
            raise MmdControlRigBuildError("Eye IK requires existing left/right combined-eye append routes")
        append = eye.authored_plugs[0].split(".", 1)[0]
        if not cmds.isConnected(joint + ".rotate", append + ".sourceRotate"):
            raise MmdControlRigBuildError("Eye IK eye append is not driven by combined eyes")
        append_edges.append([_ref(cmds, joint + ".rotate"), _ref(cmds, append + ".sourceRotate")])
        for axis in "XYZ":
            append_edges.append([_ref(cmds, append + ".outputRotate" + axis), _ref(cmds, eye.joint + ".rotate" + axis)])
        static.append({"uuid": _uuid(cmds, append), "values": {"ratio": cmds.getAttr(append + ".ratio")}})
    with _transaction(cmds, "Enable Eye IK"):
        group = cmds.createNode("transform", name="eye_IK", parent=_node(cmds, metadata["controlGroupUuid"]))
        follow = cmds.parentConstraint(parent, group, maintainOffset=False)[0]
        helper = cmds.createNode("transform", name="eye_aim", parent=group)
        cmds.setAttr(helper + ".translate", *values["translate"][0])
        target = _create_control_curve(cmds, "eye_target", "both_eyes", .25)
        target = cmds.parent(target, group)[0]
        _color_control(cmds, target, 17)
        for attr in ("scaleX", "scaleY", "scaleZ", "visibility"):
            cmds.setAttr(target + "." + attr, lock=True, keyable=False)
        compose, mult, decompose = [cmds.createNode(kind) for kind in ("composeMatrix", "multMatrix", "decomposeMatrix")]
        basis = om.MMatrix(matrix_from_quaternion(metadata["authoringBases"]["both_eyes"]["quaternion"]))
        jo = om.MEulerRotation(*[math.radians(v) for v in values["jointOrient"][0]]).asMatrix()
        cmds.setAttr(mult + ".matrixIn[0]", *list(basis * jo), type="matrix")
        cmds.setAttr(mult + ".matrixIn[2]", *list(jo.inverse() * basis.inverse()), type="matrix")
        cmds.connectAttr(helper + ".rotate", compose + ".inputRotate")
        cmds.connectAttr(compose + ".outputMatrix", mult + ".matrixIn[1]")
        cmds.connectAttr(mult + ".matrixSum", decompose + ".inputMatrix")
        cmds.setAttr(decompose + ".inputRotateOrder", cmds.getAttr(control + ".rotateOrder"))
        row = {"active": False, "targetUuid": _uuid(cmds, target), "jointUuid": _uuid(cmds, joint),
               "parentUuid": _uuid(cmds, parent), "distance": 5.0, "static": static,
               "appendEdges": append_edges, "inputs": [
                   {"controlRef": _ref(cmds, control + ".rotate"+a), "sourceRef": None, "value": 0,
                    "outputRef": _ref(cmds, decompose + ".outputRotate"+a)} for a in "XYZ"]}
        _match_target(cmds, row)
        aim = cmds.aimConstraint(target, helper, aimVector=(0,0,1), upVector=(0,1,0),
                                 worldUpType="objectrotation", worldUpObject=target,
                                 worldUpVector=(0,1,0), maintainOffset=False)[0]
        for node, attrs in (
            (helper, ("translate", "rotateOrder", "scale")),
            (target, ("rotateOrder", "scale")),
            (mult, ("matrixIn[0]", "matrixIn[2]")),
            (decompose, ("inputRotateOrder",)),
            (compose, ("inputRotateOrder",)),
            (aim, ("aimVector", "upVector", "worldUpVector", "worldUpType", "offset")),
        ):
            static.append({"uuid": _uuid(cmds, node), "values": {attr: cmds.getAttr(node + "." + attr) for attr in attrs}})
        cmds.sets(target, add=_node(cmds, metadata["selectionSetUuid"]))
        owned = set(cmds.listRelatives(group, allDescendents=True, fullPath=True) or []) | {group, follow, aim, compose, mult, decompose}
        row["nodes"] = sorted({_uuid(cmds, n) for n in owned})
        for uuid in row["nodes"]:
            node = _node(cmds, uuid)
            metadata["nodes"].append({"uuid": uuid, "name": node})
            if not cmds.objectType(node, isAType="dagNode"):
                metadata["helperNodes"].append(uuid)
        _connect(cmds, row)
        row["graph"] = _graph(cmds, row)
        metadata["eyeIk"] = row
        _write_metadata(cmds, model_root, metadata)
    return inspect_eye_ik(model_root, cmds_module=cmds)


def disable_eye_ik(model_root, *, match_fk=False, cmds_module=None):
    """Restore exact FK sources, optionally keying the current gaze pose."""
    from maya import cmds as maya_cmds
    cmds = cmds_module or maya_cmds
    metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds)
    row = validate_eye_ik(cmds, metadata)
    if not row["active"]:
        return inspect_eye_ik(model_root, cmds_module=cmds)
    _require_edit(metadata)
    values = [cmds.getAttr(_plug(cmds, item["controlRef"])) for item in row["inputs"]]
    with _transaction(cmds, "Disable Eye IK"):
        for item, value in zip(row["inputs"], values):
            control = _plug(cmds, item["controlRef"])
            cmds.disconnectAttr(_plug(cmds, item["outputRef"]), control)
            if item["sourceRef"]:
                cmds.connectAttr(_plug(cmds, item["sourceRef"]), control)
            else:
                cmds.setAttr(control, item["value"])
            if match_fk:
                cmds.setKeyframe(control, time=cmds.currentTime(query=True), value=value)
                cmds.dgdirty(control)
        cmds.currentTime(cmds.currentTime(query=True), update=True)
        row["active"] = False
        _write_metadata(cmds, model_root, metadata)
    return inspect_eye_ik(model_root, cmds_module=cmds)


def inspect_eye_ik(model_root, *, cmds_module=None):
    """Return validated active state and the rename-stable target path."""
    from maya import cmds as maya_cmds
    cmds = cmds_module or maya_cmds
    metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds)
    if not metadata or not metadata.get("eyeIk"):
        return {"active": False, "target": None}
    row = validate_eye_ik(cmds, metadata)
    return dict(row, target=_node(cmds, row["targetUuid"]))
