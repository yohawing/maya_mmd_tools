"""Shared VMD Bezier tangent helpers for animation conversion."""

import math
from typing import Dict, List, Optional, Tuple

import maya.cmds as cmds

from .vmd_bone_interpolation import get_frame_interpolation, get_frame_number, is_linear_vmd_interp


def query_key_value(logger, plug: str, frame_number: float) -> Optional[float]:
    """Return the keyed value for a plug/frame pair, or None when unavailable."""
    try:
        values = cmds.keyframe(
            plug,
            query=True,
            time=(frame_number, frame_number),
            valueChange=True,
        )
    except Exception as exc:
        logger.debug(f"Failed to query key value for {plug} at {frame_number}: {exc}")
        return None
    if not values:
        return None
    return float(values[0])


def apply_vmd_bezier_tangents(
    converter,
    joint: str,
    frames: List,
    attrs,
    channel_interp_map: Dict[str, str],
    interpolation_parser=None,
) -> None:
    """Apply VMD Bezier interpolation as Maya weighted tangents."""
    if len(frames) < 2:
        return

    if isinstance(attrs, dict):
        attr_targets = attrs
        source_attrs = list(attrs.keys())
    else:
        attr_targets = {attr: (joint, attr) for attr in attrs}
        source_attrs = list(attrs)

    for frame_index in range(len(frames) - 1):
        frame = frames[frame_index]
        next_frame = frames[frame_index + 1]
        frame_number = get_frame_number(frame)
        next_frame_number = get_frame_number(next_frame)
        frame_time = converter.vmd_frame_to_maya_time(frame_number)
        next_frame_time = converter.vmd_frame_to_maya_time(next_frame_number)
        dt = next_frame_time - frame_time
        if dt <= 0.0:
            continue

        # VMD interpolation bytes are stored on the arriving key.
        parse_interpolation = interpolation_parser or converter._parse_vmd_interpolation
        interpolation = parse_interpolation(get_frame_interpolation(next_frame))
        if not interpolation:
            continue

        for source_attr in source_attrs:
            channel_name = channel_interp_map.get(source_attr)
            if not channel_name:
                continue
            points: Optional[Tuple[float, float, float, float]] = interpolation.get(channel_name)
            if not points or is_linear_vmd_interp(points):
                continue

            target_node, target_attr = attr_targets.get(source_attr, (joint, source_attr))
            plug = f"{target_node}.{target_attr}"
            value = query_key_value(converter.logger, plug, frame_time)
            next_value = query_key_value(converter.logger, plug, next_frame_time)
            if value is None or next_value is None:
                continue

            x1, y1, x2, y2 = points
            dv = next_value - value
            out_dx = dt * x1
            out_dy = dv * y1
            in_dx = dt * (1.0 - x2)
            in_dy = dv * (1.0 - y2)
            out_angle = math.degrees(math.atan2(out_dy, out_dx))
            in_angle = math.degrees(math.atan2(in_dy, in_dx))
            out_weight = math.sqrt((out_dx * out_dx) + (out_dy * out_dy)) / (3.0 * dt)
            in_weight = math.sqrt((in_dx * in_dx) + (in_dy * in_dy)) / (3.0 * dt)

            try:
                cmds.keyTangent(
                    plug,
                    edit=True,
                    time=(frame_time, frame_time),
                    weightedTangents=True,
                )
                cmds.keyTangent(
                    plug,
                    edit=True,
                    time=(frame_time, frame_time),
                    ott="fixed",
                )
                cmds.keyTangent(
                    plug,
                    edit=True,
                    time=(frame_time, frame_time),
                    oa=out_angle,
                    ow=out_weight,
                )
                cmds.keyTangent(
                    plug,
                    edit=True,
                    time=(next_frame_time, next_frame_time),
                    weightedTangents=True,
                )
                cmds.keyTangent(
                    plug,
                    edit=True,
                    time=(next_frame_time, next_frame_time),
                    itt="fixed",
                )
                cmds.keyTangent(
                    plug,
                    edit=True,
                    time=(next_frame_time, next_frame_time),
                    ia=in_angle,
                    iw=in_weight,
                )
            except Exception as exc:
                converter.logger.debug(
                    f"Failed to apply VMD Bezier tangent for {plug} "
                    f"{frame_number}->{next_frame_number}: {exc}"
                )
