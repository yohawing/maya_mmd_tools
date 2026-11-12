"""Owned three-joint arm IK with MMD CCD and an upper-arm rotation input."""

import math
import json

from .mmd_control_rig_builder import (
    MmdControlRigBuildError,
    _write_metadata,
    _create_control_curve,
    _color_control,
    read_mmd_control_rig_metadata,
)
from .mmd_control_rig_analyzer import rotation_composer_source
from .mmd_control_rig_basis import matrix_from_quaternion
from .mmd_control_rig_ik_common import (
    _node, _uuid, _ref, _transaction, _numeric_equal, _match_world_pose,
    resolve_ik_plug as resolve_arm_plug, snapshot_ik_graph,
    create_fk_storage, release_fk_storage,
)


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
                    raise MmdControlRigBuildError("arm IK wrist/upper-arm require direct time animation curves")
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
    """Match a wrist target and a pre-IK upper-arm rotation to the FK pose."""
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
    reach = _arm_reach(cmds, joints)
    if reach < 1e-6:
        raise MmdControlRigBuildError("arm IK chain has zero reach")
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
    previous_cycles = set(cmds.cycleCheck(all=True, list=True) or [])
    with _transaction(cmds, "Enable Arm IK"):
        group = cmds.createNode("transform", name=side + "_arm_IK", parent=_node(cmds, metadata["controlGroupUuid"]))
        # The wrist is world-space; the upper-arm input follows the shoulder anchor.
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
        target = _create_control_curve(cmds, side + "_wrist_IK", side + "_wrist", max(reach * 0.08, 0.01))
        target = cmds.parent(target, group)[0]
        _color_control(cmds, target, 6 if side == "left" else 13)
        cmds.xform(target, ws=True, matrix=cmds.xform(joints[-1], q=True, ws=True, matrix=True))
        size = max(reach * 0.04, 0.01)
        pole = _create_control_curve(cmds, side + "_upper_arm_IK", side + "_arm", size)
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
        pole = cmds.parent(pole, anchor, absolute=True)[0]
        solver, extra = _create_ccd_solver(cmds, helpers, anchor, target, pole, side)
        segment_nodes.extend(extra)
        cmds.orientConstraint(target, helpers[-1], maintainOffset=False)
        cmds.setAttr(helpers[0] + ".visibility", False)
        edges = []
        grants = list(driven)
        converters = [anchor_matrix] + segment_nodes
        for role, joint, control, helper in zip(roles, joints, controls, helpers):
            storage = create_fk_storage(cmds, control, group, role + "_FK_storage", static)
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
                    storage_plug = storage + "." + item["controlRef"]["attribute"]
                    item["storageRef"] = _ref(cmds, storage_plug)
                    if item["sourceRef"]:
                        source = resolve_arm_plug(cmds, item["sourceRef"])
                        cmds.connectAttr(source, storage_plug)
                        cmds.disconnectAttr(source, plug)
                    cmds.connectAttr(resolve_arm_plug(cmds, item["outputRef"]), plug)
        from .mmd_control_rig_arm_twist import create_auto_twist
        twist_nodes, twist_inputs, twist_refs, lower_ref, visibility_ref = create_auto_twist(
            cmds, metadata, side, group, joints, pole, target, solver)
        converters.extend(twist_nodes)
        inputs.extend(twist_inputs)
        if (composers or driven or twist_inputs) and set(cmds.cycleCheck(all=True, list=True) or []) - previous_cycles:
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
            "solverUuid": _uuid(cmds, solver),
            "lowerInputRef": lower_ref,
            "autoTwist": twist_refs,
            "twistVisibilityRef": visibility_ref,
        }
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
        from .mmd_control_rig_arm_twist import release_auto_twist
        release_fk_storage(cmds, row)
        release_auto_twist(cmds, row)
        row["graph"] = _graph_snapshot(cmds, row)
        row["active"] = False
        _write_metadata(cmds, model_root, metadata)
    return row


def inspect_arm_ik(model_root, side, *, cmds_module=None):
    """Return the wrist and upper-arm inputs (pole is the saved API alias)."""
    from maya import cmds as maya_cmds

    cmds = cmds_module or maya_cmds
    metadata = read_mmd_control_rig_metadata(model_root, cmds_module=cmds)
    if not metadata or side not in metadata.get("armIk", {}):
        return {"active": False, "target": None, "pole": None, "direction": None}
    row = validate_arm_ik(cmds, metadata, side)
    direction = _node(cmds, row["poleUuid"])
    return dict(row, target=_node(cmds, row["targetUuid"]), pole=direction, direction=direction)


def _reenable(cmds, root, metadata, side):
    """Reuse the owned chain and rematch its target from the current FK pose."""
    from maya.api import OpenMaya as om

    row = validate_arm_ik(cmds, metadata, side)
    if row.get("poleGuideUuid") or not row.get("solverUuid"):
        raise MmdControlRigBuildError(
            "arm IK with a legacy RP solver or temporal pole filter requires rebuilding; "
            "restore the MMD rig and rebuild the Control Rig"
        )
    if any("storageRef" not in item for item in row["inputs"] if "baselineRef" not in item):
        raise MmdControlRigBuildError(
            "arm IK without FK rotation storage requires rebuilding; "
            "restore the MMD rig and rebuild the Control Rig"
        )
    joints = [_node(cmds, uuid) for uuid in row["jointUuids"]]
    _, driven = _segment_intermediates(cmds, metadata, joints)
    if row["active"]:
        return inspect_arm_ik(root, side, cmds_module=cmds)
    target, pole = _node(cmds, row["targetUuid"]), _node(cmds, row["poleUuid"])
    fresh = []
    for item in row["inputs"]:
        plug = resolve_arm_plug(cmds, item["controlRef"])
        writer = cmds.connectionInfo(plug, sourceFromDestination=True)
        if writer:
            _require_fk_curve(cmds, writer, plug)
        fresh.append(dict(item, sourceRef=_ref(cmds, writer) if writer else None, value=cmds.getAttr(plug)))
    if _arm_reach(cmds, joints) < 1e-6:
        raise MmdControlRigBuildError("arm IK chain has zero reach")
    previous_cycles = set(cmds.cycleCheck(all=True, list=True) or []) if driven else set()
    with _transaction(cmds, "Enable Arm IK"):
        arm_world, elbow_world = [om.MMatrix(cmds.xform(joint, q=True, ws=True, matrix=True)) for joint in joints[:2]]
        lower = om.MTransformationMatrix(elbow_world * arm_world.inverse()).rotation()
        lower_input = resolve_arm_plug(cmds, row["lowerInputRef"])
        cmds.setAttr(lower_input, *[math.degrees(value) for value in lower])
        _match_world_pose(cmds, target, cmds.xform(joints[-1], q=True, ws=True, matrix=True))
        _match_world_pose(cmds, pole, list(arm_world), rotate_only=True)
        from .mmd_control_rig_arm_twist import rematch_auto_twist, set_twist_controls_visible
        rematch_auto_twist(cmds, row, fresh)
        set_twist_controls_visible(cmds, row, False)
        for item in fresh:
            plug = resolve_arm_plug(cmds, item["controlRef"])
            if item["sourceRef"]:
                source = resolve_arm_plug(cmds, item["sourceRef"])
                if item.get("storageRef"):
                    cmds.connectAttr(source, resolve_arm_plug(cmds, item["storageRef"]))
                cmds.disconnectAttr(source, plug)
            cmds.connectAttr(resolve_arm_plug(cmds, item["outputRef"]), plug)
        if driven and set(cmds.cycleCheck(all=True, list=True) or []) - previous_cycles:
            raise MmdControlRigBuildError("arm IK intermediate or contribution depends on a solved arm rotation")
        row["inputs"] = fresh
        row["graph"] = _graph_snapshot(cmds, row)
        row["active"] = True
        _write_metadata(cmds, root, metadata)
    return inspect_arm_ik(root, side, cmds_module=cmds)


def _arm_reach(cmds, joints):
    """Measure the current upper-arm to wrist distance, without reading the elbow."""
    from maya.api import OpenMaya as om

    start, end = (om.MVector(cmds.xform(joint, q=True, ws=True, t=True))
                  for joint in (joints[0], joints[-1]))
    return (end - start).length()


def _graph_snapshot(cmds, row):
    return snapshot_ik_graph(cmds, row, (row["targetUuid"], row["poleUuid"]))


def _require_fk_curve(cmds, writer, plug):
    if cmds.nodeType(writer.split(".", 1)[0]) != "animCurveTA":
        raise MmdControlRigBuildError("arm IK requires a direct time animation curve: " + plug)


def _create_ccd_solver(cmds, helpers, anchor, target, direction, side):
    """Use the shared MMD primitive with live offsets and explicit pre-IK roll.

    The synthetic solver chain has XYZ channels and no joint orientation. The
    existing authoring bridges convert its world results into each PMX basis.
    """
    from maya.api import OpenMaya as om

    worlds = [om.MMatrix(cmds.xform(helper, q=True, ws=True, matrix=True)) for helper in helpers[:2]]
    for helper, world in zip(helpers, worlds):
        cmds.setAttr(helper + ".jointOrient", 0, 0, 0)
        cmds.setAttr(helper + ".rotateAxis", 0, 0, 0)
        cmds.setAttr(helper + ".rotateOrder", 0)
        rotation = om.MTransformationMatrix(world).rotation()
        cmds.xform(helper, ws=True, rotation=[math.degrees(value) for value in rotation])
    cmds.setAttr(direction + ".translate", *cmds.getAttr(helpers[0] + ".translate")[0])
    cmds.setAttr(direction + ".rotate", *cmds.getAttr(helpers[0] + ".rotate")[0])
    for axis in "XYZ":
        cmds.setAttr(direction + ".translate" + axis, lock=True, keyable=False)
    solver = cmds.createNode("mmdCcdIk", name=side + "_arm_CCD")
    bones = []
    for slot, node in enumerate([anchor] + helpers):
        translate = cmds.getAttr(node + ".translate")[0]
        bones.append({"parent_slot": slot - 1,
                      "rest_position": [translate[0], translate[1], -translate[2]],
                      "maya_rest_translate": list(translate)})
        cmds.connectAttr(node + ".translate", solver + ".inputTranslate[%d]" % slot)
        cmds.setAttr(solver + ".inputRotate[%d]" % slot, *cmds.getAttr(node + ".rotate")[0])
    cmds.connectAttr(anchor + ".rotate", solver + ".inputRotate[0]")
    cmds.connectAttr(direction + ".rotate", solver + ".inputRotate[1]")
    # The Python registration reads connected array inputs; use an ordinary
    # owned transform for the rematchable lower-arm seed on both registrations.
    lower_input = cmds.createNode("transform", name=side + "_elbow_CCD_rest", parent=direction)
    cmds.setAttr(lower_input + ".rotate", *cmds.getAttr(helpers[1] + ".rotate")[0])
    cmds.connectAttr(lower_input + ".rotate", solver + ".inputRotate[2]")
    cmds.setAttr(solver + ".chainJson", json.dumps({
        "bones": bones, "targetBoneSlot": 3, "controllerBoneSlot": -1,
        "links": [{"bone_slot": 2}, {"bone_slot": 1}], "iterationCount": 256, "limitAngle": 0.5,
    }), type="string")
    goal = cmds.createNode("decomposeMatrix", name=side + "_arm_CCD_goal")
    cmds.connectAttr(target + ".worldMatrix[0]", goal + ".inputMatrix")
    # Explicit world coordinates: goalWorldMatrix strips imported model roots.
    cmds.connectAttr(goal + ".outputTranslate", solver + ".goal")
    for index, helper in enumerate(reversed(helpers[:2])):
        cmds.connectAttr(solver + ".outputRotate[%d]" % index, helper + ".rotate")
    return solver, [solver, goal]


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
