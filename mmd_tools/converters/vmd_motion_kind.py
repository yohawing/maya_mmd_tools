"""VMD motion content classification helpers."""

from __future__ import annotations

from typing import Any


def detect_vmd_motion_kind(vmd_data: Any) -> str:
    """Return a coarse motion kind from the channels present in VMD data."""
    has_model = (
        bool(getattr(vmd_data, "bone_frames", []))
        or bool(getattr(vmd_data, "morph_frames", []))
        or bool(getattr(vmd_data, "ik_show_hide_frames", []))
    )
    has_camera = bool(getattr(vmd_data, "camera_frames", []))
    has_light = bool(getattr(vmd_data, "light_frames", []))

    if has_model and (has_camera or has_light):
        return "mixed"
    if has_camera:
        return "camera"
    if has_light:
        return "light"
    if has_model:
        return "model"
    return "empty"
