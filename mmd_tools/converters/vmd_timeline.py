"""Timeline helpers for VMD import."""

from __future__ import annotations

from typing import Any, Union

import maya.cmds as cmds

from .vmd_context import VmdTimelineContext


FPS_TIME_UNIT_MAPPING = {
    15.0: "game",
    24.0: "film",
    25.0: "pal",
    30.0: "ntsc",
    48.0: "show",
    50.0: "palf",
    60.0: "ntscf",
}


def get_animation_frame_range(vmd_data: Any) -> tuple:
    """Return the inclusive VMD animation frame range across supported tracks."""
    min_frame = 0
    max_frame = 0
    for frame_list in [
        getattr(vmd_data, "bone_frames", []),
        getattr(vmd_data, "morph_frames", []),
        getattr(vmd_data, "camera_frames", []),
        getattr(vmd_data, "light_frames", []),
    ]:
        for frame_data in frame_list:
            if hasattr(frame_data, "frame_number"):
                frame_number = frame_data.frame_number
            elif isinstance(frame_data, dict):
                frame_number = frame_data.get("frame_number", 0)
            else:
                frame_number = 0
            max_frame = max(max_frame, frame_number)
    return int(min_frame), int(max_frame)


def set_scene_fps(fps: float, logger: Any) -> None:
    """Set the Maya scene time unit for a supported FPS value."""
    if fps in FPS_TIME_UNIT_MAPPING:
        time_unit = FPS_TIME_UNIT_MAPPING[fps]
        cmds.currentUnit(time=time_unit)
        logger.info(f"Set scene FPS to {fps} ({time_unit})")
        return

    logger.warning(f"Specified FPS {fps} is not supported. Using default 30.0 FPS")
    cmds.currentUnit(time="ntsc")


def _resolve_timeline_context(
    converter_or_context: Union[Any, VmdTimelineContext],
) -> VmdTimelineContext:
    if isinstance(converter_or_context, VmdTimelineContext):
        return converter_or_context
    factory = getattr(converter_or_context, "_timeline_context", None)
    if callable(factory):
        return factory()
    return VmdTimelineContext(
        logger=converter_or_context.logger,
        fps=converter_or_context.fps,
        vmd_frame_to_maya_time=converter_or_context.vmd_frame_to_maya_time,
    )


def setup_timeline(converter_or_context: Union[Any, VmdTimelineContext], vmd_data: Any) -> None:
    """Apply FPS and playback range for a VMD import."""
    context = _resolve_timeline_context(converter_or_context)
    set_scene_fps(context.fps, context.logger)

    _min_frame, max_frame = get_animation_frame_range(vmd_data)
    if max_frame > 0:
        max_time = context.vmd_frame_to_maya_time(max_frame)
        cmds.playbackOptions(min=0, max=max_time, animationStartTime=0, animationEndTime=max_time)
        context.logger.info(f"Set timeline range: 0 - {max_time}")
