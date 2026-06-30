"""Camera-specific helpers for VMD animation conversion."""

import math
import uuid
from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core.constants import ATTR_MMD_CAMERA, DEFAULT_CAMERA_NAME
from ..core.coordinate_transform import mmd_point_to_maya

ATTR_MMD_CAMERA_RIG_TYPE = "mmd_camera_rig_type"
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
MMD_CAMERA_EXPR_SHAPE_ATTR = "mmd_camera_shape"
MMD_CAMERA_EXPR_SCALE_ATTR = "mmd_camera_motion_scale"

try:
    from ..core.native.mmd_anim_runtime import sample_vmd_camera_frames
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
        return existing[0]

    camera_transform, _ = cmds.camera(name=DEFAULT_CAMERA_NAME)
    cmds.addAttr(camera_transform, longName=ATTR_MMD_CAMERA, attributeType="bool")
    cmds.setAttr(f"{camera_transform}.{ATTR_MMD_CAMERA}", True)
    return _ensure_mmd_camera_rig(camera_transform)


def _ensure_mmd_camera_rig(camera_transform: str) -> str:
    """Prepare a self-contained MMD camera rig on the camera transform."""
    _delete_aim_constraints(camera_transform)
    _ensure_string_attr(camera_transform, ATTR_MMD_CAMERA_RIG_TYPE, "mmd")
    for attr_name, attr_type, default_value in MMD_CAMERA_SCALAR_ATTRS:
        _ensure_numeric_attr(camera_transform, attr_name, attr_type, default_value)
    for attr_name in MMD_CAMERA_TARGET_ATTRS:
        _ensure_numeric_attr(camera_transform, attr_name, "double", 0.0)
    for attr_name in MMD_CAMERA_ROTATION_ATTRS:
        _ensure_numeric_attr(camera_transform, attr_name, "double", 0.0)
    return camera_transform


def _delete_aim_constraints(camera_transform: str) -> None:
    """Disconnect old compatibility Aim/Up rigs so raw MMD attrs drive the camera."""
    constraints = cmds.listConnections(
        f"{camera_transform}.rotateX",
        source=True,
        destination=False,
        type="aimConstraint",
    ) or []
    if constraints:
        cmds.delete(list(set(constraints)))


def _ensure_numeric_attr(node: str, attr: str, attr_type: str, value) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, attributeType=attr_type, keyable=True)
        cmds.setAttr(f"{node}.{attr}", value)


def _ensure_string_attr(node: str, attr: str, value: str) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, dataType="string")
    cmds.setAttr(f"{node}.{attr}", value, type="string")


def _ensure_message_attr(node: str, attr: str) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, attributeType="message")


def _ensure_double_attr(node: str, attr: str, value: float) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, attributeType="double")
    cmds.setAttr(f"{node}.{attr}", float(value))


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


def evaluate_mmd_camera_rig(camera_transform: str, camera_shape: str = "", motion_scale: float = 1.0) -> None:
    """Evaluate sparse MMD camera raw attrs into Maya camera outputs."""
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
    rotate_x, rotate_y, rotate_z = maya_camera_rotation_from_vmd_state(rotation)
    cmds.setAttr(f"{camera_transform}.translate", eye_x, eye_y, eye_z, type="double3")
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
    evaluate_mmd_camera_rig(cameras[0], shapes[0] if shapes else "", motion_scale)


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


def _ensure_mmd_camera_expression(camera_transform: str, camera_shape: Optional[str], motion_scale: float) -> None:
    _delete_mmd_camera_expression(camera_transform)
    expression_id = str(uuid.uuid4())
    expression = cmds.expression(
        name=_mmd_camera_expression_name(camera_transform),
        string="",
        alwaysEvaluate=True,
        unitConversion="all",
    )
    _ensure_string_attr(expression, MMD_CAMERA_EXPR_ID_ATTR, expression_id)
    _ensure_double_attr(expression, MMD_CAMERA_EXPR_SCALE_ATTR, float(motion_scale))
    _connect_message(camera_transform, expression, MMD_CAMERA_EXPR_OWNER_ATTR)
    if camera_shape and cmds.objExists(camera_shape):
        _connect_message(camera_shape, expression, MMD_CAMERA_EXPR_SHAPE_ATTR)
    py_code = (
        "from mmd_tools.converters.vmd_camera_animation import evaluate_mmd_camera_expression; "
        f"evaluate_mmd_camera_expression({expression_id!r})"
    )
    expression_body = f'python("{py_code.replace(chr(92), chr(92) + chr(92)).replace(chr(34), chr(92) + chr(34))}");'
    cmds.expression(
        expression,
        edit=True,
        string=expression_body,
    )
    evaluate_mmd_camera_rig(camera_transform, camera_shape or "", motion_scale)


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


def _delete_camera_output_keys(camera_transform: str, camera_shape: Optional[str]) -> None:
    """Remove stale final camera output curves before sparse expression driving."""
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
    _ensure_mmd_camera_rig(camera_transform)

    camera_samples = {
        "translateX": [],
        "translateY": [],
        "translateZ": [],
        "rotateX": [],
        "rotateY": [],
        "rotateZ": [],
        "mmd_camera_distance": [],
        "mmd_camera_viewing_angle": [],
        "mmd_camera_target_x": [],
        "mmd_camera_target_y": [],
        "mmd_camera_target_z": [],
        "mmd_camera_rotation_x": [],
        "mmd_camera_rotation_y": [],
        "mmd_camera_rotation_z": [],
    }
    camera_shape_samples = {"focalLength": [], "orthographicWidth": []}
    perspective_samples = []
    orthographic_samples = []

    samples = _camera_samples_from_runtime(converter, camera_frames, vmd_bytes)
    runtime_sampled = samples is not None
    if samples is None:
        samples = _sparse_camera_samples_from_frames(converter, camera_frames)

    for sample in samples:
        maya_time = sample["maya_time"]
        position = sample["position"]
        rotation = sample["rotation"]
        distance = sample["distance"]
        viewing_angle = sample["viewing_angle"]
        perspective = sample["perspective"]
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
        camera_samples["mmd_camera_distance"].append((maya_time, distance))
        camera_samples["mmd_camera_viewing_angle"].append((maya_time, float(viewing_angle)))
        camera_samples["mmd_camera_target_x"].append((maya_time, float(position[0])))
        camera_samples["mmd_camera_target_y"].append((maya_time, float(position[1])))
        camera_samples["mmd_camera_target_z"].append((maya_time, float(position[2])))
        camera_samples["mmd_camera_rotation_x"].append((maya_time, float(rotation[0])))
        camera_samples["mmd_camera_rotation_y"].append((maya_time, float(rotation[1])))
        camera_samples["mmd_camera_rotation_z"].append((maya_time, float(rotation[2])))
        perspective_samples.append((maya_time, int(perspective)))

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
    raw_camera_samples = {
        attr: samples_for_attr
        for attr, samples_for_attr in camera_samples.items()
        if runtime_sampled or attr not in MMD_CAMERA_OUTPUT_ATTRS
    }
    keyed_shape_samples = camera_shape_samples if runtime_sampled else {}
    if not runtime_sampled:
        _delete_camera_output_keys(camera_transform, camera_shape)
    if key_animation_layer:
        converter._add_attrs_to_anim_layer(camera_transform, list(raw_camera_samples) + ["mmd_camera_perspective"])
        if camera_shape and keyed_shape_samples:
            converter._add_attrs_to_anim_layer(camera_shape, list(keyed_shape_samples) + ["orthographic"])
        raw_camera_samples = converter._samples_as_anim_layer_deltas(camera_transform, raw_camera_samples)
        if camera_shape and keyed_shape_samples:
            keyed_shape_samples = converter._samples_as_anim_layer_deltas(camera_shape, keyed_shape_samples)

    converter._batch_key_scalar_channels(camera_transform, raw_camera_samples, animation_layer=key_animation_layer)
    if camera_shape and keyed_shape_samples:
        converter._batch_key_scalar_channels(camera_shape, keyed_shape_samples, animation_layer=key_animation_layer)

    for maya_time, perspective in perspective_samples:
        key_args = {
            "attribute": "mmd_camera_perspective",
            "time": maya_time,
            "value": int(perspective),
        }
        if key_animation_layer:
            key_args["animLayer"] = key_animation_layer
        cmds.setKeyframe(camera_transform, **key_args)
    if runtime_sampled and camera_shape:
        for maya_time, orthographic in orthographic_samples:
            key_args = {
                "attribute": "orthographic",
                "time": maya_time,
                "value": bool(orthographic),
            }
            if key_animation_layer:
                key_args["animLayer"] = key_animation_layer
            cmds.setKeyframe(camera_shape, **key_args)

    camera_tangent_targets = {
        "translateX": (camera_transform, "translateX"),
        "translateY": (camera_transform, "translateY"),
        "translateZ": (camera_transform, "translateZ"),
        "rotateX": (camera_transform, "rotateX"),
        "rotateY": (camera_transform, "rotateY"),
        "rotateZ": (camera_transform, "rotateZ"),
        "mmd_camera_distance": (camera_transform, "mmd_camera_distance"),
        "mmd_camera_viewing_angle": (camera_transform, "mmd_camera_viewing_angle"),
        "mmd_camera_target_x": (camera_transform, "mmd_camera_target_x"),
        "mmd_camera_target_y": (camera_transform, "mmd_camera_target_y"),
        "mmd_camera_target_z": (camera_transform, "mmd_camera_target_z"),
        "mmd_camera_rotation_x": (camera_transform, "mmd_camera_rotation_x"),
        "mmd_camera_rotation_y": (camera_transform, "mmd_camera_rotation_y"),
        "mmd_camera_rotation_z": (camera_transform, "mmd_camera_rotation_z"),
    }
    if camera_shape:
        camera_tangent_targets["focalLength"] = (camera_shape, "focalLength")
        camera_tangent_targets["orthographicWidth"] = (camera_shape, "orthographicWidth")
    camera_channel_map = {
        "translateX": "translate_x",
        "translateY": "translate_y",
        "translateZ": "translate_z",
        "rotateX": "rotation",
        "rotateY": "rotation",
        "rotateZ": "rotation",
        "mmd_camera_distance": "distance",
        "mmd_camera_viewing_angle": "viewing_angle",
        "mmd_camera_target_x": "translate_x",
        "mmd_camera_target_y": "translate_y",
        "mmd_camera_target_z": "translate_z",
        "mmd_camera_rotation_x": "rotation",
        "mmd_camera_rotation_y": "rotation",
        "mmd_camera_rotation_z": "rotation",
        "focalLength": "viewing_angle",
        "orthographicWidth": "viewing_angle",
    }
    if not runtime_sampled:
        converter._apply_vmd_bezier_tangents(
            camera_transform,
            sorted(camera_frames, key=converter._get_frame_number),
            camera_tangent_targets,
            camera_channel_map,
            interpolation_parser=parse_vmd_camera_interpolation,
        )
        _ensure_mmd_camera_expression(camera_transform, camera_shape, converter.motion_scale)
    else:
        _delete_mmd_camera_expression(camera_transform)

    return True
