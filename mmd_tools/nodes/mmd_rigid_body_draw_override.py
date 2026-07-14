"""VP2 DrawOverride for mmdRigidBodyShape.

Draws each rigid body collider as a wireframe primitive (sphere/box/capsule)
colored by physicsMode. Positioning relies on Maya's default per-instance
transform for MPxDrawOverride (objPath.inclusiveMatrix()); only the shape's
own rotation attribute is applied locally since the authoring transform
already carries the bind-pose translate (see physics_scene_builder.py).
"""

from __future__ import annotations

import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr

def maya_useNewAPI():
    pass

_COLOR_STATIC = (0.2, 0.8, 0.2, 0.6)
_COLOR_DYNAMIC = (0.8, 0.2, 0.2, 0.6)
_COLOR_DYNAMIC_BONE = (0.2, 0.2, 0.8, 0.6)
_COLOR_DISABLED = (0.4, 0.4, 0.4, 0.3)

_SPHERE_SUBDIV_AXIS = 16
_SPHERE_SUBDIV_HEIGHT = 8
_CYLINDER_SUBDIV = 16


class ColliderDrawData(om.MUserData):
    def __init__(self):
        super().__init__(False)
        self.shape_type = 0
        self.size = (1.0, 1.0, 1.0)
        self.physics_mode = 0
        self.enabled = True
        self.rotation = (0.0, 0.0, 0.0)


def _color_for(data: ColliderDrawData) -> om.MColor:
    if not data.enabled:
        return om.MColor(_COLOR_DISABLED)
    if data.physics_mode == 1:
        return om.MColor(_COLOR_DYNAMIC)
    if data.physics_mode == 2:
        return om.MColor(_COLOR_DYNAMIC_BONE)
    return om.MColor(_COLOR_STATIC)


def _local_axes(rotation) -> tuple[om.MVector, om.MVector, om.MVector]:
    euler = om.MEulerRotation(rotation[0], rotation[1], rotation[2], om.MEulerRotation.kXYZ)
    mat = euler.asMatrix()
    x_axis = om.MVector(1.0, 0.0, 0.0) * mat
    y_axis = om.MVector(0.0, 1.0, 0.0) * mat
    z_axis = om.MVector(0.0, 0.0, 1.0) * mat
    return x_axis, y_axis, z_axis


def _draw_sphere(drawManager, center, radius) -> None:
    drawManager.sphere(center, radius, _SPHERE_SUBDIV_AXIS, _SPHERE_SUBDIV_HEIGHT, False)


def _draw_box(drawManager, center, x_axis, y_axis, size) -> None:
    drawManager.box(center, x_axis, y_axis, size[0], size[1], size[2], False)


def _draw_capsule(drawManager, center, y_axis, size) -> None:
    radius = size[0]
    height = size[1]
    if radius <= 0.0:
        return
    cyl_height = height - 2.0 * radius
    if cyl_height <= 0.0:
        _draw_sphere(drawManager, center, radius)
        return
    axis = y_axis.normal()
    drawManager.cylinder(center, axis, radius, cyl_height, _CYLINDER_SUBDIV, False)
    half = cyl_height * 0.5
    top = center + axis * half
    bottom = center - axis * half
    _draw_sphere(drawManager, top, radius)
    _draw_sphere(drawManager, bottom, radius)


class MmdRigidBodyDrawOverride(omr.MPxDrawOverride):
    kDrawDbClassification = "drawdb/geometry/mmdRigidBodyShape"
    kRegistrantId = "mmdRigidBodyDrawOverride"

    def __init__(self, obj):
        super().__init__(obj, None, False)

    @staticmethod
    def creator(obj):
        return MmdRigidBodyDrawOverride(obj)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kAllDevices

    def hasUIDrawables(self):
        return True

    def isBounded(self, objPath, cameraPath):
        return True

    def boundingBox(self, objPath, cameraPath):
        try:
            fn = om.MFnDependencyNode(objPath.node())
            sx = fn.findPlug("shapeSizeX", False).asDouble()
            sy = fn.findPlug("shapeSizeY", False).asDouble()
            sz = fn.findPlug("shapeSizeZ", False).asDouble()
        except Exception:
            sx = sy = sz = 1.0
        extent = max(sx, sy, sz, 0.001)
        corner = om.MPoint(extent, extent, extent)
        return om.MBoundingBox(-corner, corner)

    def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
        data = oldData if isinstance(oldData, ColliderDrawData) else ColliderDrawData()

        try:
            fn = om.MFnDependencyNode(objPath.node())
            data.enabled = fn.findPlug("enable", False).asBool()
            data.shape_type = fn.findPlug("shapeType", False).asShort()
            data.physics_mode = fn.findPlug("physicsMode", False).asShort()
            data.size = (
                fn.findPlug("shapeSizeX", False).asDouble(),
                fn.findPlug("shapeSizeY", False).asDouble(),
                fn.findPlug("shapeSizeZ", False).asDouble(),
            )
            data.rotation = (
                fn.findPlug("rotationX", False).asMAngle().asRadians(),
                fn.findPlug("rotationY", False).asMAngle().asRadians(),
                fn.findPlug("rotationZ", False).asMAngle().asRadians(),
            )
        except Exception:
            data.enabled = False

        return data

    def addUIDrawables(self, objPath, drawManager, frameContext, data):
        if data is None or not isinstance(data, ColliderDrawData):
            return
        if not data.enabled:
            return  # enable=False: skip drawing entirely

        center = om.MPoint(0.0, 0.0, 0.0)
        x_axis, y_axis, _z_axis = _local_axes(data.rotation)
        color = _color_for(data)

        drawManager.beginDrawable()
        drawManager.setColor(color)
        drawManager.setLineWidth(1.0)

        if data.shape_type == 0:
            _draw_sphere(drawManager, center, data.size[0])
        elif data.shape_type == 1:
            _draw_box(drawManager, center, x_axis, y_axis, data.size)
        else:
            _draw_capsule(drawManager, center, y_axis, data.size)

        drawManager.endDrawable()


def register():
    omr.MDrawRegistry.registerDrawOverrideCreator(
        MmdRigidBodyDrawOverride.kDrawDbClassification,
        MmdRigidBodyDrawOverride.kRegistrantId,
        MmdRigidBodyDrawOverride.creator,
    )


def deregister():
    omr.MDrawRegistry.deregisterDrawOverrideCreator(
        MmdRigidBodyDrawOverride.kDrawDbClassification,
        MmdRigidBodyDrawOverride.kRegistrantId,
    )
