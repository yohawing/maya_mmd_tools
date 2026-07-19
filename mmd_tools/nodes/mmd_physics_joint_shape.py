"""mmdPhysicsJointShape — MMD 物理ジョイントを表現する locator ノード.

PMX の joint (generic 6dof spring) データを保持するだけの静的ノードで、
VP2 描画は持たない。rigidBodyA/rigidBodyB の message 接続を経由して
剛体側のトランスフォームと紐付ける。
"""

from __future__ import annotations

import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui


def maya_useNewAPI():
    pass


class MmdPhysicsJointShape(omui.MPxLocatorNode):
    kTypeName = "mmdPhysicsJointShape"
    kTypeId = om.MTypeId(0x00128007)
    kClassify = "drawdb/geometry/mmdPhysicsJointShape:utility/general"

    aPmxIndex = None
    aNameJp = None
    aNameEn = None
    aEnable = None
    aJointType = None

    aPosition = None
    aPositionX = None
    aPositionY = None
    aPositionZ = None

    aRotation = None
    aRotationX = None
    aRotationY = None
    aRotationZ = None

    aTranslationLimitMin = None
    aTranslationLimitMinX = None
    aTranslationLimitMinY = None
    aTranslationLimitMinZ = None

    aTranslationLimitMax = None
    aTranslationLimitMaxX = None
    aTranslationLimitMaxY = None
    aTranslationLimitMaxZ = None

    aRotationLimitMin = None
    aRotationLimitMinX = None
    aRotationLimitMinY = None
    aRotationLimitMinZ = None

    aRotationLimitMax = None
    aRotationLimitMaxX = None
    aRotationLimitMaxY = None
    aRotationLimitMaxZ = None

    aSpringTranslation = None
    aSpringTranslationX = None
    aSpringTranslationY = None
    aSpringTranslationZ = None

    aSpringRotation = None
    aSpringRotationX = None
    aSpringRotationY = None
    aSpringRotationZ = None

    aRigidBodyAIndex = None
    aRigidBodyBIndex = None
    aRigidBodyA = None
    aRigidBodyB = None

    aOutDescriptorVersion = None

    def __init__(self):
        super().__init__()
        self._descriptor_version = 0

    def compute(self, plug, data):
        N = type(self)
        if plug != N.aOutDescriptorVersion:
            return None  # let Maya handle unknown plugs

        self._descriptor_version += 1
        data.outputValue(N.aOutDescriptorVersion).setInt(self._descriptor_version)
        data.setClean(plug)


def creator():
    return MmdPhysicsJointShape()


def initialize():
    nAttr = om.MFnNumericAttribute()
    uAttr = om.MFnUnitAttribute()
    tAttr = om.MFnTypedAttribute()
    mAttr = om.MFnMessageAttribute()
    cAttr = om.MFnCompoundAttribute()

    MmdPhysicsJointShape.aPmxIndex = nAttr.create("pmxIndex", "pmi", om.MFnNumericData.kShort, -1)
    nAttr.storable = True
    nAttr.keyable = False
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aPmxIndex)

    MmdPhysicsJointShape.aNameJp = tAttr.create("nameJp", "njp", om.MFnData.kString)
    tAttr.storable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aNameJp)

    MmdPhysicsJointShape.aNameEn = tAttr.create("nameEn", "nen", om.MFnData.kString)
    tAttr.storable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aNameEn)

    MmdPhysicsJointShape.aEnable = nAttr.create("enable", "enb", om.MFnNumericData.kBoolean, True)
    nAttr.storable = True
    nAttr.keyable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aEnable)

    MmdPhysicsJointShape.aJointType = nAttr.create("jointType", "jty", om.MFnNumericData.kShort, 0)
    nAttr.storable = True
    nAttr.keyable = False
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aJointType)

    # --- Position (input) ---
    MmdPhysicsJointShape.aPositionX = nAttr.create("positionX", "pox", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aPositionY = nAttr.create("positionY", "poy", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aPositionZ = nAttr.create("positionZ", "poz", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aPosition = cAttr.create("position", "po")
    cAttr.addChild(MmdPhysicsJointShape.aPositionX)
    cAttr.addChild(MmdPhysicsJointShape.aPositionY)
    cAttr.addChild(MmdPhysicsJointShape.aPositionZ)
    cAttr.keyable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aPosition)

    # --- Rotation (input, angle) ---
    MmdPhysicsJointShape.aRotationX = uAttr.create("rotationX", "rox", om.MFnUnitAttribute.kAngle, 0.0)
    MmdPhysicsJointShape.aRotationY = uAttr.create("rotationY", "roy", om.MFnUnitAttribute.kAngle, 0.0)
    MmdPhysicsJointShape.aRotationZ = uAttr.create("rotationZ", "roz", om.MFnUnitAttribute.kAngle, 0.0)
    MmdPhysicsJointShape.aRotation = cAttr.create("rotation", "ro")
    cAttr.addChild(MmdPhysicsJointShape.aRotationX)
    cAttr.addChild(MmdPhysicsJointShape.aRotationY)
    cAttr.addChild(MmdPhysicsJointShape.aRotationZ)
    cAttr.keyable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aRotation)

    # --- Translation limits (input) ---
    MmdPhysicsJointShape.aTranslationLimitMinX = nAttr.create("translationLimitMinX", "tlnx", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aTranslationLimitMinY = nAttr.create("translationLimitMinY", "tlny", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aTranslationLimitMinZ = nAttr.create("translationLimitMinZ", "tlnz", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aTranslationLimitMin = cAttr.create("translationLimitMin", "tln")
    cAttr.addChild(MmdPhysicsJointShape.aTranslationLimitMinX)
    cAttr.addChild(MmdPhysicsJointShape.aTranslationLimitMinY)
    cAttr.addChild(MmdPhysicsJointShape.aTranslationLimitMinZ)
    cAttr.keyable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aTranslationLimitMin)

    MmdPhysicsJointShape.aTranslationLimitMaxX = nAttr.create("translationLimitMaxX", "tlxx", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aTranslationLimitMaxY = nAttr.create("translationLimitMaxY", "tlxy", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aTranslationLimitMaxZ = nAttr.create("translationLimitMaxZ", "tlxz", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aTranslationLimitMax = cAttr.create("translationLimitMax", "tlx")
    cAttr.addChild(MmdPhysicsJointShape.aTranslationLimitMaxX)
    cAttr.addChild(MmdPhysicsJointShape.aTranslationLimitMaxY)
    cAttr.addChild(MmdPhysicsJointShape.aTranslationLimitMaxZ)
    cAttr.keyable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aTranslationLimitMax)

    # --- Rotation limits (input, angle) ---
    MmdPhysicsJointShape.aRotationLimitMinX = uAttr.create("rotationLimitMinX", "rlnx", om.MFnUnitAttribute.kAngle, 0.0)
    MmdPhysicsJointShape.aRotationLimitMinY = uAttr.create("rotationLimitMinY", "rlny", om.MFnUnitAttribute.kAngle, 0.0)
    MmdPhysicsJointShape.aRotationLimitMinZ = uAttr.create("rotationLimitMinZ", "rlnz", om.MFnUnitAttribute.kAngle, 0.0)
    MmdPhysicsJointShape.aRotationLimitMin = cAttr.create("rotationLimitMin", "rln")
    cAttr.addChild(MmdPhysicsJointShape.aRotationLimitMinX)
    cAttr.addChild(MmdPhysicsJointShape.aRotationLimitMinY)
    cAttr.addChild(MmdPhysicsJointShape.aRotationLimitMinZ)
    cAttr.keyable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aRotationLimitMin)

    MmdPhysicsJointShape.aRotationLimitMaxX = uAttr.create("rotationLimitMaxX", "rlxx", om.MFnUnitAttribute.kAngle, 0.0)
    MmdPhysicsJointShape.aRotationLimitMaxY = uAttr.create("rotationLimitMaxY", "rlxy", om.MFnUnitAttribute.kAngle, 0.0)
    MmdPhysicsJointShape.aRotationLimitMaxZ = uAttr.create("rotationLimitMaxZ", "rlxz", om.MFnUnitAttribute.kAngle, 0.0)
    MmdPhysicsJointShape.aRotationLimitMax = cAttr.create("rotationLimitMax", "rlx")
    cAttr.addChild(MmdPhysicsJointShape.aRotationLimitMaxX)
    cAttr.addChild(MmdPhysicsJointShape.aRotationLimitMaxY)
    cAttr.addChild(MmdPhysicsJointShape.aRotationLimitMaxZ)
    cAttr.keyable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aRotationLimitMax)

    # --- Spring translation (input) ---
    MmdPhysicsJointShape.aSpringTranslationX = nAttr.create("springTranslationX", "sptx", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aSpringTranslationY = nAttr.create("springTranslationY", "spty", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aSpringTranslationZ = nAttr.create("springTranslationZ", "sptz", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aSpringTranslation = cAttr.create("springTranslation", "spt")
    cAttr.addChild(MmdPhysicsJointShape.aSpringTranslationX)
    cAttr.addChild(MmdPhysicsJointShape.aSpringTranslationY)
    cAttr.addChild(MmdPhysicsJointShape.aSpringTranslationZ)
    cAttr.keyable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aSpringTranslation)

    # --- Spring rotation (input) ---
    MmdPhysicsJointShape.aSpringRotationX = nAttr.create("springRotationX", "sprx", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aSpringRotationY = nAttr.create("springRotationY", "spry", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aSpringRotationZ = nAttr.create("springRotationZ", "sprz", om.MFnNumericData.kDouble, 0.0)
    MmdPhysicsJointShape.aSpringRotation = cAttr.create("springRotation", "spr")
    cAttr.addChild(MmdPhysicsJointShape.aSpringRotationX)
    cAttr.addChild(MmdPhysicsJointShape.aSpringRotationY)
    cAttr.addChild(MmdPhysicsJointShape.aSpringRotationZ)
    cAttr.keyable = True
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aSpringRotation)

    # --- Rigid body fallback indices + message connections ---
    MmdPhysicsJointShape.aRigidBodyAIndex = nAttr.create("rigidBodyAIndex", "rbai", om.MFnNumericData.kLong, -1)
    nAttr.storable = True
    nAttr.keyable = False
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aRigidBodyAIndex)

    MmdPhysicsJointShape.aRigidBodyBIndex = nAttr.create("rigidBodyBIndex", "rbbi", om.MFnNumericData.kLong, -1)
    nAttr.storable = True
    nAttr.keyable = False
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aRigidBodyBIndex)

    MmdPhysicsJointShape.aRigidBodyA = mAttr.create("rigidBodyA", "rba")
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aRigidBodyA)

    MmdPhysicsJointShape.aRigidBodyB = mAttr.create("rigidBodyB", "rbb")
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aRigidBodyB)

    # --- Computed output ---
    MmdPhysicsJointShape.aOutDescriptorVersion = nAttr.create(
        "outDescriptorVersion", "odv", om.MFnNumericData.kLong, 0
    )
    nAttr.writable = False
    nAttr.storable = False
    MmdPhysicsJointShape.addAttribute(MmdPhysicsJointShape.aOutDescriptorVersion)

    # --- Affect relationships ---
    input_attrs = (
        MmdPhysicsJointShape.aPmxIndex,
        MmdPhysicsJointShape.aNameJp,
        MmdPhysicsJointShape.aNameEn,
        MmdPhysicsJointShape.aEnable,
        MmdPhysicsJointShape.aJointType,
        MmdPhysicsJointShape.aPositionX,
        MmdPhysicsJointShape.aPositionY,
        MmdPhysicsJointShape.aPositionZ,
        MmdPhysicsJointShape.aRotationX,
        MmdPhysicsJointShape.aRotationY,
        MmdPhysicsJointShape.aRotationZ,
        MmdPhysicsJointShape.aTranslationLimitMinX,
        MmdPhysicsJointShape.aTranslationLimitMinY,
        MmdPhysicsJointShape.aTranslationLimitMinZ,
        MmdPhysicsJointShape.aTranslationLimitMaxX,
        MmdPhysicsJointShape.aTranslationLimitMaxY,
        MmdPhysicsJointShape.aTranslationLimitMaxZ,
        MmdPhysicsJointShape.aRotationLimitMinX,
        MmdPhysicsJointShape.aRotationLimitMinY,
        MmdPhysicsJointShape.aRotationLimitMinZ,
        MmdPhysicsJointShape.aRotationLimitMaxX,
        MmdPhysicsJointShape.aRotationLimitMaxY,
        MmdPhysicsJointShape.aRotationLimitMaxZ,
        MmdPhysicsJointShape.aSpringTranslationX,
        MmdPhysicsJointShape.aSpringTranslationY,
        MmdPhysicsJointShape.aSpringTranslationZ,
        MmdPhysicsJointShape.aSpringRotationX,
        MmdPhysicsJointShape.aSpringRotationY,
        MmdPhysicsJointShape.aSpringRotationZ,
        MmdPhysicsJointShape.aRigidBodyAIndex,
        MmdPhysicsJointShape.aRigidBodyBIndex,
    )
    for in_attr in input_attrs:
        MmdPhysicsJointShape.attributeAffects(in_attr, MmdPhysicsJointShape.aOutDescriptorVersion)


def register(plugin_fn):
    plugin_fn.registerNode(
        MmdPhysicsJointShape.kTypeName,
        MmdPhysicsJointShape.kTypeId,
        creator,
        initialize,
        om.MPxNode.kLocatorNode,
        MmdPhysicsJointShape.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdPhysicsJointShape.kTypeId)
    except Exception:
        pass
