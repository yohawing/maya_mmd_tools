"""Shared coordinate conversions between MMD and Maya spaces."""

from __future__ import annotations

import math
from typing import Sequence, Tuple


def mmd_point_to_maya(point: Sequence[float], scale: float = 1.0) -> Tuple[float, float, float]:
    """Convert an MMD point to Maya coordinates."""
    return (
        float(point[0]) * scale,
        float(point[1]) * scale,
        -float(point[2]) * scale,
    )


def maya_point_to_mmd(point: Sequence[float], scale: float = 1.0) -> Tuple[float, float, float]:
    """Convert a Maya point to MMD coordinates."""
    return (
        float(point[0]) * scale,
        float(point[1]) * scale,
        -float(point[2]) * scale,
    )


def mmd_euler_xyz_to_maya(euler_xyz: Sequence[float]) -> Tuple[float, float, float]:
    """Convert MMD XYZ Euler channels to Maya XYZ Euler channels."""
    return (
        float(euler_xyz[0]),
        float(euler_xyz[1]),
        -float(euler_xyz[2]),
    )


def mmd_euler_radians_to_maya_degrees(euler_xyz: Sequence[float]) -> Tuple[float, float, float]:
    """Convert MMD XYZ Euler radians to Maya XYZ Euler degrees."""
    return (
        math.degrees(float(euler_xyz[0])),
        math.degrees(float(euler_xyz[1])),
        -math.degrees(float(euler_xyz[2])),
    )


def maya_euler_degrees_to_mmd_radians(euler_xyz: Sequence[float]) -> Tuple[float, float, float]:
    """Convert Maya XYZ Euler degrees to MMD XYZ Euler radians."""
    return (
        math.radians(float(euler_xyz[0])),
        math.radians(float(euler_xyz[1])),
        -math.radians(float(euler_xyz[2])),
    )


def mmd_matrix_to_maya(mmd_matrix: Sequence[float]) -> list[float]:
    """Convert an mmd-anim flat world matrix to a Maya cmds.xform matrix."""
    if len(mmd_matrix) != 16:
        raise ValueError("mmd_matrix must contain 16 values")

    signs = (1.0, 1.0, -1.0)
    maya_matrix = [float(v) for v in mmd_matrix]

    for row in range(3):
        for col in range(3):
            idx = row * 4 + col
            maya_matrix[idx] = float(mmd_matrix[idx]) * signs[row] * signs[col]

    for col in range(3):
        maya_matrix[12 + col] = float(mmd_matrix[12 + col]) * signs[col]

    return maya_matrix


# Z-reflection is its own inverse, so the same algorithm converts both ways.
maya_matrix_to_mmd = mmd_matrix_to_maya
