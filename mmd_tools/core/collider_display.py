"""Deterministic display styling for authoring colliders."""

from __future__ import annotations


# Fixed, color-blind-conscious categorical palette. Values are stable scene to
# scene and intentionally independent of physics mode.
COLLISION_GROUP_PALETTE = (
    (0.121, 0.466, 0.705), (1.000, 0.498, 0.054),
    (0.172, 0.627, 0.172), (0.839, 0.153, 0.157),
    (0.580, 0.404, 0.741), (0.549, 0.337, 0.294),
    (0.890, 0.467, 0.761), (0.498, 0.498, 0.498),
    (0.737, 0.741, 0.133), (0.090, 0.745, 0.811),
    (0.682, 0.780, 0.909), (1.000, 0.733, 0.470),
    (0.596, 0.875, 0.541), (1.000, 0.596, 0.588),
    (0.773, 0.690, 0.835), (0.769, 0.612, 0.580),
)


def collision_group_color(group: int, physics_mode: int) -> tuple[float, float, float, float]:
    """Return group RGB with mode represented only by alpha."""
    rgb = COLLISION_GROUP_PALETTE[min(max(int(group), 0), 15)]
    alpha = (0.90, 0.78, 0.66)[min(max(int(physics_mode), 0), 2)]
    return (*rgb, alpha)


def physics_mode_line_style(physics_mode: int) -> int:
    """Return the solid MUIDrawManager line style for every physics mode."""
    return 0
