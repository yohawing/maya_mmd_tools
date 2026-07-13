"""mmdPhysicsBoneDriver — Solver-space world matrix to Maya local channels.

Reads one bone's world matrix from the mmdPhysicsSolver's flat output array,
decomposes to Maya local translate/rotate respecting parent inverse matrix,
joint orient, rotate axis, and rotate order.

DG connections:
    mmdPhysicsSolver.outBoneMatrices → mmdPhysicsBoneDriver.inSolverBoneMatrices
    mmdPhysicsSolver.outBoneCount    → mmdPhysicsBoneDriver.inSolverBoneCount
    mmdPhysicsSolver.outSolved       → mmdPhysicsBoneDriver.inSolved
    parentJoint.worldInverseMatrix   → mmdPhysicsBoneDriver.inParentInverseMatrix
    mmdPhysicsBoneDriver.outTranslate → targetJoint.translate
    mmdPhysicsBoneDriver.outRotate    → targetJoint.rotate

This is the Python prototype; a C++ version with the same TypeId will replace
it when the C++ plugin is loaded.
"""

from __future__ import annotations


import maya.api.OpenMaya as om


def maya_useNewAPI():
    pass


_ROTATE_ORDERS = [
    om.MEulerRotation.kXYZ,
    om.MEulerRotation.kYZX,
    om.MEulerRotation.kZXY,
    om.MEulerRotation.kXZY,
    om.MEulerRotation.kYXZ,
    om.MEulerRotation.kZYX,
]


class MmdPhysicsBoneDriverNode(om.MPxNode):
    kTypeName = "mmdPhysicsBoneDriver"
    kTypeId = om.MTypeId(0x00128009)
    kClassify = "utility/general"

    aInSolverBoneMatrices = None
    aInSolverBoneCount = None
    aInBoneIndex = None
    aInParentBoneIndex = None
    aInParentInverseMatrix = None
    aInJointOrient = None
    aInJointOrientX = None
    aInJointOrientY = None
    aInJointOrientZ = None
    aInRotateAxis = None
    aInRotateAxisX = None
    aInRotateAxisY = None
    aInRotateAxisZ = None
    aInRotateOrder = None
    aInSolved = None
    aEnable = None

    aOutTranslate = None
    aOutTranslateX = None
    aOutTranslateY = None
    aOutTranslateZ = None
    aOutRotate = None
    aOutRotateX = None
    aOutRotateY = None
    aOutRotateZ = None

    def __init__(self):
        super().__init__()

    def _is_output_plug(self, plug):
        attr = plug.attribute()
        if attr in (
            self.aOutTranslate,
            self.aOutTranslateX,
            self.aOutTranslateY,
            self.aOutTranslateZ,
            self.aOutRotate,
            self.aOutRotateX,
            self.aOutRotateY,
            self.aOutRotateZ,
        ):
            return True
        if plug.isChild:
            return self._is_output_plug(plug.parent())
        return False

    def compute(self, plug, data):
        if not self._is_output_plug(plug):
            return None

        enable = data.inputValue(self.aEnable).asBool()
        solved = data.inputValue(self.aInSolved).asBool()
        if not enable or not solved:
            self._write_identity(data)
            return

        bone_count = data.inputValue(self.aInSolverBoneCount).asInt()
        bone_index = data.inputValue(self.aInBoneIndex).asInt()
        parent_bone_index = data.inputValue(self.aInParentBoneIndex).asInt()

        if bone_index < 0 or bone_index >= bone_count:
            self._write_identity(data)
            return

        mat_data = data.inputValue(self.aInSolverBoneMatrices).data()
        if mat_data.isNull():
            self._write_identity(data)
            return

        fn_arr = om.MFnDoubleArrayData(mat_data)
        arr = fn_arr.array()
        expected_len = bone_count * 16
        if len(arr) < expected_len:
            self._write_identity(data)
            return

        bone_world = self._extract_matrix(arr, bone_index)

        if 0 <= parent_bone_index < bone_count:
            parent_world = self._extract_matrix(arr, parent_bone_index)
            local_mat = bone_world * parent_world.inverse()
        else:
            parent_inv_mat = data.inputValue(self.aInParentInverseMatrix).asMatrix()
            local_mat = bone_world * parent_inv_mat

        tfm = om.MTransformationMatrix(local_mat)
        translate = tfm.translation(om.MSpace.kTransform)
        total_quat = tfm.rotation(asQuaternion=True)

        jo_x = data.inputValue(self.aInJointOrientX).asAngle().asRadians()
        jo_y = data.inputValue(self.aInJointOrientY).asAngle().asRadians()
        jo_z = data.inputValue(self.aInJointOrientZ).asAngle().asRadians()
        q_jo = om.MEulerRotation(jo_x, jo_y, jo_z).asQuaternion()

        ra_x = data.inputValue(self.aInRotateAxisX).asAngle().asRadians()
        ra_y = data.inputValue(self.aInRotateAxisY).asAngle().asRadians()
        ra_z = data.inputValue(self.aInRotateAxisZ).asAngle().asRadians()
        has_ra = abs(ra_x) > 1e-8 or abs(ra_y) > 1e-8 or abs(ra_z) > 1e-8

        if has_ra:
            q_ra = om.MEulerRotation(ra_x, ra_y, ra_z).asQuaternion()
            rotate_quat = q_ra.inverse() * total_quat * q_jo.inverse()
        else:
            rotate_quat = total_quat * q_jo.inverse()

        ro_index = data.inputValue(self.aInRotateOrder).asShort()
        ro = _ROTATE_ORDERS[ro_index] if 0 <= ro_index < len(_ROTATE_ORDERS) else om.MEulerRotation.kXYZ
        rotate_euler = rotate_quat.asEulerRotation()
        rotate_euler.reorderIt(ro)

        out_t = data.outputValue(self.aOutTranslate)
        out_t.set3Double(translate.x, translate.y, translate.z)
        out_r = data.outputValue(self.aOutRotate)
        out_r.set3Double(rotate_euler.x, rotate_euler.y, rotate_euler.z)

        data.setClean(self.aOutTranslate)
        data.setClean(self.aOutRotate)

    @staticmethod
    def _extract_matrix(arr, bone_index: int) -> om.MMatrix:
        offset = bone_index * 16
        values = [arr[offset + i] for i in range(16)]
        return om.MMatrix(values)

    def _write_identity(self, data) -> None:
        data.outputValue(self.aOutTranslate).set3Double(0.0, 0.0, 0.0)
        data.outputValue(self.aOutRotate).set3Double(0.0, 0.0, 0.0)
        data.setClean(self.aOutTranslate)
        data.setClean(self.aOutRotate)


def creator():
    return MmdPhysicsBoneDriverNode()


def initialize():
    tAttr = om.MFnTypedAttribute()
    nAttr = om.MFnNumericAttribute()
    uAttr = om.MFnUnitAttribute()
    mAttr = om.MFnMatrixAttribute()
    eAttr = om.MFnEnumAttribute()
    cAttr = om.MFnCompoundAttribute()

    # --- Inputs ---

    MmdPhysicsBoneDriverNode.aInSolverBoneMatrices = tAttr.create(
        "inSolverBoneMatrices", "isbm", om.MFnData.kDoubleArray
    )
    tAttr.storable = False
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInSolverBoneMatrices)

    MmdPhysicsBoneDriverNode.aInSolverBoneCount = nAttr.create(
        "inSolverBoneCount", "isbc", om.MFnNumericData.kInt, 0
    )
    nAttr.storable = False
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInSolverBoneCount)

    MmdPhysicsBoneDriverNode.aInBoneIndex = nAttr.create(
        "inBoneIndex", "ibi", om.MFnNumericData.kInt, -1
    )
    nAttr.storable = True
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInBoneIndex)

    MmdPhysicsBoneDriverNode.aInParentBoneIndex = nAttr.create(
        "inParentBoneIndex", "ipbi", om.MFnNumericData.kInt, -1
    )
    nAttr.storable = True
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInParentBoneIndex)

    MmdPhysicsBoneDriverNode.aInParentInverseMatrix = mAttr.create(
        "inParentInverseMatrix", "ipim"
    )
    mAttr.storable = False
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInParentInverseMatrix)

    # Joint orient (angle compound)
    MmdPhysicsBoneDriverNode.aInJointOrientX = uAttr.create(
        "inJointOrientX", "ijox", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdPhysicsBoneDriverNode.aInJointOrientY = uAttr.create(
        "inJointOrientY", "ijoy", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdPhysicsBoneDriverNode.aInJointOrientZ = uAttr.create(
        "inJointOrientZ", "ijoz", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdPhysicsBoneDriverNode.aInJointOrient = cAttr.create("inJointOrient", "ijo")
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInJointOrientX)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInJointOrientY)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInJointOrientZ)
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInJointOrient)

    # Rotate axis (angle compound)
    MmdPhysicsBoneDriverNode.aInRotateAxisX = uAttr.create(
        "inRotateAxisX", "irax", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdPhysicsBoneDriverNode.aInRotateAxisY = uAttr.create(
        "inRotateAxisY", "iray", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdPhysicsBoneDriverNode.aInRotateAxisZ = uAttr.create(
        "inRotateAxisZ", "iraz", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdPhysicsBoneDriverNode.aInRotateAxis = cAttr.create("inRotateAxis", "ira")
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInRotateAxisX)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInRotateAxisY)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInRotateAxisZ)
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInRotateAxis)

    MmdPhysicsBoneDriverNode.aInRotateOrder = eAttr.create("inRotateOrder", "iro", 0)
    eAttr.addField("xyz", 0)
    eAttr.addField("yzx", 1)
    eAttr.addField("zxy", 2)
    eAttr.addField("xzy", 3)
    eAttr.addField("yxz", 4)
    eAttr.addField("zyx", 5)
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInRotateOrder)

    MmdPhysicsBoneDriverNode.aInSolved = nAttr.create(
        "inSolved", "isv", om.MFnNumericData.kBoolean, False
    )
    nAttr.storable = False
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInSolved)

    MmdPhysicsBoneDriverNode.aEnable = nAttr.create(
        "enable", "en", om.MFnNumericData.kBoolean, True
    )
    nAttr.storable = True
    nAttr.keyable = True
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aEnable)

    # --- Outputs ---

    MmdPhysicsBoneDriverNode.aOutTranslateX = nAttr.create(
        "outTranslateX", "otx", om.MFnNumericData.kDouble, 0.0
    )
    nAttr.writable = False
    nAttr.storable = False
    MmdPhysicsBoneDriverNode.aOutTranslateY = nAttr.create(
        "outTranslateY", "oty", om.MFnNumericData.kDouble, 0.0
    )
    nAttr.writable = False
    nAttr.storable = False
    MmdPhysicsBoneDriverNode.aOutTranslateZ = nAttr.create(
        "outTranslateZ", "otz", om.MFnNumericData.kDouble, 0.0
    )
    nAttr.writable = False
    nAttr.storable = False
    MmdPhysicsBoneDriverNode.aOutTranslate = cAttr.create("outTranslate", "ot")
    cAttr.addChild(MmdPhysicsBoneDriverNode.aOutTranslateX)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aOutTranslateY)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aOutTranslateZ)
    cAttr.writable = False
    cAttr.storable = False
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aOutTranslate)

    MmdPhysicsBoneDriverNode.aOutRotateX = uAttr.create(
        "outRotateX", "orx", om.MFnUnitAttribute.kAngle, 0.0
    )
    uAttr.writable = False
    uAttr.storable = False
    MmdPhysicsBoneDriverNode.aOutRotateY = uAttr.create(
        "outRotateY", "ory", om.MFnUnitAttribute.kAngle, 0.0
    )
    uAttr.writable = False
    uAttr.storable = False
    MmdPhysicsBoneDriverNode.aOutRotateZ = uAttr.create(
        "outRotateZ", "orz", om.MFnUnitAttribute.kAngle, 0.0
    )
    uAttr.writable = False
    uAttr.storable = False
    MmdPhysicsBoneDriverNode.aOutRotate = cAttr.create("outRotate", "or")
    cAttr.addChild(MmdPhysicsBoneDriverNode.aOutRotateX)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aOutRotateY)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aOutRotateZ)
    cAttr.writable = False
    cAttr.storable = False
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aOutRotate)

    # --- Affects ---
    inputs = [
        MmdPhysicsBoneDriverNode.aInSolverBoneMatrices,
        MmdPhysicsBoneDriverNode.aInSolverBoneCount,
        MmdPhysicsBoneDriverNode.aInBoneIndex,
        MmdPhysicsBoneDriverNode.aInParentBoneIndex,
        MmdPhysicsBoneDriverNode.aInParentInverseMatrix,
        MmdPhysicsBoneDriverNode.aInJointOrient,
        MmdPhysicsBoneDriverNode.aInRotateAxis,
        MmdPhysicsBoneDriverNode.aInRotateOrder,
        MmdPhysicsBoneDriverNode.aInSolved,
        MmdPhysicsBoneDriverNode.aEnable,
    ]
    outputs = [
        MmdPhysicsBoneDriverNode.aOutTranslate,
        MmdPhysicsBoneDriverNode.aOutRotate,
    ]
    for inp in inputs:
        for out in outputs:
            MmdPhysicsBoneDriverNode.attributeAffects(inp, out)


def register(plugin_fn):
    plugin_fn.registerNode(
        MmdPhysicsBoneDriverNode.kTypeName,
        MmdPhysicsBoneDriverNode.kTypeId,
        creator,
        initialize,
        om.MPxNode.kDependNode,
        MmdPhysicsBoneDriverNode.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdPhysicsBoneDriverNode.kTypeId)
    except Exception:
        pass
