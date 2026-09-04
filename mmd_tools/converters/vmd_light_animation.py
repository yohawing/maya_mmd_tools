"""Light-specific helpers for VMD animation conversion."""

import math
from typing import Any, List, Optional, Union

import maya.cmds as cmds

from ..core.constants import ATTR_MMD_LIGHT, DEFAULT_LIGHT_NAME
from .light_converter import (
    MMD_SELF_SHADOW_DISTANCE_ATTR,
    MMD_SELF_SHADOW_MODE_ATTR,
    ensure_mmd_light_shadow_attrs,
)
from .vmd_context import VmdLightAnimationContext

try:
    from ..core.native.mmd_anim_runtime_sampling import sample_vmd_light_frames
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


def _resolve_light_animation_context(
    converter_or_context: Union[Any, VmdLightAnimationContext],
) -> VmdLightAnimationContext:
    if isinstance(converter_or_context, VmdLightAnimationContext):
        return converter_or_context
    factory = getattr(converter_or_context, "_light_animation_context", None)
    if callable(factory):
        return factory()
    return VmdLightAnimationContext(
        logger=converter_or_context.logger,
        anim_layer=converter_or_context.anim_layer,
        use_animation_layers=converter_or_context.use_animation_layers,
        get_or_create_light=converter_or_context._get_or_create_light,
        vmd_frame_to_maya_time=converter_or_context.vmd_frame_to_maya_time,
        maya_time_to_vmd_frame=converter_or_context.maya_time_to_vmd_frame,
        add_attrs_to_anim_layer=converter_or_context._add_attrs_to_anim_layer,
        samples_as_anim_layer_deltas=converter_or_context._samples_as_anim_layer_deltas,
        batch_key_scalar_channels=converter_or_context._batch_key_scalar_channels,
    )


def _light_samples_from_runtime(
    context: VmdLightAnimationContext,
    light_frames,
    vmd_bytes: Optional[bytes],
) -> Optional[List[dict]]:
    if sample_vmd_light_frames is None or not vmd_bytes:
        return None
    if not light_frames:
        return None

    min_frame, max_frame = _light_frame_range(light_frames)
    start_maya_time = math.floor(context.vmd_frame_to_maya_time(min_frame))
    end_maya_time = math.ceil(context.vmd_frame_to_maya_time(max_frame))
    frame_count = max(1, int(end_maya_time - start_maya_time) + 1)
    start_vmd_frame = context.maya_time_to_vmd_frame(start_maya_time)
    frame_step = context.maya_time_to_vmd_frame(start_maya_time + 1.0) - start_vmd_frame
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


def _sparse_light_samples_from_frames(context: VmdLightAnimationContext, light_frames) -> List[dict]:
    samples = []
    for frame in light_frames:
        frame_number = _frame_value(frame, "frame_number", "frame", 0)
        samples.append(
            {
                "maya_time": context.vmd_frame_to_maya_time(frame_number),
                "color": tuple(_frame_value(frame, "color", "color", (1, 1, 1))),
                "position": tuple(_frame_value(frame, "position", "position", (0.0, -1.0, 0.0))),
            }
        )
    return samples


def get_or_create_light() -> str:
    """Return the MMD directional light transform, creating one if needed."""
    existing = cmds.ls(f"*.{ATTR_MMD_LIGHT}", objectsOnly=True)
    if existing:
        return ensure_mmd_light_shadow_attrs(existing[0])

    light_shape = cmds.directionalLight(name=DEFAULT_LIGHT_NAME)
    light_transform = cmds.listRelatives(light_shape, parent=True)[0]
    cmds.addAttr(light_transform, longName=ATTR_MMD_LIGHT, attributeType="bool")
    cmds.setAttr(f"{light_transform}.{ATTR_MMD_LIGHT}", True)
    return ensure_mmd_light_shadow_attrs(light_transform)


def convert_self_shadow_animation(converter_or_context, shadow_frames) -> bool:
    """Convert VMD self-shadow mode/distance keys on the tagged MMD light."""
    context = _resolve_light_animation_context(converter_or_context)
    return _convert_self_shadow_animation(context, shadow_frames)


def _convert_self_shadow_animation(
    context: VmdLightAnimationContext,
    shadow_frames,
) -> bool:
    """Key parsed VMD self-shadow state without changing light direction/color."""
    if not shadow_frames:
        return False

    light_transform = ensure_mmd_light_shadow_attrs(context.get_or_create_light())
    samples = []
    for frame in shadow_frames:
        mode = int(_frame_value(frame, "mode", "mode", 0))
        if mode not in (0, 1, 2):
            raise ValueError(f"VMD self-shadow mode must be 0, 1, or 2: {mode}")
        frame_number = _frame_value(frame, "frame_number", "frame", 0)
        samples.append(
            (
                context.vmd_frame_to_maya_time(frame_number),
                mode,
                float(_frame_value(frame, "distance", "distance", 0.0)),
            )
        )

    mode_samples = [(maya_time, mode) for maya_time, mode, _distance in samples]
    distance_samples = [(maya_time, distance) for maya_time, _mode, distance in samples]
    animation_layer = context.anim_layer if context.use_animation_layers and context.anim_layer else None
    channels = {
        MMD_SELF_SHADOW_MODE_ATTR: mode_samples,
        MMD_SELF_SHADOW_DISTANCE_ATTR: distance_samples,
    }
    if animation_layer:
        context.add_attrs_to_anim_layer(light_transform, list(channels))
        # Maya layers enum channels as discrete overrides, even on an additive
        # layer. Only the continuous distance channel needs a base-value delta.
        channels.update(context.samples_as_anim_layer_deltas(
            light_transform, {MMD_SELF_SHADOW_DISTANCE_ATTR: distance_samples}
        ))

    keyed = context.batch_key_scalar_channels(light_transform, channels, animation_layer)
    if not keyed:
        return False

    # VMD mode is a discrete byte; force the generated Maya curve to remain
    # stepped while distance keeps the existing linear scalar-channel path.
    try:
        tangent_target = f"{light_transform}.{MMD_SELF_SHADOW_MODE_ATTR}"
        if animation_layer:
            tangent_target = cmds.animLayer(
                animation_layer,
                query=True,
                findCurveForPlug=f"{light_transform}.{MMD_SELF_SHADOW_MODE_ATTR}",
            ) or []
            if isinstance(tangent_target, (list, tuple)):
                if len(tangent_target) != 1:
                    raise RuntimeError("self-shadow mode animLayer curve is missing")
                tangent_target = tangent_target[0]
            if not isinstance(tangent_target, str) or not tangent_target:
                raise RuntimeError("self-shadow mode animLayer curve is missing")
        cmds.keyTangent(
            tangent_target,
            edit=True,
            outTangentType="step",
        )
    except Exception as exc:
        context.logger.error("Failed to set stepped self-shadow mode keys: %s", exc)
        return False
    return True


def convert_light_animation(converter_or_context, light_frames, vmd_bytes: Optional[bytes] = None) -> bool:
    """Convert VMD light frames using explicit light-animation context."""
    context = _resolve_light_animation_context(converter_or_context)
    return _convert_light_animation(context, light_frames, vmd_bytes=vmd_bytes)


def _convert_light_animation(
    context: VmdLightAnimationContext,
    light_frames,
    vmd_bytes: Optional[bytes] = None,
) -> bool:
    """Convert VMD light frames using explicit light-animation context."""
    if not light_frames:
        return False

    light_transform = context.get_or_create_light()
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

    samples = _light_samples_from_runtime(context, light_frames, vmd_bytes)
    if samples is None:
        samples = _sparse_light_samples_from_frames(context, light_frames)

    for sample in samples:
        maya_time = sample["maya_time"]
        color = sample["color"]
        position = sample["position"]

        for attr, value in zip(light_color_samples, color):
            light_color_samples[attr].append((maya_time, value))

        dx, dy, dz = float(position[0]), float(position[1]), -float(position[2])
        length = math.sqrt(dx * dx + dy * dy + dz * dz)

        if length < 1e-10:
            context.logger.warning(f"time {maya_time}: position is zero vector; skipping rotation key")
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

    animation_layer = context.anim_layer if context.use_animation_layers and context.anim_layer else None
    if animation_layer:
        context.add_attrs_to_anim_layer(light_color_node, list(light_color_samples))
        context.add_attrs_to_anim_layer(light_transform, list(light_rotate_samples))
        light_color_samples = context.samples_as_anim_layer_deltas(light_color_node, light_color_samples)
        light_rotate_samples = context.samples_as_anim_layer_deltas(light_transform, light_rotate_samples)

    context.batch_key_scalar_channels(light_color_node, light_color_samples, animation_layer)
    context.batch_key_scalar_channels(light_transform, light_rotate_samples, animation_layer)

    return True
