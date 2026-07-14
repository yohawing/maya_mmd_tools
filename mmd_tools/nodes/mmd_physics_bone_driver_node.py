"""mmdPhysicsBoneDriver — Solver-space world matrix to Maya local channels.

Reads one bone's world matrix from the mmdPhysicsSolver's flat output array,
applies the Maya bind-pose correction used by the existing IK path, then
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
    aInBindWorldMatrix = None
    aInNoOrientBindWorldMatrix = None
    aInParentBindWorldMatrix = None
    aInParentNoOrientBindWorldMatrix = None
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

    # Pre-physics VMD input (populated by recovery after VMD import)
    aInPreTranslate = None
    aInPreTranslateX = None
    aInPreTranslateY = None
    aInPreTranslateZ = None
    aInPreRotate = None
    aInPreRotateX = None
    aInPreRotateY = None
    aInPreRotateZ = None
    aOutPrePhysicsWorldMatrix = None

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
            self.aOutPrePhysicsWorldMatrix,
        ):
            return True
        if plug.isChild:
            return self._is_output_plug(plug.parent())
        return False

    def compute(self, plug, data):
        if not self._is_output_plug(plug):
            return None

        if plug.attribute() == self.aOutPrePhysicsWorldMatrix:
            self._compute_pre_physics_world_matrix(data)
            return

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

        bone_world = self._apply_bind_correction(
            bone_world=self._extract_matrix(arr, bone_index),
            bind_world=data.inputValue(self.aInBindWorldMatrix).asMatrix(),
            no_orient_bind_world=data.inputValue(self.aInNoOrientBindWorldMatrix).asMatrix(),
        )

        if 0 <= parent_bone_index < bone_count:
            parent_world = self._apply_bind_correction(
                bone_world=self._extract_matrix(arr, parent_bone_index),
                bind_world=data.inputValue(self.aInParentBindWorldMatrix).asMatrix(),
                no_orient_bind_world=data.inputValue(
                    self.aInParentNoOrientBindWorldMatrix
                ).asMatrix(),
            )
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

    @staticmethod
    def _apply_bind_correction(
        *,
        bone_world: om.MMatrix,
        bind_world: om.MMatrix,
        no_orient_bind_world: om.MMatrix,
    ) -> om.MMatrix:
        """Map runtime PMX world space into the Maya bind-oriented world space."""
        return bind_world * no_orient_bind_world.inverse() * bone_world

    def _compute_pre_physics_world_matrix(self, data) -> None:
        """Compose pre-physics world matrix from VMD animCurve-driven local T/R."""
        tx = data.inputValue(self.aInPreTranslateX).asDouble()
        ty = data.inputValue(self.aInPreTranslateY).asDouble()
        tz = data.inputValue(self.aInPreTranslateZ).asDouble()

        rx = data.inputValue(self.aInPreRotateX).asAngle().asRadians()
        ry = data.inputValue(self.aInPreRotateY).asAngle().asRadians()
        rz = data.inputValue(self.aInPreRotateZ).asAngle().asRadians()

        jo_x = data.inputValue(self.aInJointOrientX).asAngle().asRadians()
        jo_y = data.inputValue(self.aInJointOrientY).asAngle().asRadians()
        jo_z = data.inputValue(self.aInJointOrientZ).asAngle().asRadians()

        ra_x = data.inputValue(self.aInRotateAxisX).asAngle().asRadians()
        ra_y = data.inputValue(self.aInRotateAxisY).asAngle().asRadians()
        ra_z = data.inputValue(self.aInRotateAxisZ).asAngle().asRadians()

        ro_index = data.inputValue(self.aInRotateOrder).asShort()
        ro = _ROTATE_ORDERS[ro_index] if 0 <= ro_index < len(_ROTATE_ORDERS) else om.MEulerRotation.kXYZ

        # Joint local: T(translate) * R(jointOrient) * R(rotate, order) * R(rotateAxis)
        tfm = om.MTransformationMatrix()
        tfm.setTranslation(om.MVector(tx, ty, tz), om.MSpace.kTransform)

        q_jo = om.MEulerRotation(jo_x, jo_y, jo_z).asQuaternion()
        q_r = om.MEulerRotation(rx, ry, rz, ro).asQuaternion()
        q_ra = om.MEulerRotation(ra_x, ra_y, ra_z).asQuaternion()
        tfm.setRotation(q_jo * q_r * q_ra)

        local_mat = tfm.asMatrix()

        parent_inv = data.inputValue(self.aInParentInverseMatrix).asMatrix()
        parent_world = parent_inv.inverse()

        pre_world = local_mat * parent_world
        out_handle = data.outputValue(self.aOutPrePhysicsWorldMatrix)
        out_handle.setMMatrix(pre_world)
        data.setClean(self.aOutPrePhysicsWorldMatrix)

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

    # Runtime world matrices have PMX/rest-bone orientation.  These bind
    # matrices match the correction used by mmdCcdIk:
    # bindWorld * noOrientBindWorld.inverse() * runtimeWorld.
    MmdPhysicsBoneDriverNode.aInBindWorldMatrix = mAttr.create(
        "inBindWorldMatrix", "ibwm"
    )
    # These are imported bind-pose constants and must survive save/reopen.
    mAttr.storable = True
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInBindWorldMatrix)

    MmdPhysicsBoneDriverNode.aInNoOrientBindWorldMatrix = mAttr.create(
        "inNoOrientBindWorldMatrix", "inobwm"
    )
    mAttr.storable = True
    MmdPhysicsBoneDriverNode.addAttribute(
        MmdPhysicsBoneDriverNode.aInNoOrientBindWorldMatrix
    )

    MmdPhysicsBoneDriverNode.aInParentBindWorldMatrix = mAttr.create(
        "inParentBindWorldMatrix", "ipbwm"
    )
    mAttr.storable = True
    MmdPhysicsBoneDriverNode.addAttribute(
        MmdPhysicsBoneDriverNode.aInParentBindWorldMatrix
    )

    MmdPhysicsBoneDriverNode.aInParentNoOrientBindWorldMatrix = mAttr.create(
        "inParentNoOrientBindWorldMatrix", "ipnobwm"
    )
    mAttr.storable = True
    MmdPhysicsBoneDriverNode.addAttribute(
        MmdPhysicsBoneDriverNode.aInParentNoOrientBindWorldMatrix
    )

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

    # --- Pre-physics VMD inputs ---

    MmdPhysicsBoneDriverNode.aInPreTranslateX = nAttr.create(
        "inPreTranslateX", "iptx", om.MFnNumericData.kDouble, 0.0
    )
    MmdPhysicsBoneDriverNode.aInPreTranslateY = nAttr.create(
        "inPreTranslateY", "ipty", om.MFnNumericData.kDouble, 0.0
    )
    MmdPhysicsBoneDriverNode.aInPreTranslateZ = nAttr.create(
        "inPreTranslateZ", "iptz", om.MFnNumericData.kDouble, 0.0
    )
    MmdPhysicsBoneDriverNode.aInPreTranslate = cAttr.create("inPreTranslate", "ipt")
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInPreTranslateX)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInPreTranslateY)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInPreTranslateZ)
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInPreTranslate)

    MmdPhysicsBoneDriverNode.aInPreRotateX = uAttr.create(
        "inPreRotateX", "iprx", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdPhysicsBoneDriverNode.aInPreRotateY = uAttr.create(
        "inPreRotateY", "ipry", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdPhysicsBoneDriverNode.aInPreRotateZ = uAttr.create(
        "inPreRotateZ", "iprz", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdPhysicsBoneDriverNode.aInPreRotate = cAttr.create("inPreRotate", "ipr")
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInPreRotateX)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInPreRotateY)
    cAttr.addChild(MmdPhysicsBoneDriverNode.aInPreRotateZ)
    MmdPhysicsBoneDriverNode.addAttribute(MmdPhysicsBoneDriverNode.aInPreRotate)

    MmdPhysicsBoneDriverNode.aOutPrePhysicsWorldMatrix = mAttr.create(
        "outPrePhysicsWorldMatrix", "oppwm"
    )
    mAttr.writable = False
    mAttr.storable = False
    MmdPhysicsBoneDriverNode.addAttribute(
        MmdPhysicsBoneDriverNode.aOutPrePhysicsWorldMatrix
    )

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
        MmdPhysicsBoneDriverNode.aInBindWorldMatrix,
        MmdPhysicsBoneDriverNode.aInNoOrientBindWorldMatrix,
        MmdPhysicsBoneDriverNode.aInParentBindWorldMatrix,
        MmdPhysicsBoneDriverNode.aInParentNoOrientBindWorldMatrix,
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

    pre_inputs = [
        MmdPhysicsBoneDriverNode.aInPreTranslate,
        MmdPhysicsBoneDriverNode.aInPreRotate,
        MmdPhysicsBoneDriverNode.aInJointOrient,
        MmdPhysicsBoneDriverNode.aInRotateAxis,
        MmdPhysicsBoneDriverNode.aInRotateOrder,
        MmdPhysicsBoneDriverNode.aInParentInverseMatrix,
    ]
    for inp in pre_inputs:
        MmdPhysicsBoneDriverNode.attributeAffects(
            inp, MmdPhysicsBoneDriverNode.aOutPrePhysicsWorldMatrix
        )


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
