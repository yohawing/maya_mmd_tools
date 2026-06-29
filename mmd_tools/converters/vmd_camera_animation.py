"""Camera-specific helpers for VMD animation conversion."""

import math
from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core.constants import ATTR_MMD_CAMERA, DEFAULT_CAMERA_NAME

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
    """Convert VMD viewing_angle(deg) to Maya camera focalLength(mm)."""
    clamped_angle = max(1.0, min(179.0, float(viewing_angle)))
    aperture_inch = cmds.getAttr(f"{camera_shape}.horizontalFilmAperture")
    aperture_mm = float(aperture_inch) * 25.4
    return aperture_mm / (2.0 * math.tan(math.radians(clamped_angle) / 2.0))


def maya_camera_eye_from_vmd_state(
    position: Tuple[float, float, float],
    rotation: Tuple[float, float, float],
    distance: float,
    motion_scale: float = 1.0,
) -> Tuple[float, float, float]:
    """Convert MMD camera target/distance state to a Maya camera eye position."""
    target = om.MVector(
        float(position[0]) * motion_scale,
        float(position[1]) * motion_scale,
        -float(position[2]) * motion_scale,
    )
    maya_rotation = om.MEulerRotation(
        float(rotation[0]),
        float(rotation[1]),
        -float(rotation[2]),
        om.MEulerRotation.kXYZ,
    )
    forward = om.MVector(0.0, 0.0, -1.0) * maya_rotation.asMatrix()
    eye = target + forward * (float(distance) * motion_scale)
    return eye.x, eye.y, eye.z


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
    return camera_transform


def convert_camera_animation(converter, camera_frames, vmd_bytes: Optional[bytes] = None) -> bool:
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
    for attr_name in ("mmd_camera_target_x", "mmd_camera_target_y", "mmd_camera_target_z"):
        if not cmds.attributeQuery(attr_name, node=camera_transform, exists=True):
            cmds.addAttr(camera_transform, longName=attr_name, attributeType="double", keyable=True)

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
    }
    camera_shape_samples = {"focalLength": []}
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
        eye_x, eye_y, eye_z = maya_camera_eye_from_vmd_state(
            position,
            rotation,
            distance,
            converter.motion_scale,
        )

        camera_samples["translateX"].append((maya_time, eye_x))
        camera_samples["translateY"].append((maya_time, eye_y))
        camera_samples["translateZ"].append((maya_time, eye_z))
        camera_samples["rotateX"].append((maya_time, math.degrees(rotation[0])))
        camera_samples["rotateY"].append((maya_time, math.degrees(rotation[1])))
        camera_samples["rotateZ"].append((maya_time, -math.degrees(rotation[2])))
        camera_samples["mmd_camera_distance"].append((maya_time, distance * converter.motion_scale))
        camera_samples["mmd_camera_viewing_angle"].append((maya_time, float(viewing_angle)))
        camera_samples["mmd_camera_target_x"].append((maya_time, position[0] * converter.motion_scale))
        camera_samples["mmd_camera_target_y"].append((maya_time, position[1] * converter.motion_scale))
        camera_samples["mmd_camera_target_z"].append((maya_time, -position[2] * converter.motion_scale))
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
        "mmd_camera_target_x": (camera_transform, "mmd_camera_target_x"),
        "mmd_camera_target_y": (camera_transform, "mmd_camera_target_y"),
        "mmd_camera_target_z": (camera_transform, "mmd_camera_target_z"),
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
        "mmd_camera_target_x": "translate_x",
        "mmd_camera_target_y": "translate_y",
        "mmd_camera_target_z": "translate_z",
        "focalLength": "viewing_angle",
    }
    if not runtime_sampled:
        converter._apply_vmd_bezier_tangents(
            camera_transform,
            sorted(camera_frames, key=converter._get_frame_number),
            camera_tangent_targets,
            camera_channel_map,
            interpolation_parser=parse_vmd_camera_interpolation,
        )

    return True
