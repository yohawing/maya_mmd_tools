"""Narrow compatibility checks for PMX rigid-body constraint graphs."""

from __future__ import annotations

from collections import defaultdict, deque
import math
from typing import Any, Dict, Iterable, List, Set


LEGACY_SOFT_CONSTRAINT_WARNING_CODE = "legacy_soft_constraint_behavior"


def _valid_body_index(index: Any, body_count: int) -> bool:
    return isinstance(index, int) and not isinstance(index, bool) and 0 <= index < body_count


def _has_nonzero_locked_translation(joint: Any) -> bool:
    lower = getattr(joint, "translation_limit_min", ())
    upper = getattr(joint, "translation_limit_max", ())
    if len(lower) != 3 or len(upper) != 3:
        return False
    return any(
        minimum == maximum and minimum != 0.0 and math.isfinite(minimum)
        for minimum, maximum in zip(lower, upper)
    )


def _body_is_unbound_dynamic(body: Any) -> bool:
    return getattr(body, "related_bone_index", -1) == -1 and getattr(body, "physics_mode", 0) != 0


def _body_is_bone_bound(body: Any, *, dynamic: bool) -> bool:
    has_bone = getattr(body, "related_bone_index", -1) >= 0
    is_dynamic = getattr(body, "physics_mode", 0) != 0
    return has_bone and is_dynamic is dynamic


def _connected_components(adjacency: Dict[int, Set[int]]) -> Iterable[Set[int]]:
    remaining = set(adjacency)
    while remaining:
        start = remaining.pop()
        component = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency[current]:
                if neighbor in component:
                    continue
                component.add(neighbor)
                remaining.discard(neighbor)
                queue.append(neighbor)
        yield component


def find_legacy_soft_constraint_warnings(pmx_data: Any) -> List[Dict[str, Any]]:
    """Return warnings for the specific legacy-soft-constraint graph pattern.

    This does not claim to identify every model authored for old Bullet.  It
    only reports islands that combine the pattern seen in known affected MMD
    assets: a non-zero locked translation, an unbound dynamic body, a
    bone-follow anchor, and a physics-driven bone body.
    """
    bodies = list(getattr(pmx_data, "rigid_bodies", ()) or ())
    bones = list(getattr(pmx_data, "bones", ()) or ())
    joints = list(getattr(pmx_data, "joints", ()) or ())
    adjacency: Dict[int, Set[int]] = defaultdict(set)
    valid_joints = []

    for joint_index, joint in enumerate(joints):
        if getattr(joint, "joint_type", 0) != 0:
            continue
        body_a = getattr(joint, "rigid_body_a_index", -1)
        body_b = getattr(joint, "rigid_body_b_index", -1)
        if not (_valid_body_index(body_a, len(bodies)) and _valid_body_index(body_b, len(bodies))):
            continue
        adjacency[body_a].add(body_b)
        adjacency[body_b].add(body_a)
        valid_joints.append((joint_index, joint, body_a, body_b))

    components = list(_connected_components(adjacency))
    component_by_body = {
        body_index: component_index
        for component_index, component in enumerate(components)
        for body_index in component
    }
    matching_joints_by_component = defaultdict(list)
    for joint_index, joint, body_a, body_b in valid_joints:
        if not _has_nonzero_locked_translation(joint):
            continue
        if not (_body_is_unbound_dynamic(bodies[body_a]) or _body_is_unbound_dynamic(bodies[body_b])):
            continue
        matching_joints_by_component[component_by_body[body_a]].append((joint_index, joint))

    warnings = []
    for component_index, component in enumerate(components):
        component_bodies = [bodies[index] for index in component]
        if not any(_body_is_bone_bound(body, dynamic=False) for body in component_bodies):
            continue
        if not any(_body_is_bone_bound(body, dynamic=True) for body in component_bodies):
            continue

        matching_joints = matching_joints_by_component[component_index]
        if not matching_joints:
            continue

        joint_names = [str(getattr(joint, "name", "") or f"joint_{index}") for index, joint in matching_joints]
        body_names = [str(getattr(bodies[index], "name", "") or f"rigid_body_{index}") for index in sorted(component)]
        bone_indices = sorted(
            {
                int(getattr(body, "related_bone_index", -1))
                for body in component_bodies
                if _body_is_bone_bound(body, dynamic=True)
            }
        )
        bone_names = [
            str(getattr(bones[index], "name", "") or f"bone_{index}")
            if index < len(bones)
            else f"bone_{index}"
            for index in bone_indices
        ]
        warnings.append(
            {
                "source": "physics_compatibility",
                "code": LEGACY_SOFT_CONSTRAINT_WARNING_CODE,
                "severity": "warning",
                "reason": "nonzero_locked_translation_in_unbound_dynamic_chain",
                "message": (
                    "This rigid-body group may rely on legacy MMD soft-constraint behavior. "
                    "Modern Bullet can settle it at a different position."
                ),
                "joint_names": joint_names,
                "rigid_body_names": body_names,
                "affected_bone_indices": bone_indices,
                "affected_bone_names": bone_names,
                "fallback": "none",
            }
        )
    return warnings


__all__ = ["LEGACY_SOFT_CONSTRAINT_WARNING_CODE", "find_legacy_soft_constraint_warnings"]
