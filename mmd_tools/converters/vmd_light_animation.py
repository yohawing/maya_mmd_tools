"""Light-specific helpers for VMD animation conversion."""

import math

import maya.cmds as cmds


def convert_light_animation(converter, light_frames) -> bool:
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

    for frame in light_frames:
        frame_number = frame.frame_number if hasattr(frame, "frame_number") else frame.get("frame_number", 0)
        maya_time = converter.vmd_frame_to_maya_time(frame_number)
        color = frame.color if hasattr(frame, "color") else frame.get("color", (1, 1, 1))
        position = frame.position if hasattr(frame, "position") else frame.get("position", (0.0, -1.0, 0.0))

        for attr, value in zip(light_color_samples, color):
            light_color_samples[attr].append((maya_time, value))

        dx, dy, dz = float(position[0]), float(position[1]), -float(position[2])
        length = math.sqrt(dx * dx + dy * dy + dz * dz)

        if length < 1e-10:
            converter.logger.warning(f"frame {frame_number}: position is zero vector; skipping rotation key")
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
