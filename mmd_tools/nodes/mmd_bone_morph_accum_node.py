"""mmdBoneMorphAccum — PMX bone morph offsets を joint transform に合成する DG ノード.

各 PMX ボーンモーフ network node の weight と保存済み offset を contribution[]
として受け取り、baseTranslate/baseRotate に加算・合成した結果を出力する。
translation は線形加算、rotation は PMX morph index 順の quaternion slerp 合成。
"""

from __future__ import annotations

import math

import maya.api.OpenMaya as om


def maya_useNewAPI():
    pass


class MmdBoneMorphAccumNode(om.MPxNode):
    """PMX bone morph contribution accumulator."""

    kTypeName = "mmdBoneMorphAccum"
    kTypeId = om.MTypeId(0x00128003)
    kClassify = "utility/general"

    aBaseTranslate = None
    aBaseTranslateX = None
    aBaseTranslateY = None
    aBaseTranslateZ = None

    aBaseRotate = None
    aBaseRotateX = None
    aBaseRotateY = None
    aBaseRotateZ = None

    aRotateOrder = None

    aContribution = None
    aContributionWeight = None
    aTranslateOffset = None
    aTranslateOffsetX = None
    aTranslateOffsetY = None
    aTranslateOffsetZ = None
    aRotateOffsetQuat = None
    aRotateOffsetQuatX = None
    aRotateOffsetQuatY = None
    aRotateOffsetQuatZ = None
    aRotateOffsetQuatW = None
    aMorphOrder = None

    aOutputTranslate = None
    aOutputTranslateX = None
    aOutputTranslateY = None
    aOutputTranslateZ = None

    aOutputRotate = None
    aOutputRotateX = None
    aOutputRotateY = None
    aOutputRotateZ = None

    _ROTATE_ORDERS = (
        om.MEulerRotation.kXYZ,
        om.MEulerRotation.kYZX,
        om.MEulerRotation.kZXY,
        om.MEulerRotation.kXZY,
        om.MEulerRotation.kYXZ,
        om.MEulerRotation.kZYX,
    )

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
        is_translate_plug = self._plug_matches_any(
            plug,
            (
                N.aOutputTranslate,
                N.aOutputTranslateX,
                N.aOutputTranslateY,
                N.aOutputTranslateZ,
            ),
        )
        is_rotate_plug = self._plug_matches_any(
            plug,
            (
                N.aOutputRotate,
                N.aOutputRotateX,
                N.aOutputRotateY,
                N.aOutputRotateZ,
            ),
        )
        if not is_translate_plug and not is_rotate_plug:
            return None

        base_tx = data.inputValue(N.aBaseTranslateX).asDouble()
        base_ty = data.inputValue(N.aBaseTranslateY).asDouble()
        base_tz = data.inputValue(N.aBaseTranslateZ).asDouble()
        base_rx = data.inputValue(N.aBaseRotateX).asDouble()
        base_ry = data.inputValue(N.aBaseRotateY).asDouble()
        base_rz = data.inputValue(N.aBaseRotateZ).asDouble()
        rotate_order = self._rotate_order_from_data(data)

        tx, ty, tz = base_tx, base_ty, base_tz
        contributions = self._read_contributions(data)
        for contribution in contributions:
            weight = contribution["weight"]
            tx += weight * contribution["translate"][0]
            ty += weight * contribution["translate"][1]
            tz += weight * contribution["translate"][2]

        out_trans_handle = data.outputValue(N.aOutputTranslate)
        out_trans_handle.set3Double(tx, ty, tz)
        out_trans_handle.setClean()

        base_euler = om.MEulerRotation(base_rx, base_ry, base_rz, rotate_order)
        final_quat = base_euler.asQuaternion()
        identity = om.MQuaternion()
        for contribution in contributions:
            offset_quat = contribution["rotate"]
            offset_quat = _normalized_quat(offset_quat)
            if offset_quat is None:
                continue
            final_quat = final_quat * om.MQuaternion.slerp(identity, offset_quat, contribution["weight"])

        out_euler = final_quat.asEulerRotation()
        out_euler.reorderIt(rotate_order)
        out_rot_handle = data.outputValue(N.aOutputRotate)
        out_rot_handle.set3Double(out_euler.x, out_euler.y, out_euler.z)
        out_rot_handle.setClean()

        data.setClean(plug)

    def _rotate_order_from_data(self, data):
        try:
            order_index = int(data.inputValue(type(self).aRotateOrder).asShort())
        except Exception:
            order_index = 0
        if 0 <= order_index < len(self._ROTATE_ORDERS):
            return self._ROTATE_ORDERS[order_index]
        return self._ROTATE_ORDERS[0]

    def _read_contributions(self, data):
        contributions = []
        try:
            array_handle = data.inputArrayValue(self.aContribution)
        except Exception:
            return contributions

        while not array_handle.isDone():
            try:
                logical_index = array_handle.elementLogicalIndex()
                elem = array_handle.inputValue()
                weight = elem.child(self.aContributionWeight).asFloat()
                translate = elem.child(self.aTranslateOffset)
                tx = translate.child(self.aTranslateOffsetX).asDouble()
                ty = translate.child(self.aTranslateOffsetY).asDouble()
                tz = translate.child(self.aTranslateOffsetZ).asDouble()
                rotate_quat = elem.child(self.aRotateOffsetQuat)
                qx = rotate_quat.child(self.aRotateOffsetQuatX).asDouble()
                qy = rotate_quat.child(self.aRotateOffsetQuatY).asDouble()
                qz = rotate_quat.child(self.aRotateOffsetQuatZ).asDouble()
                qw = rotate_quat.child(self.aRotateOffsetQuatW).asDouble()
                morph_order = elem.child(self.aMorphOrder).asInt()
                contributions.append(
                    {
                        "logical_index": logical_index,
                        "morph_order": morph_order,
                        "weight": float(weight),
                        "translate": (float(tx), float(ty), float(tz)),
                        "rotate": om.MQuaternion(float(qx), float(qy), float(qz), float(qw)),
                    }
                )
            except Exception:
                pass
            array_handle.next()

        contributions.sort(key=lambda item: (item["morph_order"], item["logical_index"]))
        return contributions


def creator():
    return MmdBoneMorphAccumNode()


def _normalized_quat(quat):
    length_squared = quat.x * quat.x + quat.y * quat.y + quat.z * quat.z + quat.w * quat.w
    if length_squared <= 1e-12:
        return None
    length = math.sqrt(length_squared)
    return om.MQuaternion(quat.x / length, quat.y / length, quat.z / length, quat.w / length)


def initialize():
    nAttr = om.MFnNumericAttribute()
    uAttr = om.MFnUnitAttribute()
    eAttr = om.MFnEnumAttribute()

    cAttr = om.MFnCompoundAttribute()
    MmdBoneMorphAccumNode.aBaseTranslateX = nAttr.create(
        "baseTranslateX", "btx", om.MFnNumericData.kDouble, 0.0
    )
    MmdBoneMorphAccumNode.aBaseTranslateY = nAttr.create(
        "baseTranslateY", "bty", om.MFnNumericData.kDouble, 0.0
    )
    MmdBoneMorphAccumNode.aBaseTranslateZ = nAttr.create(
        "baseTranslateZ", "btz", om.MFnNumericData.kDouble, 0.0
    )
    MmdBoneMorphAccumNode.aBaseTranslate = cAttr.create("baseTranslate", "bt")
    cAttr.addChild(MmdBoneMorphAccumNode.aBaseTranslateX)
    cAttr.addChild(MmdBoneMorphAccumNode.aBaseTranslateY)
    cAttr.addChild(MmdBoneMorphAccumNode.aBaseTranslateZ)
    cAttr.keyable = True
    MmdBoneMorphAccumNode.addAttribute(MmdBoneMorphAccumNode.aBaseTranslate)

    cAttr = om.MFnCompoundAttribute()
    MmdBoneMorphAccumNode.aBaseRotateX = uAttr.create(
        "baseRotateX", "brx", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdBoneMorphAccumNode.aBaseRotateY = uAttr.create(
        "baseRotateY", "bry", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdBoneMorphAccumNode.aBaseRotateZ = uAttr.create(
        "baseRotateZ", "brz", om.MFnUnitAttribute.kAngle, 0.0
    )
    MmdBoneMorphAccumNode.aBaseRotate = cAttr.create("baseRotate", "br")
    cAttr.addChild(MmdBoneMorphAccumNode.aBaseRotateX)
    cAttr.addChild(MmdBoneMorphAccumNode.aBaseRotateY)
    cAttr.addChild(MmdBoneMorphAccumNode.aBaseRotateZ)
    cAttr.keyable = True
    MmdBoneMorphAccumNode.addAttribute(MmdBoneMorphAccumNode.aBaseRotate)

    MmdBoneMorphAccumNode.aRotateOrder = eAttr.create("rotateOrder", "ro", 0)
    for index, name in enumerate(("xyz", "yzx", "zxy", "xzy", "yxz", "zyx")):
        eAttr.addField(name, index)
    eAttr.keyable = True
    MmdBoneMorphAccumNode.addAttribute(MmdBoneMorphAccumNode.aRotateOrder)

    MmdBoneMorphAccumNode.aContributionWeight = nAttr.create(
        "weight", "w", om.MFnNumericData.kFloat, 0.0
    )
    nAttr.keyable = True

    MmdBoneMorphAccumNode.aTranslateOffsetX = nAttr.create(
        "translateOffsetX", "tox", om.MFnNumericData.kDouble, 0.0
    )
    MmdBoneMorphAccumNode.aTranslateOffsetY = nAttr.create(
        "translateOffsetY", "toy", om.MFnNumericData.kDouble, 0.0
    )
    MmdBoneMorphAccumNode.aTranslateOffsetZ = nAttr.create(
        "translateOffsetZ", "toz", om.MFnNumericData.kDouble, 0.0
    )
    cAttr = om.MFnCompoundAttribute()
    MmdBoneMorphAccumNode.aTranslateOffset = cAttr.create("translateOffset", "to")
    cAttr.addChild(MmdBoneMorphAccumNode.aTranslateOffsetX)
    cAttr.addChild(MmdBoneMorphAccumNode.aTranslateOffsetY)
    cAttr.addChild(MmdBoneMorphAccumNode.aTranslateOffsetZ)

    MmdBoneMorphAccumNode.aRotateOffsetQuatX = nAttr.create(
        "rotateOffsetQuatX", "roqx", om.MFnNumericData.kDouble, 0.0
    )
    MmdBoneMorphAccumNode.aRotateOffsetQuatY = nAttr.create(
        "rotateOffsetQuatY", "roqy", om.MFnNumericData.kDouble, 0.0
    )
    MmdBoneMorphAccumNode.aRotateOffsetQuatZ = nAttr.create(
        "rotateOffsetQuatZ", "roqz", om.MFnNumericData.kDouble, 0.0
    )
    MmdBoneMorphAccumNode.aRotateOffsetQuatW = nAttr.create(
        "rotateOffsetQuatW", "roqw", om.MFnNumericData.kDouble, 1.0
    )
    cAttr = om.MFnCompoundAttribute()
    MmdBoneMorphAccumNode.aRotateOffsetQuat = cAttr.create("rotateOffsetQuat", "roq")
    cAttr.addChild(MmdBoneMorphAccumNode.aRotateOffsetQuatX)
    cAttr.addChild(MmdBoneMorphAccumNode.aRotateOffsetQuatY)
    cAttr.addChild(MmdBoneMorphAccumNode.aRotateOffsetQuatZ)
    cAttr.addChild(MmdBoneMorphAccumNode.aRotateOffsetQuatW)

    MmdBoneMorphAccumNode.aMorphOrder = nAttr.create(
        "morphOrder", "mo", om.MFnNumericData.kInt, 0
    )

    cAttr = om.MFnCompoundAttribute()
    MmdBoneMorphAccumNode.aContribution = cAttr.create("contribution", "ctb")
    cAttr.addChild(MmdBoneMorphAccumNode.aContributionWeight)
    cAttr.addChild(MmdBoneMorphAccumNode.aTranslateOffset)
    cAttr.addChild(MmdBoneMorphAccumNode.aRotateOffsetQuat)
    cAttr.addChild(MmdBoneMorphAccumNode.aMorphOrder)
    cAttr.array = True
    cAttr.usesArrayDataBuilder = True
    MmdBoneMorphAccumNode.addAttribute(MmdBoneMorphAccumNode.aContribution)

    MmdBoneMorphAccumNode.aOutputTranslateX = nAttr.create(
        "outputTranslateX", "otx", om.MFnNumericData.kDouble, 0.0
    )
    nAttr.writable = False
    nAttr.storable = False
    MmdBoneMorphAccumNode.aOutputTranslateY = nAttr.create(
        "outputTranslateY", "oty", om.MFnNumericData.kDouble, 0.0
    )
    nAttr.writable = False
    nAttr.storable = False
    MmdBoneMorphAccumNode.aOutputTranslateZ = nAttr.create(
        "outputTranslateZ", "otz", om.MFnNumericData.kDouble, 0.0
    )
    nAttr.writable = False
    nAttr.storable = False
    cAttr = om.MFnCompoundAttribute()
    MmdBoneMorphAccumNode.aOutputTranslate = cAttr.create("outputTranslate", "ot")
    cAttr.writable = False
    cAttr.storable = False
    cAttr.addChild(MmdBoneMorphAccumNode.aOutputTranslateX)
    cAttr.addChild(MmdBoneMorphAccumNode.aOutputTranslateY)
    cAttr.addChild(MmdBoneMorphAccumNode.aOutputTranslateZ)
    MmdBoneMorphAccumNode.addAttribute(MmdBoneMorphAccumNode.aOutputTranslate)

    MmdBoneMorphAccumNode.aOutputRotateX = uAttr.create(
        "outputRotateX", "orx", om.MFnUnitAttribute.kAngle, 0.0
    )
    uAttr.writable = False
    uAttr.storable = False
    MmdBoneMorphAccumNode.aOutputRotateY = uAttr.create(
        "outputRotateY", "ory", om.MFnUnitAttribute.kAngle, 0.0
    )
    uAttr.writable = False
    uAttr.storable = False
    MmdBoneMorphAccumNode.aOutputRotateZ = uAttr.create(
        "outputRotateZ", "orz", om.MFnUnitAttribute.kAngle, 0.0
    )
    uAttr.writable = False
    uAttr.storable = False
    cAttr = om.MFnCompoundAttribute()
    MmdBoneMorphAccumNode.aOutputRotate = cAttr.create("outputRotate", "or")
    cAttr.writable = False
    cAttr.storable = False
    cAttr.addChild(MmdBoneMorphAccumNode.aOutputRotateX)
    cAttr.addChild(MmdBoneMorphAccumNode.aOutputRotateY)
    cAttr.addChild(MmdBoneMorphAccumNode.aOutputRotateZ)
    MmdBoneMorphAccumNode.addAttribute(MmdBoneMorphAccumNode.aOutputRotate)

    output_translate_attrs = (
        MmdBoneMorphAccumNode.aOutputTranslateX,
        MmdBoneMorphAccumNode.aOutputTranslateY,
        MmdBoneMorphAccumNode.aOutputTranslateZ,
    )
    output_rotate_attrs = (
        MmdBoneMorphAccumNode.aOutputRotateX,
        MmdBoneMorphAccumNode.aOutputRotateY,
        MmdBoneMorphAccumNode.aOutputRotateZ,
    )

    for base_attr in (
        MmdBoneMorphAccumNode.aBaseTranslateX,
        MmdBoneMorphAccumNode.aBaseTranslateY,
        MmdBoneMorphAccumNode.aBaseTranslateZ,
    ):
        for out_attr in output_translate_attrs:
            MmdBoneMorphAccumNode.attributeAffects(base_attr, out_attr)

    for base_attr in (
        MmdBoneMorphAccumNode.aBaseRotateX,
        MmdBoneMorphAccumNode.aBaseRotateY,
        MmdBoneMorphAccumNode.aBaseRotateZ,
        MmdBoneMorphAccumNode.aRotateOrder,
    ):
        for out_attr in output_rotate_attrs:
            MmdBoneMorphAccumNode.attributeAffects(base_attr, out_attr)

    for contrib_attr in (
        MmdBoneMorphAccumNode.aContribution,
        MmdBoneMorphAccumNode.aContributionWeight,
        MmdBoneMorphAccumNode.aTranslateOffsetX,
        MmdBoneMorphAccumNode.aTranslateOffsetY,
        MmdBoneMorphAccumNode.aTranslateOffsetZ,
        MmdBoneMorphAccumNode.aRotateOffsetQuatX,
        MmdBoneMorphAccumNode.aRotateOffsetQuatY,
        MmdBoneMorphAccumNode.aRotateOffsetQuatZ,
        MmdBoneMorphAccumNode.aRotateOffsetQuatW,
        MmdBoneMorphAccumNode.aMorphOrder,
    ):
        for out_attr in output_translate_attrs + output_rotate_attrs:
            MmdBoneMorphAccumNode.attributeAffects(contrib_attr, out_attr)


def register(plugin_fn):
    plugin_fn.registerNode(
        MmdBoneMorphAccumNode.kTypeName,
        MmdBoneMorphAccumNode.kTypeId,
        creator,
        initialize,
        om.MPxNode.kDependNode,
        MmdBoneMorphAccumNode.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdBoneMorphAccumNode.kTypeId)
    except Exception:
        pass
