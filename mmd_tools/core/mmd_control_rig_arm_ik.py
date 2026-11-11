"""Owned three-joint arm IK for Control Rig EDIT, using Maya stock DG nodes."""

import math
from uuid import uuid4
import json
from contextlib import contextmanager

from .mmd_control_rig_builder import (
    MmdControlRigBuildError,
    _write_metadata,
    _create_control_curve,
    _color_control,
    read_mmd_control_rig_metadata,
)
from .mmd_control_rig_analyzer import rotation_composer_source
from .mmd_control_rig_basis import matrix_from_quaternion


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


def resolve_arm_plug(cmds, ref):
    """Resolve a saved arm plug without relying on names or namespaces."""
    return _node(cmds, ref["nodeUuid"]) + "." + ref["attribute"]


@contextmanager
def _transaction(cmds, label):
    if not cmds.undoInfo(query=True, state=True):
        raise MmdControlRigBuildError("arm IK requires Maya Undo to be enabled")
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


def validate_arm_ik(cmds, metadata, side):
    """Reject missing/replaced owned nodes or modified live input connections."""
    row = metadata.get("armIk", {}).get(side)
    if not row:
        raise MmdControlRigBuildError("arm IK is not built: " + side)
    owned = {item["uuid"] for item in metadata["nodes"]}
    for uuid in row["nodes"]:
        if uuid not in owned:
            raise MmdControlRigBuildError("arm IK ownership metadata is incomplete")
        _node(cmds, uuid)
    actual_graph = _graph_snapshot(cmds, row)
    if row.get("graph") != actual_graph:
        changed = [_node(cmds, uuid) for uuid in row["nodes"] if row.get("graph", {}).get(uuid) != actual_graph.get(uuid)]
        raise MmdControlRigBuildError("arm IK graph ownership or connections changed: " + ", ".join(changed))
    for uuid in (row["targetUuid"], row["poleUuid"]):
        node = _node(cmds, uuid)
        for channel in ("translate", "rotate"):
            for axis in "XYZ":
                writer = cmds.connectionInfo(node + "." + channel + axis, sourceFromDestination=True)
                if writer and cmds.nodeType(writer.split(".", 1)[0]) not in ("animCurveTA", "animCurveTL"):
                    raise MmdControlRigBuildError("arm IK wrist/pole require direct time animation curves")
    for item in row["inputs"]:
        if item["sourceRef"]:
            resolve_arm_plug(cmds, item["sourceRef"])
    for edge in row["edges"]:
        if not cmds.isConnected(resolve_arm_plug(cmds, edge[0]), resolve_arm_plug(cmds, edge[1])):
            raise MmdControlRigBuildError("arm IK graph connection was modified")
    if row["active"]:
        for item in row["inputs"]:
            if not cmds.isConnected(resolve_arm_plug(cmds, item["outputRef"]), resolve_arm_plug(cmds, item["controlRef"])):
                raise MmdControlRigBuildError("arm IK control writer was modified")
    else:
        for item in row["inputs"]:
            if cmds.isConnected(resolve_arm_plug(cmds, item["outputRef"]), resolve_arm_plug(cmds, item["controlRef"])):
                raise MmdControlRigBuildError("inactive arm IK bridge was reconnected")
    for item in row["static"]:
        node = _node(cmds, item["uuid"])
        for attribute, value in item["values"].items():
            if cmds.getAttr(node + "." + attribute) != value:
                # JSON changes tuples into lists after save/reload.
                if not _numeric_equal(cmds.getAttr(node + "." + attribute), value):
                    raise MmdControlRigBuildError("arm IK static joint configuration changed")
    return row


def _rotation_composers(cmds, joint, targets):
    """Walk the verified XYZ path from the joint back to the EDIT journal."""
    destination = joint + ".rotate"
    chain = []
    while True:
        node = rotation_composer_source(cmds, destination)
        if not node or node in chain:
            raise MmdControlRigBuildError("arm IK requires a supported XYZ rotation composer on " + destination)
        kind = cmds.nodeType(node)
        if kind == "mmdAppend" and cmds.getAttr(node + ".schemaMode") != 2:
            raise MmdControlRigBuildError("arm IK requires the base/source append schema")
        if kind == "mmdBoneMorphAccum" and cmds.getAttr(node + ".rotateOrder") != 0:
            raise MmdControlRigBuildError("arm IK requires XYZ bone morph rotation order")
        chain.append(node)
        destination = node + ".baseRotate"
        if targets == {destination + axis for axis in "XYZ"}:
            return list(reversed(chain))


def enable_arm_ik(model_root, side, *, cmds_module=None):
    """Match an independent wrist/pole solver to the current FK pose in EDIT."""
    from maya import cmds as maya_cmds
    from maya.api import OpenMaya as om
    from . import mmd_control_rig_motion as motion

    cmds = cmds_module or maya_cmds
    if side not in ("left", "right"):
        raise MmdControlRigBuildError("arm IK side must be left or right")
    metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds)
    if not metadata or metadata["state"] != "EDIT" or metadata.get("owner") != "CONTROL_OWNED":
        raise MmdControlRigBuildError("arm IK requires Control Rig EDIT")
    if metadata.get("armIk", {}).get(side):
        return _reenable(cmds, model_root, metadata, side)
    roles = [side + suffix for suffix in ("_arm", "_elbow", "_wrist")]
    joints = [_node(cmds, metadata["bindings"][role]["jointUuid"]) for role in roles]
    controls = [_node(cmds, metadata["controls"][role]) for role in roles]
    _, journal, _ = motion._resolve_edit_journal(cmds, metadata)
    for item in journal:
        if item["control"].split(".")[0] in controls and (
            item.get("layerRoute") or item.get("twistController") or item.get("fixedAxisTwist")
        ):
            raise MmdControlRigBuildError("arm IK does not support layered or twist control routes")
    composers = {}
    scale_nodes = set()
    for control in controls:
        if cmds.listConnections(control + ".rotateOrder", source=True, destination=False):
            raise MmdControlRigBuildError("arm IK requires static rotation order")
    for role, joint, control in zip(roles, joints, controls):
        for attribute in ("jointOrient", "rotateAxis", "rotateOrder"):
            if cmds.listConnections(joint + "." + attribute, source=True, destination=False):
                raise MmdControlRigBuildError("arm IK requires static joint orientation")
        kind = metadata["bindings"][role]["inputKind"]
        if kind in {"append_base", "bone_morph_base"}:
            rotations = {control + ".rotate" + axis for axis in "XYZ"}
            targets = {item["target"] for item in journal if item["control"] in rotations}
            if cmds.getAttr(joint + ".rotateOrder") != 0:
                raise MmdControlRigBuildError("arm IK requires XYZ composed bone rotation")
            composers[role] = _rotation_composers(cmds, joint, targets)
        elif kind != "direct_channel":
            raise MmdControlRigBuildError("arm IK does not support bone input route: " + kind)
        ancestors = [joint]
        while cmds.listRelatives(ancestors[-1], parent=True, fullPath=True):
            ancestors.append(cmds.listRelatives(ancestors[-1], parent=True, fullPath=True)[0])
        scale_nodes.update(ancestors)
        for node in ancestors:
            if cmds.listConnections(node + ".scale", source=True, destination=False):
                raise MmdControlRigBuildError("arm IK requires static unit scale")
            if any(abs(value - 1) > 1e-8 for value in cmds.getAttr(node + ".scale")[0]):
                raise MmdControlRigBuildError("arm IK currently requires unit scale")
    segments, driven = _segment_intermediates(cmds, metadata, joints)
    positions = [om.MVector(cmds.xform(node, q=True, ws=True, t=True)) for node in joints]
    shoulder, elbow, wrist = positions
    axis = wrist - shoulder
    if axis.length() < 1e-6:
        raise MmdControlRigBuildError("arm IK chain has zero reach")
    bend, straight = _bend_plane(cmds, joints[0], shoulder, elbow, wrist)
    inputs = []
    for control in controls:
        for axis_name in "XYZ":
            plug = control + ".rotate" + axis_name
            writer = cmds.connectionInfo(plug, sourceFromDestination=True)
            if writer:
                _require_fk_curve(cmds, writer, plug)
            inputs.append(
                {
                    "controlRef": _ref(cmds, plug),
                    "sourceRef": _ref(cmds, writer) if writer else None,
                    "value": cmds.getAttr(plug),
                }
            )
    previous_cycles = set(cmds.cycleCheck(all=True, list=True) or []) if composers or driven else set()
    with _transaction(cmds, "Enable Arm IK"):
        group = cmds.createNode("transform", name=side + "_arm_IK", parent=_node(cmds, metadata["controlGroupUuid"]))
        # Wrist/pole remain in world space while only the shoulder anchor follows torso.
        cmds.setAttr(group + ".inheritsTransform", False, lock=True)
        for channel in ("translate", "rotate", "scale"):
            for axis_name in "XYZ":
                cmds.setAttr(group + "." + channel + axis_name, lock=True)
        anchor = cmds.createNode("transform", name=side + "_shoulder_IK_space", parent=group)
        anchor_matrix = cmds.createNode("decomposeMatrix", name=side + "_shoulder_IK_parent")
        shoulder_parent = cmds.listRelatives(joints[0], parent=True, fullPath=True)[0]
        cmds.connectAttr(shoulder_parent + ".worldMatrix[0]", anchor_matrix + ".inputMatrix")
        cmds.connectAttr(anchor_matrix + ".outputTranslate", anchor + ".translate")
        cmds.connectAttr(anchor_matrix + ".outputRotate", anchor + ".rotate")
        helpers = []
        static = [
            {"uuid": _uuid(cmds, node), "values": {"scale": cmds.getAttr(node + ".scale")}} for node in sorted(scale_nodes)
        ]
        static.extend(
            {"uuid": _uuid(cmds, node), "values": {"rotateOrder": cmds.getAttr(node + ".rotateOrder")}} for node in controls
        )
        for role, joint in zip(roles, joints):
            helper = cmds.createNode("joint", name=role + "_IK_helper", parent=helpers[-1] if helpers else anchor)
            values = {}
            for attribute in ("jointOrient", "rotateAxis", "rotateOrder"):
                value = cmds.getAttr(joint + "." + attribute)
                values[attribute] = value
                if attribute == "rotateOrder":
                    cmds.setAttr(helper + "." + attribute, value)
                else:
                    cmds.setAttr(helper + "." + attribute, *value[0])
            values["scale"] = cmds.getAttr(joint + ".scale")
            static.append({"uuid": _uuid(cmds, joint), "values": values})
            cmds.xform(helper, ws=True, matrix=cmds.xform(joint, q=True, ws=True, matrix=True))
            helpers.append(helper)
        segment_nodes = _create_segment_offsets(cmds, joints, helpers, side, segments)
        target = _create_control_curve(cmds, side + "_wrist_IK", side + "_wrist", max((wrist - shoulder).length() * 0.08, 0.01))
        target = cmds.parent(target, group)[0]
        _color_control(cmds, target, 6 if side == "left" else 13)
        cmds.xform(target, ws=True, matrix=cmds.xform(joints[-1], q=True, ws=True, matrix=True))
        size = max((wrist - shoulder).length() * 0.04, 0.01)
        pole = cmds.curve(
            name=side + "_elbow_IK", degree=1, point=[(0, size, 0), (size, 0, 0), (0, -size, 0), (-size, 0, 0), (0, size, 0)]
        )
        pole = cmds.parent(pole, group)[0]
        _color_control(cmds, pole, 6 if side == "left" else 13)
        cmds.sets([target, pole], add=_node(cmds, metadata["selectionSetUuid"]))
        for node in (target, pole):
            for axis_name in "XYZ":
                cmds.setAttr(node + ".scale" + axis_name, lock=True)
            static.append(
                {
                    "uuid": _uuid(cmds, node),
                    "values": {"scale": cmds.getAttr(node + ".scale"), "rotateOrder": cmds.getAttr(node + ".rotateOrder")},
                }
            )
        cmds.xform(
            pole, ws=True, translation=list(elbow + bend.normal() * ((elbow - shoulder).length() + (wrist - elbow).length()))
        )
        references = []
        handle = None
        if straight:
            references, extra = _straight_solver(cmds, joints, helpers, target, pole, side)
            segment_nodes.extend(extra)
        else:
            cmds.joint(helpers[0], edit=True, setPreferredAngles=True, children=True)
            handle, _ = cmds.ikHandle(
                startJoint=helpers[0], endEffector=helpers[-1], solver="ikRPsolver", name=side + "_arm_IK_handle"
            )
            cmds.parent(handle, target, absolute=True)
            cmds.poleVectorConstraint(pole, handle)
            cmds.setAttr(handle + ".visibility", False)
        cmds.orientConstraint(target, helpers[-1], maintainOffset=False)
        cmds.setAttr(helpers[0] + ".visibility", False)
        edges = []
        grants = list(driven)
        converters = [anchor_matrix] + segment_nodes
        for role, joint, control, helper in zip(roles, joints, controls, helpers):
            basis = om.MMatrix(matrix_from_quaternion(metadata["authoringBases"][role]["quaternion"]))

            chain = composers.get(role, ())
            if chain and metadata["bindings"][role]["inputKind"] == "append_base":
                # EDIT connects ordinary append base axes in bone space.
                basis = om.MMatrix()

            def orientation(attribute):
                return om.MEulerRotation(
                    *[math.radians(value) for value in cmds.getAttr(joint + "." + attribute)[0]]
                ).asMatrix()

            mult = cmds.createNode("multMatrix", name=role + "_IK_basis")
            decomp = cmds.createNode("decomposeMatrix", name=role + "_IK_rotation")
            converters.extend((mult, decomp))
            cmds.setAttr(mult + ".matrixIn[0]", *list(basis * orientation("rotateAxis").inverse()), type="matrix")
            cmds.setAttr(mult + ".matrixIn[3]", *list(orientation("jointOrient").inverse()), type="matrix")
            previous = None
            for composer in chain:
                # Evaluate the imported composer with identity base and the same
                # upstream animation. Remove that contribution from the solved
                # rotation before the original composer applies it.
                grant = cmds.duplicate(composer, inputConnections=True, name=role + "_IK_contribution")[0]
                for attr in ("baseRotate", "baseTranslate"):
                    for suffix in ("", "X", "Y", "Z"):
                        dest = grant + "." + attr + suffix
                        if cmds.connectionInfo(dest, isExactDestination=True):
                            cmds.disconnectAttr(cmds.connectionInfo(dest, sourceFromDestination=True), dest)
                    cmds.setAttr(grant + "." + attr, 0, 0, 0)
                # Follow edits to the animator-facing contribution even when
                # its first key is added after enabling IK.
                if cmds.nodeType(composer) == "mmdBoneMorphAccum":
                    # Only the imported accumulator owns the VMD authoring channels.
                    if cmds.attributeQuery("mmd_bone_morph_accum", node=grant, exists=True):
                        cmds.setAttr(grant + ".mmd_bone_morph_accum", False)
                    attributes = ["contribution[%d]" % index for index in
                                  cmds.getAttr(composer + ".contribution", multiIndices=True) or []]
                else:
                    attributes = ["sourceRotate" + axis for axis in "XYZ"]
                for attr in attributes:
                    cmds.connectAttr(composer + "." + attr, grant + "." + attr, force=True)
                if previous:
                    cmds.connectAttr(previous + ".outputRotate", grant + ".baseRotate")
                previous = grant
                grants.append(_uuid(cmds, grant))
                converters.append(grant)
            if previous:
                offset = cmds.createNode("transform", name=role + "_IK_grant", parent=group)
                cmds.connectAttr(previous + ".outputRotate", offset + ".rotate")
                cmds.connectAttr(offset + ".inverseMatrix", mult + ".matrixIn[4]")
            cmds.setAttr(mult + ".matrixIn[5]", *list(basis.inverse()), type="matrix")
            parent = cmds.listRelatives(joint, parent=True, fullPath=True)[0]
            for src, dst in (
                (helper + ".worldMatrix[0]", mult + ".matrixIn[1]"),
                (parent + ".worldInverseMatrix[0]", mult + ".matrixIn[2]"),
                (mult + ".matrixSum", decomp + ".inputMatrix"),
            ):
                cmds.connectAttr(src, dst)
                edges.append([_ref(cmds, src), _ref(cmds, dst)])
            cmds.setAttr(decomp + ".inputRotateOrder", cmds.getAttr(control + ".rotateOrder"))
            for item in inputs:
                if item["controlRef"]["nodeUuid"] == _uuid(cmds, control):
                    item["outputRef"] = _ref(cmds, decomp + ".outputRotate" + item["controlRef"]["attribute"][-1])
                    plug = resolve_arm_plug(cmds, item["controlRef"])
                    if item["sourceRef"]:
                        cmds.disconnectAttr(resolve_arm_plug(cmds, item["sourceRef"]), plug)
                    cmds.connectAttr(resolve_arm_plug(cmds, item["outputRef"]), plug)
        if (composers or driven) and set(cmds.cycleCheck(all=True, list=True) or []) - previous_cycles:
            raise MmdControlRigBuildError("arm IK intermediate or contribution depends on a solved arm rotation")
        owned = [
            _uuid(cmds, node)
            for node in [group] + (cmds.listRelatives(group, allDescendents=True, fullPath=True) or []) + converters
        ]
        for uuid in owned:
            node = _node(cmds, uuid)
            metadata["nodes"].append({"uuid": uuid, "name": node})
            if not cmds.objectType(node, isAType="dagNode"):
                metadata["helperNodes"].append(uuid)
        row = {
            "active": True,
            "nodes": owned,
            "inputs": inputs,
            "edges": edges,
            "contributionUuids": grants,
            "static": static,
            "targetUuid": _uuid(cmds, target),
            "poleUuid": _uuid(cmds, pole),
            "helperUuids": [_uuid(cmds, node) for node in helpers],
            "jointUuids": [_uuid(cmds, node) for node in joints],
        }
        if handle:
            row["handleUuid"] = _uuid(cmds, handle)
        if references:
            row["straightReferenceUuids"] = [_uuid(cmds, node) for node in references]
        row["graph"] = _graph_snapshot(cmds, row)
        metadata.setdefault("armIk", {})[side] = row
        _write_metadata(cmds, model_root, metadata)
    return inspect_arm_ik(model_root, side, cmds_module=cmds)


def disable_arm_ik(model_root, side, *, match_fk=False, cmds_module=None):
    """Restore exact stashed FK inputs, optionally keying the current IK pose."""
    from maya import cmds as maya_cmds

    cmds = cmds_module or maya_cmds
    metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds)
    row = validate_arm_ik(cmds, metadata, side)
    if not row["active"]:
        return row
    if metadata.get("state") != "EDIT" or metadata.get("owner") != "CONTROL_OWNED":
        raise MmdControlRigBuildError("arm IK requires Control Rig EDIT ownership")
    values = [cmds.getAttr(resolve_arm_plug(cmds, item["controlRef"])) for item in row["inputs"]]
    with _transaction(cmds, "Disable Arm IK"):
        for item, value in zip(row["inputs"], values):
            plug = resolve_arm_plug(cmds, item["controlRef"])
            cmds.disconnectAttr(resolve_arm_plug(cmds, item["outputRef"]), plug)
            if item["sourceRef"]:
                cmds.connectAttr(resolve_arm_plug(cmds, item["sourceRef"]), plug)
            else:
                cmds.setAttr(plug, item["value"])
            if match_fk:
                cmds.setKeyframe(plug, time=cmds.currentTime(q=True), value=value)
        if match_fk:
            for item in row["inputs"]:
                cmds.dgdirty(resolve_arm_plug(cmds, item["controlRef"]))
            cmds.currentTime(cmds.currentTime(query=True), update=True)
        row["active"] = False
        _write_metadata(cmds, model_root, metadata)
    return row


def inspect_arm_ik(model_root, side, *, cmds_module=None):
    """Return validated active state and resolved wrist/pole selection paths."""
    from maya import cmds as maya_cmds

    cmds = cmds_module or maya_cmds
    metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds)
    if not metadata or side not in metadata.get("armIk", {}):
        return {"active": False, "target": None, "pole": None}
    row = validate_arm_ik(cmds, metadata, side)
    return dict(row, target=_node(cmds, row["targetUuid"]), pole=_node(cmds, row["poleUuid"]))


def _reenable(cmds, root, metadata, side):
    """Reuse the owned chain and rematch its target from the current FK pose."""
    from maya.api import OpenMaya as om

    row = validate_arm_ik(cmds, metadata, side)
    if row.get("poleGuideUuid"):
        raise MmdControlRigBuildError(
            "arm IK with a temporal pole filter is no longer supported; "
            "restore the MMD rig and rebuild the Control Rig"
        )
    joints = [_node(cmds, uuid) for uuid in row["jointUuids"]]
    _, driven = _segment_intermediates(cmds, metadata, joints)
    if row["active"]:
        return inspect_arm_ik(root, side, cmds_module=cmds)
    helper = [_node(cmds, uuid) for uuid in row["helperUuids"]]
    target, pole = _node(cmds, row["targetUuid"]), _node(cmds, row["poleUuid"])
    fresh = []
    for item in row["inputs"]:
        plug = resolve_arm_plug(cmds, item["controlRef"])
        writer = cmds.connectionInfo(plug, sourceFromDestination=True)
        if writer:
            _require_fk_curve(cmds, writer, plug)
        fresh.append(dict(item, sourceRef=_ref(cmds, writer) if writer else None, value=cmds.getAttr(plug)))
    positions = [om.MVector(cmds.xform(node, q=True, ws=True, t=True)) for node in joints]
    shoulder, elbow, wrist = positions
    axis = wrist - shoulder
    if axis.length() < 1e-6:
        raise MmdControlRigBuildError("arm IK chain has zero reach")
    bend, straight = _bend_plane(cmds, joints[0], shoulder, elbow, wrist)
    previous_cycles = set(cmds.cycleCheck(all=True, list=True) or []) if driven else set()
    with _transaction(cmds, "Enable Arm IK"):
        handle = _node(cmds, row["handleUuid"]) if row.get("handleUuid") else None
        if handle:
            cmds.setAttr(handle + ".ikBlend", 0)
        references = [_node(cmds, uid) for uid in row.get("straightReferenceUuids", ())]
        if not references and not straight:
            for solved, joint in zip(helper[:-1], joints):
                cmds.xform(solved, ws=True, rotation=cmds.xform(joint, q=True, ws=True, rotation=True))
        _match_world_pose(cmds, target, cmds.xform(joints[-1], q=True, ws=True, matrix=True))
        pole_matrix = cmds.xform(pole, q=True, ws=True, matrix=True)
        pole_matrix[12:15] = list(elbow + bend.normal() * ((elbow - shoulder).length() + (wrist - elbow).length()))
        _match_world_pose(cmds, pole, pole_matrix)
        if references:
            _match_straight_references(cmds, references, joints)
        elif straight:
            references, extra = _straight_solver(cmds, joints, helper, target, pole, side)
            row["straightReferenceUuids"] = [_uuid(cmds, node) for node in references]
            for node in references + extra:
                uid = _uuid(cmds, node)
                row["nodes"].append(uid)
                metadata["nodes"].append({"uuid": uid, "name": node})
                if not cmds.objectType(node, isAType="dagNode"):
                    metadata["helperNodes"].append(uid)
            row["graph"] = _graph_snapshot(cmds, row)
        else:
            cmds.joint(helper[0], edit=True, setPreferredAngles=True, children=True)
            cmds.setAttr(handle + ".ikBlend", 1)
            _rebind_handle(cmds, metadata, row, handle, helper)
        for item in fresh:
            plug = resolve_arm_plug(cmds, item["controlRef"])
            if item["sourceRef"]:
                cmds.disconnectAttr(resolve_arm_plug(cmds, item["sourceRef"]), plug)
            cmds.connectAttr(resolve_arm_plug(cmds, item["outputRef"]), plug)
        if driven and set(cmds.cycleCheck(all=True, list=True) or []) - previous_cycles:
            raise MmdControlRigBuildError("arm IK intermediate or contribution depends on a solved arm rotation")
        row["inputs"] = fresh
        row["active"] = True
        _write_metadata(cmds, root, metadata)
    return inspect_arm_ik(root, side, cmds_module=cmds)


def _graph_snapshot(cmds, row):
    """Record exact types, DAG parents and solver inputs; targets remain animatable."""
    result = {}
    editable = {row["targetUuid"], row["poleUuid"]}
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


def _require_fk_curve(cmds, writer, plug):
    if cmds.nodeType(writer.split(".", 1)[0]) != "animCurveTA":
        raise MmdControlRigBuildError("arm IK requires a direct time animation curve: " + plug)


def _match_world_pose(cmds, node, matrix):
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
        for axis, value in zip("XYZ", values):
            plug = node + "." + channel + axis
            if cmds.connectionInfo(plug, sourceFromDestination=True):
                cmds.setKeyframe(plug, time=cmds.currentTime(q=True), value=value)
                cmds.dgdirty(plug)
            else:
                cmds.setAttr(plug, value)
    cmds.currentTime(cmds.currentTime(q=True), update=True)


def _bend_plane(cmds, joint, shoulder, elbow, wrist):
    """Distinguish a nearly extended two-link chain from a folded arm."""
    axis = wrist - shoulder
    bend = elbow - shoulder - axis * (((elbow - shoulder) * axis) / (axis * axis))
    upper, lower = elbow - shoulder, wrist - elbow
    straight = upper.normal() * lower.normal() > math.cos(math.radians(2))
    if bend.length() < axis.length() * 1e-10:
        bend = _straight_pole_direction(cmds, joint, axis)
    return bend, straight


def _match_straight_references(cmds, references, joints):
    """Keep FK roll in the shoulder-parent and upper-arm coordinate frames."""
    from maya.api import OpenMaya as om

    shoulder_parent = cmds.listRelatives(joints[0], parent=True, fullPath=True)[0]
    for ref, joint, parent in zip(references, joints, (shoulder_parent, joints[0])):
        world = om.MMatrix(cmds.xform(joint, q=True, ws=True, matrix=True))
        parent_world = om.MMatrix(cmds.xform(parent, q=True, ws=True, matrix=True))
        rotation = om.MTransformationMatrix(world * parent_world.inverse()).rotation()
        cmds.setAttr(ref + ".rotate", *(math.degrees(value) for value in (rotation.x, rotation.y, rotation.z)))


def _straight_solver(cmds, joints, helpers, target, pole, side):
    """Solve the elbow triangle without asking the native RP solver to infer a rest plane."""
    nodes = []

    def create(kind, suffix):
        node = cmds.createNode(kind, name=side + "_arm_IK_" + suffix)
        nodes.append(node)
        return node

    # Use the same live segment offsets for lengths, aim axes and joint positions.
    offsets = [cmds.connectionInfo(child + ".translate", sourceFromDestination=True) for child in helpers[1:]]
    references = []
    for helper, part, offset in zip(helpers, ("upper", "lower"), (None, offsets[0])):
        parent = cmds.listRelatives(helper, parent=True, fullPath=True)[0]
        ref = cmds.createNode("transform", name=side + "_arm_IK_" + part + "Rest", parent=parent)
        if offset:
            cmds.connectAttr(offset, ref + ".translate")
        else:
            cmds.setAttr(ref + ".translate", *cmds.getAttr(helper + ".translate")[0])
        references.append(ref)
    _match_straight_references(cmds, references, joints)
    def position(node, name):
        decompose = create("decomposeMatrix", name)
        cmds.connectAttr(node + ".worldMatrix[0]", decompose + ".inputMatrix")
        return "<<" + ", ".join(decompose + ".outputTranslate" + axis for axis in "XYZ") + ">>"

    shoulder_position = position(references[0], "origin")
    target_position = position(target, "targetPosition")
    pole_position = position(pole, "polePosition")
    lengths = []
    for offset in offsets:
        length = create("distanceBetween", "length")
        cmds.connectAttr(offset, length + ".point1")
        lengths.append(length)
    goal = create("composeMatrix", "elbowGoal")

    # Project the pole into the bend plane. Clamp distances at either end of
    # the reachable interval, including the equal-length, fully folded pose.
    expression = f"""
vector $s = {shoulder_position};
vector $t = {target_position} - $s;
vector $p = {pole_position} - $s;
float $a = {lengths[0]}.distance, $b = {lengths[1]}.distance;
float $len = mag($t);
vector $axis = <<1,0,0>>;
if ($len > 1e-12) $axis = unit($t);
vector $bend = $p - dot($p, $axis) * $axis;
if (mag($bend) < 1e-12) {{
    vector $alt = <<0,1,0>>;
    if (abs($axis.y) > 0.9) $alt = <<0,0,1>>;
    $bend = $alt - dot($alt, $axis) * $axis;
}}
float $d = clamp(abs($a - $b), $a + $b, $len);
float $x = 0;
if ($d > 1e-12) $x = clamp(-$a, $a, 0.5 * ($d + ($a - $b) * ($a + $b) / $d));
vector $e = $s + $x * $axis + sqrt(max(0, ($a - $x) * ($a + $x))) * unit($bend);
{goal}.inputTranslateX = $e.x;
{goal}.inputTranslateY = $e.y;
{goal}.inputTranslateZ = $e.z;
"""
    nodes.append(cmds.expression(string=expression, name=side + "_arm_IK_triangle",
                                 alwaysEvaluate=False, unitConversion="none"))
    for ref, helper, offset, world_goal in zip(references, helpers, offsets,
                                               (goal + ".outputMatrix", target + ".worldMatrix[0]")):
        # Work in parent space so a torso rotation carries the FK rest roll.
        parent = cmds.listRelatives(helper, parent=True, fullPath=True)[0]
        local = create("multMatrix", "localGoal")
        cmds.connectAttr(world_goal, local + ".matrixIn[0]")
        cmds.connectAttr(parent + ".worldInverseMatrix[0]", local + ".matrixIn[1]")
        aim = create("aimMatrix", "aim")
        cmds.connectAttr(ref + ".matrix", aim + ".inputMatrix")
        cmds.connectAttr(offset, aim + ".primaryInputAxis")
        cmds.connectAttr(local + ".matrixSum", aim + ".primaryTargetMatrix")
        rotation = create("decomposeMatrix", "aimRotation")
        cmds.connectAttr(aim + ".outputMatrix", rotation + ".inputMatrix")
        cmds.setAttr(rotation + ".inputRotateOrder", cmds.getAttr(helper + ".rotateOrder"))
        for attr in ("jointOrient", "rotateAxis"):
            cmds.setAttr(helper + "." + attr, 0, 0, 0)
        cmds.connectAttr(rotation + ".outputRotate", helper + ".rotate")
    return references, nodes


def _straight_pole_direction(cmds, joint, axis):
    """Use the least parallel FK bone axis when a straight elbow has no bend plane."""
    from maya.api import OpenMaya as om

    matrix = cmds.xform(joint, q=True, ws=True, matrix=True)
    unit = axis.normal()
    candidates = [om.MVector(matrix[index : index + 3]) for index in (0, 4, 8)]
    projected = [candidate - unit * (candidate * unit) for candidate in candidates]
    return max(projected, key=lambda value: value.length()).normal()


def _rebind_handle(cmds, metadata, row, handle, helper):
    """Refresh native RP solver state at this frame without advancing scene time.

    Maya recreates the effector when rebinding the same owned handle. Keep that
    exact replacement in the ownership journal; solver, chain and pole stay put.
    """
    old_effector = cmds.ikHandle(handle, query=True, endEffector=True)
    old_uuid = _uuid(cmds, old_effector)
    if old_uuid not in row["nodes"]:
        raise MmdControlRigBuildError("arm IK effector is not owned")
    cmds.ikHandle(handle, edit=True, startJoint=helper[0], endEffector=helper[-1])
    effector = cmds.ikHandle(handle, query=True, endEffector=True)
    new_uuid = _uuid(cmds, effector)
    if old_uuid != new_uuid:
        remaining = cmds.ls(old_uuid, long=True) or []
        if remaining:
            cmds.delete(remaining)
        row["nodes"] = [uid for uid in row["nodes"] if uid != old_uuid] + [new_uuid]
        metadata["nodes"] = [item for item in metadata["nodes"] if item["uuid"] != old_uuid]
        metadata["nodes"].append({"uuid": new_uuid, "name": _node(cmds, new_uuid)})
    row["graph"] = _graph_snapshot(cmds, row)


def _create_segment_offsets(cmds, joints, helpers, side, segments):
    """Follow intermediate local offsets without reading a solved bone world matrix.

    A fixed-axis twist need not point at its child. Its rotation can therefore
    change the next main joint's offset even when segment length barely changes.
    The end joint contributes only translation, so its solved rotation cannot
    feed back into the helper chain.
    """
    nodes = []
    for index, intermediates in enumerate(segments, 1):
        end = joints[index]
        if not intermediates:
            cmds.connectAttr(end + ".translate", helpers[index] + ".translate")
            continue
        compose = cmds.createNode("composeMatrix", name=side + "_arm_IK_endTranslate")
        mult = cmds.createNode("multMatrix", name=side + "_arm_IK_localOffset")
        decompose = cmds.createNode("decomposeMatrix", name=side + "_arm_IK_segment")
        cmds.connectAttr(end + ".translate", compose + ".inputTranslate")
        cmds.connectAttr(compose + ".outputMatrix", mult + ".matrixIn[0]")
        for slot, intermediate in enumerate(intermediates, 1):
            cmds.connectAttr(intermediate + ".matrix", mult + ".matrixIn[%d]" % slot)
        cmds.connectAttr(mult + ".matrixSum", decompose + ".inputMatrix")
        cmds.connectAttr(decompose + ".outputTranslate", helpers[index] + ".translate")
        nodes.extend((compose, mult, decompose))
    return nodes


def _depends_on_solved_arm(cmds, source_plug, solved):
    """A local rotation input follows its channel drivers, not its DAG parent."""
    pending = [source_plug]
    visited = set()
    while pending:
        plug = pending.pop()
        if plug in visited:
            continue
        visited.add(plug)
        node, attr = plug.split(".", 1)
        if _uuid(cmds, node) in solved:
            return True
        if cmds.objectType(node, isAType="transform"):
            if attr in ("rotate", "translate"):
                inputs = [plug] + [plug + axis for axis in "XYZ"]
            elif attr in ("rotateOrder", "rotateX", "rotateY", "rotateZ", "translateX", "translateY", "translateZ"):
                inputs = [plug]
            else:
                # Matrix and other transform outputs may include parent motion.
                upstream = cmds.listHistory(node, allConnections=True) or []
                if solved.intersection(cmds.ls(upstream, uuid=True) or []):
                    return True
                continue
            for channel in inputs:
                source = cmds.connectionInfo(channel, sourceFromDestination=True)
                if source:
                    pending.append(source)
        else:
            pending.extend(cmds.listConnections(node, source=True, destination=False, plugs=True) or [])
    return False


def _segment_intermediates(cmds, metadata, joints):
    """Check current intermediate routes, including bones without a named control role."""
    from .mmd_control_rig_analyzer import analyze_mmd_control_rig
    bindings = {binding["jointUuid"]: binding for binding in metadata["bindings"].values()}
    root = _node(cmds, metadata["modelRootUuid"])
    live = {_uuid(cmds, bone.joint): bone for bone in analyze_mmd_control_rig(root, cmds_module=cmds).bones}
    owned = {node["uuid"] for node in metadata["nodes"]}
    solved = {_uuid(cmds, joint) for joint in joints}
    solved.update(metadata["controls"][role] for role, binding in metadata["bindings"].items()
                  if binding["jointUuid"] in solved)
    driven = []
    segments = []
    for start, end in zip(joints, joints[1:]):
        parent = cmds.listRelatives(end, parent=True, fullPath=True)[0]
        intermediates = []
        while parent != start:
            uuid = _uuid(cmds, parent)
            binding = bindings.get(uuid)
            current = live.get(uuid)
            kind = current.input_kind if current else "direct_channel"
            # Existing EDIT converters are owned writers, not a new external route.
            if (binding and binding["inputKind"] == "direct_channel" and kind == "unsupported"
                    and current.incoming and all(_uuid(cmds, edge.source_plug.split(".", 1)[0]) in owned
                                                 for edge in current.incoming)):
                kind = "direct_channel"
            if kind in {"append_base", "bone_morph_base"}:
                if any(_depends_on_solved_arm(cmds, edge.source_plug, solved) for edge in current.incoming):
                    raise MmdControlRigBuildError("arm IK intermediate depends on a solved arm rotation: " + parent)
                driven.append(uuid)
            elif kind != "direct_channel" or (binding and binding["inputKind"] != kind):
                raise MmdControlRigBuildError("arm IK unsupported intermediate bone route: " + parent)
            intermediates.append(parent)
            parents = cmds.listRelatives(parent, parent=True, fullPath=True) or []
            if not parents:
                raise MmdControlRigBuildError("arm IK roles must form one ancestor chain")
            parent = parents[0]
        segments.append(intermediates)
    return segments, driven
