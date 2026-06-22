"""mmdAppend — MMD 付与変形を計算する DG ノード (Python MPxNode prototype).

mmd-anim FFI の MmdAppendSolver を内部で呼び出し、
source joint の回転/移動から grant contribution を計算して出力する。

DG 接続例:
    source_joint.rotate  → mmdAppend1.sourceRotate
    mmdAppend1.outputRotate → target_joint.rotate
"""

from __future__ import annotations

import maya.api.OpenMaya as om

from mmd_tools.core.native.mmd_anim_runtime import MmdAppendSolver, is_rig_primitive_available


def maya_useNewAPI():
    pass


# ── Node definition ──────────────────────────────────────────────

class MmdAppendNode(om.MPxNode):
    kTypeName = "mmdAppend"
    kTypeId = om.MTypeId(0x00128001)
    kClassify = "utility/general"

    # attr objects (set in initialize())
    aSourceRotate = None
    aSourceRotateX = None
    aSourceRotateY = None
    aSourceRotateZ = None

    aSourceTranslate = None
    aSourceTranslateX = None
    aSourceTranslateY = None
    aSourceTranslateZ = None

    aRatio = None
    aAffectRotation = None
    aAffectTranslation = None

    aOutputRotate = None
    aOutputRotateX = None
    aOutputRotateY = None
    aOutputRotateZ = None

    aOutputTranslate = None
    aOutputTranslateX = None
    aOutputTranslateY = None
    aOutputTranslateZ = None

    def __init__(self):
        super().__init__()
        self._solver = None
        self._cached_ratio = None
        self._cached_affect_rot = None
        self._cached_affect_trans = None

    def _ensure_solver(self, ratio: float, affect_rot: bool, affect_trans: bool):
        if (
            self._solver is not None
            and self._cached_ratio == ratio
            and self._cached_affect_rot == affect_rot
            and self._cached_affect_trans == affect_trans
        ):
            return
        if self._solver is not None:
            self._solver.free()
            self._solver = None
        self._solver = MmdAppendSolver.create(
            ratio=ratio,
            affect_rotation=affect_rot,
            affect_translation=affect_trans,
        )
        self._cached_ratio = ratio
        self._cached_affect_rot = affect_rot
        self._cached_affect_trans = affect_trans

    def compute(self, plug, data):
        is_rot_plug = (
            plug == self.aOutputRotate
            or plug == self.aOutputRotateX
            or plug == self.aOutputRotateY
            or plug == self.aOutputRotateZ
        )
        is_trans_plug = (
            plug == self.aOutputTranslate
            or plug == self.aOutputTranslateX
            or plug == self.aOutputTranslateY
            or plug == self.aOutputTranslateZ
        )
        if not is_rot_plug and not is_trans_plug:
            return None  # let Maya handle unknown plugs

        ratio = data.inputValue(self.aRatio).asFloat()
        affect_rot = data.inputValue(self.aAffectRotation).asBool()
        affect_trans = data.inputValue(self.aAffectTranslation).asBool()

        self._ensure_solver(ratio, affect_rot, affect_trans)
        if self._solver is None:
            data.setClean(plug)
            return

        # kAngle attrs store radians internally; asDouble() returns radians
        src_rx = data.inputValue(self.aSourceRotateX).asDouble()
        src_ry = data.inputValue(self.aSourceRotateY).asDouble()
        src_rz = data.inputValue(self.aSourceRotateZ).asDouble()

        euler = om.MEulerRotation(src_rx, src_ry, src_rz)
        quat = euler.asQuaternion()

        # Read source translate
        src_tx = data.inputValue(self.aSourceTranslateX).asDouble()
        src_ty = data.inputValue(self.aSourceTranslateY).asDouble()
        src_tz = data.inputValue(self.aSourceTranslateZ).asDouble()

        result = self._solver.solve(
            source_position=[src_tx, src_ty, src_tz],
            source_rotation=[quat.x, quat.y, quat.z, quat.w],
        )
        if result is None:
            data.setClean(plug)
            return

        out_pos, out_rot = result

        out_quat = om.MQuaternion(out_rot[0], out_rot[1], out_rot[2], out_rot[3])
        out_euler = out_quat.asEulerRotation()

        # kAngle output attrs expect radians
        out_rot_handle = data.outputValue(self.aOutputRotate)
        out_rot_handle.set3Double(out_euler.x, out_euler.y, out_euler.z)
        out_rot_handle.setClean()

        out_trans_handle = data.outputValue(self.aOutputTranslate)
        out_trans_handle.set3Double(out_pos[0], out_pos[1], out_pos[2])
        out_trans_handle.setClean()

        data.setClean(plug)

    def __del__(self):
        if self._solver is not None:
            try:
                self._solver.free()
            except Exception:
                pass


# ── Creator / Initializer ────────────────────────────────────────

def creator():
    return MmdAppendNode()


def initialize():
    nAttr = om.MFnNumericAttribute()
    uAttr = om.MFnUnitAttribute()

    # --- Source Rotate (input, angle) ---
    MmdAppendNode.aSourceRotateX = uAttr.create("sourceRotateX", "srx", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aSourceRotateY = uAttr.create("sourceRotateY", "sry", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aSourceRotateZ = uAttr.create("sourceRotateZ", "srz", om.MFnUnitAttribute.kAngle, 0.0)

    cAttr = om.MFnCompoundAttribute()
    MmdAppendNode.aSourceRotate = cAttr.create("sourceRotate", "sr")
    cAttr.addChild(MmdAppendNode.aSourceRotateX)
    cAttr.addChild(MmdAppendNode.aSourceRotateY)
    cAttr.addChild(MmdAppendNode.aSourceRotateZ)
    cAttr.keyable = True
    MmdAppendNode.addAttribute(MmdAppendNode.aSourceRotate)

    # --- Source Translate (input) ---
    MmdAppendNode.aSourceTranslateX = nAttr.create("sourceTranslateX", "stx", om.MFnNumericData.kDouble, 0.0)
    MmdAppendNode.aSourceTranslateY = nAttr.create("sourceTranslateY", "sty", om.MFnNumericData.kDouble, 0.0)
    MmdAppendNode.aSourceTranslateZ = nAttr.create("sourceTranslateZ", "stz", om.MFnNumericData.kDouble, 0.0)

    MmdAppendNode.aSourceTranslate = cAttr.create("sourceTranslate", "st")
    cAttr.addChild(MmdAppendNode.aSourceTranslateX)
    cAttr.addChild(MmdAppendNode.aSourceTranslateY)
    cAttr.addChild(MmdAppendNode.aSourceTranslateZ)
    cAttr.keyable = True
    MmdAppendNode.addAttribute(MmdAppendNode.aSourceTranslate)

    # --- Parameters ---
    MmdAppendNode.aRatio = nAttr.create("ratio", "rat", om.MFnNumericData.kFloat, 1.0)
    nAttr.keyable = True
    MmdAppendNode.addAttribute(MmdAppendNode.aRatio)

    MmdAppendNode.aAffectRotation = nAttr.create("affectRotation", "afr", om.MFnNumericData.kBoolean, True)
    MmdAppendNode.addAttribute(MmdAppendNode.aAffectRotation)

    MmdAppendNode.aAffectTranslation = nAttr.create("affectTranslation", "aft", om.MFnNumericData.kBoolean, False)
    MmdAppendNode.addAttribute(MmdAppendNode.aAffectTranslation)

    # --- Output Rotate (angle) ---
    MmdAppendNode.aOutputRotateX = uAttr.create("outputRotateX", "orx", om.MFnUnitAttribute.kAngle, 0.0)
    uAttr.writable = False
    uAttr.storable = False
    MmdAppendNode.aOutputRotateY = uAttr.create("outputRotateY", "ory", om.MFnUnitAttribute.kAngle, 0.0)
    uAttr.writable = False
    uAttr.storable = False
    MmdAppendNode.aOutputRotateZ = uAttr.create("outputRotateZ", "orz", om.MFnUnitAttribute.kAngle, 0.0)
    uAttr.writable = False
    uAttr.storable = False

    MmdAppendNode.aOutputRotate = cAttr.create("outputRotate", "or")
    cAttr.writable = False
    cAttr.storable = False
    cAttr.addChild(MmdAppendNode.aOutputRotateX)
    cAttr.addChild(MmdAppendNode.aOutputRotateY)
    cAttr.addChild(MmdAppendNode.aOutputRotateZ)
    MmdAppendNode.addAttribute(MmdAppendNode.aOutputRotate)

    # --- Output Translate ---
    MmdAppendNode.aOutputTranslateX = nAttr.create("outputTranslateX", "otx", om.MFnNumericData.kDouble, 0.0)
    nAttr.writable = False
    nAttr.storable = False
    MmdAppendNode.aOutputTranslateY = nAttr.create("outputTranslateY", "oty", om.MFnNumericData.kDouble, 0.0)
    nAttr.writable = False
    nAttr.storable = False
    MmdAppendNode.aOutputTranslateZ = nAttr.create("outputTranslateZ", "otz", om.MFnNumericData.kDouble, 0.0)
    nAttr.writable = False
    nAttr.storable = False

    MmdAppendNode.aOutputTranslate = cAttr.create("outputTranslate", "ot")
    cAttr.writable = False
    cAttr.storable = False
    cAttr.addChild(MmdAppendNode.aOutputTranslateX)
    cAttr.addChild(MmdAppendNode.aOutputTranslateY)
    cAttr.addChild(MmdAppendNode.aOutputTranslateZ)
    MmdAppendNode.addAttribute(MmdAppendNode.aOutputTranslate)

    # --- Affect relationships ---
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceRotateX, MmdAppendNode.aOutputRotateX)
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceRotateX, MmdAppendNode.aOutputRotateY)
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceRotateX, MmdAppendNode.aOutputRotateZ)
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceRotateY, MmdAppendNode.aOutputRotateX)
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceRotateY, MmdAppendNode.aOutputRotateY)
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceRotateY, MmdAppendNode.aOutputRotateZ)
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceRotateZ, MmdAppendNode.aOutputRotateX)
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceRotateZ, MmdAppendNode.aOutputRotateY)
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceRotateZ, MmdAppendNode.aOutputRotateZ)

    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceTranslateX, MmdAppendNode.aOutputTranslateX)
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceTranslateY, MmdAppendNode.aOutputTranslateY)
    MmdAppendNode.attributeAffects(MmdAppendNode.aSourceTranslateZ, MmdAppendNode.aOutputTranslateZ)

    MmdAppendNode.attributeAffects(MmdAppendNode.aRatio, MmdAppendNode.aOutputRotateX)
    MmdAppendNode.attributeAffects(MmdAppendNode.aRatio, MmdAppendNode.aOutputRotateY)
    MmdAppendNode.attributeAffects(MmdAppendNode.aRatio, MmdAppendNode.aOutputRotateZ)
    MmdAppendNode.attributeAffects(MmdAppendNode.aRatio, MmdAppendNode.aOutputTranslateX)
    MmdAppendNode.attributeAffects(MmdAppendNode.aRatio, MmdAppendNode.aOutputTranslateY)
    MmdAppendNode.attributeAffects(MmdAppendNode.aRatio, MmdAppendNode.aOutputTranslateZ)

    MmdAppendNode.attributeAffects(MmdAppendNode.aAffectRotation, MmdAppendNode.aOutputRotateX)
    MmdAppendNode.attributeAffects(MmdAppendNode.aAffectRotation, MmdAppendNode.aOutputRotateY)
    MmdAppendNode.attributeAffects(MmdAppendNode.aAffectRotation, MmdAppendNode.aOutputRotateZ)

    MmdAppendNode.attributeAffects(MmdAppendNode.aAffectTranslation, MmdAppendNode.aOutputTranslateX)
    MmdAppendNode.attributeAffects(MmdAppendNode.aAffectTranslation, MmdAppendNode.aOutputTranslateY)
    MmdAppendNode.attributeAffects(MmdAppendNode.aAffectTranslation, MmdAppendNode.aOutputTranslateZ)


# ── Plugin registration helpers ──────────────────────────────────

def register(plugin_fn):
    """Call from the host plugin's initializePlugin."""
    if not is_rig_primitive_available():
        om.MGlobal.displayWarning("mmd-anim DLL not available — mmdAppend node not registered")
        return
    plugin_fn.registerNode(
        MmdAppendNode.kTypeName,
        MmdAppendNode.kTypeId,
        creator,
        initialize,
        om.MPxNode.kDependNode,
        MmdAppendNode.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdAppendNode.kTypeId)
    except Exception:
        pass
