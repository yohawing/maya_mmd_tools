"""Canonical Maya DAG contract for rigid-body authoring colliders."""

from __future__ import annotations

from maya import cmds
import maya.api.OpenMaya as om

from mmd_tools.core.coordinate_transform import (
    mmd_euler_xyz_to_maya_quaternion,
    mmd_point_to_maya,
)
from mmd_tools.core.maya_angle import maya_angle_to_radians, radians_to_maya_angle


_FOLLOW_TAG = "mmdColliderAuthoringFollow"
_POSE_VERSION_ATTR = "mmdColliderAuthoringPoseVersion"
_CURRENT_POSE_VERSION = 4
_IDENTITY_MATRIX = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def _is_referenced(node: str) -> bool:
    """Return whether *node* belongs to a read-only referenced scene."""
    try:
        return bool(cmds.referenceQuery(node, isNodeReferenced=True))
    except (RuntimeError, ValueError):
        return False


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
    transform = om.MTransformationMatrix()
    transform.setTranslation(
        om.MVector(*mmd_point_to_maya(position, display_scale)), om.MSpace.kTransform
    )
    if legacy:
        transform.setRotation(
            om.MEulerRotation(
                rotation_radians[0],
                rotation_radians[1],
                -rotation_radians[2],
            )
        )
    else:
        transform.setRotation(
            om.MQuaternion(*mmd_euler_xyz_to_maya_quaternion(rotation_radians))
        )
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


def _authoring_follow_matrix_nodes(transform: str) -> list[str]:
    """Return tagged matrix nodes driving the collider's offset parent matrix."""
    nodes = cmds.listConnections(
        f"{transform}.offsetParentMatrix",
        source=True,
        destination=False,
        type="multMatrix",
    ) or []
    return [
        node
        for node in set(nodes)
        if cmds.attributeQuery(_FOLLOW_TAG, node=node, exists=True)
        and cmds.getAttr(f"{node}.{_FOLLOW_TAG}")
    ]


def _remove_authoring_follow_nodes(transform: str) -> None:
    """Remove both the current OPM follow graph and the legacy constraint."""
    matrix_nodes = _authoring_follow_matrix_nodes(transform)
    if matrix_nodes:
        destination = f"{transform}.offsetParentMatrix"
        for node in matrix_nodes:
            source = f"{node}.matrixSum"
            if cmds.isConnected(source, destination):
                cmds.disconnectAttr(source, destination)
        # Do not leave the previous evaluated OPM as a hidden transform offset
        # after deleting the graph.  The authored TRS values remain intact.
        cmds.setAttr(destination, _IDENTITY_MATRIX, type="matrix")
        cmds.delete(matrix_nodes)

    constraints = _authoring_follow_constraints(transform)
    if constraints:
        cmds.delete(constraints)


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


def connect_collider_authoring_follow(
    transform: str,
    shape: str,
    *,
    follow_offset: om.MMatrix | None = None,
) -> str | None:
    """Make a bound collider follow its related bone while preserving its rest offset.

    Maya's ``offsetParentMatrix`` keeps this authoring-only relationship in a
    small matrix graph instead of creating one evaluating ``parentConstraint``
    per rigid body.  ``follow_offset`` is used only by legacy-scene migration
    to preserve the already-authored relationship exactly.
    """
    connect_collider_authoring_transform(transform, shape)
    existing = _authoring_follow_matrix_nodes(transform)
    if existing:
        return existing[0]
    # Keep old scenes stable until the explicit migration path converts their
    # parentConstraint.  Creating both relationships would double-transform a
    # collider.
    existing_constraints = _authoring_follow_constraints(transform)
    if existing_constraints:
        return existing_constraints[0]

    bones = cmds.listConnections(f"{shape}.relatedBone", source=True, destination=False) or []
    if not bones or not cmds.objExists(bones[0]):
        return None

    parents = cmds.listRelatives(transform, parent=True, fullPath=True) or []
    parent_world = (
        om.MMatrix(cmds.xform(parents[0], query=True, worldSpace=True, matrix=True))
        if parents
        else om.MMatrix()
    )
    if follow_offset is None:
        bone_bind_world = _bind_pose_world_matrix(bones[0])
        position = cmds.getAttr(f"{shape}.position")[0]
        rotation_radians = maya_angle_to_radians(cmds.getAttr(f"{shape}.rotation")[0])
        display_scale = float(cmds.getAttr(f"{transform}.scaleX"))
        body_bind_local = om.MTransformationMatrix(
            _pose_matrix(position, rotation_radians, display_scale, legacy=False)
        )
        # Collider display scale is shape geometry, not part of the rigid
        # transform offset from its related bone.
        body_bind_local.setScale((1.0, 1.0, 1.0), om.MSpace.kTransform)
        body_bind_world = body_bind_local.asMatrix() * parent_world
        if bone_bind_world is None:
            bone_bind_world = om.MMatrix(
                cmds.xform(bones[0], query=True, worldSpace=True, matrix=True)
            )
        follow_offset = body_bind_world * bone_bind_world.inverse()

    if cmds.attributeQuery("offsetParentMatrix", node=transform, exists=True):
        local_matrix = om.MMatrix(
            cmds.xform(transform, query=True, objectSpace=True, matrix=True)
        )
        opm_constant = local_matrix.inverse() * follow_offset
        short_name = transform.rsplit("|", 1)[-1].replace(":", "_")
        matrix_node = None
        try:
            matrix_node = cmds.createNode(
                "multMatrix", name=f"{short_name}_authoringFollowMatrix"
            )
            cmds.setAttr(
                f"{matrix_node}.matrixIn[0]", list(opm_constant), type="matrix"
            )
            cmds.connectAttr(
                f"{bones[0]}.worldMatrix[0]",
                f"{matrix_node}.matrixIn[1]",
                force=True,
            )
            if parents:
                cmds.connectAttr(
                    f"{parents[0]}.worldInverseMatrix[0]",
                    f"{matrix_node}.matrixIn[2]",
                    force=True,
                )
            else:
                cmds.setAttr(
                    f"{matrix_node}.matrixIn[2]", _IDENTITY_MATRIX, type="matrix"
                )
            cmds.addAttr(matrix_node, longName=_FOLLOW_TAG, attributeType="bool")
            cmds.setAttr(f"{matrix_node}.{_FOLLOW_TAG}", True)
            cmds.addAttr(
                matrix_node,
                longName=_POSE_VERSION_ATTR,
                attributeType="long",
                defaultValue=_CURRENT_POSE_VERSION,
            )
            cmds.setAttr(
                f"{matrix_node}.{_POSE_VERSION_ATTR}", _CURRENT_POSE_VERSION
            )
            cmds.connectAttr(
                f"{matrix_node}.matrixSum",
                f"{transform}.offsetParentMatrix",
                force=True,
            )
            return matrix_node
        except Exception:
            if matrix_node and cmds.objExists(matrix_node):
                destination = f"{transform}.offsetParentMatrix"
                source = f"{matrix_node}.matrixSum"
                if cmds.isConnected(source, destination):
                    cmds.disconnectAttr(source, destination)
                cmds.setAttr(destination, _IDENTITY_MATRIX, type="matrix")
                cmds.delete(matrix_node)

    # Maya versions without offsetParentMatrix retain the legacy behavior.
    bone_world = om.MMatrix(
        cmds.xform(bones[0], query=True, worldSpace=True, matrix=True)
    )
    cmds.xform(transform, worldSpace=True, matrix=list(follow_offset * bone_world))
    display_scale = float(cmds.getAttr(f"{transform}.scaleX"))
    cmds.setAttr(
        f"{transform}.scale",
        display_scale,
        display_scale,
        display_scale,
        type="double3",
    )
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
    _remove_authoring_follow_nodes(transform)
    cmds.setAttr(f"{shape}.position", *position, type="double3")
    to_ui_angle = radians_to_maya_angle
    cmds.setAttr(
        f"{shape}.rotation",
        *to_ui_angle(rotation_radians),
        type="double3",
    )
    cmds.setAttr(
        f"{transform}.translate",
        *mmd_point_to_maya(position, display_scale),
        type="double3",
    )
    maya_rotation = om.MQuaternion(
        *mmd_euler_xyz_to_maya_quaternion(rotation_radians)
    ).asEulerRotation()
    cmds.setAttr(
        f"{transform}.rotate",
        *to_ui_angle(maya_rotation),
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
    if _is_referenced(transform) or _is_referenced(shape):
        return False
    pose_version = (
        int(cmds.getAttr(f"{shape}.{_POSE_VERSION_ATTR}") or 0)
        if cmds.attributeQuery(_POSE_VERSION_ATTR, node=shape, exists=True)
        else 0
    )
    constraints = _authoring_follow_constraints(transform)
    matrix_nodes = _authoring_follow_matrix_nodes(transform)
    if pose_version >= _CURRENT_POSE_VERSION and not constraints:
        return False

    position = cmds.getAttr(f"{shape}.position")[0]
    rotation_radians = maya_angle_to_radians(cmds.getAttr(f"{shape}.rotation")[0])
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

        _remove_authoring_follow_nodes(transform)
        cmds.xform(transform, worldSpace=True, matrix=list(canonical_world))
        _mark_pose_version(shape)
        connect_collider_authoring_transform(transform, shape)
        connect_collider_authoring_follow(
            transform, shape, follow_offset=canonical_offset
        )
    elif matrix_nodes:
        _mark_pose_version(shape)
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
    if _is_referenced(transform) or _is_referenced(shape):
        return
    position = cmds.getAttr(f"{shape}.position")[0]
    rotation_radians = maya_angle_to_radians(cmds.getAttr(f"{shape}.rotation")[0])
    set_collider_authoring_pose(
        transform,
        shape,
        position,
        rotation_radians,
        display_scale,
    )
