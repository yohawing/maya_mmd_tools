"""Focused standalone smoke for authored normals in mmdFastLoad."""

from __future__ import annotations

import math
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SINGLE_FIXTURE = ROOT / "tests" / "data" / "mmt_test_model.pmx"
SPLIT_FIXTURE = ROOT / "tests" / "data" / "test_morph_model.pmx"


def _plugin_path() -> Path:
    """Resolve the built C++ plugin for the selected Maya/config pair."""
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise FileNotFoundError(path)

    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    candidate = ROOT / "plug-ins" / version / config / "mmd_tools_cpp.mll"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(candidate)


def _mesh_fn(om, mesh: str):
    """Return an MFnMesh for a transform or shape path."""
    selection = om.MSelectionList()
    selection.add(mesh)
    dag_path = selection.getDagPath(0)
    if dag_path.node().hasFn(om.MFn.kTransform):
        dag_path.extendToShape()
    return om.MFnMesh(dag_path)


def _assert_locked_normals(om, mesh_fn, expected_vertex_normal=None) -> None:
    """Assert all authored normals are locked and optionally check one value."""
    _, vertex_ids = mesh_fn.getVertices()
    _, normal_ids = mesh_fn.getNormalIds()
    normals = mesh_fn.getNormals(om.MSpace.kObject)
    if len(vertex_ids) == 0 or len(vertex_ids) != len(normal_ids) or len(normals) == 0:
        raise RuntimeError("mmdFastLoad mesh has incomplete normal topology")

    for normal_id in normal_ids:
        if not mesh_fn.isNormalLocked(normal_id):
            raise RuntimeError(f"mmdFastLoad normal {normal_id} is not locked")

    if expected_vertex_normal is None:
        return
    expected_vertex, expected = expected_vertex_normal
    normal_id = None
    for vertex_id, candidate_id in zip(vertex_ids, normal_ids):
        if int(vertex_id) == expected_vertex:
            normal_id = int(candidate_id)
            break
    if normal_id is None:
        raise RuntimeError(f"mmdFastLoad mesh is missing expected vertex {expected_vertex}")

    actual = normals[normal_id]
    for axis, observed, target in zip("xyz", (actual.x, actual.y, actual.z), expected):
        if not math.isclose(float(observed), float(target), rel_tol=0.0, abs_tol=1.0e-4):
            raise RuntimeError(
                f"mmdFastLoad authored normal mismatch ({axis}): "
                f"observed={float(observed):.7f}, expected={float(target):.7f}"
            )


def _assert_vertex_normals_are_finite_and_nonzero(om, mesh_fn) -> None:
    """Reject invalid vertex normals after FastLoad authored-normal assignment."""
    vertex_normals = mesh_fn.getVertexNormals(True, om.MSpace.kObject)
    if len(vertex_normals) == 0:
        raise RuntimeError("mmdFastLoad mesh has no vertex normals")
    for vertex_id, normal in enumerate(vertex_normals):
        components = (float(normal.x), float(normal.y), float(normal.z))
        length = math.sqrt(sum(component * component for component in components))
        if not math.isfinite(length) or length <= 1.0e-12:
            raise RuntimeError(
                f"mmdFastLoad vertex normal {vertex_id} is not finite and non-zero: {components!r}"
            )


def main() -> int:
    """Run the single- and material-split authored-normal smoke checks."""
    import maya.cmds as cmds
    import maya.standalone
    from maya.api import OpenMaya as om

    plugin_path = _plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)

        single = cmds.mmdFastLoad(f=str(SINGLE_FIXTURE), n="focused_fast_normals", s=1.0)
        if not single or len(single) != 2:
            raise RuntimeError(f"single mmdFastLoad returned unexpected result: {single!r}")
        single_transform, single_mesh = single
        if cmds.polyEvaluate(single_mesh, vertex=True) != 1822 or cmds.polyEvaluate(single_mesh, face=True) != 3516:
            raise RuntimeError("single mmdFastLoad fixture counts changed")
        single_mesh_fn = _mesh_fn(om, single_mesh)
        _assert_locked_normals(
            om,
            single_mesh_fn,
            expected_vertex_normal=(0, (-0.1534272, -0.3778850, -0.9130514)),
        )
        _assert_vertex_normals_are_finite_and_nonzero(om, single_mesh_fn)
        cmds.undo()
        if cmds.objExists(single_transform):
            raise RuntimeError("single mmdFastLoad undo did not delete the transform")
        # Force Maya to tear down the post-undo DAG immediately. A DG modifier
        # deleting the transform used to leave corrupt DAG state that crashed
        # standalone during this reset on multiple Maya versions.
        cmds.file(new=True, force=True)

        split = cmds.mmdFastLoad(f=str(SPLIT_FIXTURE), n="focused_fast_split_normals", s=1.0, sp=True)
        if not split or len(split) != 1:
            raise RuntimeError(f"split mmdFastLoad returned unexpected result: {split!r}")
        split_group = split[0]
        split_meshes = cmds.listRelatives(split_group, allDescendents=True, type="mesh", fullPath=True) or []
        if len(split_meshes) != 2:
            raise RuntimeError(f"split mmdFastLoad expected 2 meshes, got {split_meshes!r}")

        signs = []
        for mesh in split_meshes:
            if cmds.polyEvaluate(mesh, vertex=True) != 4 or cmds.polyEvaluate(mesh, face=True) != 2:
                raise RuntimeError(f"split mmdFastLoad fixture counts changed: {mesh}")
            mesh_fn = _mesh_fn(om, mesh)
            _assert_locked_normals(om, mesh_fn)
            _assert_vertex_normals_are_finite_and_nonzero(om, mesh_fn)
            normals = mesh_fn.getNormals(om.MSpace.kObject)
            average_z = sum(float(normal.z) for normal in normals) / len(normals)
            if not math.isclose(abs(average_z), 1.0, rel_tol=0.0, abs_tol=1.0e-4):
                raise RuntimeError(f"split authored normal value mismatch: {mesh}")
            signs.append(1 if average_z > 0.0 else -1)
        if sorted(signs) != [-1, 1]:
            raise RuntimeError(f"split authored normal signs mismatch: {signs!r}")

        # The split command creates a group plus child meshes; explicit delete
        # avoids Maya 2024 standalone undo selection crashes while preserving
        # the single-mesh command undo assertion above.
        cmds.delete(split_group)
        if cmds.objExists(split_group):
            raise RuntimeError("split mmdFastLoad cleanup did not delete the group")
        print("OK: focused mmdFastLoad authored normals (single + split)")
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
