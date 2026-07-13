"""Runtime morph bake helpers for VMD conversion."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

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


def bake_morph_weights_from_runtime(
    converter_or_context,
    frame: int,
    morph_weights: list,
    pmx_morph_names: Optional[List[str]] = None,
) -> None:
    """Bake one runtime-evaluated PMX morph-weight row to Maya blendShape attrs."""
    context = _resolve_morph_animation_context(converter_or_context)
    if not morph_weights:
        return

    pmx_morph_names = pmx_morph_names or []
    for index, weight in enumerate(morph_weights):
        if index >= len(pmx_morph_names):
            continue
        morph_name = pmx_morph_names[index]
        mappings = context.iter_morph_mappings(context.morph_name_mapping.get(morph_name))
        if not mappings:
            continue

        for morph_node, weight_attr, _ in mappings:
            try:
                cmds.setKeyframe(
                    morph_node,
                    attribute=weight_attr,
                    time=frame,
                    value=float(weight),
                )
            except Exception as e:
                context.logger.debug(f"runtime morph bake error for {morph_name} at frame {frame}: {e}")


def _build_runtime_morph_target_map(
    context: VmdMorphAnimationContext,
    pmx_morph_names: List[str],
) -> Dict[int, Tuple[str, list]]:
    """Map PMX morph indices to Maya weight attrs that should receive keys."""
    target_map: Dict[int, Tuple[str, list]] = {}
    for index, morph_name in enumerate(pmx_morph_names):
        mappings = list(context.iter_morph_mappings(context.morph_name_mapping.get(morph_name)))
        if mappings:
            target_map[index] = (morph_name, mappings)
    return target_map


def bake_morph_weight_cache_from_runtime(
    converter_or_context,
    morph_cache: List[Tuple[float, list]],
    pmx_morph_names: Optional[List[str]] = None,
) -> None:
    """Batch-key runtime-evaluated morph weight cache to blendShape/network weights."""
    context = _resolve_morph_animation_context(converter_or_context)
    if not morph_cache:
        return

    pmx_morph_names = pmx_morph_names or []
    target_map = _build_runtime_morph_target_map(context, pmx_morph_names)
    if not target_map:
        return

    samples_by_node: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
    keyed_morphs = set()
    for frame, morph_weights in morph_cache:
        for index, (morph_name, mappings) in target_map.items():
            if index >= len(morph_weights):
                continue
            weight = morph_weights[index]
            keyed_morphs.add(morph_name)
            for morph_node, weight_attr, _ in mappings:
                node_samples = samples_by_node.setdefault(morph_node, {})
                node_samples.setdefault(weight_attr, []).append((float(frame), float(weight)))

    if not samples_by_node:
        return

    keyed_nodes = 0
    for morph_node, channel_samples in samples_by_node.items():
        animation_layer = context.anim_layer if context.use_animation_layers and context.anim_layer else None
        samples_to_key = (
            context.samples_as_anim_layer_deltas(morph_node, channel_samples)
            if animation_layer
            else channel_samples
        )
        try:
            if context.batch_key_scalar_channels(morph_node, samples_to_key, animation_layer):
                keyed_nodes += 1
                continue
        except Exception as exc:
            context.logger.debug(f"runtime morph batch keying failed for {morph_node}: {exc}")
            for weight_attr in samples_to_key:
                _ensure_fallback_allowed(
                    morph_node,
                    weight_attr,
                    animation_layer,
                    f"runtime morph batch keying failed: {exc!r}",
                )

        for weight_attr, samples in samples_to_key.items():
            _ensure_fallback_allowed(
                morph_node,
                weight_attr,
                animation_layer,
                "batch_key_scalar_channels returned False for runtime morph samples",
            )
            for frame, weight in samples:
                try:
                    key_args = {
                        "attribute": weight_attr,
                        "time": frame,
                        "value": float(weight),
                    }
                    if animation_layer:
                        key_args["animLayer"] = animation_layer
                    with vmd_profile.scope("fallback_setKeyframe"):
                        cmds.setKeyframe(morph_node, **key_args)
                except Exception as exc:
                    context.logger.debug(
                        f"runtime morph fallback keying failed for {morph_node}.{weight_attr} at {frame}: {exc}"
                    )

    context.logger.info(
        f"runtime morph batch keying: nodes={keyed_nodes}/{len(samples_by_node)}, morphs={len(keyed_morphs)}"
    )
