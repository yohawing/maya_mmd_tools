"""Canonical Maya DAG contract for rigid-body authoring colliders."""

from __future__ import annotations

import math

from maya import cmds


def connect_collider_authoring_transform(transform: str, shape: str) -> None:
    """Drive the parent draw transform from the persisted PMX pose fields."""
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
        if not cmds.isConnected(source, destination):
            cmds.connectAttr(source, destination, force=True)
    matrix_source = f"{transform}.worldMatrix[0]"
    matrix_destination = f"{shape}.authoringMatrix"
    if not cmds.isConnected(matrix_source, matrix_destination):
        cmds.connectAttr(matrix_source, matrix_destination, force=True)


def set_collider_authoring_pose(
    transform: str,
    shape: str,
    position,
    rotation_radians,
) -> None:
    """Set a PMX pose on the parent transform and establish its shape outputs."""
    cmds.setAttr(f"{shape}.position", *position, type="double3")
    cmds.setAttr(
        f"{shape}.rotation",
        *(math.degrees(value) for value in rotation_radians),
        type="double3",
    )
    connect_collider_authoring_transform(transform, shape)
