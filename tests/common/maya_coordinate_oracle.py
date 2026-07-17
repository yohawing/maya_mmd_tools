"""Independent Maya-space coordinate oracles for integration and capture tests."""

from __future__ import annotations

from collections.abc import Sequence

import maya.api.OpenMaya as om


def reflected_mmd_euler_matrix(euler_xyz: Sequence[float]) -> om.MMatrix:
    """Return ``P * R(euler_xyz) * P`` without production conversion helpers."""
    signs = (1.0, 1.0, -1.0)
    source = om.MEulerRotation(*(float(value) for value in euler_xyz)).asMatrix()
    reflected = [float(source[element]) for element in range(16)]
    for row in range(3):
        for column in range(3):
            element = row * 4 + column
            reflected[element] *= signs[row] * signs[column]
    return om.MMatrix(reflected)
