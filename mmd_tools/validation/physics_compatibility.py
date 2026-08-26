"""Compatibility check for a known legacy MMD constraint pattern."""

from typing import Any


def has_legacy_soft_constraint_pattern(pmx_data: Any) -> bool:
    """Return whether a non-zero lock drives an unbound dynamic body."""
    bodies = list(getattr(pmx_data, "rigid_bodies", ()) or ())
    for joint in getattr(pmx_data, "joints", ()) or ():
        body_indices = (
            getattr(joint, "rigid_body_a_index", -1),
            getattr(joint, "rigid_body_b_index", -1),
        )
        if getattr(joint, "joint_type", 0) != 0 or not all(
            isinstance(index, int) and 0 <= index < len(bodies) for index in body_indices
        ):
            continue
        locked_away_from_zero = any(
            minimum == maximum and minimum != 0.0
            for minimum, maximum in zip(
                getattr(joint, "translation_limit_min", ()),
                getattr(joint, "translation_limit_max", ()),
            )
        )
        has_unbound_dynamic_body = any(
            getattr(bodies[index], "related_bone_index", -1) == -1
            and getattr(bodies[index], "physics_mode", 0) != 0
            for index in body_indices
        )
        if locked_away_from_zero and has_unbound_dynamic_body:
            return True
    return False
