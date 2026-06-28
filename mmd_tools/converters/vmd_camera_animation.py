"""Camera-specific helpers for VMD animation conversion."""

import math
from typing import Dict, Tuple

import maya.cmds as cmds


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
            _norm(data[offset + 2]),
            _norm(data[offset + 1]),
            _norm(data[offset + 3]),
        )
    return parsed


def viewing_angle_to_focal_length(camera_shape: str, viewing_angle: float) -> float:
    """Convert VMD viewing_angle(deg) to Maya camera focalLength(mm)."""
    clamped_angle = max(1.0, min(179.0, float(viewing_angle)))
    aperture_inch = cmds.getAttr(f"{camera_shape}.horizontalFilmAperture")
    aperture_mm = float(aperture_inch) * 25.4
    return aperture_mm / (2.0 * math.tan(math.radians(clamped_angle) / 2.0))


def convert_camera_animation(converter, camera_frames) -> bool:
    """Convert VMD camera frames using the converter's shared Maya helpers."""
    if not camera_frames:
        return False

    camera_transform = converter._get_or_create_camera()
    camera_shapes = cmds.listRelatives(camera_transform, shapes=True, type="camera") or []
    camera_shape = camera_shapes[0] if camera_shapes else None

    for attr_name, attr_type, default_value in (
        ("mmd_camera_distance", "double", 0.0),
        ("mmd_camera_viewing_angle", "double", 45.0),
        ("mmd_camera_perspective", "long", 0),
    ):
        if not cmds.attributeQuery(attr_name, node=camera_transform, exists=True):
            cmds.addAttr(camera_transform, longName=attr_name, attributeType=attr_type, keyable=True)
            cmds.setAttr(f"{camera_transform}.{attr_name}", default_value)

    camera_samples = {
        "translateX": [],
        "translateY": [],
        "translateZ": [],
        "rotateX": [],
        "rotateY": [],
        "rotateZ": [],
        "mmd_camera_distance": [],
        "mmd_camera_viewing_angle": [],
    }
    camera_shape_samples = {"focalLength": []}
    perspective_samples = []
    orthographic_samples = []

    for frame in camera_frames:
        frame_number = frame.frame_number if hasattr(frame, "frame_number") else frame.get("frame_number", 0)
        maya_time = converter.vmd_frame_to_maya_time(frame_number)
        position = frame.position if hasattr(frame, "position") else frame.get("position", (0, 0, 0))
        rotation = frame.rotation if hasattr(frame, "rotation") else frame.get("rotation", (0, 0, 0))
        distance = frame.distance if hasattr(frame, "distance") else frame.get("distance", 0.0)
        viewing_angle = frame.viewing_angle if hasattr(frame, "viewing_angle") else frame.get("viewing_angle", 45)
        perspective = frame.perspective if hasattr(frame, "perspective") else frame.get("perspective", 0)

        camera_samples["translateX"].append((maya_time, position[0] * converter.motion_scale))
        camera_samples["translateY"].append((maya_time, position[1] * converter.motion_scale))
        camera_samples["translateZ"].append((maya_time, -position[2] * converter.motion_scale))
        camera_samples["rotateX"].append((maya_time, math.degrees(rotation[0])))
        camera_samples["rotateY"].append((maya_time, math.degrees(rotation[1])))
        camera_samples["rotateZ"].append((maya_time, -math.degrees(rotation[2])))
        camera_samples["mmd_camera_distance"].append((maya_time, distance * converter.motion_scale))
        camera_samples["mmd_camera_viewing_angle"].append((maya_time, float(viewing_angle)))
        perspective_samples.append((maya_time, int(perspective)))

        if camera_shape:
            focal_length = viewing_angle_to_focal_length(camera_shape, float(viewing_angle))
            camera_shape_samples["focalLength"].append((maya_time, focal_length))
            if cmds.attributeQuery("orthographic", node=camera_shape, exists=True):
                orthographic_samples.append((maya_time, bool(perspective)))

    animation_layer = converter.anim_layer if converter.use_animation_layers and converter.anim_layer else None
    if animation_layer:
        converter._add_attrs_to_anim_layer(camera_transform, list(camera_samples) + ["mmd_camera_perspective"])
        if camera_shape:
            converter._add_attrs_to_anim_layer(camera_shape, list(camera_shape_samples) + ["orthographic"])
        camera_samples = converter._samples_as_anim_layer_deltas(camera_transform, camera_samples)
        if camera_shape:
            camera_shape_samples = converter._samples_as_anim_layer_deltas(camera_shape, camera_shape_samples)

    converter._batch_key_scalar_channels(camera_transform, camera_samples, animation_layer=animation_layer)
    if camera_shape:
        converter._batch_key_scalar_channels(camera_shape, camera_shape_samples, animation_layer=animation_layer)

    for maya_time, perspective in perspective_samples:
        key_args = {
            "attribute": "mmd_camera_perspective",
            "time": maya_time,
            "value": int(perspective),
        }
        if animation_layer:
            key_args["animLayer"] = animation_layer
        cmds.setKeyframe(camera_transform, **key_args)
    if camera_shape:
        for maya_time, orthographic in orthographic_samples:
            key_args = {
                "attribute": "orthographic",
                "time": maya_time,
                "value": bool(orthographic),
            }
            if animation_layer:
                key_args["animLayer"] = animation_layer
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
    }
    if camera_shape:
        camera_tangent_targets["focalLength"] = (camera_shape, "focalLength")
    camera_channel_map = {
        "translateX": "translate_x",
        "translateY": "translate_y",
        "translateZ": "translate_z",
        "rotateX": "rotation",
        "rotateY": "rotation",
        "rotateZ": "rotation",
        "mmd_camera_distance": "distance",
        "mmd_camera_viewing_angle": "viewing_angle",
        "focalLength": "viewing_angle",
    }
    converter._apply_vmd_bezier_tangents(
        camera_transform,
        sorted(camera_frames, key=converter._get_frame_number),
        camera_tangent_targets,
        camera_channel_map,
        interpolation_parser=parse_vmd_camera_interpolation,
    )

    return True
