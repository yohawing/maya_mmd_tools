"""Light-specific helpers for VMD animation conversion."""

import math
from typing import List, Optional

import maya.cmds as cmds

from ..core.constants import ATTR_MMD_LIGHT, DEFAULT_LIGHT_NAME

try:
    from ..core.native.mmd_anim_runtime import sample_vmd_light_frames
except Exception:
    sample_vmd_light_frames = None  # type: ignore


def _frame_value(frame, attr_name: str, key_name: str, default):
    if hasattr(frame, attr_name):
        return getattr(frame, attr_name)
    if hasattr(frame, key_name):
        return getattr(frame, key_name)
    if isinstance(frame, dict):
        return frame.get(key_name, frame.get(attr_name, default))
    return default


def _light_frame_range(light_frames) -> tuple[float, float]:
    frame_numbers = [float(_frame_value(frame, "frame_number", "frame", 0.0)) for frame in light_frames]
    return min(frame_numbers), max(frame_numbers)


def _light_samples_from_runtime(converter, light_frames, vmd_bytes: Optional[bytes]) -> Optional[List[dict]]:
    if sample_vmd_light_frames is None or not vmd_bytes:
        return None
    if not light_frames:
        return None

    min_frame, max_frame = _light_frame_range(light_frames)
    start_maya_time = math.floor(converter.vmd_frame_to_maya_time(min_frame))
    end_maya_time = math.ceil(converter.vmd_frame_to_maya_time(max_frame))
    frame_count = max(1, int(end_maya_time - start_maya_time) + 1)
    start_vmd_frame = converter.maya_time_to_vmd_frame(start_maya_time)
    frame_step = converter.maya_time_to_vmd_frame(start_maya_time + 1.0) - start_vmd_frame
    samples = sample_vmd_light_frames(vmd_bytes, start_vmd_frame, frame_step, frame_count)
    if not samples:
        return None

    dense = []
    for index, sample in enumerate(samples):
        dense.append(
            {
                "maya_time": start_maya_time + index,
                "color": tuple(sample.get("color", (1.0, 1.0, 1.0))),
                "position": tuple(sample.get("position", (0.0, -1.0, 0.0))),
            }
        )
    return dense


def _sparse_light_samples_from_frames(converter, light_frames) -> List[dict]:
    samples = []
    for frame in light_frames:
        frame_number = _frame_value(frame, "frame_number", "frame", 0)
        samples.append(
            {
                "maya_time": converter.vmd_frame_to_maya_time(frame_number),
                "color": tuple(_frame_value(frame, "color", "color", (1, 1, 1))),
                "position": tuple(_frame_value(frame, "position", "position", (0.0, -1.0, 0.0))),
            }
        )
    return samples


def get_or_create_light() -> str:
    """Return the MMD directional light transform, creating one if needed."""
    existing = cmds.ls(f"*.{ATTR_MMD_LIGHT}", objectsOnly=True)
    if existing:
        return existing[0]

    light_shape = cmds.directionalLight(name=DEFAULT_LIGHT_NAME)
    light_transform = cmds.listRelatives(light_shape, parent=True)[0]
    cmds.addAttr(light_transform, longName=ATTR_MMD_LIGHT, attributeType="bool")
    cmds.setAttr(f"{light_transform}.{ATTR_MMD_LIGHT}", True)
    return light_transform


def convert_light_animation(converter, light_frames, vmd_bytes: Optional[bytes] = None) -> bool:
    """Convert VMD light frames using the converter's shared Maya helpers."""
    if not light_frames:
        return False

    light_transform = converter._get_or_create_light()
    light_shapes = cmds.listRelatives(light_transform, shapes=True, type="directionalLight") or []
    if not light_shapes:
        return False
    light_shape = light_shapes[0]

    if cmds.attributeQuery("mmd_light_color", node=light_transform, exists=True):
        light_color_node = light_transform
        light_color_samples = {
            "mmd_light_colorR": [],
            "mmd_light_colorG": [],
            "mmd_light_colorB": [],
        }
    else:
        light_color_node = light_shape
        light_color_samples = {"colorR": [], "colorG": [], "colorB": []}
    light_rotate_samples = {"rotateX": [], "rotateY": [], "rotateZ": []}

    samples = _light_samples_from_runtime(converter, light_frames, vmd_bytes)
    if samples is None:
        samples = _sparse_light_samples_from_frames(converter, light_frames)

    for sample in samples:
        maya_time = sample["maya_time"]
        color = sample["color"]
        position = sample["position"]

        for attr, value in zip(light_color_samples, color):
            light_color_samples[attr].append((maya_time, value))

        dx, dy, dz = float(position[0]), float(position[1]), -float(position[2])
        length = math.sqrt(dx * dx + dy * dy + dz * dz)

        if length < 1e-10:
            converter.logger.warning(f"time {maya_time}: position is zero vector; skipping rotation key")
            continue

        dx /= length
        dy /= length
        dz /= length

        rx = math.asin(dy)
        cos_rx = math.cos(rx)
        if abs(cos_rx) > 1e-10:
            ry = math.atan2(-dx / cos_rx, -dz / cos_rx)
        else:
            ry = 0.0

        light_rotate_samples["rotateX"].append((maya_time, math.degrees(rx)))
        light_rotate_samples["rotateY"].append((maya_time, math.degrees(ry)))
        light_rotate_samples["rotateZ"].append((maya_time, 0.0))

    animation_layer = converter.anim_layer if converter.use_animation_layers and converter.anim_layer else None
    if animation_layer:
        converter._add_attrs_to_anim_layer(light_color_node, list(light_color_samples))
        converter._add_attrs_to_anim_layer(light_transform, list(light_rotate_samples))
        light_color_samples = converter._samples_as_anim_layer_deltas(light_color_node, light_color_samples)
        light_rotate_samples = converter._samples_as_anim_layer_deltas(light_transform, light_rotate_samples)

    converter._batch_key_scalar_channels(light_color_node, light_color_samples, animation_layer=animation_layer)
    converter._batch_key_scalar_channels(light_transform, light_rotate_samples, animation_layer=animation_layer)

    return True
