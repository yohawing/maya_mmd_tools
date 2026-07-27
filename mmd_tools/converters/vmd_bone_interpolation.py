"""Bone interpolation helpers for VMD animation conversion."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple


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
    """Evaluate an MMD/VMD cubic Bezier using the runtime's bounded solver."""
    x = max(0.0, min(1.0, float(x)))
    if x <= 0.0 or x >= 1.0:
        return x
    x1, y1, x2, y2 = points
    if is_linear_vmd_interp(points):
        return x

    # Match mmd-anim's 12-step binary subdivision rather than relying on a
    # DCC-specific curve evaluator.  This keeps sampled rotations numerically
    # aligned with the external MMD oracle.
    c = 0.5
    t = c
    s = 1.0 - t
    for _ in range(12):
        sst3 = 3.0 * s * s * t
        stt3 = 3.0 * s * t * t
        ttt = t * t * t
        ft = sst3 * x1 + stt3 * x2 + ttt - x
        if ft == 0.0:
            return sst3 * y1 + stt3 * y2 + ttt
        c *= 0.5
        t += c if ft < 0.0 else -c
        s = 1.0 - t
    return sst3 * y1 + stt3 * y2 + ttt


def sample_vmd_rotation_quaternions(frames: List) -> List[Tuple[float, Tuple[float, float, float, float]]]:
    """Sample sparse VMD rotations at integer frames using Bezier+slerp.

    VMD stores the rotation interpolation on the arriving key.  Translation
    remains sparse/DCC-keyed; this helper only supplies additional rotation
    samples for the live-rig path so Maya's Euler curves do not replace MMD's
    quaternion Bezier timing with a plain SLERP segment.
    """

    def _rotation(frame):
        return tuple(float(value) for value in (frame.rotation if hasattr(frame, "rotation") else frame.get("rotation", (0.0, 0.0, 0.0, 1.0))))

    def _slerp(a, b, t):
        ax, ay, az, aw = a
        bx, by, bz, bw = b
        dot = ax * bx + ay * by + az * bz + aw * bw
        if dot < 0.0:
            bx, by, bz, bw = -bx, -by, -bz, -bw
            dot = -dot
        dot = max(-1.0, min(1.0, dot))
        if dot > 0.9995:
            q = (ax + t * (bx - ax), ay + t * (by - ay), az + t * (bz - az), aw + t * (bw - aw))
            length = math.sqrt(sum(value * value for value in q)) or 1.0
            return tuple(value / length for value in q)
        angle = math.acos(dot)
        sin_angle = math.sin(angle)
        wa = math.sin((1.0 - t) * angle) / sin_angle
        wb = math.sin(t * angle) / sin_angle
        return (ax * wa + bx * wb, ay * wa + by * wb, az * wa + bz * wb, aw * wa + bw * wb)

    ordered = sorted(frames, key=get_frame_number)
    if not ordered:
        return []
    sampled = {get_frame_number(frame): _rotation(frame) for frame in ordered}
    for previous, arriving in zip(ordered, ordered[1:]):
        start = get_frame_number(previous)
        end = get_frame_number(arriving)
        if end <= start:
            continue
        points = parse_vmd_interpolation(get_frame_interpolation(arriving)).get("rotation")
        if points is None:
            points = (20.0 / 127.0, 20.0 / 127.0, 107.0 / 127.0, 107.0 / 127.0)
        first = int(math.ceil(start))
        last = int(math.floor(end))
        q0 = _rotation(previous)
        q1 = _rotation(arriving)
        for frame_number in range(first, last):
            if frame_number <= start or frame_number >= end:
                continue
            t = evaluate_vmd_bezier(points, (frame_number - start) / (end - start))
            sampled[float(frame_number)] = _slerp(q0, q1, t)
    return sorted(sampled.items(), key=lambda item: item[0])




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
