"""Canonical Maya DAG contract for rigid-body authoring colliders."""

from __future__ import annotations

import math

from maya import cmds
import maya.api.OpenMaya as om

from mmd_tools.core.coordinate_transform import (
    mmd_euler_xyz_to_maya,
    mmd_euler_radians_to_maya_degrees,
    mmd_point_to_maya,
)


_FOLLOW_TAG = "mmdColliderAuthoringFollow"
_POSE_VERSION_ATTR = "mmdColliderAuthoringPoseVersion"
_CURRENT_POSE_VERSION = 3


def _mark_pose_version(shape: str) -> None:
    if not cmds.attributeQuery(_POSE_VERSION_ATTR, node=shape, exists=True):
        cmds.addAttr(
            shape,
            longName=_POSE_VERSION_ATTR,
            attributeType="long",
            defaultValue=_CURRENT_POSE_VERSION,
        )
    cmds.setAttr(f"{shape}.{_POSE_VERSION_ATTR}", _CURRENT_POSE_VERSION)


def _pose_matrix(position, rotation_radians, display_scale: float, *, legacy: bool) -> om.MMatrix:
    rotation = (
        (rotation_radians[0], rotation_radians[1], -rotation_radians[2])
        if legacy
        else mmd_euler_xyz_to_maya(rotation_radians)
    )
    transform = om.MTransformationMatrix()
    transform.setTranslation(
        om.MVector(*mmd_point_to_maya(position, display_scale)), om.MSpace.kTransform
    )
    transform.setRotation(om.MEulerRotation(*rotation))
    transform.setScale((display_scale, display_scale, display_scale), om.MSpace.kTransform)
    return transform.asMatrix()


def _authoring_follow_constraints(transform: str) -> list[str]:
    constraints = set()
    for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
        constraints.update(
            cmds.listConnections(
                f"{transform}.{attr}", source=True, destination=False, type="parentConstraint"
            )
            or []
        )
    return [
        constraint
        for constraint in constraints
        if cmds.attributeQuery(_FOLLOW_TAG, node=constraint, exists=True)
        and cmds.getAttr(f"{constraint}.{_FOLLOW_TAG}")
    ]


def _bind_pose_world_matrix(node: str) -> om.MMatrix | None:
    """Read *node*'s saved bind world matrix without changing Maya time."""
    bind_poses = set(cmds.dagPose(node, query=True, bindPose=True) or [])
    if not bind_poses or not cmds.attributeQuery("bindPose", node=node, exists=True):
        return None
    plugs = cmds.listConnections(
        f"{node}.bindPose",
        source=False,
        destination=True,
        type="dagPose",
        plugs=True,
    ) or []
    for plug in plugs:
        if plug.split(".", 1)[0] in bind_poses and ".worldMatrix[" in plug:
            return om.MMatrix(cmds.getAttr(plug))
    return None


def _has_keyed_transform_in_hierarchy(node: str) -> bool:
    """Return whether *node* or a DAG parent has keyed transform channels."""
    hierarchy = [node]
    hierarchy.extend(cmds.listRelatives(node, allParents=True, fullPath=True) or [])
    return any((cmds.keyframe(item, query=True, keyframeCount=True) or 0) > 0 for item in hierarchy)


def connect_collider_authoring_transform(transform: str, shape: str) -> None:
    """Connect the canonical parent world matrix without mixing PMX and Maya spaces."""
    for shape_attr, transform_attr in (
        ("positionX", "translateX"),
        ("positionY", "translateY"),
        ("positionZ", "translateZ"),
        ("rotationX", "rotateX"),
        ("rotationY", "rotateY"),
        ("rotationZ", "rotateZ"),
    ):
        source = f"{shape}.{shape_attr}"
        destination = f"{transform}.{transform_attr}"
        if cmds.isConnected(source, destination):
            cmds.disconnectAttr(source, destination)
    matrix_source = f"{transform}.worldMatrix[0]"
    matrix_destination = f"{shape}.authoringMatrix"
    if not cmds.isConnected(matrix_source, matrix_destination):
        cmds.connectAttr(matrix_source, matrix_destination, force=True)


def connect_collider_authoring_follow(transform: str, shape: str) -> str | None:
    """Make a bound collider follow its related bone while preserving its rest offset."""
    connect_collider_authoring_transform(transform, shape)
    existing = _authoring_follow_constraints(transform)
    if existing:
        return existing[0]

    bones = cmds.listConnections(f"{shape}.relatedBone", source=True, destination=False) or []
    if not bones or not cmds.objExists(bones[0]):
        return None

    bone_bind_world = _bind_pose_world_matrix(bones[0])
    if bone_bind_world is not None:
        # set_collider_authoring_pose has already placed the raw PMX body pose
        # below its Physics hierarchy.  Read that world matrix so the body and
        # saved bone bind matrices use the same space, including non-identity
        # model and Physics parents.
        body_bind_world = om.MMatrix(
            cmds.xform(transform, query=True, worldSpace=True, matrix=True)
        )
        bone_world = om.MMatrix(
            cmds.xform(bones[0], query=True, worldSpace=True, matrix=True)
        )
        if _has_keyed_transform_in_hierarchy(bones[0]):
            body_from_bone = body_bind_world * bone_bind_world.inverse()
            cmds.xform(transform, worldSpace=True, matrix=list(body_from_bone * bone_world))

    short_name = transform.rsplit("|", 1)[-1].replace(":", "_")
    constraint = cmds.parentConstraint(
        bones[0],
        transform,
        maintainOffset=True,
        name=f"{short_name}_authoringFollowConstraint",
    )[0]
    cmds.addAttr(constraint, longName=_FOLLOW_TAG, attributeType="bool")
    cmds.setAttr(f"{constraint}.{_FOLLOW_TAG}", True)
    cmds.addAttr(
        constraint,
        longName=_POSE_VERSION_ATTR,
        attributeType="long",
        defaultValue=_CURRENT_POSE_VERSION,
    )
    cmds.setAttr(f"{constraint}.{_POSE_VERSION_ATTR}", _CURRENT_POSE_VERSION)
    return constraint


def set_collider_authoring_pose(
    transform: str,
    shape: str,
    position,
    rotation_radians,
    display_scale: float = 1.0,
) -> None:
    """Persist a raw PMX pose and place the display transform in Maya space."""
    existing_constraints = _authoring_follow_constraints(transform)
    if existing_constraints:
        cmds.delete(existing_constraints)
    cmds.setAttr(f"{shape}.position", *position, type="double3")
    cmds.setAttr(
        f"{shape}.rotation",
        *(math.degrees(value) for value in rotation_radians),
        type="double3",
    )
    cmds.setAttr(
        f"{transform}.translate",
        *mmd_point_to_maya(position, display_scale),
        type="double3",
    )
    cmds.setAttr(
        f"{transform}.rotate",
        *mmd_euler_radians_to_maya_degrees(rotation_radians),
        type="double3",
    )
    cmds.setAttr(
        f"{transform}.scale",
        display_scale,
        display_scale,
        display_scale,
        type="double3",
    )
    _mark_pose_version(shape)
    connect_collider_authoring_transform(transform, shape)
    connect_collider_authoring_follow(transform, shape)


def migrate_legacy_collider_authoring_pose(
    transform: str,
    shape: str,
    display_scale: float = 1.0,
) -> bool:
    """Upgrade a stored legacy display pose while preserving its live bone offset."""
    if (
        cmds.attributeQuery(_POSE_VERSION_ATTR, node=shape, exists=True)
        and cmds.getAttr(f"{shape}.{_POSE_VERSION_ATTR}") >= _CURRENT_POSE_VERSION
    ):
        return False

    position = cmds.getAttr(f"{shape}.position")[0]
    rotation_radians = tuple(
        math.radians(value) for value in cmds.getAttr(f"{shape}.rotation")[0]
    )
    constraints = _authoring_follow_constraints(transform)
    bones = cmds.listConnections(f"{shape}.relatedBone", source=True, destination=False) or []
    if constraints and bones and cmds.objExists(bones[0]):
        collider_world = om.MMatrix(
            cmds.xform(transform, query=True, worldSpace=True, matrix=True)
        )
        bone_world = om.MMatrix(cmds.xform(bones[0], query=True, worldSpace=True, matrix=True))
        old_offset = collider_world * bone_world.inverse()
        legacy_rest = _pose_matrix(position, rotation_radians, display_scale, legacy=True)
        canonical_rest = _pose_matrix(position, rotation_radians, display_scale, legacy=False)
        canonical_offset = canonical_rest * legacy_rest.inverse() * old_offset
        canonical_world = canonical_offset * bone_world

        cmds.delete(constraints)
        cmds.xform(transform, worldSpace=True, matrix=list(canonical_world))
        _mark_pose_version(shape)
        connect_collider_authoring_transform(transform, shape)
        connect_collider_authoring_follow(transform, shape)
    else:
        set_collider_authoring_pose(
            transform,
            shape,
            position,
            rotation_radians,
            display_scale,
        )
    return True


def refresh_collider_authoring_pose(
    transform: str,
    shape: str,
    display_scale: float = 1.0,
) -> None:
    """Reapply a stored PMX pose after import-time graph connections settle."""
    position = cmds.getAttr(f"{shape}.position")[0]
    rotation_degrees = cmds.getAttr(f"{shape}.rotation")[0]
    set_collider_authoring_pose(
        transform,
        shape,
        position,
        tuple(math.radians(value) for value in rotation_degrees),
        display_scale,
    )
