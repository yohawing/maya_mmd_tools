"""Smoke test for loading the C++ plugin in Maya.

This script intentionally has no pytest dependency. It is launched by mayapy
from Nox or by hand, initializes Maya standalone, loads the compiled plugin,
and verifies that the mmdRuntimeInstance node and mmdFastLoad command work.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NODE_TYPE = "mmdRuntimeInstance"
FAST_LOAD_MODEL = ROOT / "tests" / "data" / "mmt_test_model.pmx"
FAST_IMPORT_SKIN_MODEL = ROOT / "tests" / "data" / "for_unit_test" / "test_1bone_cube.pmx"
FAST_LOAD_MORPH_MODEL = ROOT / "tests" / "data" / "Lumine" / "Lumine.pmx"


def _candidate_plugin_paths() -> list[Path]:
    """Return possible C++ plugin artifact paths."""
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        return [Path(explicit)]

    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Debug")
    extensions = [".mll", ".bundle", ".so"]
    configs = [config]
    if config != "Release":
        configs.append("Release")
    if config != "Debug":
        configs.append("Debug")

    paths: list[Path] = []
    for cfg in configs:
        for suffix in extensions:
            paths.append(ROOT / "plug-ins" / version / cfg / f"mmd_tools_cpp{suffix}")
    return paths


def _find_plugin_path() -> Path:
    """Find the compiled C++ plugin artifact."""
    for path in _candidate_plugin_paths():
        if path.exists():
            return path

    candidates = "\n".join(str(path) for path in _candidate_plugin_paths())
    raise FileNotFoundError(f"mmd_tools_cpp plugin was not found. Checked:\n{candidates}")


def main() -> int:
    """Run the Maya standalone smoke check."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import maya.cmds as cmds
    import maya.standalone

    plugin_path = _find_plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        node = cmds.createNode(NODE_TYPE)
        if not cmds.objExists(node):
            raise RuntimeError(f"Failed to create node: {NODE_TYPE}")

        for attr in ("time", "pmxData", "vmdData", "worldMatrices", "morphWeights", "ikEnabled"):
            if not cmds.attributeQuery(attr, node=node, exists=True):
                raise RuntimeError(f"Missing attribute {attr!r} on {node}")

        print(f"OK: loaded {plugin_path}")
        print(f"OK: created {node} ({NODE_TYPE})")

        result = cmds.mmdFastLoad(f=str(FAST_LOAD_MODEL), n="mmt_fast_smoke", s=1.0)
        if not result or len(result) != 2:
            raise RuntimeError(f"mmdFastLoad returned unexpected result: {result!r}")

        transform, mesh = result
        if not cmds.objExists(transform) or not cmds.objExists(mesh):
            raise RuntimeError(f"mmdFastLoad result nodes do not exist: {result!r}")

        vertex_count = cmds.polyEvaluate(mesh, vertex=True)
        face_count = cmds.polyEvaluate(mesh, face=True)
        if vertex_count <= 0 or face_count <= 0:
            raise RuntimeError(
                f"mmdFastLoad created empty mesh: vertices={vertex_count}, faces={face_count}"
            )

        cmds.undo()
        if cmds.objExists(transform):
            raise RuntimeError(f"mmdFastLoad undo did not delete transform: {transform}")

        print(f"OK: mmdFastLoad created {vertex_count} vertices / {face_count} faces and undo succeeded")

        morph_result = cmds.mmdFastLoad(f=str(FAST_LOAD_MORPH_MODEL), n="mmd_fast_morph_smoke", s=1.0, mo=True)
        if not morph_result or len(morph_result) != 2:
            raise RuntimeError(f"mmdFastLoad morph smoke returned unexpected result: {morph_result!r}")
        morph_transform, _morph_mesh = morph_result
        blend_shapes = cmds.ls(type="blendShape") or []
        if not blend_shapes:
            raise RuntimeError("mmdFastLoad(morphs=True) did not create a blendShape")
        weight_count = cmds.blendShape(blend_shapes[0], query=True, weightCount=True) or 0
        if int(weight_count) <= 0:
            raise RuntimeError(f"mmdFastLoad(morphs=True) blendShape has no weights: {blend_shapes[0]}")
        cmds.delete(morph_transform)
        print(f"OK: mmdFastLoad(morphs=True) created {int(weight_count)} vertex morph target(s)")

        from mmd_tools.io.cpp_fast_importer import fast_import

        root = fast_import(
            str(FAST_IMPORT_SKIN_MODEL),
            base_name="fast_import_skin_smoke",
            scale=1.0,
            mesh_only=False,
        )
        if not root or not cmds.objExists(root):
            raise RuntimeError(f"fast_import(mesh_only=False) did not create a root: {root!r}")

        joints = cmds.ls(type="joint") or []
        skins = cmds.ls(type="skinCluster") or []
        if not joints:
            raise RuntimeError("fast_import(mesh_only=False) did not create joints")
        if not skins:
            raise RuntimeError("fast_import(mesh_only=False) did not create a skinCluster")

        mesh_shapes = cmds.listRelatives(root, shapes=True, type="mesh") or []
        if not mesh_shapes:
            raise RuntimeError(f"fast_import(mesh_only=False) created no mesh shapes under {root}")
        weights = cmds.skinPercent(skins[0], f"{mesh_shapes[0]}.vtx[0]", query=True, value=True)
        if not weights or abs(sum(weights) - 1.0) > 0.0001:
            raise RuntimeError(f"fast_import skin weights are invalid: {weights!r}")

        cmds.delete(root)
        print(
            f"OK: fast_import(mesh_only=False) created {len(joints)} joints, "
            f"{len(skins)} skinCluster(s), and normalized weights"
        )
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
