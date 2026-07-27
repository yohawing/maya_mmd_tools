"""Joint rotation helpers for VMD conversion."""

from __future__ import annotations

import math
from typing import Any, Iterable, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds


def unwrap_euler_sequence(
    euler_angles: Iterable[Tuple[float, float, float]],
    rotate_order: int = 0,
) -> List[Tuple[float, float, float]]:
    """Return a degree-valued Euler sequence with continuous nearest branches.

    Maya stores each Euler channel as an angle in degrees, while quaternion
    decomposition chooses a canonical branch independently for every sample.
    For dense VMD samples that can turn a physically small ``179 -> -179``
    step into a 358-degree Euler jump.  This helper keeps the first sample
    unchanged and adds the nearest multiple of 360 degrees to each subsequent
    axis relative to the previously unwrapped sample.

    Args:
        euler_angles: Euler triples in chronological order, expressed in
            degrees.

    Returns:
        A new list of Euler triples.  The input iterable is never modified.
    """
    unwrapped: List[Tuple[float, float, float]] = []
    order_map = {
        0: om.MEulerRotation.kXYZ,
        1: om.MEulerRotation.kYZX,
        2: om.MEulerRotation.kZXY,
        3: om.MEulerRotation.kXZY,
        4: om.MEulerRotation.kYXZ,
        5: om.MEulerRotation.kZYX,
    }
    maya_order = order_map.get(int(rotate_order), om.MEulerRotation.kXYZ)
    previous: Optional[om.MEulerRotation] = None

    for angles in euler_angles:
        current = tuple(float(value) for value in angles)
        if len(current) != 3:
            raise ValueError("Euler samples must contain exactly three angles")

        current_euler = om.MEulerRotation(
            math.radians(current[0]),
            math.radians(current[1]),
            math.radians(current[2]),
            maya_order,
        )
        if previous is not None:
            current_euler.setToClosestSolution(previous)
        current = tuple(
            math.degrees(value)
            for value in (current_euler.x, current_euler.y, current_euler.z)
        )
        unwrapped.append(current)
        previous = current_euler

    return unwrapped


def extract_euler_from_matrix(m: om.MMatrix, rotate_order: int) -> Tuple[float, float, float]:
    """Extract Euler angles in degrees for a Maya rotateOrder value."""
    try:
        tm = om.MTransformationMatrix(m)
        q = tm.rotation(asQuaternion=True)
        order_map = {
            0: om.MEulerRotation.kXYZ,
            1: om.MEulerRotation.kYZX,
            2: om.MEulerRotation.kZXY,
            3: om.MEulerRotation.kXZY,
            4: om.MEulerRotation.kYXZ,
            5: om.MEulerRotation.kZYX,
        }
        order = order_map.get(rotate_order, om.MEulerRotation.kXYZ)
        e = q.asEulerRotation()
        if e.order != order:
            e.reorderIt(order)
        return (math.degrees(e.x), math.degrees(e.y), math.degrees(e.z))
    except Exception:
        return (0.0, 0.0, 0.0)


def get_joint_orient_cache(converter: Any, joint_name: str) -> Tuple[Optional[om.MQuaternion], int]:
    """Return a cached jointOrient quaternion and rotateOrder for a joint."""
    if not hasattr(converter, "_joint_orient_cache"):
        converter._joint_orient_cache = {}
    cached = converter._joint_orient_cache.get(joint_name)
    if cached is not None:
        return cached

    joint_orient = cmds.getAttr(f"{joint_name}.jointOrient")[0]
    rotate_order = int(cmds.getAttr(f"{joint_name}.rotateOrder"))

    if any(abs(v) > 1e-8 for v in joint_orient):
        q_jo = om.MEulerRotation(
            math.radians(joint_orient[0]),
            math.radians(joint_orient[1]),
            math.radians(joint_orient[2]),
        ).asQuaternion()
    else:
        q_jo = None

    rotate_axis = cmds.getAttr(f"{joint_name}.rotateAxis")[0]
    if any(abs(v) > 1e-8 for v in rotate_axis):
        converter.logger.warning(
            f"{joint_name} has non-zero rotateAxis ({rotate_axis})."
            "Legacy path does not support rotateAxis; rotation accuracy may be reduced"
        )

    result = (q_jo, rotate_order)
    converter._joint_orient_cache[joint_name] = result
    return result


def convert_vmd_quat_to_joint_rotate(
    converter: Any,
    joint_name: str,
    qx: float,
    qy: float,
    qz: float,
    qw: float,
) -> Tuple[float, float, float]:
    """Convert a VMD quaternion into Maya joint.rotate Euler angles in degrees."""
    q_maya = om.MQuaternion(-float(qx), -float(qy), float(qz), float(qw))

    q_jo, rotate_order = get_joint_orient_cache(converter, joint_name)
    q_rotate = convert_vmd_quat_to_bind_space_rotate(converter, joint_name, q_maya, q_jo)

    euler = q_rotate.asEulerRotation()
    euler.reorderIt(rotate_order)
    return (math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z))


def convert_vmd_quat_to_bind_space_rotate(
    converter: Any,
    joint_name: str,
    q_maya: om.MQuaternion,
    q_jo: Optional[om.MQuaternion],
) -> om.MQuaternion:
    """Convert a sparse VMD local rotation into this joint's JO-aware rotate space."""
    bone_index = None
    for idx, joint in getattr(converter, "bone_index_to_joint", {}).items():
        if joint == joint_name:
            bone_index = idx
            break
    if bone_index is None:
        if q_jo is not None:
            return q_jo * q_maya * q_jo.inverse()
        return q_maya

    if not hasattr(converter, "_runtime_bind_world_matrices"):
        try:
            converter._build_runtime_bind_world_maps()
        except Exception:
            pass

    bind_world = getattr(converter, "_runtime_bind_world_matrices", {}).get(bone_index)
    bind_no_orient = getattr(converter, "_runtime_no_orient_bind_world_matrices", {}).get(bone_index)
    if bind_world is None or bind_no_orient is None:
        if q_jo is not None:
            return q_jo * q_maya * q_jo.inverse()
        return q_maya

    parent_index = getattr(converter, "_bone_parent_map", {}).get(bone_index)
    parent_bind_world = getattr(converter, "_runtime_bind_world_matrices", {}).get(parent_index, om.MMatrix())
    parent_bind_no_orient = getattr(converter, "_runtime_no_orient_bind_world_matrices", {}).get(
        parent_index,
        om.MMatrix(),
    )

    try:
        no_orient_local = bind_no_orient * parent_bind_no_orient.inverse()
        local_translation = om.MTransformationMatrix(no_orient_local).translation(om.MSpace.kTransform)
        local_tfm = om.MTransformationMatrix()
        local_tfm.setTranslation(local_translation, om.MSpace.kTransform)
        local_tfm.setRotation(q_maya)
        local_no_orient = local_tfm.asMatrix()
        local_total = (
            bind_world
            * bind_no_orient.inverse()
            * local_no_orient
            * parent_bind_no_orient
            * parent_bind_world.inverse()
        )
        q_total = om.MTransformationMatrix(local_total).rotation(asQuaternion=True)
        return q_total * q_jo.inverse() if q_jo is not None else q_total
    except Exception:
        if q_jo is not None:
            return q_jo * q_maya * q_jo.inverse()
        return q_maya
