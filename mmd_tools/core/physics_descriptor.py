"""Canonical physics descriptor schema, validation, and identity hash.

Converts PMX rigid-body / joint data into the typed descriptor structs
expected by ``mmd_runtime_physics_world_create``.  The body_from_bone and
bone_from_body transforms are computed here from PMX bone rest positions
and rigid-body bind poses, matching the mmd-anim Rust implementation.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_JOINT_KIND_GENERIC_6DOF_SPRING,
    MMD_RUNTIME_PHYSICS_JOINT_KIND_UNSUPPORTED,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC_BONE,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_STATIC,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_BOX,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_CAPSULE,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_SPHERE,
    MmdRuntimeFfiPhysicsJointDesc,
    MmdRuntimeFfiPhysicsRigidbodyDesc,
)

_VALID_SHAPES = {
    MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_SPHERE,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_BOX,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_CAPSULE,
}
_VALID_MODES = {
    MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_STATIC,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC_BONE,
}
_VALID_JOINT_KINDS = {
    MMD_RUNTIME_PHYSICS_JOINT_KIND_GENERIC_6DOF_SPRING,
}

Vec3 = Tuple[float, float, float]
Vec4 = Tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# Quaternion / matrix helpers (pure Python, no external deps)
# ---------------------------------------------------------------------------

def _quat_from_euler_zyx(x: float, y: float, z: float) -> Vec4:
    """Euler XYZ radians -> quaternion (x,y,z,w) using intrinsic ZYX order.

    Matches glam ``Quat::from_euler(EulerRot::ZYX, z, y, x)`` which is
    equivalent to intrinsic X then Y then Z rotation — the MMD/PMX convention.
    """
    cx, sx = math.cos(x * 0.5), math.sin(x * 0.5)
    cy, sy = math.cos(y * 0.5), math.sin(y * 0.5)
    cz, sz = math.cos(z * 0.5), math.sin(z * 0.5)
    qw = cx * cy * cz + sx * sy * sz
    qx = sx * cy * cz - cx * sy * sz
    qy = cx * sy * cz + sx * cy * sz
    qz = cx * cy * sz - sx * sy * cz
    return (qx, qy, qz, qw)


def _quat_conjugate(q: Vec4) -> Vec4:
    return (-q[0], -q[1], -q[2], q[3])


def _quat_mul(a: Vec4, b: Vec4) -> Vec4:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _quat_rotate_vec3(q: Vec4, v: Vec3) -> Vec3:
    vq = (v[0], v[1], v[2], 0.0)
    r = _quat_mul(_quat_mul(q, vq), _quat_conjugate(q))
    return (r[0], r[1], r[2])


def _body_from_bone(
    body_pos: Vec3,
    body_rot_euler: Vec3,
    bone_pos: Vec3,
) -> Tuple[Vec3, Vec4]:
    """Compute body_from_bone transform: inv(bone_bind) * body_bind.

    bone_bind is translation-only. body_bind is translation + ZYX euler rotation.
    Result is (position_xyz, rotation_xyzw).
    """
    body_quat = _quat_from_euler_zyx(*body_rot_euler)
    rel_pos = (
        body_pos[0] - bone_pos[0],
        body_pos[1] - bone_pos[1],
        body_pos[2] - bone_pos[2],
    )
    return rel_pos, body_quat


def _bone_from_body(
    body_pos: Vec3,
    body_rot_euler: Vec3,
    bone_pos: Vec3,
) -> Tuple[Vec3, Vec4]:
    """Compute bone_from_body transform: inv(body_bind) * bone_bind.

    Result is (position_xyz, rotation_xyzw).
    """
    body_quat = _quat_from_euler_zyx(*body_rot_euler)
    inv_body_quat = _quat_conjugate(body_quat)
    rel_pos = (
        bone_pos[0] - body_pos[0],
        bone_pos[1] - body_pos[1],
        bone_pos[2] - body_pos[2],
    )
    rotated_pos = _quat_rotate_vec3(inv_body_quat, rel_pos)
    return rotated_pos, inv_body_quat


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class DescriptorValidationError:
    index: int
    kind: str  # "rigid_body" or "joint"
    field: str
    message: str


def _is_finite(v: float) -> bool:
    return math.isfinite(v)


def _all_finite_3(v: Vec3) -> bool:
    return all(math.isfinite(c) for c in v)


def _all_finite_4(v: Vec4) -> bool:
    return all(math.isfinite(c) for c in v)


def validate_rigid_body_fields(
    index: int,
    shape: int,
    shape_size: Vec3,
    position: Vec3,
    rotation: Vec3,
    mass: float,
    linear_damping: float,
    angular_damping: float,
    friction: float,
    restitution: float,
    collision_group: int,
    collision_mask: int,
    bone_index: int,
    mode: int,
    bone_count: int | None = None,
) -> List[DescriptorValidationError]:
    errors: List[DescriptorValidationError] = []

    def err(f: str, m: str) -> None:
        errors.append(DescriptorValidationError(index, "rigid_body", f, m))

    if shape not in _VALID_SHAPES:
        err("shape", f"invalid shape type {shape}")
    if not _all_finite_3(shape_size):
        err("shape_size", "non-finite shape size")
    if not _all_finite_3(position):
        err("position", "non-finite position")
    if not _all_finite_3(rotation):
        err("rotation", "non-finite rotation")
    if not _is_finite(mass):
        err("mass", "non-finite mass")
    if not _is_finite(linear_damping):
        err("linear_damping", "non-finite linear_damping")
    if not _is_finite(angular_damping):
        err("angular_damping", "non-finite angular_damping")
    if not _is_finite(friction):
        err("friction", "non-finite friction")
    if not _is_finite(restitution):
        err("restitution", "non-finite restitution")
    if not (0 <= collision_group <= 0xFFFF):
        err("collision_group", f"out of range: {collision_group}")
    if not (0 <= collision_mask <= 0xFFFF):
        err("collision_mask", f"out of range: {collision_mask}")
    if mode not in _VALID_MODES:
        err("mode", f"invalid physics mode {mode}")
    # PMX uses -1 for rigid bodies that are intentionally not attached to a
    # bone.  It is a valid sentinel, not a malformed reference.
    if bone_count is not None and not (-1 <= bone_index < bone_count):
        err("bone_index", f"out of range: {bone_index} (bone_count={bone_count})")
    return errors


def validate_joint_fields(
    index: int,
    kind: int,
    rigidbody_a: int,
    rigidbody_b: int,
    rigidbody_count: int,
    position: Vec3,
    rotation: Vec3,
    translation_lower: Vec3,
    translation_upper: Vec3,
    rotation_lower: Vec3,
    rotation_upper: Vec3,
    spring_translation: Vec3,
    spring_rotation: Vec3,
) -> List[DescriptorValidationError]:
    errors: List[DescriptorValidationError] = []

    def err(f: str, m: str) -> None:
        errors.append(DescriptorValidationError(index, "joint", f, m))

    if kind not in _VALID_JOINT_KINDS:
        err("kind", f"unsupported joint kind {kind}")
    if rigidbody_a < 0 or rigidbody_a >= rigidbody_count:
        err("rigidbody_a", f"out of range: {rigidbody_a}")
    if rigidbody_b < 0 or rigidbody_b >= rigidbody_count:
        err("rigidbody_b", f"out of range: {rigidbody_b}")
    for name, vec in [
        ("position", position),
        ("rotation", rotation),
        ("translation_lower_limit", translation_lower),
        ("translation_upper_limit", translation_upper),
        ("rotation_lower_limit", rotation_lower),
        ("rotation_upper_limit", rotation_upper),
        ("spring_translation", spring_translation),
        ("spring_rotation", spring_rotation),
    ]:
        if not _all_finite_3(vec):
            err(name, f"non-finite {name}")
    return errors


# ---------------------------------------------------------------------------
# PMX -> Descriptor conversion
# ---------------------------------------------------------------------------

@dataclass
class PhysicsDescriptorSet:
    """Validated set of rigid-body and joint descriptors ready for FFI."""

    rigid_bodies: List[MmdRuntimeFfiPhysicsRigidbodyDesc]
    joints: List[MmdRuntimeFfiPhysicsJointDesc]
    identity_hash: str
    validation_errors: List[DescriptorValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


def _set_float3(target, values: Vec3) -> None:
    target[0], target[1], target[2] = values


def _set_float4(target, values: Vec4) -> None:
    target[0], target[1], target[2], target[3] = values


def build_descriptors_from_pmx(
    rigid_bodies: Sequence,
    joints: Sequence,
    bones: Sequence,
) -> PhysicsDescriptorSet:
    """Build typed descriptors from parsed PMX data.

    Args:
        rigid_bodies: Sequence of PmxRigidBody instances.
        joints: Sequence of PmxJoint instances.
        bones: Sequence of PmxBone instances (need .position attribute).

    Returns:
        PhysicsDescriptorSet with ctypes arrays ready for FFI and an identity hash.
    """
    errors: List[DescriptorValidationError] = []
    rb_descs: List[MmdRuntimeFfiPhysicsRigidbodyDesc] = []
    hash_parts: List[bytes] = []

    bone_count = len(bones)

    for i, rb in enumerate(rigid_bodies):
        errs = validate_rigid_body_fields(
            i,
            rb.shape_type,
            rb.size,
            rb.position,
            rb.rotation,
            rb.mass,
            rb.velocity_attenuation,
            rb.rotation_attenuation,
            rb.friction,
            rb.elasticity,
            rb.group,
            rb.collision_mask,
            rb.related_bone_index,
            rb.physics_mode,
            bone_count=bone_count,
        )
        errors.extend(errs)

        bone_idx = rb.related_bone_index
        if 0 <= bone_idx < bone_count:
            bone_pos = bones[bone_idx].position
        else:
            bone_pos = (0.0, 0.0, 0.0)

        bfb_pos, bfb_rot = _body_from_bone(rb.position, rb.rotation, bone_pos)
        bfr_pos, bfr_rot = _bone_from_body(rb.position, rb.rotation, bone_pos)

        desc = MmdRuntimeFfiPhysicsRigidbodyDesc()
        desc.shape = rb.shape_type
        _set_float3(desc.shape_size, rb.size)
        _set_float3(desc.position_xyz, rb.position)
        _set_float3(desc.rotation_euler_xyz, rb.rotation)
        desc.mass = rb.mass
        desc.linear_damping = rb.velocity_attenuation
        desc.angular_damping = rb.rotation_attenuation
        desc.friction = rb.friction
        desc.restitution = rb.elasticity
        desc.collision_group = rb.group
        desc.collision_mask = rb.collision_mask
        desc.bone_index = bone_idx
        desc.mode = rb.physics_mode
        _set_float3(desc.body_from_bone_position_xyz, bfb_pos)
        _set_float4(desc.body_from_bone_rotation_xyzw, bfb_rot)
        _set_float3(desc.bone_from_body_position_xyz, bfr_pos)
        _set_float4(desc.bone_from_body_rotation_xyzw, bfr_rot)

        rb_descs.append(desc)

        hash_parts.append(struct.pack(
            "<I3f3f3f5fHHiI3f4f3f4f",
            rb.shape_type,
            *rb.size, *rb.position, *rb.rotation,
            rb.mass, rb.velocity_attenuation, rb.rotation_attenuation,
            rb.friction, rb.elasticity,
            rb.group, rb.collision_mask,
            bone_idx, rb.physics_mode,
            *bfb_pos, *bfb_rot, *bfr_pos, *bfr_rot,
        ))

    jt_descs: List[MmdRuntimeFfiPhysicsJointDesc] = []
    rb_count = len(rigid_bodies)

    for i, jt in enumerate(joints):
        # PMX uses a negative body index for joints that are placeholders
        # rather than constraints.  Match the native builder by omitting
        # those joints without turning them into a validation failure.
        if jt.rigid_body_a_index < 0 or jt.rigid_body_b_index < 0:
            continue
        jt_kind = (
            MMD_RUNTIME_PHYSICS_JOINT_KIND_GENERIC_6DOF_SPRING
            if jt.joint_type == 0
            else MMD_RUNTIME_PHYSICS_JOINT_KIND_UNSUPPORTED
        )
        errs = validate_joint_fields(
            i,
            jt_kind,
            jt.rigid_body_a_index,
            jt.rigid_body_b_index,
            rb_count,
            jt.position,
            jt.rotation,
            jt.translation_limit_min,
            jt.translation_limit_max,
            jt.rotation_limit_min,
            jt.rotation_limit_max,
            jt.spring_translation,
            jt.spring_rotation,
        )
        errors.extend(errs)
        if errs:
            # c_size_t cannot represent an invalid reference.  Keep the
            # validation error, but never emit a body-zero constraint.
            continue

        desc = MmdRuntimeFfiPhysicsJointDesc()
        desc.kind = jt_kind
        desc.rigidbody_a = jt.rigid_body_a_index
        desc.rigidbody_b = jt.rigid_body_b_index
        _set_float3(desc.position_xyz, jt.position)
        _set_float3(desc.rotation_euler_xyz, jt.rotation)
        _set_float3(desc.translation_lower_limit_xyz, jt.translation_limit_min)
        _set_float3(desc.translation_upper_limit_xyz, jt.translation_limit_max)
        _set_float3(desc.rotation_lower_limit_xyz, jt.rotation_limit_min)
        _set_float3(desc.rotation_upper_limit_xyz, jt.rotation_limit_max)
        _set_float3(desc.spring_translation_factor_xyz, jt.spring_translation)
        _set_float3(desc.spring_rotation_factor_xyz, jt.spring_rotation)

        jt_descs.append(desc)

        hash_parts.append(struct.pack(
            "<IQQ3f3f3f3f3f3f3f3f",
            jt_kind,
            max(0, jt.rigid_body_a_index),
            max(0, jt.rigid_body_b_index),
            *jt.position, *jt.rotation,
            *jt.translation_limit_min, *jt.translation_limit_max,
            *jt.rotation_limit_min, *jt.rotation_limit_max,
            *jt.spring_translation, *jt.spring_rotation,
        ))

    identity = hashlib.sha256(b"".join(hash_parts)).hexdigest()

    return PhysicsDescriptorSet(
        rigid_bodies=rb_descs,
        joints=jt_descs,
        identity_hash=identity,
        validation_errors=errors,
    )
