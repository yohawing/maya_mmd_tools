"""Build physics descriptors from the Maya Physics DAG hierarchy.

Reads mmdRigidBodyShape / mmdPhysicsJointShape attributes and produces
a PhysicsDescriptorSet identical to build_descriptors_from_pmx output.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import List, Optional, Sequence

from maya import cmds

from mmd_tools.core.constants import (
    ATTR_MMD_PMX_REST_POSITION,
    CONSTRAINTS_GROUP,
    PHYSICS_GROUP,
    RIGID_BODIES_GROUP,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_JOINT_KIND_GENERIC_6DOF_SPRING,
    MMD_RUNTIME_PHYSICS_JOINT_KIND_UNSUPPORTED,
    MmdRuntimeFfiPhysicsJointDesc,
    MmdRuntimeFfiPhysicsRigidbodyDesc,
)
from mmd_tools.core.physics_descriptor import (
    DescriptorValidationError,
    PhysicsDescriptorSet,
    _body_from_bone,
    _bone_from_body,
    _set_float3,
    _set_float4,
    validate_joint_fields,
    validate_rigid_body_fields,
)


def _find_group(parent: str, name: str) -> Optional[str]:
    children = cmds.listRelatives(parent, children=True, fullPath=True, type="transform") or []
    for child in children:
        leaf_name = child.rsplit("|", 1)[-1]
        if leaf_name.rsplit(":", 1)[-1] == name:
            return child
    return None


def _find_shapes(group: str, node_type: str) -> list[tuple[str, str]]:
    if not group:
        return []
    result = []
    children = cmds.listRelatives(group, children=True, fullPath=True, type="transform") or []
    for transform in children:
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, type=node_type) or []
        if shapes:
            result.append((transform, shapes[0]))
    result.sort(key=lambda p: _get_attr(p[1], "pmxIndex", 9999))
    return result


def _get_attr(node: str, attr: str, default=None):
    try:
        return cmds.getAttr(f"{node}.{attr}")
    except Exception:
        return default


def _get_vector(node: str, attr: str) -> tuple[float, float, float]:
    x = float(_get_attr(node, f"{attr}X", 0.0))
    y = float(_get_attr(node, f"{attr}Y", 0.0))
    z = float(_get_attr(node, f"{attr}Z", 0.0))
    return (x, y, z)


def _get_angle_vector_radians(node: str, attr: str) -> tuple[float, float, float]:
    """Read kAngle attrs (Maya returns degrees) and convert to radians."""
    x = float(_get_attr(node, f"{attr}X", 0.0))
    y = float(_get_attr(node, f"{attr}Y", 0.0))
    z = float(_get_attr(node, f"{attr}Z", 0.0))
    return (math.radians(x), math.radians(y), math.radians(z))


def _resolve_bone_world_position(
    shape: str,
    bone_joints: Optional[Sequence[Optional[str]]] = None,
) -> tuple[float, float, float]:
    """Get rest-pose world position of the bone linked to this rigid body."""
    joint = None
    connections = cmds.listConnections(f"{shape}.relatedBone", source=True, destination=False) or []
    if connections:
        joint = connections[0]

    if not joint:
        bone_idx = int(_get_attr(shape, "relatedBoneIndex", -1))
        if bone_joints and 0 <= bone_idx < len(bone_joints):
            joint = bone_joints[bone_idx]

    if joint and cmds.objExists(joint):
        if cmds.attributeQuery(ATTR_MMD_PMX_REST_POSITION, node=joint, exists=True):
            value = cmds.getAttr(f"{joint}.{ATTR_MMD_PMX_REST_POSITION}")
            if value:
                return tuple(float(component) for component in value[0])
        pos = cmds.xform(joint, query=True, worldSpace=True, translation=True)
        return (pos[0], pos[1], pos[2])

    return (0.0, 0.0, 0.0)


def build_descriptors_from_dag(
    root_group: str,
    bone_joints: Optional[Sequence[Optional[str]]] = None,
    bone_count: int = 0,
) -> PhysicsDescriptorSet:
    """Build typed descriptors from the Physics DAG hierarchy.

    Reads rigid body and joint shapes under ``root_group/Physics/…`` and
    produces the same ctypes descriptor arrays as ``build_descriptors_from_pmx``.

    Args:
        root_group: Model root transform containing the Physics hierarchy.
        bone_joints: Maya joint paths indexed by PMX bone index (for bone
            position lookup).  Falls back to message connections on each shape.
        bone_count: Total number of PMX bones (for validation range checks).

    Returns:
        PhysicsDescriptorSet with ctypes arrays ready for FFI.
    """
    physics_group = _find_group(root_group, PHYSICS_GROUP)
    if not physics_group:
        return PhysicsDescriptorSet(
            rigid_bodies=[], joints=[], identity_hash="", validation_errors=[]
        )

    rb_group = _find_group(physics_group, RIGID_BODIES_GROUP)
    jt_group = _find_group(physics_group, CONSTRAINTS_GROUP)

    errors: List[DescriptorValidationError] = []
    rb_descs: List[MmdRuntimeFfiPhysicsRigidbodyDesc] = []
    hash_parts: List[bytes] = []

    rb_pairs = _find_shapes(rb_group, "mmdRigidBodyShape") if rb_group else []
    rb_index_to_dense: dict[int, int] = {}
    for dense_index, (_transform, shape) in enumerate(rb_pairs):
        source_index = int(_get_attr(shape, "pmxIndex", -1))
        if source_index < 0:
            errors.append(DescriptorValidationError(
                dense_index, "rigid_body", "pmx_index", f"invalid pmxIndex {source_index}",
            ))
        elif source_index in rb_index_to_dense:
            errors.append(DescriptorValidationError(
                dense_index, "rigid_body", "pmx_index", f"duplicate pmxIndex {source_index}",
            ))
        else:
            rb_index_to_dense[source_index] = dense_index

    for i, (_transform, shape) in enumerate(rb_pairs):
        shape_type = int(_get_attr(shape, "shapeType", 0))
        shape_size = _get_vector(shape, "shapeSize")
        position = _get_vector(shape, "position")
        rotation = _get_angle_vector_radians(shape, "rotation")
        mode = int(_get_attr(shape, "physicsMode", 0))
        mass = float(_get_attr(shape, "mass", 0.0))
        linear_damping = float(_get_attr(shape, "linearDamping", 0.0))
        angular_damping = float(_get_attr(shape, "angularDamping", 0.0))
        friction = float(_get_attr(shape, "friction", 0.0))
        restitution = float(_get_attr(shape, "restitution", 0.0))
        collision_group = int(_get_attr(shape, "collisionGroup", 0))
        collision_mask = int(_get_attr(shape, "collisionMask", 0))
        bone_index = int(_get_attr(shape, "relatedBoneIndex", -1))

        errs = validate_rigid_body_fields(
            i, shape_type, shape_size, position, rotation,
            mass, linear_damping, angular_damping, friction, restitution,
            collision_group, collision_mask, bone_index, mode,
            bone_count=bone_count,
        )
        errors.extend(errs)

        bone_pos = _resolve_bone_world_position(shape, bone_joints)
        bfb_pos, bfb_rot = _body_from_bone(position, rotation, bone_pos)
        bfr_pos, bfr_rot = _bone_from_body(position, rotation, bone_pos)

        desc = MmdRuntimeFfiPhysicsRigidbodyDesc()
        desc.shape = shape_type
        _set_float3(desc.shape_size, shape_size)
        _set_float3(desc.position_xyz, position)
        _set_float3(desc.rotation_euler_xyz, rotation)
        desc.mass = mass
        desc.linear_damping = linear_damping
        desc.angular_damping = angular_damping
        desc.friction = friction
        desc.restitution = restitution
        desc.collision_group = collision_group
        desc.collision_mask = collision_mask
        desc.bone_index = bone_index
        desc.mode = mode
        _set_float3(desc.body_from_bone_position_xyz, bfb_pos)
        _set_float4(desc.body_from_bone_rotation_xyzw, bfb_rot)
        _set_float3(desc.bone_from_body_position_xyz, bfr_pos)
        _set_float4(desc.bone_from_body_rotation_xyzw, bfr_rot)
        rb_descs.append(desc)

        hash_parts.append(struct.pack(
            "<I3f3f3f5fHHiI3f4f3f4f",
            shape_type,
            *shape_size, *position, *rotation,
            mass, linear_damping, angular_damping, friction, restitution,
            collision_group, collision_mask, bone_index, mode,
            *bfb_pos, *bfb_rot, *bfr_pos, *bfr_rot,
        ))

    jt_descs: List[MmdRuntimeFfiPhysicsJointDesc] = []
    rb_count = len(rb_descs)

    jt_pairs = _find_shapes(jt_group, "mmdPhysicsJointShape") if jt_group else []
    for i, (_transform, shape) in enumerate(jt_pairs):
        raw_type = int(_get_attr(shape, "jointType", 0))
        jt_kind = (
            MMD_RUNTIME_PHYSICS_JOINT_KIND_GENERIC_6DOF_SPRING
            if raw_type == 0
            else MMD_RUNTIME_PHYSICS_JOINT_KIND_UNSUPPORTED
        )
        position = _get_vector(shape, "position")
        rotation = _get_angle_vector_radians(shape, "rotation")
        trans_min = _get_vector(shape, "translationLimitMin")
        trans_max = _get_vector(shape, "translationLimitMax")
        rot_min = _get_angle_vector_radians(shape, "rotationLimitMin")
        rot_max = _get_angle_vector_radians(shape, "rotationLimitMax")
        spring_trans = _get_vector(shape, "springTranslation")
        spring_rot = _get_vector(shape, "springRotation")

        rb_a_source_index = int(_get_attr(shape, "rigidBodyAIndex", -1))
        rb_b_source_index = int(_get_attr(shape, "rigidBodyBIndex", -1))
        rb_a_index = rb_index_to_dense.get(rb_a_source_index, -1)
        rb_b_index = rb_index_to_dense.get(rb_b_source_index, -1)
        if rb_a_source_index >= 0 and rb_a_index < 0:
            errors.append(DescriptorValidationError(
                i, "joint", "rigidbody_a_pmx_index",
                f"missing rigid body pmxIndex {rb_a_source_index}",
            ))
        if rb_b_source_index >= 0 and rb_b_index < 0:
            errors.append(DescriptorValidationError(
                i, "joint", "rigidbody_b_pmx_index",
                f"missing rigid body pmxIndex {rb_b_source_index}",
            ))

        errs = validate_joint_fields(
            i, jt_kind, rb_a_index, rb_b_index, rb_count,
            position, rotation, trans_min, trans_max,
            rot_min, rot_max, spring_trans, spring_rot,
        )
        errors.extend(errs)

        desc = MmdRuntimeFfiPhysicsJointDesc()
        desc.kind = jt_kind
        desc.rigidbody_a = rb_a_index if rb_a_index >= 0 else 0
        desc.rigidbody_b = rb_b_index if rb_b_index >= 0 else 0
        _set_float3(desc.position_xyz, position)
        _set_float3(desc.rotation_euler_xyz, rotation)
        _set_float3(desc.translation_lower_limit_xyz, trans_min)
        _set_float3(desc.translation_upper_limit_xyz, trans_max)
        _set_float3(desc.rotation_lower_limit_xyz, rot_min)
        _set_float3(desc.rotation_upper_limit_xyz, rot_max)
        _set_float3(desc.spring_translation_factor_xyz, spring_trans)
        _set_float3(desc.spring_rotation_factor_xyz, spring_rot)
        jt_descs.append(desc)

        hash_parts.append(struct.pack(
            "<IQQ3f3f3f3f3f3f3f3f",
            jt_kind,
            max(0, rb_a_index), max(0, rb_b_index),
            *position, *rotation,
            *trans_min, *trans_max, *rot_min, *rot_max,
            *spring_trans, *spring_rot,
        ))

    identity = hashlib.sha256(b"".join(hash_parts)).hexdigest() if hash_parts else ""

    return PhysicsDescriptorSet(
        rigid_bodies=rb_descs,
        joints=jt_descs,
        identity_hash=identity,
        validation_errors=errors,
    )
