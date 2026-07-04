"""mmdRigidBodyLocator — VP2.0 wireframe display for imported MMD rigid bodies.

The node is a draw-only locator shape parented under a Bullet rigid body
transform.  Export and simulation continue to use the Bullet shape and MMD
metadata on the transform as the source of truth.
"""

from __future__ import annotations

import math
from typing import Iterable

import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr
import maya.api.OpenMayaUI as omui


def maya_useNewAPI():
    pass


_DRAW_REGISTRANT_ID = "mmdRigidBodyLocatorDrawOverride"
_SHAPE_BOX = 1
_SHAPE_SPHERE = 2
_SHAPE_CAPSULE = 3


class MmdRigidBodyLocatorNode(omui.MPxLocatorNode):
    """Locator shape that stores collider dimensions for VP2 wire drawing."""

    kTypeName = "mmdRigidBodyLocator"
    kTypeId = om.MTypeId(0x00128005)
    kDrawDbClassification = "drawdb/geometry/mmdRigidBodyLocator"

    aDrawEnabled = None
    aColliderShapeType = None
    aRadius = None
    aLength = None
    aBoxSizeX = None
    aBoxSizeY = None
    aBoxSizeZ = None

    def boundingBox(self):
        shape, radius, length, box_size, enabled = _read_node_state(self.thisMObject())
        if not enabled:
            return om.MBoundingBox()
        min_point, max_point = _bounds_for_shape(shape, radius, length, box_size)
        return om.MBoundingBox(min_point, max_point)


class MmdRigidBodyLocatorDrawOverride(omr.MPxDrawOverride):
    """Draw override that renders collider wireframes with MUIDrawManager."""

    def __init__(self, obj):
        super().__init__(obj, None, True)
        self._state = (_SHAPE_SPHERE, 1.0, 2.0, (2.0, 2.0, 2.0), True)

    @staticmethod
    def creator(obj):
        return MmdRigidBodyLocatorDrawOverride(obj)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kDirectX11 | omr.MRenderer.kOpenGL | omr.MRenderer.kOpenGLCoreProfile

    def isBounded(self, obj_path, camera_path):
        return True

    def boundingBox(self, obj_path, camera_path):
        shape, radius, length, box_size, enabled = self._state
        if not enabled:
            return om.MBoundingBox()
        min_point, max_point = _bounds_for_shape(shape, radius, length, box_size)
        return om.MBoundingBox(min_point, max_point)

    def hasUIDrawables(self):
        return True

    def prepareForDraw(self, obj_path, camera_path, frame_context, old_data):
        node_obj = obj_path.node()
        self._state = _read_node_state(node_obj)
        return old_data

    def addUIDrawables(self, obj_path, draw_manager, frame_context, data):
        shape, radius, length, box_size, enabled = self._state
        if not enabled:
            return
        draw_manager.beginDrawable()
        try:
            draw_manager.setColor(om.MColor((0.1, 0.8, 1.0, 1.0)))
            draw_manager.setLineWidth(1.0)
            if shape == _SHAPE_BOX:
                _draw_box(draw_manager, box_size)
            elif shape == _SHAPE_CAPSULE:
                _draw_capsule(draw_manager, radius, length)
            else:
                _draw_sphere(draw_manager, radius)
        finally:
            draw_manager.endDrawable()


def _read_node_state(node_obj):
    shape = _plug_int(node_obj, MmdRigidBodyLocatorNode.aColliderShapeType, _SHAPE_SPHERE)
    radius = max(_plug_double(node_obj, MmdRigidBodyLocatorNode.aRadius, 1.0), 0.001)
    length = max(_plug_double(node_obj, MmdRigidBodyLocatorNode.aLength, radius * 2.0), radius * 2.0)
    box_size = (
        max(_plug_double(node_obj, MmdRigidBodyLocatorNode.aBoxSizeX, radius * 2.0), 0.001),
        max(_plug_double(node_obj, MmdRigidBodyLocatorNode.aBoxSizeY, radius * 2.0), 0.001),
        max(_plug_double(node_obj, MmdRigidBodyLocatorNode.aBoxSizeZ, radius * 2.0), 0.001),
    )
    shape, radius, length, box_size = _read_bullet_sibling_state(node_obj, shape, radius, length, box_size)
    enabled = _plug_bool(node_obj, MmdRigidBodyLocatorNode.aDrawEnabled, True)
    return shape, radius, length, box_size, enabled


def _read_bullet_sibling_state(
    node_obj,
    fallback_shape: int,
    fallback_radius: float,
    fallback_length: float,
    fallback_box_size: tuple[float, float, float],
) -> tuple[int, float, float, tuple[float, float, float]]:
    """Read the sibling Bullet shape when available, falling back to locator attrs."""
    try:
        locator_dag = om.MFnDagNode(node_obj)
        parent_obj = locator_dag.parent(0)
        parent_dag = om.MFnDagNode(parent_obj)
    except Exception:
        return fallback_shape, fallback_radius, fallback_length, fallback_box_size

    for index in range(parent_dag.childCount()):
        child_obj = parent_dag.child(index)
        try:
            child_fn = om.MFnDependencyNode(child_obj)
            shape_plug = child_fn.findPlug("colliderShapeType", False)
        except Exception:
            continue

        shape = int(shape_plug.asInt())
        radius = max(_named_plug_double(child_fn, "radius", fallback_radius), 0.001)
        length = max(_named_plug_double(child_fn, "length", fallback_length), radius * 2.0)
        box_size = fallback_box_size
        if shape == _SHAPE_BOX:
            # The Bullet box path stores full extents in the parent transform
            # scale.  VP2 applies that transform to this locator shape, so the
            # local wireframe must remain unit-sized to avoid double scaling.
            parent_scale = _parent_scale(parent_obj)
            if all(abs(value) > 0.001 for value in parent_scale):
                box_size = (1.0, 1.0, 1.0)
        return shape, radius, length, box_size

    return fallback_shape, fallback_radius, fallback_length, fallback_box_size


def _named_plug_double(node_fn, attr_name: str, default: float) -> float:
    try:
        return float(node_fn.findPlug(attr_name, False).asDouble())
    except Exception:
        return default


def _parent_scale(parent_obj) -> tuple[float, float, float]:
    try:
        scale = om.MFnTransform(parent_obj).scale(om.MSpace.kTransform)
        return float(scale[0]), float(scale[1]), float(scale[2])
    except Exception:
        return (1.0, 1.0, 1.0)


def _plug_bool(node_obj, attr, default: bool) -> bool:
    try:
        return bool(om.MPlug(node_obj, attr).asBool())
    except Exception:
        return default


def _plug_int(node_obj, attr, default: int) -> int:
    try:
        return int(om.MPlug(node_obj, attr).asInt())
    except Exception:
        return default


def _plug_double(node_obj, attr, default: float) -> float:
    try:
        return float(om.MPlug(node_obj, attr).asDouble())
    except Exception:
        return default


def _bounds_for_shape(shape: int, radius: float, length: float, box_size: tuple[float, float, float]):
    if shape == _SHAPE_BOX:
        half_x = box_size[0] * 0.5
        half_y = box_size[1] * 0.5
        half_z = box_size[2] * 0.5
        return om.MPoint(-half_x, -half_y, -half_z), om.MPoint(half_x, half_y, half_z)
    if shape == _SHAPE_CAPSULE:
        half_y = length * 0.5
        return om.MPoint(-radius, -half_y, -radius), om.MPoint(radius, half_y, radius)
    return om.MPoint(-radius, -radius, -radius), om.MPoint(radius, radius, radius)


def _circle_points(axis: str, radius: float, offset_y: float = 0.0, segments: int = 32) -> Iterable[om.MPoint]:
    for index in range(segments + 1):
        angle = 2.0 * math.pi * index / segments
        a = math.cos(angle) * radius
        b = math.sin(angle) * radius
        if axis == "xy":
            yield om.MPoint(a, b + offset_y, 0.0)
        elif axis == "xz":
            yield om.MPoint(a, offset_y, b)
        else:
            yield om.MPoint(0.0, a + offset_y, b)


def _draw_polyline(draw_manager, points: Iterable[om.MPoint]) -> None:
    previous = None
    for point in points:
        if previous is not None:
            draw_manager.line(previous, point)
        previous = point


def _draw_sphere(draw_manager, radius: float) -> None:
    _draw_polyline(draw_manager, _circle_points("xy", radius))
    _draw_polyline(draw_manager, _circle_points("xz", radius))
    _draw_polyline(draw_manager, _circle_points("yz", radius))


def _draw_capsule(draw_manager, radius: float, length: float) -> None:
    cylinder_half = max((length - radius * 2.0) * 0.5, 0.0)
    top_y = cylinder_half
    bottom_y = -cylinder_half
    _draw_polyline(draw_manager, _circle_points("xz", radius, top_y))
    _draw_polyline(draw_manager, _circle_points("xz", radius, bottom_y))
    for x, z in ((radius, 0.0), (-radius, 0.0), (0.0, radius), (0.0, -radius)):
        draw_manager.line(om.MPoint(x, bottom_y, z), om.MPoint(x, top_y, z))
    _draw_polyline(draw_manager, _circle_points("xy", radius, top_y))
    _draw_polyline(draw_manager, _circle_points("xy", radius, bottom_y))
    _draw_polyline(draw_manager, _circle_points("yz", radius, top_y))
    _draw_polyline(draw_manager, _circle_points("yz", radius, bottom_y))


def _draw_box(draw_manager, box_size: tuple[float, float, float]) -> None:
    hx, hy, hz = (box_size[0] * 0.5, box_size[1] * 0.5, box_size[2] * 0.5)
    corners = [
        om.MPoint(-hx, -hy, -hz),
        om.MPoint(hx, -hy, -hz),
        om.MPoint(hx, -hy, hz),
        om.MPoint(-hx, -hy, hz),
        om.MPoint(-hx, hy, -hz),
        om.MPoint(hx, hy, -hz),
        om.MPoint(hx, hy, hz),
        om.MPoint(-hx, hy, hz),
    ]
    for start, end in (
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ):
        draw_manager.line(corners[start], corners[end])


def creator():
    return MmdRigidBodyLocatorNode()


def initialize():
    n_attr = om.MFnNumericAttribute()

    MmdRigidBodyLocatorNode.aDrawEnabled = n_attr.create(
        "drawEnabled", "den", om.MFnNumericData.kBoolean, True
    )
    n_attr.keyable = True
    MmdRigidBodyLocatorNode.addAttribute(MmdRigidBodyLocatorNode.aDrawEnabled)

    MmdRigidBodyLocatorNode.aColliderShapeType = n_attr.create(
        "colliderShapeType", "cst", om.MFnNumericData.kInt, _SHAPE_SPHERE
    )
    n_attr.keyable = True
    MmdRigidBodyLocatorNode.addAttribute(MmdRigidBodyLocatorNode.aColliderShapeType)

    MmdRigidBodyLocatorNode.aRadius = n_attr.create("radius", "rad", om.MFnNumericData.kDouble, 1.0)
    n_attr.keyable = True
    MmdRigidBodyLocatorNode.addAttribute(MmdRigidBodyLocatorNode.aRadius)

    MmdRigidBodyLocatorNode.aLength = n_attr.create("length", "len", om.MFnNumericData.kDouble, 2.0)
    n_attr.keyable = True
    MmdRigidBodyLocatorNode.addAttribute(MmdRigidBodyLocatorNode.aLength)

    MmdRigidBodyLocatorNode.aBoxSizeX = n_attr.create("boxSizeX", "bsx", om.MFnNumericData.kDouble, 2.0)
    n_attr.keyable = True
    MmdRigidBodyLocatorNode.addAttribute(MmdRigidBodyLocatorNode.aBoxSizeX)

    MmdRigidBodyLocatorNode.aBoxSizeY = n_attr.create("boxSizeY", "bsy", om.MFnNumericData.kDouble, 2.0)
    n_attr.keyable = True
    MmdRigidBodyLocatorNode.addAttribute(MmdRigidBodyLocatorNode.aBoxSizeY)

    MmdRigidBodyLocatorNode.aBoxSizeZ = n_attr.create("boxSizeZ", "bsz", om.MFnNumericData.kDouble, 2.0)
    n_attr.keyable = True
    MmdRigidBodyLocatorNode.addAttribute(MmdRigidBodyLocatorNode.aBoxSizeZ)


def register(plugin_fn):
    """Call from the host plugin's initializePlugin."""
    plugin_fn.registerNode(
        MmdRigidBodyLocatorNode.kTypeName,
        MmdRigidBodyLocatorNode.kTypeId,
        creator,
        initialize,
        om.MPxNode.kLocatorNode,
        MmdRigidBodyLocatorNode.kDrawDbClassification,
    )
    omr.MDrawRegistry.registerDrawOverrideCreator(
        MmdRigidBodyLocatorNode.kDrawDbClassification,
        _DRAW_REGISTRANT_ID,
        MmdRigidBodyLocatorDrawOverride.creator,
    )


def deregister(plugin_fn):
    try:
        omr.MDrawRegistry.deregisterDrawOverrideCreator(
            MmdRigidBodyLocatorNode.kDrawDbClassification,
            _DRAW_REGISTRANT_ID,
        )
    except Exception:
        pass
    try:
        plugin_fn.deregisterNode(MmdRigidBodyLocatorNode.kTypeId)
    except Exception:
        pass
