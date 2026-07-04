"""mmdAppend — MMD 付与変形を計算する DG ノード (Python MPxNode prototype).

mmd-anim FFI の MmdAppendSolver を内部で呼び出し、
source joint の回転/移動から grant contribution を計算し、
baseRotate/baseTranslate と合成して出力する。

output = base * slerp(identity, source, ratio)

DG 接続例 (パイプライン統合):
    animCurve → mmdAppend1.baseRotate     (ボーン自身のアニメ回転)
    source_joint.rotate → mmdAppend1.sourceRotate (付与元の回転)
    mmdAppend1.outputRotate → target_joint.rotate
"""

from __future__ import annotations

import maya.api.OpenMaya as om

from mmd_tools.core.native.mmd_anim_runtime import MmdAppendSolver, is_rig_primitive_available


def maya_useNewAPI():
    pass


class MmdAppendNode(om.MPxNode):
    kTypeName = "mmdAppend"
    kTypeId = om.MTypeId(0x00128001)
    kClassify = "utility/general"

    aBaseRotate = None
    aBaseRotateX = None
    aBaseRotateY = None
    aBaseRotateZ = None

    aBaseTranslate = None
    aBaseTranslateX = None
    aBaseTranslateY = None
    aBaseTranslateZ = None

    aSourceRotate = None
    aSourceRotateX = None
    aSourceRotateY = None
    aSourceRotateZ = None

    aSourceJointOrient = None
    aSourceJointOrientX = None
    aSourceJointOrientY = None
    aSourceJointOrientZ = None

    aTargetJointOrient = None
    aTargetJointOrientX = None
    aTargetJointOrientY = None
    aTargetJointOrientZ = None

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

    aAppendRotate = None
    aAppendRotateX = None
    aAppendRotateY = None
    aAppendRotateZ = None

    aAppendTranslate = None
    aAppendTranslateX = None
    aAppendTranslateY = None
    aAppendTranslateZ = None

    aLocalAppend = None

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

    @staticmethod
    def _maya_translate_to_mmd(tx: float, ty: float, tz: float) -> list[float]:
        """Convert a Maya-space translation offset to MMD-space."""
        return [float(tx), float(ty), -float(tz)]

    @staticmethod
    def _mmd_translate_to_maya(translate: list[float]) -> tuple[float, float, float]:
        """Convert an MMD-space translation offset to Maya-space."""
        return (float(translate[0]), float(translate[1]), -float(translate[2]))

    @staticmethod
    def _plug_matches_any(plug, attributes):
        """Return whether *plug* matches any initialized output attribute."""
        for attr in attributes:
            if attr is None:
                continue
            try:
                if plug == attr:
                    return True
            except TypeError:
                pass
            except Exception:
                pass
            try:
                if plug.attribute() == attr:
                    return True
            except Exception:
                pass
        return False

    def compute(self, plug, data):
        N = type(self)
        is_rot_plug = self._plug_matches_any(
            plug,
            (
                N.aOutputRotate,
                N.aOutputRotateX,
                N.aOutputRotateY,
                N.aOutputRotateZ,
                N.aAppendRotate,
                N.aAppendRotateX,
                N.aAppendRotateY,
                N.aAppendRotateZ,
            ),
        )
        is_trans_plug = self._plug_matches_any(
            plug,
            (
                N.aOutputTranslate,
                N.aOutputTranslateX,
                N.aOutputTranslateY,
                N.aOutputTranslateZ,
                N.aAppendTranslate,
                N.aAppendTranslateX,
                N.aAppendTranslateY,
                N.aAppendTranslateZ,
            ),
        )
        if not is_rot_plug and not is_trans_plug:
            return None  # let Maya handle unknown plugs

        ratio = data.inputValue(N.aRatio).asFloat()
        affect_rot = data.inputValue(N.aAffectRotation).asBool()
        affect_trans = data.inputValue(N.aAffectTranslation).asBool()

        self._ensure_solver(ratio, affect_rot, affect_trans)
        if self._solver is None:
            data.setClean(plug)
            return

        # kAngle attrs store radians internally; asDouble() returns radians
        src_rx = data.inputValue(N.aSourceRotateX).asDouble()
        src_ry = data.inputValue(N.aSourceRotateY).asDouble()
        src_rz = data.inputValue(N.aSourceRotateZ).asDouble()
        src_quat = om.MEulerRotation(src_rx, src_ry, src_rz).asQuaternion()
        src_jo = om.MEulerRotation(
            data.inputValue(N.aSourceJointOrientX).asDouble(),
            data.inputValue(N.aSourceJointOrientY).asDouble(),
            data.inputValue(N.aSourceJointOrientZ).asDouble(),
        ).asQuaternion()
        target_jo = om.MEulerRotation(
            data.inputValue(N.aTargetJointOrientX).asDouble(),
            data.inputValue(N.aTargetJointOrientY).asDouble(),
            data.inputValue(N.aTargetJointOrientZ).asDouble(),
        ).asQuaternion()
        source_mmd_quat = src_jo.inverse() * src_quat * src_jo

        src_tx = data.inputValue(N.aSourceTranslateX).asDouble()
        src_ty = data.inputValue(N.aSourceTranslateY).asDouble()
        src_tz = data.inputValue(N.aSourceTranslateZ).asDouble()

        source_position_mmd = self._maya_translate_to_mmd(src_tx, src_ty, src_tz)
        result = self._solver.solve(
            source_position=source_position_mmd,
            source_rotation=[
                source_mmd_quat.x,
                source_mmd_quat.y,
                source_mmd_quat.z,
                source_mmd_quat.w,
            ],
        )
        if result is None:
            data.setClean(plug)
            return

        grant_pos, grant_rot = result
        grant_tx, grant_ty, grant_tz = self._mmd_translate_to_maya(grant_pos)
        grant_quat = om.MQuaternion(grant_rot[0], grant_rot[1], grant_rot[2], grant_rot[3])
        grant_euler = grant_quat.asEulerRotation()
        target_grant_quat = target_jo * grant_quat * target_jo.inverse()

        append_rot_handle = data.outputValue(N.aAppendRotate)
        append_rot_handle.set3Double(grant_euler.x, grant_euler.y, grant_euler.z)
        append_rot_handle.setClean()

        append_trans_handle = data.outputValue(N.aAppendTranslate)
        append_trans_handle.set3Double(grant_tx, grant_ty, grant_tz)
        append_trans_handle.setClean()

        # Compose: output = base * grant_contribution
        base_rx = data.inputValue(N.aBaseRotateX).asDouble()
        base_ry = data.inputValue(N.aBaseRotateY).asDouble()
        base_rz = data.inputValue(N.aBaseRotateZ).asDouble()
        base_quat = om.MEulerRotation(base_rx, base_ry, base_rz).asQuaternion()
        final_quat = base_quat * target_grant_quat
        final_euler = final_quat.asEulerRotation()

        out_rot_handle = data.outputValue(N.aOutputRotate)
        out_rot_handle.set3Double(final_euler.x, final_euler.y, final_euler.z)
        out_rot_handle.setClean()

        base_tx = data.inputValue(N.aBaseTranslateX).asDouble()
        base_ty = data.inputValue(N.aBaseTranslateY).asDouble()
        base_tz = data.inputValue(N.aBaseTranslateZ).asDouble()

        out_trans_handle = data.outputValue(N.aOutputTranslate)
        out_trans_handle.set3Double(
            base_tx + grant_tx,
            base_ty + grant_ty,
            base_tz + grant_tz,
        )
        out_trans_handle.setClean()

        data.setClean(plug)

    def __del__(self):
        if self._solver is not None:
            try:
                self._solver.free()
            except Exception:
                pass


def creator():
    return MmdAppendNode()


def initialize():
    nAttr = om.MFnNumericAttribute()
    uAttr = om.MFnUnitAttribute()
    cAttr = om.MFnCompoundAttribute()

    # --- Base Rotate (input, angle) — bone's own animation rotation ---
    MmdAppendNode.aBaseRotateX = uAttr.create("baseRotateX", "brx", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aBaseRotateY = uAttr.create("baseRotateY", "bry", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aBaseRotateZ = uAttr.create("baseRotateZ", "brz", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aBaseRotate = cAttr.create("baseRotate", "br")
    cAttr.addChild(MmdAppendNode.aBaseRotateX)
    cAttr.addChild(MmdAppendNode.aBaseRotateY)
    cAttr.addChild(MmdAppendNode.aBaseRotateZ)
    cAttr.keyable = True
    MmdAppendNode.addAttribute(MmdAppendNode.aBaseRotate)

    # --- Base Translate (input) ---
    MmdAppendNode.aBaseTranslateX = nAttr.create("baseTranslateX", "btx", om.MFnNumericData.kDouble, 0.0)
    MmdAppendNode.aBaseTranslateY = nAttr.create("baseTranslateY", "bty", om.MFnNumericData.kDouble, 0.0)
    MmdAppendNode.aBaseTranslateZ = nAttr.create("baseTranslateZ", "btz", om.MFnNumericData.kDouble, 0.0)
    MmdAppendNode.aBaseTranslate = cAttr.create("baseTranslate", "bt")
    cAttr.addChild(MmdAppendNode.aBaseTranslateX)
    cAttr.addChild(MmdAppendNode.aBaseTranslateY)
    cAttr.addChild(MmdAppendNode.aBaseTranslateZ)
    cAttr.keyable = True
    MmdAppendNode.addAttribute(MmdAppendNode.aBaseTranslate)

    # --- Source Rotate (input, angle) ---
    MmdAppendNode.aSourceRotateX = uAttr.create("sourceRotateX", "srx", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aSourceRotateY = uAttr.create("sourceRotateY", "sry", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aSourceRotateZ = uAttr.create("sourceRotateZ", "srz", om.MFnUnitAttribute.kAngle, 0.0)

    MmdAppendNode.aSourceRotate = cAttr.create("sourceRotate", "sr")
    cAttr.addChild(MmdAppendNode.aSourceRotateX)
    cAttr.addChild(MmdAppendNode.aSourceRotateY)
    cAttr.addChild(MmdAppendNode.aSourceRotateZ)
    cAttr.keyable = True
    MmdAppendNode.addAttribute(MmdAppendNode.aSourceRotate)

    # --- JointOrient boundaries (input, angle) ---
    MmdAppendNode.aSourceJointOrientX = uAttr.create("sourceJointOrientX", "sjox", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aSourceJointOrientY = uAttr.create("sourceJointOrientY", "sjoy", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aSourceJointOrientZ = uAttr.create("sourceJointOrientZ", "sjoz", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aSourceJointOrient = cAttr.create("sourceJointOrient", "sjo")
    cAttr.addChild(MmdAppendNode.aSourceJointOrientX)
    cAttr.addChild(MmdAppendNode.aSourceJointOrientY)
    cAttr.addChild(MmdAppendNode.aSourceJointOrientZ)
    MmdAppendNode.addAttribute(MmdAppendNode.aSourceJointOrient)

    MmdAppendNode.aTargetJointOrientX = uAttr.create("targetJointOrientX", "tjox", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aTargetJointOrientY = uAttr.create("targetJointOrientY", "tjoy", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aTargetJointOrientZ = uAttr.create("targetJointOrientZ", "tjoz", om.MFnUnitAttribute.kAngle, 0.0)
    MmdAppendNode.aTargetJointOrient = cAttr.create("targetJointOrient", "tjo")
    cAttr.addChild(MmdAppendNode.aTargetJointOrientX)
    cAttr.addChild(MmdAppendNode.aTargetJointOrientY)
    cAttr.addChild(MmdAppendNode.aTargetJointOrientZ)
    MmdAppendNode.addAttribute(MmdAppendNode.aTargetJointOrient)

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

    MmdAppendNode.aLocalAppend = nAttr.create("localAppend", "lap", om.MFnNumericData.kBoolean, False)
    MmdAppendNode.addAttribute(MmdAppendNode.aLocalAppend)

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

    # --- Append contribution outputs ---
    MmdAppendNode.aAppendRotateX = uAttr.create("appendRotateX", "arx", om.MFnUnitAttribute.kAngle, 0.0)
    uAttr.writable = False
    uAttr.storable = False
    MmdAppendNode.aAppendRotateY = uAttr.create("appendRotateY", "ary", om.MFnUnitAttribute.kAngle, 0.0)
    uAttr.writable = False
    uAttr.storable = False
    MmdAppendNode.aAppendRotateZ = uAttr.create("appendRotateZ", "arz", om.MFnUnitAttribute.kAngle, 0.0)
    uAttr.writable = False
    uAttr.storable = False

    MmdAppendNode.aAppendRotate = cAttr.create("appendRotate", "ar")
    cAttr.writable = False
    cAttr.storable = False
    cAttr.addChild(MmdAppendNode.aAppendRotateX)
    cAttr.addChild(MmdAppendNode.aAppendRotateY)
    cAttr.addChild(MmdAppendNode.aAppendRotateZ)
    MmdAppendNode.addAttribute(MmdAppendNode.aAppendRotate)

    MmdAppendNode.aAppendTranslateX = nAttr.create("appendTranslateX", "atx", om.MFnNumericData.kDouble, 0.0)
    nAttr.writable = False
    nAttr.storable = False
    MmdAppendNode.aAppendTranslateY = nAttr.create("appendTranslateY", "aty", om.MFnNumericData.kDouble, 0.0)
    nAttr.writable = False
    nAttr.storable = False
    MmdAppendNode.aAppendTranslateZ = nAttr.create("appendTranslateZ", "atz", om.MFnNumericData.kDouble, 0.0)
    nAttr.writable = False
    nAttr.storable = False

    MmdAppendNode.aAppendTranslate = cAttr.create("appendTranslate", "at")
    cAttr.writable = False
    cAttr.storable = False
    cAttr.addChild(MmdAppendNode.aAppendTranslateX)
    cAttr.addChild(MmdAppendNode.aAppendTranslateY)
    cAttr.addChild(MmdAppendNode.aAppendTranslateZ)
    MmdAppendNode.addAttribute(MmdAppendNode.aAppendTranslate)

    # --- Affect relationships ---
    output_rotate_attrs = (
        MmdAppendNode.aOutputRotateX,
        MmdAppendNode.aOutputRotateY,
        MmdAppendNode.aOutputRotateZ,
    )
    append_rotate_attrs = (
        MmdAppendNode.aAppendRotateX,
        MmdAppendNode.aAppendRotateY,
        MmdAppendNode.aAppendRotateZ,
    )
    output_translate_attrs = (
        MmdAppendNode.aOutputTranslateX,
        MmdAppendNode.aOutputTranslateY,
        MmdAppendNode.aOutputTranslateZ,
    )
    append_translate_attrs = (
        MmdAppendNode.aAppendTranslateX,
        MmdAppendNode.aAppendTranslateY,
        MmdAppendNode.aAppendTranslateZ,
    )

    for base_attr in (MmdAppendNode.aBaseRotateX, MmdAppendNode.aBaseRotateY, MmdAppendNode.aBaseRotateZ):
        for out_attr in output_rotate_attrs:
            MmdAppendNode.attributeAffects(base_attr, out_attr)

    for base_attr in (MmdAppendNode.aBaseTranslateX, MmdAppendNode.aBaseTranslateY, MmdAppendNode.aBaseTranslateZ):
        for out_attr in output_translate_attrs:
            MmdAppendNode.attributeAffects(base_attr, out_attr)

    for source_attr in (MmdAppendNode.aSourceRotateX, MmdAppendNode.aSourceRotateY, MmdAppendNode.aSourceRotateZ):
        for out_attr in output_rotate_attrs + append_rotate_attrs:
            MmdAppendNode.attributeAffects(source_attr, out_attr)

    for jo_attr in (
        MmdAppendNode.aSourceJointOrientX,
        MmdAppendNode.aSourceJointOrientY,
        MmdAppendNode.aSourceJointOrientZ,
        MmdAppendNode.aTargetJointOrientX,
        MmdAppendNode.aTargetJointOrientY,
        MmdAppendNode.aTargetJointOrientZ,
    ):
        for out_attr in output_rotate_attrs + append_rotate_attrs:
            MmdAppendNode.attributeAffects(jo_attr, out_attr)

    for source_attr in (
        MmdAppendNode.aSourceTranslateX,
        MmdAppendNode.aSourceTranslateY,
        MmdAppendNode.aSourceTranslateZ,
    ):
        for out_attr in output_translate_attrs + append_translate_attrs:
            MmdAppendNode.attributeAffects(source_attr, out_attr)

    for out_attr in output_rotate_attrs + append_rotate_attrs + output_translate_attrs + append_translate_attrs:
        MmdAppendNode.attributeAffects(MmdAppendNode.aRatio, out_attr)

    for out_attr in output_rotate_attrs + append_rotate_attrs:
        MmdAppendNode.attributeAffects(MmdAppendNode.aAffectRotation, out_attr)

    for out_attr in output_translate_attrs + append_translate_attrs:
        MmdAppendNode.attributeAffects(MmdAppendNode.aAffectTranslation, out_attr)


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
