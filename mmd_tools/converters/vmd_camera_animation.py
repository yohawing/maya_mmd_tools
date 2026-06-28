"""Camera-specific helpers for VMD animation conversion."""

import math
from typing import Dict, Tuple

import maya.cmds as cmds


def parse_vmd_camera_interpolation(interpolation_bytes) -> Dict[str, Tuple[float, float, float, float]]:
    """Convert VMD camera interpolation bytes into channel Bezier control points."""
    if not interpolation_bytes or len(interpolation_bytes) < 24:
        return {}

    data = bytes(interpolation_bytes[:24])

    def _norm(value):
        return max(0.0, min(127.0, float(value))) / 127.0

    channels = (
        "translate_x",
        "translate_y",
        "translate_z",
        "rotation",
        "distance",
        "viewing_angle",
    )
    parsed = {}
    for index, channel in enumerate(channels):
        offset = index * 4
        parsed[channel] = (
            _norm(data[offset]),
            _norm(data[offset + 2]),
            _norm(data[offset + 1]),
            _norm(data[offset + 3]),
        )
    return parsed


def viewing_angle_to_focal_length(camera_shape: str, viewing_angle: float) -> float:
    """Convert VMD viewing_angle(deg) to Maya camera focalLength(mm)."""
    clamped_angle = max(1.0, min(179.0, float(viewing_angle)))
    aperture_inch = cmds.getAttr(f"{camera_shape}.horizontalFilmAperture")
    aperture_mm = float(aperture_inch) * 25.4
    return aperture_mm / (2.0 * math.tan(math.radians(clamped_angle) / 2.0))
