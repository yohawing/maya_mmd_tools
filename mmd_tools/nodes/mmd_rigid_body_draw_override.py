"""VP2 DrawOverride for mmdRigidBodyShape.

Draws each rigid body collider as a wireframe primitive (sphere/box/capsule)
colored by physicsMode.  The primitive is always drawn in object-local space;
the parent transform's inclusive matrix is the canonical authoring/rest-pose
draw matrix and is mirrored by the shape's connected ``authoringMatrix``.
"""

from __future__ import annotations

import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr

from mmd_tools.core.collider_geometry import box_draw_scale, capsule_dimensions, collider_half_extents
from mmd_tools.core.collider_display import collision_group_color, physics_mode_line_style

def maya_useNewAPI():
    pass

_SPHERE_SUBDIV_AXIS = 16
_SPHERE_SUBDIV_HEIGHT = 8
_CYLINDER_SUBDIV = 16


class ColliderDrawData(om.MUserData):
    def __init__(self):
        super().__init__(False)
        self.shape_type = 0
        self.size = (1.0, 1.0, 1.0)
        self.physics_mode = 0
        self.collision_group = 0
        self.enabled = True
        self.selected = False
        self.selection_color = None


def _color_for(data: ColliderDrawData) -> om.MColor:
    if data.selected and data.selection_color is not None:
        return data.selection_color
    return om.MColor(collision_group_color(data.collision_group, data.physics_mode))


def _draw_sphere(drawManager, center, radius) -> None:
    drawManager.sphere(center, radius, _SPHERE_SUBDIV_AXIS, _SPHERE_SUBDIV_HEIGHT, False)


def _draw_box(drawManager, center, x_axis, y_axis, size) -> None:
    # Maya applies scaleX along ``right`` and scaleY along ``up`` and treats
    # both as half extents.  Keep PMX X on right and PMX Y on up.
    drawManager.box(center, y_axis, x_axis, *box_draw_scale(size), False)


def _draw_capsule(drawManager, center, y_axis, size) -> None:
    radius, cylinder_height, _total_height = capsule_dimensions(size)
    if radius <= 0.0:
        return
    axis = y_axis.normal()
    if cylinder_height > 0.0:
        drawManager.cylinder(center, axis, radius, cylinder_height, _CYLINDER_SUBDIV, False)
    half = cylinder_height * 0.5
    top = center + axis * half
    bottom = center - axis * half
    _draw_sphere(drawManager, top, radius)
    _draw_sphere(drawManager, bottom, radius)


class MmdRigidBodyDrawOverride(omr.MPxDrawOverride):
    kDrawDbClassification = "drawdb/geometry/mmdRigidBodyShape"
    kRegistrantId = "mmdRigidBodyDrawOverride"

    def __init__(self, obj):
        super().__init__(obj, None, True)

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
            shape_type = fn.findPlug("shapeType", False).asShort()
            size = (
                fn.findPlug("shapeSizeX", False).asDouble(),
                fn.findPlug("shapeSizeY", False).asDouble(),
                fn.findPlug("shapeSizeZ", False).asDouble(),
            )
        except Exception:
            shape_type, size = 0, (1.0, 1.0, 1.0)
        half_extents = collider_half_extents(shape_type, size)
        center = om.MPoint(0.0, 0.0, 0.0)
        corner = om.MVector(*(max(value, 0.001) for value in half_extents))
        return om.MBoundingBox(center - corner, center + corner)

    def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
        data = oldData if isinstance(oldData, ColliderDrawData) else ColliderDrawData()

        try:
            fn = om.MFnDependencyNode(objPath.node())
            data.enabled = fn.findPlug("enable", False).asBool()
            data.shape_type = fn.findPlug("shapeType", False).asShort()
            data.physics_mode = fn.findPlug("physicsMode", False).asShort()
            data.collision_group = fn.findPlug("collisionGroup", False).asShort()
            data.size = (
                fn.findPlug("shapeSizeX", False).asDouble(),
                fn.findPlug("shapeSizeY", False).asDouble(),
                fn.findPlug("shapeSizeZ", False).asDouble(),
            )
            status = omr.MGeometryUtilities.displayStatus(objPath)
            data.selected = status in (
                omr.MGeometryUtilities.kActive,
                omr.MGeometryUtilities.kLead,
                omr.MGeometryUtilities.kActiveComponent,
                omr.MGeometryUtilities.kHilite,
            )
            data.selection_color = (
                omr.MGeometryUtilities.wireframeColor(objPath) if data.selected else None
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
        x_axis = om.MVector(1.0, 0.0, 0.0)
        y_axis = om.MVector(0.0, 1.0, 0.0)

        color = _color_for(data)

        drawManager.beginDrawable()
        drawManager.setColor(color)
        drawManager.setLineWidth(2.5 if data.selected else 1.0)
        drawManager.setLineStyle(physics_mode_line_style(data.physics_mode))

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
