"""Collect rigid-body and joint data from the Physics DAG for PMX export."""

from __future__ import annotations

from typing import Optional

from maya import cmds

from mmd_tools.core.constants import CONSTRAINTS_GROUP, PHYSICS_GROUP, RIGID_BODIES_GROUP
from mmd_tools.core.maya_angle import maya_angle_to_radians


def _find_group(parent: str, group_name: str) -> Optional[str]:
    children = cmds.listRelatives(parent, children=True, fullPath=True, type="transform") or []
    for child in children:
        leaf_name = child.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
        if leaf_name == group_name:
            return child
    return None


def _find_shapes_of_type(parent_group: str, node_type: str) -> list[tuple[str, str]]:
    """Return (transform, shape) pairs for all children with the given shape type."""
    result = []
    children = cmds.listRelatives(parent_group, children=True, fullPath=True, type="transform") or []
    for transform in children:
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, type=node_type) or []
        if shapes:
            result.append((transform, shapes[0]))
    return result


def _get_attr(node: str, attr: str, default=None):
    try:
        return cmds.getAttr(f"{node}.{attr}")
    except Exception:
        return default


def _get_vector_attr(shape: str, attr: str) -> tuple[float, float, float]:
    x = _get_attr(shape, f"{attr}X", 0.0)
    y = _get_attr(shape, f"{attr}Y", 0.0)
    z = _get_attr(shape, f"{attr}Z", 0.0)
    return (x, y, z)


def _get_angle_vector_attr(shape: str, attr: str) -> tuple[float, float, float]:
    """Read angle attributes in Maya's current unit and return radians."""
    values = (
        _get_attr(shape, f"{attr}X", 0.0),
        _get_attr(shape, f"{attr}Y", 0.0),
        _get_attr(shape, f"{attr}Z", 0.0),
    )
    return maya_angle_to_radians(values)


def _resolve_message_target(shape: str, attr: str) -> Optional[str]:
    """Follow a message connection and return the source node, or None."""
    connections = cmds.listConnections(f"{shape}.{attr}", source=True, destination=False) or []
    return connections[0] if connections else None


def _resolve_bone_index(
    shape: str,
    bone_index_by_joint: dict[str, int],
) -> int:
    """Resolve relatedBone message to an export bone index.

    Returns -1 when the bone is not in the export bone list (e.g.
    non-deforming physics-only bones that carry no skin weight).
    Never falls back to the stale PMX-import-time index.
    """
    target = _resolve_message_target(shape, "relatedBone")
    if target:
        long_names = cmds.ls(target, long=True) or []
        for name in long_names:
            if name in bone_index_by_joint:
                return bone_index_by_joint[name]
        short_name = target.rsplit("|", 1)[-1]
        if short_name in bone_index_by_joint:
            return bone_index_by_joint[short_name]
    return -1


def _collect_rigid_body(shape: str, bone_index_by_joint: dict[str, int]) -> dict:
    return {
        "name": _get_attr(shape, "nameJp", "") or "",
        "name_english": _get_attr(shape, "nameEn", "") or "",
        "related_bone_index": _resolve_bone_index(shape, bone_index_by_joint),
        "group": int(_get_attr(shape, "collisionGroup", 0)),
        "collision_mask": int(_get_attr(shape, "collisionMask", 0)),
        "shape_type": int(_get_attr(shape, "shapeType", 0)),
        "size": _get_vector_attr(shape, "shapeSize"),
        "position": _get_vector_attr(shape, "position"),
        "rotation": _get_angle_vector_attr(shape, "rotation"),
        "mass": float(_get_attr(shape, "mass", 0.0)),
        "velocity_attenuation": float(_get_attr(shape, "linearDamping", 0.0)),
        "rotation_attenuation": float(_get_attr(shape, "angularDamping", 0.0)),
        "elasticity": float(_get_attr(shape, "restitution", 0.0)),
        "friction": float(_get_attr(shape, "friction", 0.0)),
        "physics_mode": int(_get_attr(shape, "physicsMode", 0)),
    }


def _collect_joint(
    shape: str,
    rb_transform_to_index: dict[str, int],
) -> dict:
    def _resolve_rb_index(attr_msg: str, attr_fallback: str) -> int:
        target = _resolve_message_target(shape, attr_msg)
        if target:
            long_names = cmds.ls(target, long=True) or []
            for name in long_names:
                if name in rb_transform_to_index:
                    return rb_transform_to_index[name]
            short_name = target.rsplit("|", 1)[-1]
            if short_name in rb_transform_to_index:
                return rb_transform_to_index[short_name]
        return int(_get_attr(shape, attr_fallback, -1))

    return {
        "name": _get_attr(shape, "nameJp", "") or "",
        "name_english": _get_attr(shape, "nameEn", "") or "",
        "joint_type": int(_get_attr(shape, "jointType", 0)),
        "rigid_body_a_index": _resolve_rb_index("rigidBodyA", "rigidBodyAIndex"),
        "rigid_body_b_index": _resolve_rb_index("rigidBodyB", "rigidBodyBIndex"),
        "position": _get_vector_attr(shape, "position"),
        "rotation": _get_angle_vector_attr(shape, "rotation"),
        "translation_limit_min": _get_vector_attr(shape, "translationLimitMin"),
        "translation_limit_max": _get_vector_attr(shape, "translationLimitMax"),
        "rotation_limit_min": _get_angle_vector_attr(shape, "rotationLimitMin"),
        "rotation_limit_max": _get_angle_vector_attr(shape, "rotationLimitMax"),
        "spring_translation": _get_vector_attr(shape, "springTranslation"),
        "spring_rotation": _get_vector_attr(shape, "springRotation"),
    }


def collect_physics_from_scene(
    root_group: str,
    bone_index_by_joint: dict[str, int],
) -> tuple[list[dict], list[dict]]:
    """Collect rigid bodies and joints from the Physics DAG hierarchy.

    Returns ``(rigid_body_dicts, joint_dicts)`` ready for ``PmxExporter``.
    If the Physics hierarchy does not exist, returns empty lists.
    """
    physics_group = _find_group(root_group, PHYSICS_GROUP)
    if not physics_group:
        return [], []

    rb_group = _find_group(physics_group, RIGID_BODIES_GROUP)
    jt_group = _find_group(physics_group, CONSTRAINTS_GROUP)

    rigid_bodies = []
    rb_transform_to_index: dict[str, int] = {}

    if rb_group:
        pairs = _find_shapes_of_type(rb_group, "mmdRigidBodyShape")
        pairs.sort(key=lambda p: int(_get_attr(p[1], "pmxIndex", 9999)))
        for transform, shape in pairs:
            rb_dict = _collect_rigid_body(shape, bone_index_by_joint)
            export_index = len(rigid_bodies)
            rigid_bodies.append(rb_dict)
            for name in cmds.ls(transform, long=True) or []:
                rb_transform_to_index[name] = export_index
            rb_transform_to_index[transform.rsplit("|", 1)[-1]] = export_index

    joints = []
    if jt_group:
        pairs = _find_shapes_of_type(jt_group, "mmdPhysicsJointShape")
        pairs.sort(key=lambda p: int(_get_attr(p[1], "pmxIndex", 9999)))
        for _transform, shape in pairs:
            jt_dict = _collect_joint(shape, rb_transform_to_index)
            joints.append(jt_dict)

    return rigid_bodies, joints
