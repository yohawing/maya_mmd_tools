"""Timeline helpers for VMD import."""

from __future__ import annotations

from typing import Any

import maya.cmds as cmds


FPS_TIME_UNIT_MAPPING = {
    15.0: "game",
    24.0: "film",
    25.0: "pal",
    30.0: "ntsc",
    48.0: "show",
    50.0: "palf",
    60.0: "ntscf",
}


def get_max_bone_frame(vmd_data: Any) -> int:
    """Return the largest bone frame number present in VMD data."""
    max_frame = 0
    if not hasattr(vmd_data, "bone_frames"):
        return max_frame

    for frame_data in vmd_data.bone_frames:
        if hasattr(frame_data, "frame_number"):
            max_frame = max(max_frame, frame_data.frame_number)
        else:
            max_frame = max(max_frame, frame_data.get("frame_number", 0))
    return max_frame


def set_scene_fps(fps: float, logger: Any) -> None:
    """Set the Maya scene time unit for a supported FPS value."""
    if fps in FPS_TIME_UNIT_MAPPING:
        time_unit = FPS_TIME_UNIT_MAPPING[fps]
        cmds.currentUnit(time=time_unit)
        logger.info(f"Set scene FPS to {fps} ({time_unit})")
        return

    logger.warning(f"Specified FPS {fps} is not supported. Using default 30.0 FPS")
    cmds.currentUnit(time="ntsc")


def setup_timeline(converter: Any, vmd_data: Any) -> None:
    """Apply FPS and playback range for a VMD import."""
    set_scene_fps(converter.fps, converter.logger)

    max_frame = get_max_bone_frame(vmd_data)
    if max_frame > 0:
        max_time = converter.vmd_frame_to_maya_time(max_frame)
        cmds.playbackOptions(min=0, max=max_time, animationStartTime=0, animationEndTime=max_time)
        converter.logger.info(f"Set timeline range: 0 - {max_time}")
