"""mmdPhysicsWorldShape — scene-level physics authority node (MPxLocatorNode).

Holds global Bullet physics configuration (gravity, timestep, reset signal, …)
shared by all solvers in the scene. This node owns no Bullet handles itself;
it only provides input attributes and a dirty-counter output
(outSettingsVersion) that downstream solvers can watch for changes. VP2
drawing is not implemented in this slice.
"""

from __future__ import annotations

import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui


def maya_useNewAPI():
    pass


class MmdPhysicsWorldShape(omui.MPxLocatorNode):
    kTypeName = "mmdPhysicsWorldShape"
    kTypeId = om.MTypeId(0x0012800A)
    kClassify = "drawdb/geometry/mmdPhysicsWorldShape:utility/general"

    aEnable = None

    aGravity = None
    aGravityX = None
    aGravityY = None
    aGravityZ = None

    aFixedTimestep = None
    aMaxSubsteps = None
    aTimeScale = None
    aStartFrame = None
    aResetGeneration = None
    aPhysicsMode = None

    aOutSettingsVersion = None

    def __init__(self):
        super().__init__()
        self._settings_version = 0

    def compute(self, plug, data):
        N = type(self)
        if plug != N.aOutSettingsVersion:
            return None  # let Maya handle unknown plugs

        self._settings_version += 1
        data.outputValue(N.aOutSettingsVersion).setInt(self._settings_version)
        data.setClean(plug)


def creator():
    return MmdPhysicsWorldShape()


def _hide_attribute(fn_attr) -> None:
    fn_attr.keyable = False
    fn_attr.channelBox = False
    fn_attr.hidden = True


def initialize():
    nAttr = om.MFnNumericAttribute()
    cAttr = om.MFnCompoundAttribute()

    MmdPhysicsWorldShape.aEnable = nAttr.create("enable", "enb", om.MFnNumericData.kBoolean, False)
    nAttr.storable = True
    nAttr.keyable = True
    MmdPhysicsWorldShape.addAttribute(MmdPhysicsWorldShape.aEnable)

    # --- Gravity (input) ---
    MmdPhysicsWorldShape.aGravityX = nAttr.create("gravityX", "grx", om.MFnNumericData.kDouble, 0.0)
    _hide_attribute(nAttr)
    MmdPhysicsWorldShape.aGravityY = nAttr.create("gravityY", "gry", om.MFnNumericData.kDouble, -9.8)
    _hide_attribute(nAttr)
    MmdPhysicsWorldShape.aGravityZ = nAttr.create("gravityZ", "grz", om.MFnNumericData.kDouble, 0.0)
    _hide_attribute(nAttr)
    MmdPhysicsWorldShape.aGravity = cAttr.create("gravity", "grv")
    cAttr.addChild(MmdPhysicsWorldShape.aGravityX)
    cAttr.addChild(MmdPhysicsWorldShape.aGravityY)
    cAttr.addChild(MmdPhysicsWorldShape.aGravityZ)
    _hide_attribute(cAttr)
    MmdPhysicsWorldShape.addAttribute(MmdPhysicsWorldShape.aGravity)

    MmdPhysicsWorldShape.aFixedTimestep = nAttr.create(
        "fixedTimestep", "fts", om.MFnNumericData.kDouble, 1.0 / 60.0
    )
    nAttr.storable = True
    nAttr.keyable = True
    nAttr.setMin(0.0001)
    _hide_attribute(nAttr)
    MmdPhysicsWorldShape.addAttribute(MmdPhysicsWorldShape.aFixedTimestep)

    MmdPhysicsWorldShape.aMaxSubsteps = nAttr.create("maxSubsteps", "mss", om.MFnNumericData.kInt, 10)
    nAttr.storable = True
    nAttr.keyable = True
    nAttr.setMin(1)
    _hide_attribute(nAttr)
    MmdPhysicsWorldShape.addAttribute(MmdPhysicsWorldShape.aMaxSubsteps)

    MmdPhysicsWorldShape.aTimeScale = nAttr.create("timeScale", "tsc", om.MFnNumericData.kDouble, 1.0)
    nAttr.storable = True
    nAttr.keyable = True
    nAttr.setMin(0.0)
    _hide_attribute(nAttr)
    MmdPhysicsWorldShape.addAttribute(MmdPhysicsWorldShape.aTimeScale)

    MmdPhysicsWorldShape.aStartFrame = nAttr.create("startFrame", "stf", om.MFnNumericData.kInt, 0)
    nAttr.storable = True
    _hide_attribute(nAttr)
    MmdPhysicsWorldShape.addAttribute(MmdPhysicsWorldShape.aStartFrame)

    MmdPhysicsWorldShape.aResetGeneration = nAttr.create("resetGeneration", "rsg", om.MFnNumericData.kInt, 0)
    nAttr.storable = True
    _hide_attribute(nAttr)
    MmdPhysicsWorldShape.addAttribute(MmdPhysicsWorldShape.aResetGeneration)

    MmdPhysicsWorldShape.aPhysicsMode = nAttr.create("physicsMode", "phm", om.MFnNumericData.kShort, 0)
    nAttr.storable = True
    _hide_attribute(nAttr)
    MmdPhysicsWorldShape.addAttribute(MmdPhysicsWorldShape.aPhysicsMode)

    MmdPhysicsWorldShape.aOutSettingsVersion = nAttr.create(
        "outSettingsVersion", "osv", om.MFnNumericData.kLong, 0
    )
    nAttr.storable = False
    nAttr.writable = False
    _hide_attribute(nAttr)
    MmdPhysicsWorldShape.addAttribute(MmdPhysicsWorldShape.aOutSettingsVersion)

    input_attrs = (
        MmdPhysicsWorldShape.aEnable,
        MmdPhysicsWorldShape.aGravityX,
        MmdPhysicsWorldShape.aGravityY,
        MmdPhysicsWorldShape.aGravityZ,
        MmdPhysicsWorldShape.aFixedTimestep,
        MmdPhysicsWorldShape.aMaxSubsteps,
        MmdPhysicsWorldShape.aTimeScale,
        MmdPhysicsWorldShape.aStartFrame,
        MmdPhysicsWorldShape.aResetGeneration,
        MmdPhysicsWorldShape.aPhysicsMode,
    )
    for in_attr in input_attrs:
        MmdPhysicsWorldShape.attributeAffects(in_attr, MmdPhysicsWorldShape.aOutSettingsVersion)


def register(plugin_fn):
    plugin_fn.registerNode(
        MmdPhysicsWorldShape.kTypeName,
        MmdPhysicsWorldShape.kTypeId,
        creator,
        initialize,
        om.MPxNode.kLocatorNode,
        MmdPhysicsWorldShape.kClassify,
    )


def deregister(plugin_fn):
    try:
        plugin_fn.deregisterNode(MmdPhysicsWorldShape.kTypeId)
    except Exception:
        pass
