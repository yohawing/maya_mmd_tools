"""Morph-specific helpers for VMD animation conversion."""

from typing import Dict, List

import maya.cmds as cmds


def convert_morph_animation(converter, morph_frames) -> bool:
    """Convert VMD morph frames using the converter's shared Maya helpers."""
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
        mappings = converter._iter_morph_mappings(converter.morph_name_mapping.get(morph_name))
        if not mappings:
            continue

        for morph_node, weight_attr, _ in mappings:
            samples = []
            for frame in frames:
                frame_number = frame.frame_number if hasattr(frame, "frame_number") else frame.get("frame_number", 0)
                value = frame.value if hasattr(frame, "value") else frame.get("value", 0.0)
                samples.append((converter.vmd_frame_to_maya_time(frame_number), float(value)))
            if not converter._batch_key_scalar_channels(morph_node, {weight_attr: samples}):
                for frame in frames:
                    frame_number = frame.frame_number if hasattr(frame, "frame_number") else frame.get("frame_number", 0)
                    value = frame.value if hasattr(frame, "value") else frame.get("value", 0.0)
                    cmds.setKeyframe(
                        morph_node,
                        attribute=weight_attr,
                        time=converter.vmd_frame_to_maya_time(frame_number),
                        value=value,
                    )

        success_count += 1

    return success_count > 0
