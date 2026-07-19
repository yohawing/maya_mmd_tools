"""Convert Maya kAngle values to and from the runtime's radian convention."""

from __future__ import annotations

import math

from maya import cmds


def _uses_radians() -> bool:
    """Return whether Maya's current UI angle unit is radians."""
    unit = str(cmds.currentUnit(query=True, angle=True)).lower()
    return unit.startswith("rad")


def maya_angle_to_radians(values) -> tuple[float, float, float]:
    """Convert a three-component Maya kAngle value into radians."""
    components = tuple(float(value) for value in values)
    if _uses_radians():
        return components
    return tuple(math.radians(value) for value in components)


def radians_to_maya_angle(values) -> tuple[float, float, float]:
    """Convert runtime radians into Maya's current UI angle unit."""
    components = tuple(float(value) for value in values)
    if _uses_radians():
        return components
    return tuple(math.degrees(value) for value in components)
