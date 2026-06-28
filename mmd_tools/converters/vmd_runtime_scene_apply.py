"""Runtime bake scene-application helpers for VMD conversion."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om


def apply_runtime_channel_arrays_to_scene(
    converter,
    joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
    joint_channel_static: Dict[str, Dict[str, dict]],
    bake_times: om.MTimeArray,
    baked_frames: List[float],
    morph_cache: List[Tuple[float, list]],
    pmx_morph_names: List[str],
) -> None:
    """Apply collected runtime bake channel arrays to the Maya scene."""
    keyed_channels = 0
    skipped_static_channels = 0
    total_channels = 0

    append_info = converter._collect_append_info()
    ik_passthrough_info = converter._collect_mmd_ik_passthrough_info()
    decomposed_rotations = converter._decompose_append_rotations_for_scene(
        joint_channel_values,
        joint_channel_static,
        append_info,
        len(baked_frames),
    )
    decomposed_translations = converter._decompose_append_translations_for_scene(
        joint_channel_values,
        joint_channel_static,
        append_info,
        len(baked_frames),
    )

    for joint, channels in joint_channel_values.items():
        total_channels += len(channels)
        try:
            ik_info = ik_passthrough_info.get(joint)
            if ik_info:
                channels = dict(channels)
                redirected = converter._key_mmd_ik_passthrough_rotation(
                    ik_info,
                    channels,
                    joint_channel_static.get(joint, {}),
                    bake_times,
                    baked_frames,
                )
                if redirected:
                    keyed_channels += redirected
                    for attr in ("rotateX", "rotateY", "rotateZ"):
                        channels.pop(attr, None)
                if not channels:
                    continue

            target_static = joint_channel_static.get(joint, {})
            info = append_info.get(joint)
            if info and info["attr_map"]:
                append_node = info["node"]
                attr_map = dict(info["attr_map"])
                decomposed_rotation_channels = decomposed_rotations.get(joint, {})
                decomposed_translation_channels = decomposed_translations.get(joint, {})
                decomposed_channels = dict(decomposed_rotation_channels)
                decomposed_channels.update(decomposed_translation_channels)

                if info["affect_rotation"] and not decomposed_rotation_channels:
                    attr_map.pop("rotateX", None)
                    attr_map.pop("rotateY", None)
                    attr_map.pop("rotateZ", None)
                if info["affect_translation"] and not decomposed_translation_channels:
                    attr_map.pop("translateX", None)
                    attr_map.pop("translateY", None)
                    attr_map.pop("translateZ", None)

                redirected_channels = {}
                redirected_static = {}
                passthrough_channels = {}
                passthrough_static = {}
                for attr, values in channels.items():
                    new_attr = attr_map.get(attr)
                    if new_attr:
                        redirected_channels[new_attr] = decomposed_channels.get(attr, values)
                        if attr not in decomposed_channels:
                            orig_state = target_static.get(attr)
                            if orig_state:
                                redirected_static[new_attr] = orig_state
                    else:
                        passthrough_channels[attr] = values
                        orig_state = target_static.get(attr)
                        if orig_state:
                            passthrough_static[attr] = orig_state

                if redirected_channels:
                    keyed, skipped = converter._batch_create_and_key_curve_arrays(
                        append_node,
                        redirected_channels,
                        redirected_static,
                        bake_times,
                        baked_frames,
                    )
                    keyed_channels += keyed
                    skipped_static_channels += skipped

                if passthrough_channels:
                    keyed, skipped = converter._batch_create_and_key_curve_arrays(
                        joint,
                        passthrough_channels,
                        passthrough_static,
                        bake_times,
                        baked_frames,
                    )
                    keyed_channels += keyed
                    skipped_static_channels += skipped

                if redirected_channels or passthrough_channels:
                    continue

            keyed, skipped = converter._batch_create_and_key_curve_arrays(
                joint,
                channels,
                target_static,
                bake_times,
                baked_frames,
            )
            keyed_channels += keyed
            skipped_static_channels += skipped
        except Exception as e:
            converter.logger.debug(f"batch array keying error for {joint}: {e}")

    converter.logger.info(
        "runtime joint channel pruning: "
        f"keyed={keyed_channels}, skipped_static={skipped_static_channels}, "
        f"total={total_channels}"
    )

    converter._bake_morph_weight_cache_from_runtime(morph_cache, pmx_morph_names)

    converter.logger.info(f"Applied runtime cache: keyed {len(baked_frames)} frames")
