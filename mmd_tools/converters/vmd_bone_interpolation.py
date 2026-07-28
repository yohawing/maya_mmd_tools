"""Bone interpolation helpers for VMD animation conversion."""

from __future__ import annotations

from typing import Dict, Optional, Tuple


def parse_vmd_interpolation(interpolation_bytes) -> Dict[str, Tuple[float, float, float, float]]:
    """Parse VMD bone interpolation bytes into normalized Bezier control points."""
    if not interpolation_bytes or len(interpolation_bytes) < 16:
        return {}

    data = bytes(interpolation_bytes[:16])

    def _norm(value):
        return max(0.0, min(127.0, float(value))) / 127.0

    channels = ("translate_x", "translate_y", "translate_z", "rotation")
    parsed = {}
    for index, channel in enumerate(channels):
        parsed[channel] = (
            _norm(data[index]),
            _norm(data[4 + index]),
            _norm(data[8 + index]),
            _norm(data[12 + index]),
        )
    return parsed


def vmd_interp_channel_for_attr(attr: str) -> Optional[str]:
    """Return the VMD interpolation channel for a Maya attribute."""
    if attr == "translateX":
        return "translate_x"
    if attr == "translateY":
        return "translate_y"
    if attr == "translateZ":
        return "translate_z"
    if attr.startswith("rotate") or "inputRotateElement" in attr:
        return "rotation"
    return None


def is_linear_vmd_interp(points: Tuple[float, float, float, float]) -> bool:
    """Return True when VMD Bezier control points represent a linear segment."""
    x1, y1, x2, y2 = points
    return abs(x1 - y1) < 1e-9 and abs(x2 - y2) < 1e-9


def get_frame_number(frame) -> float:
    """Return frame_number from a VMD frame object or dict."""
    if hasattr(frame, "frame_number"):
        return float(frame.frame_number)
    return float(frame.get("frame_number", 0))


def get_frame_interpolation(frame):
    """Return interpolation bytes from a VMD frame object or dict."""
    if hasattr(frame, "interpolation"):
        return frame.interpolation
    return frame.get("interpolation", b"")
