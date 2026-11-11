"""Distribute authored arm-IK roll through the existing PMX twist controls.

Only the pre-solver inputs are read: solved joints must never feed the live
segment offsets back into CCD. Existing twist animation remains the baseline.
"""
import math

from .mmd_control_rig_basis import matrix_from_quaternion


def create_auto_twist(cmds, metadata, side, group, joints, direction, target, solver):
    """Return owned nodes, ordinary EDIT input records, and rematch references."""
    from maya.api import OpenMaya as om
    from .mmd_control_rig_arm_ik import _node, _ref, _require_fk_curve

    nodes, inputs, references = [], [], []
    lower_plug = cmds.connectionInfo(solver + ".inputRotate[2]", sourceFromDestination=True)
    lower = lower_plug.split(".")[0]
    cmds.addAttr(group, longName="twistControlsVisible", attributeType="bool", defaultValue=False)

    def new(kind, suffix):
        node = cmds.createNode(kind, name=side + "_autoTwist_" + suffix)
        nodes.append(node)
        return node

    for index, suffix in enumerate(("_arm_twist", "_wrist_twist")):
        role = side + suffix
        if role not in metadata["controls"]:
            continue
        control = _node(cmds, metadata["controls"][role])
        joint = _node(cmds, metadata["bindings"][role]["jointUuid"])
        # The scalar control already uses the PMX fixed axis; auxiliary twist
        # bones continue to inherit through their original PMX grant rates.
        plug = control + ".rotateZ"
        source = cmds.connectionInfo(plug, sourceFromDestination=True)
        if source:
            _require_fk_curve(cmds, source, plug)
        value = cmds.getAttr(plug)
        basis = om.MMatrix(matrix_from_quaternion(metadata["authoringBases"][role]["quaternion"]))
        rotate_axis = om.MEulerRotation(*[math.radians(v) for v in cmds.getAttr(joint + ".rotateAxis")[0]]).asMatrix()
        frame = joints[0] if index == 0 else target
        frame_world = om.MMatrix(cmds.xform(frame, q=True, ws=True, matrix=True))
        axis = om.MVector(0, 0, 1) * basis * rotate_axis.inverse() * om.MMatrix(
            cmds.xform(joint, q=True, ws=True, matrix=True)) * frame_world.inverse()
        alignment = om.MVector(0, 0, 1).rotateTo(axis.normal()).asMatrix()
        matrices = [direction + ".matrix"] if index == 0 else [target + ".worldMatrix[0]", lower + ".worldInverseMatrix[0]"]
        current = om.MMatrix()
        for matrix in matrices:
            current *= om.MMatrix(cmds.getAttr(matrix))
        delta = new("multMatrix", suffix + "Delta")
        cmds.setAttr(delta + ".matrixIn[0]", *list(alignment), type="matrix")
        for slot, matrix in enumerate(matrices, 1):
            cmds.connectAttr(matrix, delta + ".matrixIn[%d]" % slot)
        reference = delta + ".matrixIn[%d]" % (len(matrices) + 1)
        cmds.setAttr(reference, *list(current.inverse()), type="matrix")
        cmds.setAttr(delta + ".matrixIn[%d]" % (len(matrices) + 2), *list(alignment.inverse()), type="matrix")
        rotation = new("decomposeMatrix", suffix + "Rotation")
        projection = new("composeMatrix", suffix + "Roll")
        angle = new("decomposeMatrix", suffix + "Angle")
        cmds.connectAttr(delta + ".matrixSum", rotation + ".inputMatrix")
        cmds.setAttr(projection + ".useEulerRotation", False)
        # A 180-degree pure swing has no defined roll. Avoid the zero
        # quaternion (composeMatrix would produce NaNs) and choose no twist.
        length = new("vectorProduct", suffix + "RollLength")
        cmds.setAttr(length + ".operation", 1)
        valid = new("condition", suffix + "DefinedRoll")
        cmds.setAttr(valid + ".operation", 2)
        cmds.setAttr(valid + ".secondTerm", 1e-12)
        cmds.setAttr(valid + ".colorIfFalse", 1, 0, 0)
        for component, axis, color in (("Z", "X", "G"), ("W", "Y", "R")):
            source_quat = rotation + ".outputQuat" + component
            for operand in ("input1", "input2"):
                cmds.connectAttr(source_quat, length + "." + operand + axis)
            cmds.connectAttr(source_quat, valid + ".colorIfTrue" + color)
            cmds.connectAttr(valid + ".outColor" + color, projection + ".inputQuat" + component)
        cmds.connectAttr(length + ".outputX", valid + ".firstTerm")
        cmds.connectAttr(projection + ".outputMatrix", angle + ".inputMatrix")
        add = new("animBlendNodeAdditiveDA", suffix + "Baseline")
        cmds.setAttr(add + ".weightA", 1)
        cmds.setAttr(add + ".weightB", 1)
        cmds.setAttr(add + ".inputA", value)
        if source:
            cmds.connectAttr(source, add + ".inputA")
            cmds.disconnectAttr(source, plug)
        cmds.connectAttr(angle + ".outputRotateZ", add + ".inputB")
        cmds.connectAttr(add + ".output", plug)
        inputs.append({"controlRef": _ref(cmds, plug), "sourceRef": _ref(cmds, source) if source else None,
                       "value": value, "outputRef": _ref(cmds, add + ".output"), "baselineRef": _ref(cmds, add + ".inputA")})
        references.append({"matrices": [_ref(cmds, m) for m in matrices], "reference": _ref(cmds, reference)})
        for shape in cmds.listRelatives(control, shapes=True, fullPath=True) or []:
            if cmds.getAttr(shape + ".visibility") and not cmds.connectionInfo(shape + ".visibility", sourceFromDestination=True):
                cmds.connectAttr(group + ".twistControlsVisible", shape + ".visibility")
        if index == 0:
            # Transfer upper-input roll out of the upper joint and into the
            # intermediate twist, retaining it in the lower-arm seed. This
            # preserves the animator's bend direction without applying roll twice.
            inverse = cmds.createNode("transform", name=side + "_autoTwist_roll", parent=group)
            cmds.connectAttr(angle + ".outputRotateZ", inverse + ".rotateZ")
            for bone_slot, source_matrix, roll_matrix in (
                (1, direction + ".matrix", inverse + ".inverseMatrix"),
                (2, lower + ".matrix", projection + ".outputMatrix"),
            ):
                mult = new("multMatrix", "seed%d" % bone_slot)
                decomp = new("decomposeMatrix", "seedRotation%d" % bone_slot)
                parts = [alignment.inverse(), roll_matrix, alignment]
                parts = parts + [source_matrix] if bone_slot == 1 else [source_matrix] + parts
                for slot, part in enumerate(parts):
                    if isinstance(part, str):
                        cmds.connectAttr(part, mult + ".matrixIn[%d]" % slot)
                    else:
                        cmds.setAttr(mult + ".matrixIn[%d]" % slot, *list(part), type="matrix")
                cmds.connectAttr(mult + ".matrixSum", decomp + ".inputMatrix")
                cmds.connectAttr(decomp + ".outputRotate", solver + ".inputRotate[%d]" % bone_slot, force=True)
    return nodes, inputs, references, _ref(cmds, lower + ".rotate"), _ref(cmds, group + ".twistControlsVisible")


def rematch_auto_twist(cmds, row, inputs):
    """Rebase only at an explicit FK-to-IK match; keep the saved FK curves live."""
    from maya.api import OpenMaya as om
    from .mmd_control_rig_arm_ik import resolve_arm_plug

    for reference in row.get("autoTwist", ()):
        current = om.MMatrix()
        for matrix in reference["matrices"]:
            current *= om.MMatrix(cmds.getAttr(resolve_arm_plug(cmds, matrix)))
        cmds.setAttr(resolve_arm_plug(cmds, reference["reference"]), *list(current.inverse()), type="matrix")
    for item in inputs:
        if "baselineRef" not in item:
            continue
        plug = resolve_arm_plug(cmds, item["baselineRef"])
        source = cmds.connectionInfo(plug, sourceFromDestination=True)
        if source:
            cmds.disconnectAttr(source, plug)
        if item["sourceRef"]:
            cmds.connectAttr(resolve_arm_plug(cmds, item["sourceRef"]), plug)
        else:
            cmds.setAttr(plug, item["value"])


def set_twist_controls_visible(cmds, row, visible):
    """Show FK twist shapes again when the arm layer relinquishes its inputs."""
    from .mmd_control_rig_arm_ik import resolve_arm_plug
    if row.get("twistVisibilityRef"):
        cmds.setAttr(resolve_arm_plug(cmds, row["twistVisibilityRef"]), visible)


def release_auto_twist(cmds, row):
    """Drop baseline taps when inactive, restoring the original curve fan-out."""
    from .mmd_control_rig_arm_ik import resolve_arm_plug
    for item in row["inputs"]:
        if item.get("baselineRef"):
            plug = resolve_arm_plug(cmds, item["baselineRef"])
            source = cmds.connectionInfo(plug, sourceFromDestination=True)
            if source:
                cmds.disconnectAttr(source, plug)
    set_twist_controls_visible(cmds, row, True)
