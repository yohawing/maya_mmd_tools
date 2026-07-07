"""Camera-specific helpers for VMD animation conversion."""

import math
from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core.constants import ATTR_MMD_CAMERA, DEFAULT_CAMERA_NAME
from ..core.coordinate_transform import mmd_point_to_maya

ATTR_MMD_CAMERA_RIG_TYPE = "mmd_camera_rig_type"
ATTR_MMD_CAMERA_TARGET_NODE = "mmd_camera_target_node"
ATTR_MMD_CAMERA_ROOT_NODE = "mmd_camera_root_node"
MMD_CAMERA_RIG_ROOT_NAME = f"{DEFAULT_CAMERA_NAME}_rig"
MMD_CAMERA_TARGET_NAME = f"{DEFAULT_CAMERA_NAME}_target"
MMD_CAMERA_TARGET_ATTRS = (
    "mmd_camera_target_x",
    "mmd_camera_target_y",
    "mmd_camera_target_z",
)
MMD_CAMERA_ROTATION_ATTRS = (
    "mmd_camera_rotation_x",
    "mmd_camera_rotation_y",
    "mmd_camera_rotation_z",
)
MMD_CAMERA_SCALAR_ATTRS = (
    ("mmd_camera_distance", "double", 0.0),
    ("mmd_camera_viewing_angle", "double", 45.0),
    ("mmd_camera_perspective", "long", 0),
)
MMD_CAMERA_OUTPUT_ATTRS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
MMD_CAMERA_SHAPE_OUTPUT_ATTRS = ("focalLength", "orthographicWidth", "orthographic")
MMD_CAMERA_EXPR_ID_ATTR = "mmd_camera_expression_id"
MMD_CAMERA_EXPR_OWNER_ATTR = "mmd_camera_owner"
MMD_CAMERA_EXPR_TARGET_ATTR = "mmd_camera_target"
MMD_CAMERA_EXPR_SHAPE_ATTR = "mmd_camera_shape"
MMD_CAMERA_EXPR_SCALE_ATTR = "mmd_camera_motion_scale"

try:
    from ..core.native.mmd_anim_runtime_sampling import sample_vmd_camera_frames
except Exception:
    sample_vmd_camera_frames = None  # type: ignore


def parse_vmd_camera_interpolation(interpolation_bytes) -> Dict[str, Tuple[float, float, float, float]]:
    """Convert VMD camera interpolation bytes into channel Bezier control points."""
    if not interpolation_bytes or len(interpolation_bytes) < 24:
        return {}

    data = bytes(interpolation_bytes[:24])

    def _norm(value):
        return max(0.0, min(127.0, float(value))) / 127.0

    channels = (
        "translate_x",
        "translate_y",
        "translate_z",
        "rotation",
        "distance",
        "viewing_angle",
    )
    parsed = {}
    for index, channel in enumerate(channels):
        offset = index * 4
        parsed[channel] = (
            _norm(data[offset]),
            _norm(data[offset + 1]),
            _norm(data[offset + 2]),
            _norm(data[offset + 3]),
        )
    return parsed


def viewing_angle_to_focal_length(camera_shape: str, viewing_angle: float) -> float:
    """Convert VMD vertical viewing_angle(deg) to Maya camera focalLength(mm)."""
    clamped_angle = max(1.0, min(179.0, float(viewing_angle)))
    aperture_inch = cmds.getAttr(f"{camera_shape}.verticalFilmAperture")
    aperture_mm = float(aperture_inch) * 25.4
    return aperture_mm / (2.0 * math.tan(math.radians(clamped_angle) / 2.0))


def viewing_angle_to_orthographic_width(camera_shape: str, viewing_angle: float, distance: float) -> float:
    """Convert MMD orthographic camera distance/FOV to Maya orthographicWidth."""
    clamped_angle = max(1.0, min(179.0, float(viewing_angle)))
    height = 2.0 * abs(float(distance)) * math.tan(math.radians(clamped_angle) / 2.0)
    try:
        aspect = float(cmds.camera(camera_shape, query=True, aspectRatio=True))
    except Exception:
        vertical = float(cmds.getAttr(f"{camera_shape}.verticalFilmAperture") or 1.0)
        horizontal = float(cmds.getAttr(f"{camera_shape}.horizontalFilmAperture") or vertical)
        aspect = horizontal / vertical if abs(vertical) > 1e-9 else 1.0
    return max(1e-6, height * max(1e-6, aspect))


def maya_camera_eye_from_vmd_state(
    position: Tuple[float, float, float],
    rotation: Tuple[float, float, float],
    distance: float,
    motion_scale: float = 1.0,
) -> Tuple[float, float, float]:
    """Convert MMD camera target/distance state to a Maya camera eye position."""
    target = om.MVector(*mmd_point_to_maya(position, motion_scale))
    camera_rotation = _mmd_camera_rotation_matrix(rotation)
    offset = om.MVector(0.0, 0.0, float(distance)) * camera_rotation
    eye = target + om.MVector(offset.x * motion_scale, offset.y * motion_scale, -offset.z * motion_scale)
    return eye.x, eye.y, eye.z


def maya_camera_rotation_from_vmd_state(rotation: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Convert MMD camera Euler channels to Maya camera transform Euler channels."""
    camera_rotation = _mmd_camera_rotation_matrix(rotation)
    look = om.MVector(0.0, 0.0, 1.0) * camera_rotation
    up = om.MVector(0.0, 1.0, 0.0) * camera_rotation
    forward = om.MVector(look.x, look.y, -look.z)
    maya_up = om.MVector(up.x, up.y, -up.z)
    return _maya_camera_euler_from_forward_up(forward, maya_up)


def maya_camera_up_from_vmd_state(rotation: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Convert MMD camera Euler channels to a Maya-space up vector."""
    camera_rotation = _mmd_camera_rotation_matrix(rotation)
    up = om.MVector(0.0, 1.0, 0.0) * camera_rotation
    maya_up = om.MVector(up.x, up.y, -up.z)
    if maya_up.length() <= 1e-12:
        return 0.0, 1.0, 0.0
    maya_up.normalize()
    return maya_up.x, maya_up.y, maya_up.z


def mmd_camera_rotation_from_maya_forward_up(
    forward: Tuple[float, float, float],
    up: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    """Convert Maya camera forward/up vectors back to MMD camera Euler channels."""
    look = om.MVector(float(forward[0]), float(forward[1]), -float(forward[2]))
    if look.length() <= 1e-12:
        look = om.MVector(0.0, 0.0, 1.0)
    look.normalize()
    mmd_up = om.MVector(float(up[0]), float(up[1]), -float(up[2]))
    if mmd_up.length() <= 1e-12:
        mmd_up = om.MVector(0.0, 1.0, 0.0)
    mmd_up.normalize()
    x_axis = mmd_up ^ look
    if x_axis.length() <= 1e-12:
        x_axis = om.MVector(1.0, 0.0, 0.0)
    x_axis.normalize()
    y_axis = look ^ x_axis
    y_axis.normalize()
    matrix = om.MMatrix(
        [
            x_axis.x,
            x_axis.y,
            x_axis.z,
            0.0,
            y_axis.x,
            y_axis.y,
            y_axis.z,
            0.0,
            look.x,
            look.y,
            look.z,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ]
    )
    euler = om.MTransformationMatrix(matrix).rotation()
    euler.reorderIt(om.MEulerRotation.kZXY)
    return -euler.x, -euler.y, -euler.z


def _mmd_camera_rotation_matrix(rotation: Tuple[float, float, float]) -> om.MMatrix:
    """Return the MMD camera orbit rotation matrix.

    This mirrors three-mmd-loader's Euler(-x, -y, -z, "YXZ") camera convention
    under Maya API's row-vector multiplication. In this convention roll is applied
    before the distance offset, so it does not move the camera eye.
    """
    return om.MEulerRotation(
        -float(rotation[0]),
        -float(rotation[1]),
        -float(rotation[2]),
        om.MEulerRotation.kZXY,
    ).asMatrix()


def _maya_camera_euler_from_forward_up(forward: om.MVector, up: om.MVector) -> Tuple[float, float, float]:
    """Build Maya transform Euler angles from camera forward/up vectors."""
    forward = om.MVector(forward)
    up = om.MVector(up)
    if forward.length() <= 1e-12:
        forward = om.MVector(0.0, 0.0, -1.0)
    forward.normalize()
    if up.length() <= 1e-12:
        up = om.MVector(0.0, 1.0, 0.0)
    up.normalize()

    z_axis = -forward
    x_axis = up ^ z_axis
    if x_axis.length() <= 1e-12:
        x_axis = om.MVector(1.0, 0.0, 0.0)
    x_axis.normalize()
    y_axis = z_axis ^ x_axis
    y_axis.normalize()

    matrix = om.MMatrix(
        [
            x_axis.x,
            x_axis.y,
            x_axis.z,
            0.0,
            y_axis.x,
            y_axis.y,
            y_axis.z,
            0.0,
            z_axis.x,
            z_axis.y,
            z_axis.z,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ]
    )
    euler = om.MTransformationMatrix(matrix).rotation()
    return euler.x, euler.y, euler.z


def _maya_camera_zxy_aim_euler_from_eye_target(
    eye: Tuple[float, float, float],
    target: Tuple[float, float, float],
) -> Tuple[float, float]:
    forward = om.MVector(*target) - om.MVector(*eye)
    if forward.length() <= 1e-12:
        forward = om.MVector(0.0, 0.0, -1.0)
    forward.normalize()
    up = om.MVector(0.0, 1.0, 0.0)
    up = up - forward * (up * forward)
    if up.length() <= 1e-12:
        up = om.MVector(1.0, 0.0, 0.0)
    euler = om.MEulerRotation(*_maya_camera_euler_from_forward_up(forward, up))
    euler.reorderIt(om.MEulerRotation.kZXY)
    return euler.x, euler.y


def _camera_frame_range(camera_frames) -> Tuple[float, float]:
    frame_numbers = [float(_frame_value(frame, "frame_number", "frame", 0.0)) for frame in camera_frames]
    return min(frame_numbers), max(frame_numbers)


def _frame_value(frame, attr_name: str, key_name: str, default):
    if hasattr(frame, attr_name):
        return getattr(frame, attr_name)
    if hasattr(frame, key_name):
        return getattr(frame, key_name)
    if isinstance(frame, dict):
        return frame.get(key_name, frame.get(attr_name, default))
    return default


def _camera_samples_from_runtime(converter, camera_frames, vmd_bytes: Optional[bytes]) -> Optional[List[dict]]:
    if sample_vmd_camera_frames is None or not vmd_bytes:
        return None
    if not camera_frames:
        return None

    min_frame, max_frame = _camera_frame_range(camera_frames)
    start_maya_time = math.floor(converter.vmd_frame_to_maya_time(min_frame))
    end_maya_time = math.ceil(converter.vmd_frame_to_maya_time(max_frame))
    frame_count = max(1, int(end_maya_time - start_maya_time) + 1)
    start_vmd_frame = converter.maya_time_to_vmd_frame(start_maya_time)
    frame_step = converter.maya_time_to_vmd_frame(start_maya_time + 1.0) - start_vmd_frame
    samples = sample_vmd_camera_frames(vmd_bytes, start_vmd_frame, frame_step, frame_count)
    if not samples:
        return None

    dense = []
    for index, sample in enumerate(samples):
        dense.append(
            {
                "maya_time": start_maya_time + index,
                "position": tuple(sample.get("position", (0.0, 0.0, 0.0))),
                "rotation": tuple(sample.get("rotation", (0.0, 0.0, 0.0))),
                "distance": float(sample.get("distance", 0.0)),
                "viewing_angle": float(sample.get("fov", 45.0)),
                "perspective": 0 if bool(sample.get("perspective", True)) else 1,
                "runtime_sampled": True,
            }
        )
    return dense


def _sparse_camera_samples_from_frames(converter, camera_frames) -> List[dict]:
    samples = []
    for frame in camera_frames:
        frame_number = _frame_value(frame, "frame_number", "frame", 0)
        samples.append(
            {
                "maya_time": converter.vmd_frame_to_maya_time(frame_number),
                "position": tuple(_frame_value(frame, "position", "position", (0, 0, 0))),
                "rotation": tuple(_frame_value(frame, "rotation", "rotation", (0, 0, 0))),
                "distance": float(_frame_value(frame, "distance", "distance", 0.0)),
                "viewing_angle": float(_frame_value(frame, "viewing_angle", "fov", 45)),
                "perspective": int(_frame_value(frame, "perspective", "perspective", 0)),
                "runtime_sampled": False,
            }
        )
    return samples


def get_or_create_camera() -> str:
    """Return the MMD camera transform, creating one if needed."""
    existing = cmds.ls(f"*.{ATTR_MMD_CAMERA}", objectsOnly=True)
    if existing:
        camera_transform = existing[0]
        if not cmds.attributeQuery(ATTR_MMD_CAMERA_TARGET_NODE, node=camera_transform, exists=True):
            return _ensure_mmd_camera_rig(camera_transform)
        return camera_transform

    root = cmds.group(empty=True, name=MMD_CAMERA_RIG_ROOT_NAME)
    camera_transform, _ = cmds.camera(name=DEFAULT_CAMERA_NAME)
    cmds.parent(camera_transform, root)
    cmds.addAttr(camera_transform, longName=ATTR_MMD_CAMERA, attributeType="bool")
    cmds.setAttr(f"{camera_transform}.{ATTR_MMD_CAMERA}", True)
    return _ensure_mmd_camera_rig(camera_transform)


def _ensure_mmd_camera_rig(camera_transform: str, *, orbit_hierarchy: bool = False) -> str:
    """Prepare a self-contained MMD camera rig container."""
    _ensure_string_attr(camera_transform, ATTR_MMD_CAMERA_RIG_TYPE, "mmd_aim_roll")
    _ensure_message_attr(camera_transform, ATTR_MMD_CAMERA_TARGET_NODE)
    _ensure_message_attr(camera_transform, ATTR_MMD_CAMERA_ROOT_NODE)
    target = _ensure_mmd_camera_target(camera_transform)
    root = _ensure_mmd_camera_root(camera_transform, target, orbit_hierarchy=orbit_hierarchy)
    _connect_message(target, camera_transform, ATTR_MMD_CAMERA_TARGET_NODE)
    _connect_message(root, camera_transform, ATTR_MMD_CAMERA_ROOT_NODE)
    if cmds.attributeQuery("rotateOrder", node=camera_transform, exists=True):
        cmds.setAttr(f"{camera_transform}.rotateOrder", 2)  # zxy: rotateZ is the local roll channel.
    if cmds.attributeQuery("rotateOrder", node=target, exists=True):
        cmds.setAttr(f"{target}.rotateOrder", 2)
    _delete_aim_constraints(camera_transform)
    return camera_transform


def _ensure_mmd_camera_target(camera_transform: str) -> str:
    connected = cmds.listConnections(
        f"{camera_transform}.{ATTR_MMD_CAMERA_TARGET_NODE}",
        source=True,
        destination=False,
    ) or []
    if connected and cmds.objExists(connected[0]):
        return connected[0]
    short_camera = camera_transform.rsplit("|", 1)[-1]
    target_name = MMD_CAMERA_TARGET_NAME if short_camera == DEFAULT_CAMERA_NAME else f"{short_camera}_target"
    target = cmds.spaceLocator(name=target_name)[0]
    return target


def _parent_camera_rig_node(child: str, parent: str) -> None:
    current_parent = (cmds.listRelatives(child, parent=True, fullPath=False) or [None])[0]
    if current_parent == parent:
        return
    try:
        cmds.parent(child, parent)
    except Exception:
        pass


def _ensure_mmd_camera_root(camera_transform: str, target: str, *, orbit_hierarchy: bool = False) -> str:
    connected = cmds.listConnections(
        f"{camera_transform}.{ATTR_MMD_CAMERA_ROOT_NODE}",
        source=True,
        destination=False,
    ) or []
    if connected and cmds.objExists(connected[0]):
        root = connected[0]
    else:
        parent = (cmds.listRelatives(camera_transform, parent=True, fullPath=False) or [None])[0]
        root = parent if parent else cmds.group(empty=True, name=MMD_CAMERA_RIG_ROOT_NAME)
    if orbit_hierarchy:
        _parent_camera_rig_node(target, root)
        _parent_camera_rig_node(camera_transform, target)
    else:
        _parent_camera_rig_node(camera_transform, root)
        _parent_camera_rig_node(target, root)
    return root


def _delete_aim_constraints(camera_transform: str) -> None:
    """Disconnect old compatibility Aim/Up rigs before installing the editable camera rig."""
    constraints = []
    for attr in ("rotateX", "rotateY", "rotateZ"):
        constraints.extend(
            cmds.listConnections(
                f"{camera_transform}.{attr}",
                source=True,
                destination=False,
                type="aimConstraint",
            )
            or []
        )
    if constraints:
        cmds.delete(list(set(constraints)))


def _ensure_string_attr(node: str, attr: str, value: str) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, dataType="string")
    cmds.setAttr(f"{node}.{attr}", value, type="string")


def _ensure_message_attr(node: str, attr: str) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, attributeType="message")


def _connect_message(source_node: str, target_node: str, target_attr: str) -> None:
    _ensure_message_attr(target_node, target_attr)
    target_plug = f"{target_node}.{target_attr}"
    for connection in cmds.listConnections(target_plug, source=True, destination=False, plugs=True) or []:
        try:
            cmds.disconnectAttr(connection, target_plug)
        except Exception:
            pass
    cmds.connectAttr(f"{source_node}.message", target_plug, force=True)


def _prepare_mmd_camera_shape(camera_shape: str) -> None:
    """Set fixed camera shape options needed for MMD camera projection parity."""
    if cmds.attributeQuery("filmFit", node=camera_shape, exists=True):
        cmds.setAttr(f"{camera_shape}.filmFit", 2)  # vertical


def evaluate_mmd_camera_rig(
    camera_transform: str,
    camera_shape: str = "",
    motion_scale: float = 1.0,
    target_transform: str = "",
) -> None:
    """Evaluate sparse MMD camera raw attrs into the Maya aim rig."""
    if target_transform and cmds.objExists(target_transform):
        target_position = cmds.xform(target_transform, query=True, worldSpace=True, translation=True)
        scale = float(motion_scale) if abs(float(motion_scale)) > 1.0e-12 else 1.0
        position = (
            float(target_position[0]) / scale,
            float(target_position[1]) / scale,
            -float(target_position[2]) / scale,
        )
    else:
        position = (
            cmds.getAttr(f"{camera_transform}.mmd_camera_target_x"),
            cmds.getAttr(f"{camera_transform}.mmd_camera_target_y"),
            cmds.getAttr(f"{camera_transform}.mmd_camera_target_z"),
        )
    rotation = (
        cmds.getAttr(f"{camera_transform}.mmd_camera_rotation_x"),
        cmds.getAttr(f"{camera_transform}.mmd_camera_rotation_y"),
        cmds.getAttr(f"{camera_transform}.mmd_camera_rotation_z"),
    )
    distance = float(cmds.getAttr(f"{camera_transform}.mmd_camera_distance"))
    viewing_angle = float(cmds.getAttr(f"{camera_transform}.mmd_camera_viewing_angle"))
    eye_x, eye_y, eye_z = maya_camera_eye_from_vmd_state(position, rotation, distance, motion_scale)
    target_x, target_y, target_z = mmd_point_to_maya(position, motion_scale)
    cmds.setAttr(f"{camera_transform}.translate", eye_x, eye_y, eye_z, type="double3")
    if target_transform and cmds.objExists(target_transform):
        cmds.setAttr(f"{target_transform}.translate", target_x, target_y, target_z, type="double3")
    if not target_transform:
        rotate_x, rotate_y, rotate_z = maya_camera_rotation_from_vmd_state(rotation)
        cmds.setAttr(
            f"{camera_transform}.rotate",
            math.degrees(rotate_x),
            math.degrees(rotate_y),
            math.degrees(rotate_z),
            type="double3",
        )
    if camera_shape and cmds.objExists(camera_shape):
        cmds.setAttr(f"{camera_shape}.focalLength", viewing_angle_to_focal_length(camera_shape, viewing_angle))
        cmds.setAttr(
            f"{camera_shape}.orthographicWidth",
            viewing_angle_to_orthographic_width(camera_shape, viewing_angle, distance * motion_scale),
        )
        if cmds.attributeQuery("orthographic", node=camera_shape, exists=True):
            perspective = int(round(cmds.getAttr(f"{camera_transform}.mmd_camera_perspective") or 0))
            cmds.setAttr(f"{camera_shape}.orthographic", bool(perspective))
    for attr in ("rotateX", "rotateY", "rotateZ", "worldMatrix"):
        try:
            cmds.dgdirty(f"{camera_transform}.{attr}")
        except Exception:
            pass
    try:
        cmds.dgeval(camera_transform)
    except Exception:
        pass


def evaluate_mmd_camera_aim_roll_rig(camera_transform: str, target_transform: str) -> None:
    """Evaluate camera rotateX/Y from camera position, target position, and keyed rotateZ roll."""
    if not (cmds.objExists(camera_transform) and cmds.objExists(target_transform)):
        return
    eye = cmds.xform(camera_transform, query=True, worldSpace=True, translation=True)
    target = cmds.xform(target_transform, query=True, worldSpace=True, translation=True)
    rotate_x, rotate_y = _maya_camera_zxy_aim_euler_from_eye_target(tuple(eye), tuple(target))
    if cmds.attributeQuery("rotateOrder", node=camera_transform, exists=True):
        cmds.setAttr(f"{camera_transform}.rotateOrder", 2)
    cmds.setAttr(f"{camera_transform}.rotateX", math.degrees(rotate_x))
    cmds.setAttr(f"{camera_transform}.rotateY", math.degrees(rotate_y))


def _find_mmd_camera_expression(expression_id: str) -> Optional[str]:
    for expression in cmds.ls(type="expression") or []:
        if not cmds.attributeQuery(MMD_CAMERA_EXPR_ID_ATTR, node=expression, exists=True):
            continue
        try:
            if cmds.getAttr(f"{expression}.{MMD_CAMERA_EXPR_ID_ATTR}") == expression_id:
                return expression
        except Exception:
            continue
    return None


def evaluate_mmd_camera_expression(expression_id: str) -> None:
    """Evaluate an MMD camera expression through message-connected scene nodes."""
    expression = _find_mmd_camera_expression(expression_id)
    if not expression:
        return
    if not cmds.attributeQuery(MMD_CAMERA_EXPR_OWNER_ATTR, node=expression, exists=True):
        return
    cameras = cmds.listConnections(
        f"{expression}.{MMD_CAMERA_EXPR_OWNER_ATTR}",
        source=True,
        destination=False,
    ) or []
    if not cameras:
        return
    targets = []
    if cmds.attributeQuery(MMD_CAMERA_EXPR_TARGET_ATTR, node=expression, exists=True):
        targets = cmds.listConnections(
            f"{expression}.{MMD_CAMERA_EXPR_TARGET_ATTR}",
            source=True,
            destination=False,
        ) or []
    shapes = []
    if cmds.attributeQuery(MMD_CAMERA_EXPR_SHAPE_ATTR, node=expression, exists=True):
        shapes = cmds.listConnections(
            f"{expression}.{MMD_CAMERA_EXPR_SHAPE_ATTR}",
            source=True,
            destination=False,
            shapes=True,
        ) or []
    try:
        motion_scale = float(cmds.getAttr(f"{expression}.{MMD_CAMERA_EXPR_SCALE_ATTR}"))
    except Exception:
        motion_scale = 1.0
    evaluate_mmd_camera_rig(
        cameras[0],
        shapes[0] if shapes else "",
        motion_scale,
        targets[0] if targets else "",
    )


def _mmd_camera_expression_name(camera_transform: str) -> str:
    short_name = camera_transform.rsplit("|", 1)[-1].replace(":", "_")
    return f"{short_name}_mmdCameraRigExpression"


def _delete_mmd_camera_expression(camera_transform: str) -> None:
    expressions = set()
    if cmds.objExists(camera_transform):
        for expression in (
            cmds.listConnections(
                f"{camera_transform}.message",
                source=False,
                destination=True,
                type="expression",
            ) or []
        ):
            if cmds.attributeQuery(MMD_CAMERA_EXPR_ID_ATTR, node=expression, exists=True):
                expressions.add(expression)
    named_expression = _mmd_camera_expression_name(camera_transform)
    if cmds.objExists(named_expression):
        expressions.add(named_expression)
    for expression in expressions:
        if cmds.objExists(expression):
            cmds.delete(expression)


def _remove_camera_output_from_anim_layers(node: str, attr: str) -> None:
    plug = f"{node}.{attr}"
    for layer in cmds.ls(type="animLayer") or []:
        try:
            layer_attrs = cmds.animLayer(layer, query=True, attribute=True) or []
        except Exception:
            continue
        if plug not in layer_attrs:
            continue
        try:
            cmds.animLayer(layer, edit=True, removeAttribute=plug)
        except Exception:
            pass


def _disconnect_camera_output_animation_sources(node: str, attr: str) -> None:
    plug = f"{node}.{attr}"
    for source_plug in cmds.listConnections(plug, source=True, destination=False, plugs=True) or []:
        source_node = source_plug.split(".", 1)[0]
        try:
            source_type = cmds.nodeType(source_node)
        except Exception:
            continue
        if not (source_type.startswith("animCurve") or source_type.startswith("animBlendNode") or source_type == "pairBlend"):
            continue
        try:
            cmds.disconnectAttr(source_plug, plug)
        except Exception:
            pass


def _delete_camera_output_attr_keys(node: str, attr: str) -> None:
    _remove_camera_output_from_anim_layers(node, attr)
    try:
        cmds.cutKey(node, attribute=attr)
    except Exception:
        pass
    _disconnect_camera_output_animation_sources(node, attr)


def _delete_mmd_camera_raw_attrs(camera_transform: str) -> None:
    raw_attrs = (
        tuple(name for name, _attr_type, _default in MMD_CAMERA_SCALAR_ATTRS)
        + MMD_CAMERA_TARGET_ATTRS
        + MMD_CAMERA_ROTATION_ATTRS
    )
    for attr in raw_attrs:
        if not cmds.attributeQuery(attr, node=camera_transform, exists=True):
            continue
        _delete_camera_output_attr_keys(camera_transform, attr)
        try:
            cmds.deleteAttr(f"{camera_transform}.{attr}")
        except Exception:
            pass


def _delete_camera_output_keys(camera_transform: str, camera_shape: Optional[str]) -> None:
    """Remove stale final camera output curves before sparse direct-key import."""
    for attr in MMD_CAMERA_OUTPUT_ATTRS:
        if cmds.attributeQuery(attr, node=camera_transform, exists=True):
            _delete_camera_output_attr_keys(camera_transform, attr)
    if camera_shape and cmds.objExists(camera_shape):
        for attr in MMD_CAMERA_SHAPE_OUTPUT_ATTRS:
            if cmds.attributeQuery(attr, node=camera_shape, exists=True):
                _delete_camera_output_attr_keys(camera_shape, attr)


def convert_camera_animation(converter, camera_frames, vmd_bytes: Optional[bytes] = None) -> bool:
    """Convert VMD camera frames using the converter's shared Maya helpers."""
    if not camera_frames:
        return False

    camera_transform = converter._get_or_create_camera()
    camera_shapes = cmds.listRelatives(camera_transform, shapes=True, type="camera") or []
    camera_shape = camera_shapes[0] if camera_shapes else None
    if camera_shape:
        _prepare_mmd_camera_shape(camera_shape)

    samples = _camera_samples_from_runtime(converter, camera_frames, vmd_bytes)
    runtime_sampled = samples is not None
    if samples is None:
        samples = _sparse_camera_samples_from_frames(converter, camera_frames)

    _ensure_mmd_camera_rig(camera_transform, orbit_hierarchy=not runtime_sampled)
    camera_target = _ensure_mmd_camera_target(camera_transform)
    camera_root = _ensure_mmd_camera_root(camera_transform, camera_target, orbit_hierarchy=not runtime_sampled)
    _delete_mmd_camera_expression(camera_transform)
    _delete_mmd_camera_raw_attrs(camera_transform)
    if cmds.attributeQuery("rotateOrder", node=camera_transform, exists=True):
        cmds.setAttr(f"{camera_transform}.rotateOrder", 0 if runtime_sampled else 2)

    camera_samples = {
        "translateX": [],
        "translateY": [],
        "translateZ": [],
        "rotateX": [],
        "rotateY": [],
        "rotateZ": [],
    }
    target_samples = {
        "translateX": [],
        "translateY": [],
        "translateZ": [],
        "rotateX": [],
        "rotateY": [],
    }
    camera_shape_samples = {"focalLength": [], "orthographicWidth": []}
    orthographic_samples = []

    for sample in samples:
        maya_time = sample["maya_time"]
        position = sample["position"]
        rotation = sample["rotation"]
        distance = sample["distance"]
        viewing_angle = sample["viewing_angle"]
        perspective = sample["perspective"]
        target_x, target_y, target_z = mmd_point_to_maya(position, converter.motion_scale)
        if runtime_sampled:
            rotate_x, rotate_y, rotate_z = maya_camera_rotation_from_vmd_state(rotation)
            eye_x, eye_y, eye_z = maya_camera_eye_from_vmd_state(
                position,
                rotation,
                distance,
                converter.motion_scale,
            )
            camera_samples["translateX"].append((maya_time, eye_x))
            camera_samples["translateY"].append((maya_time, eye_y))
            camera_samples["translateZ"].append((maya_time, eye_z))
            camera_samples["rotateX"].append((maya_time, math.degrees(rotate_x)))
            camera_samples["rotateY"].append((maya_time, math.degrees(rotate_y)))
            camera_samples["rotateZ"].append((maya_time, math.degrees(rotate_z)))
            target_samples["translateX"].append((maya_time, target_x))
            target_samples["translateY"].append((maya_time, target_y))
            target_samples["translateZ"].append((maya_time, target_z))
        else:
            camera_samples["translateZ"].append((maya_time, -float(distance) * converter.motion_scale))
            target_samples["translateX"].append((maya_time, target_x))
            target_samples["translateY"].append((maya_time, target_y))
            target_samples["translateZ"].append((maya_time, target_z))
            target_samples["rotateX"].append((maya_time, math.degrees(float(rotation[0]))))
            target_samples["rotateY"].append((maya_time, math.degrees(float(rotation[1]))))
            camera_samples["rotateZ"].append((maya_time, -math.degrees(float(rotation[2]))))

        if camera_shape:
            focal_length = viewing_angle_to_focal_length(camera_shape, float(viewing_angle))
            camera_shape_samples["focalLength"].append((maya_time, focal_length))
            orthographic_width = viewing_angle_to_orthographic_width(
                camera_shape,
                float(viewing_angle),
                float(distance) * converter.motion_scale,
            )
            camera_shape_samples["orthographicWidth"].append((maya_time, orthographic_width))
            if cmds.attributeQuery("orthographic", node=camera_shape, exists=True):
                orthographic_samples.append((maya_time, bool(perspective)))

    animation_layer = converter.anim_layer if converter.use_animation_layers and converter.anim_layer else None
    key_animation_layer = animation_layer if runtime_sampled else None
    raw_camera_samples = {attr: attr_samples for attr, attr_samples in camera_samples.items() if attr_samples}
    target_samples = {attr: attr_samples for attr, attr_samples in target_samples.items() if attr_samples}
    keyed_shape_samples = camera_shape_samples
    if not runtime_sampled:
        _delete_camera_output_keys(camera_transform, camera_shape)
        for node in (camera_root, camera_target):
            for attr in MMD_CAMERA_OUTPUT_ATTRS:
                if cmds.attributeQuery(attr, node=node, exists=True):
                    _delete_camera_output_attr_keys(node, attr)
        for attr, value in (
            ("translateX", 0.0),
            ("translateY", 0.0),
            ("rotateX", 0.0),
            ("rotateY", 0.0),
        ):
            if cmds.attributeQuery(attr, node=camera_transform, exists=True):
                cmds.setAttr(f"{camera_transform}.{attr}", value)
        if cmds.attributeQuery("rotateZ", node=camera_target, exists=True):
            cmds.setAttr(f"{camera_target}.rotateZ", 0.0)
    if key_animation_layer:
        converter._add_attrs_to_anim_layer(camera_transform, list(raw_camera_samples))
        if camera_shape and keyed_shape_samples:
            converter._add_attrs_to_anim_layer(camera_shape, list(keyed_shape_samples) + ["orthographic"])
        raw_camera_samples = converter._samples_as_anim_layer_deltas(camera_transform, raw_camera_samples)
        if camera_shape and keyed_shape_samples:
            keyed_shape_samples = converter._samples_as_anim_layer_deltas(camera_shape, keyed_shape_samples)

    converter._batch_key_scalar_channels(camera_transform, raw_camera_samples, animation_layer=key_animation_layer)
    converter._batch_key_scalar_channels(camera_target, target_samples, animation_layer=key_animation_layer)
    if camera_shape and keyed_shape_samples:
        converter._batch_key_scalar_channels(camera_shape, keyed_shape_samples, animation_layer=key_animation_layer)

    if camera_shape:
        for maya_time, orthographic in orthographic_samples:
            key_args = {
                "attribute": "orthographic",
                "time": maya_time,
                "value": bool(orthographic),
            }
            if key_animation_layer:
                key_args["animLayer"] = key_animation_layer
            cmds.setKeyframe(camera_shape, **key_args)

    if runtime_sampled:
        camera_tangent_targets = {}
        camera_channel_map = {}
    else:
        camera_tangent_targets = {
            "targetX": (camera_target, "translateX"),
            "targetY": (camera_target, "translateY"),
            "targetZ": (camera_target, "translateZ"),
            "targetRotateX": (camera_target, "rotateX"),
            "targetRotateY": (camera_target, "rotateY"),
            "cameraTranslateZ": (camera_transform, "translateZ"),
            "focalLength": (camera_shape, "focalLength") if camera_shape else (camera_transform, "translateX"),
            "orthographicWidth": (camera_shape, "orthographicWidth") if camera_shape else (camera_transform, "translateX"),
            "rollZ": (camera_transform, "rotateZ"),
        }
        camera_channel_map = {
            "targetX": "translate_x",
            "targetY": "translate_y",
            "targetZ": "translate_z",
            "targetRotateX": "rotation",
            "targetRotateY": "rotation",
            "cameraTranslateZ": "distance",
            "focalLength": "viewing_angle",
            "orthographicWidth": "viewing_angle",
            "rollZ": "rotation",
        }
    if not runtime_sampled:
        converter._apply_vmd_bezier_tangents(
            camera_transform,
            sorted(camera_frames, key=converter._get_frame_number),
            camera_tangent_targets,
            camera_channel_map,
            interpolation_parser=parse_vmd_camera_interpolation,
        )
        _delete_mmd_camera_expression(camera_transform)
    else:
        _delete_mmd_camera_expression(camera_transform)

    return True
