"""Focused Maya standalone smoke for the C++ fast skeleton/skin path."""

from __future__ import annotations

import os
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube.pmx"


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


def main() -> int:
    """Import one skinned PMX and verify the per-deformer normal policy."""
    import maya.cmds as cmds
    import maya.standalone
    from maya.api import OpenMaya as om

    plugin_path = _plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    root = None
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        from mmd_tools.core import maya_mesh_utils
        from mmd_tools.io.cpp_fast_importer import fast_import

        root = fast_import(
            str(MODEL),
            base_name="focused_fast_import_skin",
            scale=1.0,
            mesh_only=False,
            include_morphs=False,
        )
        if not root or not cmds.objExists(root):
            raise RuntimeError(f"fast_import(mesh_only=False) returned no root: {root!r}")

        mesh_shapes = cmds.listRelatives(
            root, allDescendents=True, type="mesh", fullPath=True
        ) or []
        if not mesh_shapes:
            raise RuntimeError(f"fast_import created no mesh shape under {root}")

        history = cmds.listHistory(mesh_shapes[0], pruneDagObjects=True) or []
        skin_clusters = [node for node in history if cmds.nodeType(node) == "skinCluster"]
        if len(skin_clusters) != 1:
            raise RuntimeError(f"expected one fast skinCluster, got {skin_clusters!r}")
        skin_cluster = skin_clusters[0]

        selection = om.MSelectionList()
        selection.add(mesh_shapes[0])
        mesh_fn = om.MFnMesh(selection.getDagPath(0))
        _, normal_ids = mesh_fn.getNormalIds()
        normals = mesh_fn.getNormals(om.MSpace.kObject)
        if not normal_ids:
            raise RuntimeError("fast skin fixture has no face-vertex normal IDs")
        first_normal_id = int(normal_ids[0])
        expected = (-0.8164966, -0.4082483, 0.4082483)
        actual = normals[first_normal_id]
        for axis, observed, target in zip("xyz", (actual.x, actual.y, actual.z), expected):
            if not math.isclose(float(observed), target, rel_tol=0.0, abs_tol=1.0e-4):
                raise RuntimeError(
                    f"fast skin authored normal mismatch ({axis}): "
                    f"observed={float(observed):.7f}, expected={target:.7f}"
                )
        if not mesh_fn.isNormalLocked(first_normal_id):
            raise RuntimeError("fast skin authored normal is not locked")

        if not bool(cmds.getAttr(f"{skin_cluster}.deformUserNormals")):
            raise RuntimeError("fast skinCluster deformUserNormals is not enabled")

        normal_difference = maya_mesh_utils.has_materially_different_authored_normals(
            mesh_shapes[0]
        )
        if not normal_difference:
            raise RuntimeError("fast skin fixture lost its known authored-normal difference")
        block_gpu = bool(cmds.getAttr(f"{skin_cluster}.blockGPU"))
        if block_gpu != bool(normal_difference):
            raise RuntimeError(
                "fast skinCluster blockGPU policy mismatch: "
                f"expected={bool(normal_difference)} observed={block_gpu}"
            )

        print(
            "OK: fast_import skeleton/skin authored-normal policy "
            f"(deformUserNormals=True, blockGPU={block_gpu})"
        )
        return 0
    finally:
        if root and cmds.objExists(root):
            cmds.delete(root)
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
