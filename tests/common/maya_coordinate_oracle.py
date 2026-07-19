"""Independent Maya-space coordinate oracles for integration and capture tests."""

from __future__ import annotations

import math
from collections.abc import Sequence

import maya.api.OpenMaya as om
from maya import cmds


def reflected_mmd_euler_matrix(euler_xyz: Sequence[float]) -> om.MMatrix:
    """Return the reflected PMX Euler matrix without production helpers."""
    half_x, half_y, half_z = (float(value) * 0.5 for value in euler_xyz)
    sin_x, cos_x = math.sin(half_x), math.cos(half_x)
    sin_y, cos_y = math.sin(half_y), math.cos(half_y)
    sin_z, cos_z = math.sin(half_z), math.cos(half_z)
    return om.MQuaternion(
        -(sin_x * cos_y * cos_z + cos_x * sin_y * sin_z),
        -(cos_x * sin_y * cos_z - sin_x * cos_y * sin_z),
        cos_x * cos_y * sin_z - sin_x * sin_y * cos_z,
        cos_x * cos_y * cos_z + sin_x * sin_y * sin_z,
    ).asMatrix()


def saved_bind_pose_world_matrix(node: str) -> om.MMatrix:
    """Return a joint's persisted Maya bind-world matrix for follow oracles."""
    bind_poses = set(cmds.dagPose(node, query=True, bindPose=True) or [])
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
    raise AssertionError(f"No saved bind-world matrix found for {node}")
