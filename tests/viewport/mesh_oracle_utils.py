"""Shared Maya mesh helpers for viewport and mesh oracle scripts."""

from __future__ import annotations

import math
from typing import Any, Sequence

import maya.api.OpenMaya as om
import maya.cmds as cmds


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Return the Euclidean distance between two 3D points."""
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def bbox(points: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Return a rounded bounding box summary for 3D points."""
    if not points:
        return {"min": [], "max": [], "center": [], "diag": 0.0}
    mins = [min(float(point[i]) for point in points) for i in range(3)]
    maxs = [max(float(point[i]) for point in points) for i in range(3)]
    center = [(mins[i] + maxs[i]) * 0.5 for i in range(3)]
    return {
        "min": [round(value, 6) for value in mins],
        "max": [round(value, 6) for value in maxs],
        "center": [round(value, 6) for value in center],
        "diag": round(distance(mins, maxs), 6),
    }


def node_is_visible(node: str) -> bool:
    """Return False when a node or any parent has visibility disabled."""
    current = node
    while current:
        try:
            if cmds.attributeQuery("visibility", node=current, exists=True) and not cmds.getAttr(f"{current}.visibility"):
                return False
        except Exception:
            pass
        parent = cmds.listRelatives(current, parent=True, fullPath=True) or []
        current = parent[0] if parent else ""
    return True


def has_skin_cluster(mesh_transform: str) -> bool:
    """Return whether a mesh transform has skinCluster history."""
    history = cmds.listHistory(mesh_transform, pruneDagObjects=True) or []
    return any(cmds.nodeType(node) == "skinCluster" for node in history)


def visible_mesh_transforms(
    root: str,
    *,
    require_skin_cluster: bool = False,
    prefer_skin_cluster: bool = False,
) -> list[str]:
    """Return visible mesh transforms under root with optional skinCluster filtering."""
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    transforms: list[str] = []
    skinned: list[str] = []
    for shape in shapes:
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        if not node_is_visible(shape):
            continue
        parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if not parent or not node_is_visible(parent[0]) or parent[0] in transforms:
            continue
        is_skinned = has_skin_cluster(parent[0])
        if require_skin_cluster and not is_skinned:
            continue
        transforms.append(parent[0])
        if is_skinned:
            skinned.append(parent[0])
    return sorted(skinned if prefer_skin_cluster and skinned else transforms)


def source_indices(mesh: str) -> list[int]:
    """Return PMX source vertex indices for a Maya mesh transform."""
    from mmd_tools.core import maya_attribute_utils
    from mmd_tools.core.constants import ATTR_MMD_SOURCE_VERTEX_INDICES

    if cmds.attributeQuery(ATTR_MMD_SOURCE_VERTEX_INDICES, node=mesh, exists=True):
        return list(maya_attribute_utils.get_int_array_attribute(mesh, ATTR_MMD_SOURCE_VERTEX_INDICES))
    return list(range(int(cmds.polyEvaluate(mesh, vertex=True))))


def mesh_points(mesh_transform: str) -> list[tuple[float, float, float]]:
    """Return world-space points for visible non-intermediate shapes on a mesh transform."""
    shapes = cmds.listRelatives(mesh_transform, shapes=True, noIntermediate=True, fullPath=True) or []
    points: list[tuple[float, float, float]] = []
    for shape in shapes:
        sel = om.MSelectionList()
        sel.add(shape)
        fn = om.MFnMesh(sel.getDagPath(0))
        points.extend((float(p.x), float(p.y), float(p.z)) for p in fn.getPoints(om.MSpace.kWorld))
    return points


def mesh_points_under_root(root: str) -> list[tuple[float, float, float]]:
    """Return world-space points for non-intermediate mesh shapes under root."""
    points: list[tuple[float, float, float]] = []
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        sel = om.MSelectionList()
        sel.add(shape)
        fn = om.MFnMesh(sel.getDagPath(0))
        points.extend((float(p.x), float(p.y), float(p.z)) for p in fn.getPoints(om.MSpace.kWorld))
    return points
