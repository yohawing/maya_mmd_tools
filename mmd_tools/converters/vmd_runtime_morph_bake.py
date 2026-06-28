"""Runtime morph bake helpers for VMD conversion."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import maya.cmds as cmds


def bake_morph_weights_from_runtime(
    converter,
    frame: int,
    morph_weights: list,
    pmx_morph_names: Optional[List[str]] = None,
) -> None:
    """Bake one runtime-evaluated PMX morph-weight row to Maya blendShape attrs."""
    if not morph_weights:
        return

    pmx_morph_names = pmx_morph_names or []
    for index, weight in enumerate(morph_weights):
        if index >= len(pmx_morph_names):
            continue
        morph_name = pmx_morph_names[index]
        mappings = converter._iter_morph_mappings(converter.morph_name_mapping.get(morph_name))
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
                converter.logger.debug(f"runtime morph bake error for {morph_name} at frame {frame}: {e}")


def bake_morph_weight_cache_from_runtime(
    converter,
    morph_cache: List[Tuple[float, list]],
    pmx_morph_names: Optional[List[str]] = None,
) -> None:
    """Batch-key runtime-evaluated morph weight cache to blendShape/network weights."""
    if not morph_cache:
        return

    pmx_morph_names = pmx_morph_names or []
    samples_by_node: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
    keyed_morphs = set()
    for frame, morph_weights in morph_cache:
        for index, weight in enumerate(morph_weights):
            if index >= len(pmx_morph_names):
                continue
            morph_name = pmx_morph_names[index]
            mappings = converter._iter_morph_mappings(converter.morph_name_mapping.get(morph_name))
            if not mappings:
                continue
            keyed_morphs.add(morph_name)
            for morph_node, weight_attr, _ in mappings:
                node_samples = samples_by_node.setdefault(morph_node, {})
                node_samples.setdefault(weight_attr, []).append((float(frame), float(weight)))

    if not samples_by_node:
        return

    keyed_nodes = 0
    for morph_node, channel_samples in samples_by_node.items():
        try:
            if converter._batch_key_scalar_channels(morph_node, channel_samples):
                keyed_nodes += 1
                continue
        except Exception as exc:
            converter.logger.debug(f"runtime morph batch keying failed for {morph_node}, fallback: {exc}")

        for weight_attr, samples in channel_samples.items():
            for frame, weight in samples:
                try:
                    cmds.setKeyframe(
                        morph_node,
                        attribute=weight_attr,
                        time=frame,
                        value=float(weight),
                    )
                except Exception as exc:
                    converter.logger.debug(
                        f"runtime morph fallback keying failed for {morph_node}.{weight_attr} at {frame}: {exc}"
                    )

    converter.logger.info(
        f"runtime morph batch keying: nodes={keyed_nodes}/{len(samples_by_node)}, morphs={len(keyed_morphs)}"
    )
