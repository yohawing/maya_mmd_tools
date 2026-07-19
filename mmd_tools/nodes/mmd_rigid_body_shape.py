"""mmdRigidBodyShape — MMD 剛体を表す MPxLocatorNode.

PMX rigid body の全フィールドを保持するシェイプノード。実際の
MmdRuntimeFfiPhysicsRigidbodyDesc 構築はソルバー/シーンビルダー側で行い、
本ノードは入力属性群と、下流の dirty 検出用カウンタ (outDescriptorVersion)
のみを提供する。VP2 描画は mmd_rigid_body_draw_override.py の
MmdRigidBodyDrawOverride が担当する。

authoringMatrix / simulatedWorldMatrix は VP2 描画のための補助入力で、
descriptor 構築 (outDescriptorVersion) には影響しない。
"""

from __future__ import annotations

import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui

from mmd_tools.core.collider_geometry import collider_half_extents


def maya_useNewAPI():
    pass


class MmdRigidBodyShape(omui.MPxLocatorNode):
    kTypeName = "mmdRigidBodyShape"
    kTypeId = om.MTypeId(0x00128005)
    kClassify = "drawdb/geometry/mmdRigidBodyShape:utility/general"

    aPmxIndex = None
    aNameJp = None
    aNameEn = None
    aEnable = None
    aShapeType = None

    aShapeSize = None
    aShapeSizeX = None
    aShapeSizeY = None
    aShapeSizeZ = None

    aPosition = None
    aPositionX = None
    aPositionY = None
    aPositionZ = None

    aRotation = None
    aRotationX = None
    aRotationY = None
    aRotationZ = None

    aPhysicsMode = None
    aMass = None
    aLinearDamping = None
    aAngularDamping = None
    aFriction = None
    aRestitution = None
    aCollisionGroup = None
    aCollisionMask = None
    aRelatedBoneIndex = None
    aRelatedBone = None

    aOutDescriptorVersion = None

    aAuthoringMatrix = None
    aSimulatedWorldMatrix = None

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

    def isBounded(self):
        return True

    def boundingBox(self):
        fn = om.MFnDependencyNode(self.thisMObject())
        shape_type = fn.findPlug("shapeType", False).asShort()
        size = tuple(
            fn.findPlug(f"shapeSize{axis}", False).asDouble()
            for axis in "XYZ"
        )
        half_extents = collider_half_extents(shape_type, size)
        corner = om.MVector(*(max(value, 0.001) for value in half_extents))
        center = om.MPoint(0.0, 0.0, 0.0)
        return om.MBoundingBox(center - corner, center + corner)


def creator():
    return MmdRigidBodyShape()


def initialize():
    nAttr = om.MFnNumericAttribute()
    uAttr = om.MFnUnitAttribute()
    tAttr = om.MFnTypedAttribute()
    mAttr = om.MFnMessageAttribute()
    cAttr = om.MFnCompoundAttribute()
    xAttr = om.MFnMatrixAttribute()

    MmdRigidBodyShape.aPmxIndex = nAttr.create("pmxIndex", "pmi", om.MFnNumericData.kShort, -1)
    nAttr.storable = True
    nAttr.keyable = False
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aPmxIndex)

    MmdRigidBodyShape.aNameJp = tAttr.create("nameJp", "njp", om.MFnData.kString)
    tAttr.storable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aNameJp)

    MmdRigidBodyShape.aNameEn = tAttr.create("nameEn", "nen", om.MFnData.kString)
    tAttr.storable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aNameEn)

    MmdRigidBodyShape.aEnable = nAttr.create("enable", "enb", om.MFnNumericData.kBoolean, True)
    nAttr.storable = True
    nAttr.keyable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aEnable)

    MmdRigidBodyShape.aShapeType = nAttr.create("shapeType", "sht", om.MFnNumericData.kShort, 0)
    nAttr.storable = True
    nAttr.keyable = False
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aShapeType)

    # --- Shape size (input) ---
    MmdRigidBodyShape.aShapeSizeX = nAttr.create("shapeSizeX", "ssx", om.MFnNumericData.kDouble, 1.0)
    MmdRigidBodyShape.aShapeSizeY = nAttr.create("shapeSizeY", "ssy", om.MFnNumericData.kDouble, 1.0)
    MmdRigidBodyShape.aShapeSizeZ = nAttr.create("shapeSizeZ", "ssz", om.MFnNumericData.kDouble, 1.0)
    MmdRigidBodyShape.aShapeSize = cAttr.create("shapeSize", "sss")
    cAttr.addChild(MmdRigidBodyShape.aShapeSizeX)
    cAttr.addChild(MmdRigidBodyShape.aShapeSizeY)
    cAttr.addChild(MmdRigidBodyShape.aShapeSizeZ)
    cAttr.keyable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aShapeSize)

    # --- Bind-pose position (input) ---
    MmdRigidBodyShape.aPositionX = nAttr.create("positionX", "pox", om.MFnNumericData.kDouble, 0.0)
    MmdRigidBodyShape.aPositionY = nAttr.create("positionY", "poy", om.MFnNumericData.kDouble, 0.0)
    MmdRigidBodyShape.aPositionZ = nAttr.create("positionZ", "poz", om.MFnNumericData.kDouble, 0.0)
    MmdRigidBodyShape.aPosition = cAttr.create("position", "po")
    cAttr.addChild(MmdRigidBodyShape.aPositionX)
    cAttr.addChild(MmdRigidBodyShape.aPositionY)
    cAttr.addChild(MmdRigidBodyShape.aPositionZ)
    cAttr.keyable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aPosition)

    # --- Bind-pose rotation (input, angle) ---
    MmdRigidBodyShape.aRotationX = uAttr.create("rotationX", "rox", om.MFnUnitAttribute.kAngle, 0.0)
    MmdRigidBodyShape.aRotationY = uAttr.create("rotationY", "roy", om.MFnUnitAttribute.kAngle, 0.0)
    MmdRigidBodyShape.aRotationZ = uAttr.create("rotationZ", "roz", om.MFnUnitAttribute.kAngle, 0.0)
    MmdRigidBodyShape.aRotation = cAttr.create("rotation", "ro")
    cAttr.addChild(MmdRigidBodyShape.aRotationX)
    cAttr.addChild(MmdRigidBodyShape.aRotationY)
    cAttr.addChild(MmdRigidBodyShape.aRotationZ)
    cAttr.keyable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aRotation)

    MmdRigidBodyShape.aPhysicsMode = nAttr.create("physicsMode", "phm", om.MFnNumericData.kShort, 0)
    nAttr.storable = True
    nAttr.keyable = False
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aPhysicsMode)

    MmdRigidBodyShape.aMass = nAttr.create("mass", "mas", om.MFnNumericData.kDouble, 1.0)
    nAttr.storable = True
    nAttr.keyable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aMass)

    MmdRigidBodyShape.aLinearDamping = nAttr.create("linearDamping", "lda", om.MFnNumericData.kDouble, 0.0)
    nAttr.storable = True
    nAttr.keyable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aLinearDamping)

    MmdRigidBodyShape.aAngularDamping = nAttr.create("angularDamping", "ada", om.MFnNumericData.kDouble, 0.0)
    nAttr.storable = True
    nAttr.keyable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aAngularDamping)

    MmdRigidBodyShape.aFriction = nAttr.create("friction", "fri", om.MFnNumericData.kDouble, 0.5)
    nAttr.storable = True
    nAttr.keyable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aFriction)

    MmdRigidBodyShape.aRestitution = nAttr.create("restitution", "res", om.MFnNumericData.kDouble, 0.0)
    nAttr.storable = True
    nAttr.keyable = True
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aRestitution)

    MmdRigidBodyShape.aCollisionGroup = nAttr.create("collisionGroup", "cgr", om.MFnNumericData.kShort, 0)
    nAttr.storable = True
    nAttr.keyable = False
    nAttr.setMin(0)
    nAttr.setMax(15)
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aCollisionGroup)

    MmdRigidBodyShape.aCollisionMask = nAttr.create("collisionMask", "cma", om.MFnNumericData.kLong, 0xFFFF)
    nAttr.storable = True
    nAttr.keyable = False
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aCollisionMask)

    MmdRigidBodyShape.aRelatedBoneIndex = nAttr.create("relatedBoneIndex", "rbi", om.MFnNumericData.kLong, -1)
    nAttr.storable = True
    nAttr.keyable = False
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aRelatedBoneIndex)

    MmdRigidBodyShape.aRelatedBone = mAttr.create("relatedBone", "rbn")
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aRelatedBone)

    MmdRigidBodyShape.aOutDescriptorVersion = nAttr.create("outDescriptorVersion", "odv", om.MFnNumericData.kLong, 0)
    nAttr.storable = False
    nAttr.writable = False
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aOutDescriptorVersion)

    # --- Authoring world matrix (storable, reflects DAG transform in world space) ---
    MmdRigidBodyShape.aAuthoringMatrix = xAttr.create("authoringMatrix", "aum", om.MFnMatrixAttribute.kDouble)
    xAttr.storable = True
    xAttr.keyable = False
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aAuthoringMatrix)

    # --- Simulated world matrix (fed by solver during playback, not storable) ---
    MmdRigidBodyShape.aSimulatedWorldMatrix = xAttr.create("simulatedWorldMatrix", "swm", om.MFnMatrixAttribute.kDouble)
    xAttr.storable = False
    xAttr.keyable = False
    MmdRigidBodyShape.addAttribute(MmdRigidBodyShape.aSimulatedWorldMatrix)

    input_attrs = (
        MmdRigidBodyShape.aPmxIndex,
        MmdRigidBodyShape.aNameJp,
        MmdRigidBodyShape.aNameEn,
        MmdRigidBodyShape.aEnable,
        MmdRigidBodyShape.aShapeType,
        MmdRigidBodyShape.aShapeSizeX,
        MmdRigidBodyShape.aShapeSizeY,
        MmdRigidBodyShape.aShapeSizeZ,
        MmdRigidBodyShape.aPositionX,
        MmdRigidBodyShape.aPositionY,
        MmdRigidBodyShape.aPositionZ,
        MmdRigidBodyShape.aRotationX,
        MmdRigidBodyShape.aRotationY,
        MmdRigidBodyShape.aRotationZ,
        MmdRigidBodyShape.aPhysicsMode,
        MmdRigidBodyShape.aMass,
        MmdRigidBodyShape.aLinearDamping,
        MmdRigidBodyShape.aAngularDamping,
        MmdRigidBodyShape.aFriction,
        MmdRigidBodyShape.aRestitution,
        MmdRigidBodyShape.aCollisionGroup,
        MmdRigidBodyShape.aCollisionMask,
        MmdRigidBodyShape.aRelatedBoneIndex,
    )
    for in_attr in input_attrs:
        MmdRigidBodyShape.attributeAffects(in_attr, MmdRigidBodyShape.aOutDescriptorVersion)


def register(plugin_fn):
    plugin_fn.registerNode(
        MmdRigidBodyShape.kTypeName,
        MmdRigidBodyShape.kTypeId,
        creator,
        initialize,
        om.MPxNode.kLocatorNode,
        MmdRigidBodyShape.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdRigidBodyShape.kTypeId)
    except Exception:
        pass
