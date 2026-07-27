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


def evaluate_vmd_bezier(points: Tuple[float, float, float, float], x: float) -> float:
    """Evaluate a normalized VMD Bezier segment using mmd-anim's 12 steps.

    VMD stores the control points in ``(x1, y1, x2, y2)`` order.  The runtime
    solves the cubic's x coordinate with twelve binary-search iterations and
    then evaluates its y coordinate; keeping the same fixed iteration count
    avoids tiny host-specific differences at integer samples.
    """
    x1, y1, x2, y2 = (float(value) for value in points)
    x = max(0.0, min(1.0, float(x)))
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    if abs(x1 - y1) < 1e-12 and abs(x2 - y2) < 1e-12:
        return x

    c = 0.5
    t = c
    s = 1.0 - t
    for _ in range(12):
        sst3 = 3.0 * s * s * t
        stt3 = 3.0 * s * t * t
        ttt = t * t * t
        f_t = sst3 * x1 + stt3 * x2 + ttt - x
        if f_t == 0.0:
            return sst3 * y1 + stt3 * y2 + ttt
        c *= 0.5
        t += c if f_t < 0.0 else -c
        s = 1.0 - t

    sst3 = 3.0 * s * s * t
    stt3 = 3.0 * s * t * t
    ttt = t * t * t
    return sst3 * y1 + stt3 * y2 + ttt


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
