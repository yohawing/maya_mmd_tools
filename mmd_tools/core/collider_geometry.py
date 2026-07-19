"""Canonical local-space geometry for authoring colliders."""

from __future__ import annotations


SHAPE_SPHERE = 0
SHAPE_BOX = 1
SHAPE_CAPSULE = 2


def collider_half_extents(shape_type: int, size) -> tuple[float, float, float]:
    """Return the axis-aligned local half extents of a collider primitive."""
    sx, sy, sz = (max(float(value), 0.0) for value in size)
    if shape_type == SHAPE_SPHERE:
        return sx, sx, sx
    if shape_type == SHAPE_BOX:
        return sx, sy, sz
    radius = sx
    return radius, sy * 0.5 + radius, radius


def capsule_dimensions(size) -> tuple[float, float, float]:
    """Return ``(radius, cylinder_height, total_height)`` for a capsule."""
    radius = max(float(size[0]), 0.0)
    cylinder_height = max(float(size[1]), 0.0)
    return radius, cylinder_height, cylinder_height + 2.0 * radius


def box_draw_scale(size) -> tuple[float, float, float]:
    """Return the PMX half extents consumed by ``MUIDrawManager.box``."""
    return tuple(max(float(value), 0.0) for value in size)
