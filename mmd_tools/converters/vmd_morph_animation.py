"""Morph-specific helpers for VMD animation conversion."""

from typing import Any, Dict, List, Union

import maya.cmds as cmds

from . import vmd_profile
from .vmd_context import VmdMorphAnimationContext
from .vmd_scene_keying import _ensure_fallback_allowed


def _resolve_morph_animation_context(
    converter_or_context: Union[Any, VmdMorphAnimationContext],
) -> VmdMorphAnimationContext:
    if isinstance(converter_or_context, VmdMorphAnimationContext):
        return converter_or_context
    factory = getattr(converter_or_context, "_morph_animation_context", None)
    if callable(factory):
        return factory()
    return VmdMorphAnimationContext(
        logger=converter_or_context.logger,
        morph_name_mapping=converter_or_context.morph_name_mapping,
        anim_layer=converter_or_context.anim_layer,
        use_animation_layers=converter_or_context.use_animation_layers,
        iter_morph_mappings=converter_or_context._iter_morph_mappings,
        vmd_frame_to_maya_time=converter_or_context.vmd_frame_to_maya_time,
        samples_as_anim_layer_deltas=converter_or_context._samples_as_anim_layer_deltas,
        batch_key_scalar_channels=converter_or_context._batch_key_scalar_channels,
    )


def convert_morph_animation(converter_or_context, morph_frames) -> bool:
    """Convert VMD morph frames using explicit morph-animation context."""
    context = _resolve_morph_animation_context(converter_or_context)
    return _convert_morph_animation(context, morph_frames)


def _convert_morph_animation(context: VmdMorphAnimationContext, morph_frames) -> bool:
    """Convert VMD morph frames using explicit morph-animation context."""
    if not morph_frames:
        return False

    success_count = 0
    morph_frame_map: Dict[str, List] = {}

    for frame in morph_frames:
        morph_name = frame.morph_name if hasattr(frame, "morph_name") else frame.get("morph_name", "")
        if morph_name not in morph_frame_map:
            morph_frame_map[morph_name] = []
        morph_frame_map[morph_name].append(frame)

    for morph_name, frames in morph_frame_map.items():
        mappings = context.iter_morph_mappings(context.morph_name_mapping.get(morph_name))
        if not mappings:
            continue

        for morph_node, weight_attr, _ in mappings:
            samples = []
            for frame in frames:
                frame_number = frame.frame_number if hasattr(frame, "frame_number") else frame.get("frame_number", 0)
                value = frame.value if hasattr(frame, "value") else frame.get("value", 0.0)
                samples.append((context.vmd_frame_to_maya_time(frame_number), float(value)))
            if not context.batch_key_scalar_channels(morph_node, {weight_attr: samples}, None):
                _ensure_fallback_allowed(
                    morph_node,
                    weight_attr,
                    None,
                    "batch_key_scalar_channels returned False for morph samples",
                )
                for frame in frames:
                    frame_number = frame.frame_number if hasattr(frame, "frame_number") else frame.get("frame_number", 0)
                    value = frame.value if hasattr(frame, "value") else frame.get("value", 0.0)
                    with vmd_profile.scope("fallback_setKeyframe"):
                        cmds.setKeyframe(
                            morph_node,
                            attribute=weight_attr,
                            time=context.vmd_frame_to_maya_time(frame_number),
                            value=value,
                        )

        success_count += 1

    return success_count > 0
