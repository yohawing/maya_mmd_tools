"""Build the Physics DAG hierarchy (rigid bodies + joints) from parsed PMX data."""

from __future__ import annotations

import math
import re
from typing import Optional

from maya import cmds

from mmd_tools.core.constants import CONSTRAINTS_GROUP, PHYSICS_GROUP, RIGID_BODIES_GROUP
from mmd_tools.core.logger import get_logger

_logger = get_logger(__name__)

_INVALID_NAME_CHARS_RE = re.compile(r"[^0-9A-Za-z_]+")


def _sanitize_node_name(name: str) -> str:
    """Turn an arbitrary PMX name into a Maya-safe node name fragment."""
    sanitized = _INVALID_NAME_CHARS_RE.sub("_", name or "").strip("_")
    if not sanitized:
        return "unnamed"
    if sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def _display_name(name_english: str, name_japanese: str) -> str:
    return name_english or name_japanese or "unnamed"


def _set_vector_attr(shape: str, attr: str, values) -> None:
    x, y, z = values
    cmds.setAttr(f"{shape}.{attr}X", x)
    cmds.setAttr(f"{shape}.{attr}Y", y)
    cmds.setAttr(f"{shape}.{attr}Z", z)


def _set_angle_vector_attr(shape: str, attr: str, values) -> None:
    # rotationX/Y/Z-style attributes are MFnUnitAttribute(kAngle); cmds.setAttr
    # expects degrees while PMX stores Euler angles in radians.
    x, y, z = values
    cmds.setAttr(f"{shape}.{attr}X", math.degrees(x))
    cmds.setAttr(f"{shape}.{attr}Y", math.degrees(y))
    cmds.setAttr(f"{shape}.{attr}Z", math.degrees(z))


def _set_position_attr(node: str, attr_prefix: str, position) -> None:
    x, y, z = position
    cmds.setAttr(f"{node}.{attr_prefix}X", x)
    cmds.setAttr(f"{node}.{attr_prefix}Y", y)
    cmds.setAttr(f"{node}.{attr_prefix}Z", z)


def _resolve_rigid_body_transform(rigid_body_transforms: list, index: int) -> Optional[str]:
    if index is None or index < 0 or index >= len(rigid_body_transforms):
        return None
    transform = rigid_body_transforms[index]
    return transform if transform and cmds.objExists(transform) else None


def _build_rigid_body(index: int, rb, maya_joints: list, parent_group: str, logger) -> Optional[str]:
    base_name = _display_name(rb.name_english, rb.name)
    node_name = f"rb_{index}_{_sanitize_node_name(base_name)}"
    transform = None
    try:
        transform = cmds.createNode("transform", name=node_name, parent=parent_group)
        shape = cmds.createNode("mmdRigidBodyShape", name=f"{node_name}Shape", parent=transform)

        cmds.setAttr(f"{shape}.pmxIndex", index)
        cmds.setAttr(f"{shape}.nameJp", rb.name or "", type="string")
        cmds.setAttr(f"{shape}.nameEn", rb.name_english or "", type="string")
        cmds.setAttr(f"{shape}.enable", True)
        cmds.setAttr(f"{shape}.shapeType", rb.shape_type)

        _set_vector_attr(shape, "shapeSize", rb.size)
        _set_vector_attr(shape, "position", rb.position)
        _set_angle_vector_attr(shape, "rotation", rb.rotation)

        cmds.setAttr(f"{shape}.physicsMode", rb.physics_mode)
        cmds.setAttr(f"{shape}.mass", rb.mass)
        cmds.setAttr(f"{shape}.linearDamping", rb.velocity_attenuation)
        cmds.setAttr(f"{shape}.angularDamping", rb.rotation_attenuation)
        cmds.setAttr(f"{shape}.friction", rb.friction)
        cmds.setAttr(f"{shape}.restitution", rb.elasticity)
        cmds.setAttr(f"{shape}.collisionGroup", min(rb.group, 15))
        cmds.setAttr(f"{shape}.collisionMask", rb.collision_mask)
        cmds.setAttr(f"{shape}.relatedBoneIndex", rb.related_bone_index)

        if 0 <= rb.related_bone_index < len(maya_joints):
            maya_joint = maya_joints[rb.related_bone_index]
            if maya_joint and cmds.objExists(maya_joint):
                cmds.connectAttr(f"{maya_joint}.message", f"{shape}.relatedBone")

        _set_position_attr(transform, "translate", rb.position)

        return transform
    except Exception as exc:
        logger.warning(f"event=rigid_body_build_failed index={index} name={base_name!r} error={exc}")
        if transform and cmds.objExists(transform):
            cmds.delete(transform)
        return None


def _build_joint(index: int, jt, rigid_body_transforms: list, parent_group: str, logger) -> Optional[str]:
    base_name = _display_name(jt.name_english, jt.name)
    node_name = f"jt_{index}_{_sanitize_node_name(base_name)}"
    transform = None
    try:
        transform = cmds.createNode("transform", name=node_name, parent=parent_group)
        shape = cmds.createNode("mmdPhysicsJointShape", name=f"{node_name}Shape", parent=transform)

        cmds.setAttr(f"{shape}.pmxIndex", index)
        cmds.setAttr(f"{shape}.nameJp", jt.name or "", type="string")
        cmds.setAttr(f"{shape}.nameEn", jt.name_english or "", type="string")
        cmds.setAttr(f"{shape}.enable", True)
        cmds.setAttr(f"{shape}.jointType", jt.joint_type)

        _set_vector_attr(shape, "position", jt.position)
        _set_angle_vector_attr(shape, "rotation", jt.rotation)
        _set_vector_attr(shape, "translationLimitMin", jt.translation_limit_min)
        _set_vector_attr(shape, "translationLimitMax", jt.translation_limit_max)
        _set_angle_vector_attr(shape, "rotationLimitMin", jt.rotation_limit_min)
        _set_angle_vector_attr(shape, "rotationLimitMax", jt.rotation_limit_max)
        _set_vector_attr(shape, "springTranslation", jt.spring_translation)
        _set_vector_attr(shape, "springRotation", jt.spring_rotation)

        cmds.setAttr(f"{shape}.rigidBodyAIndex", jt.rigid_body_a_index)
        cmds.setAttr(f"{shape}.rigidBodyBIndex", jt.rigid_body_b_index)

        rb_a = _resolve_rigid_body_transform(rigid_body_transforms, jt.rigid_body_a_index)
        if rb_a:
            cmds.connectAttr(f"{rb_a}.message", f"{shape}.rigidBodyA")
        rb_b = _resolve_rigid_body_transform(rigid_body_transforms, jt.rigid_body_b_index)
        if rb_b:
            cmds.connectAttr(f"{rb_b}.message", f"{shape}.rigidBodyB")

        _set_position_attr(transform, "translate", jt.position)

        return transform
    except Exception as exc:
        logger.warning(f"event=joint_build_failed index={index} name={base_name!r} error={exc}")
        if transform and cmds.objExists(transform):
            cmds.delete(transform)
        return None


def build_physics_scene(
    *,
    rigid_bodies,
    joints,
    bones,
    maya_joints,
    root_group: str,
    logger=None,
) -> tuple[list[str], list[str]]:
    """Build physics DAG nodes from PMX data.

    Creates ``root_group/Physics/RigidBodies`` and ``root_group/Physics/Constraints``
    groups, then one transform + ``mmdRigidBodyShape``/``mmdPhysicsJointShape`` pair
    per PMX rigid body / joint, with all PMX fields copied onto the shape attributes.

    Returns (rigid_body_transforms, joint_transforms).
    """
    log = logger or _logger

    physics_group = cmds.group(empty=True, name=PHYSICS_GROUP, parent=root_group)
    rigid_bodies_group = cmds.group(empty=True, name=RIGID_BODIES_GROUP, parent=physics_group)
    constraints_group = cmds.group(empty=True, name=CONSTRAINTS_GROUP, parent=physics_group)

    # Kept positional (index-aligned with the PMX lists, holes as None on
    # failure) because joints resolve rigid_body_a/b_index by list position.
    rigid_body_transforms = [
        _build_rigid_body(index, rb, maya_joints, rigid_bodies_group, log) for index, rb in enumerate(rigid_bodies)
    ]
    joint_transforms = [
        _build_joint(index, jt, rigid_body_transforms, constraints_group, log) for index, jt in enumerate(joints)
    ]

    return rigid_body_transforms, joint_transforms
