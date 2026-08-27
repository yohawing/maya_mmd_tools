"""Focused Maya standalone smoke for the Python-callable C++ UV weld command."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(
    os.environ.get(
        "MMD_UV_WELD_MODEL",
        str(ROOT / "tests" / "data" / "mmt_test_model.pmx"),
    )
)
PYTHON_PLUGIN = ROOT / "mmd_tools" / "plugin_main.py"


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


def _write_morph_equivalence_fixture(path: Path) -> None:
    """Write seam copies whose indexed morph deltas are equal or conflicting."""
    from mmd_tools.io.pmx_exporter import PmxExporter

    vertices = [
        {
            "position": position,
            "normal": [0.0, 0.0, 1.0],
            "uv": uv,
            "additional_uvs": [[0.0, 0.0, 0.0, 0.0]],
        }
        for position, uv in (
            ([0.0, 0.0, 0.0], [0.0, 0.0]),
            ([0.0, 0.0, 0.0], [1.0, 0.0]),
            ([2.0, 0.0, 0.0], [0.0, 0.0]),
            ([2.0, 0.0, 0.0], [1.0, 0.0]),
            ([0.0, 1.0, 0.0], [0.0, 1.0]),
            ([1.0, 1.0, 0.0], [1.0, 1.0]),
        )
    ]
    PmxExporter(native_parts_exporter=None).export_pmx_model(
        str(path),
        {
            "model_name": "NativeMorphEquivalentWeld",
            "vertices": vertices,
            "faces": [[0, 4, 5], [1, 4, 5], [2, 4, 5], [3, 4, 5]],
            "morphs": [
                {
                    "type": "vertex",
                    "name": "vertex",
                    "offsets": [
                        {"vertex_index": 0, "position_offset": [0.0, 0.5, 0.0]},
                        {"vertex_index": 1, "position_offset": [0.0, 0.5, 0.0]},
                        {"vertex_index": 2, "position_offset": [0.0, 0.5, 0.0]},
                        {"vertex_index": 3, "position_offset": [0.0, 1.0, 0.0]},
                    ],
                },
                {
                    "type": "uv",
                    "name": "uv",
                    "offsets": [
                        {"vertex_index": 0, "uv_offset": [0.1, 0.0, 0.0, 0.0]},
                        {"vertex_index": 1, "uv_offset": [0.1, 0.0, 0.0, 0.0]},
                    ],
                },
                {
                    "type": "additional_uv1",
                    "name": "additional_uv",
                    "offsets": [
                        {"vertex_index": 0, "uv_offset": [0.0, 0.2, 0.0, 0.0]},
                        {"vertex_index": 1, "uv_offset": [0.0, 0.2, 0.0, 0.0]},
                    ],
                },
            ],
        },
    )


def main() -> int:
    """Create a real PMX mesh, weld it through C++, and verify topology/UVs."""
    import maya.cmds as cmds
    import maya.standalone
    from maya.api import OpenMaya as om

    plugin_path = _plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    dll_handle = None
    if hasattr(os, "add_dll_directory"):
        dll_handle = os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        if not hasattr(cmds, "mmdWeldUvSeamVertices"):
            raise RuntimeError("mmdWeldUvSeamVertices was not registered")
        capabilities = cmds.mmdWeldUvSeamVertices(queryCapabilities=True)
        for capability in ("sourceToLocalV1", "morphEquivalentV1"):
            if capability not in capabilities:
                raise RuntimeError(f"mmdWeldUvSeamVertices lacks {capability}")

        loaded = cmds.mmdFastLoad(f=str(FIXTURE), n="focused_uv_weld", s=1.0)
        if not loaded or len(loaded) != 2:
            raise RuntimeError(f"mmdFastLoad returned unexpected result: {loaded!r}")
        transform, mesh = loaded
        mesh_fn = _mesh_fn(om, mesh)
        old_vertex_count = int(cmds.polyEvaluate(mesh, vertex=True))
        old_face_count = int(cmds.polyEvaluate(mesh, face=True))
        old_uv_counts, old_uv_ids = mesh_fn.getAssignedUVs()

        result = cmds.mmdWeldUvSeamVertices(m=transform, f=str(FIXTURE))
        if not isinstance(result, (list, tuple)) or len(result) != 3:
            raise RuntimeError(f"mmdWeldUvSeamVertices returned unexpected result: {result!r}")
        reported_old = int(result[1])
        reported_new = int(result[2])
        actual_new = int(cmds.polyEvaluate(transform, vertex=True))
        actual_faces = int(cmds.polyEvaluate(transform, face=True))
        if reported_old != old_vertex_count or reported_new != actual_new:
            raise RuntimeError(
                f"weld count mismatch: result={result!r}, before={old_vertex_count}, after={actual_new}"
            )
        if actual_new >= old_vertex_count:
            raise RuntimeError("real PMX fixture did not remove any UV-seam vertex slots")
        if actual_faces != old_face_count:
            raise RuntimeError("UV weld changed the polygon count")

        welded_fn = _mesh_fn(om, transform)
        new_uv_counts, new_uv_ids = welded_fn.getAssignedUVs()
        if list(new_uv_counts) != list(old_uv_counts) or len(new_uv_ids) != len(old_uv_ids):
            raise RuntimeError("UV face-corner assignments changed during weld")

        if not cmds.attributeQuery("mmd_source_vertex_indices", node=transform, exists=True):
            raise RuntimeError("C++ weld did not write mmd_source_vertex_indices")
        source_indices = cmds.getAttr(f"{transform}.mmd_source_vertex_indices") or []
        if len(source_indices) != actual_new or len(set(int(value) for value in source_indices)) != actual_new:
            raise RuntimeError("source-vertex mapping does not match welded topology")

        cmds.delete(transform)
        if cmds.objExists(transform):
            raise RuntimeError("weld smoke cleanup failed")

        with tempfile.TemporaryDirectory() as directory:
            morph_fixture = Path(directory) / "morph_equivalence.pmx"
            _write_morph_equivalence_fixture(morph_fixture)
            morph_loaded = cmds.mmdFastLoad(
                f=str(morph_fixture), n="morph_equivalence", s=1.0, mo=False
            )
            morph_transform = morph_loaded[0]
            morph_result = cmds.mmdWeldUvSeamVertices(
                m=morph_transform, f=str(morph_fixture)
            )
            if [int(morph_result[1]), int(morph_result[2])] != [6, 5]:
                raise RuntimeError(
                    "native morph equivalence did not weld only the equal seam pair: "
                    f"{morph_result!r}"
                )
            morph_map = cmds.getAttr(
                f"{morph_transform}.mmd_source_to_local_indices"
            ) or []
            if int(morph_map[0]) != int(morph_map[1]):
                raise RuntimeError("equivalent Vertex/UV/Additional-UV sources were not welded")
            if int(morph_map[2]) == int(morph_map[3]):
                raise RuntimeError("conflicting vertex morph sources were welded")
            cmds.delete(morph_transform)

        # Exercise the actual normal Python PMX mesh path as well.  The
        # converter must keep the source topology intact until it invokes the
        # same C++ command, otherwise this would silently test only the
        # standalone command API.
        cmds.file(new=True, force=True)
        cmds.loadPlugin(str(PYTHON_PLUGIN), quiet=True)
        from mmd_tools.core.mmd_parser import parse_pmx_file
        from mmd_tools.core.settings import settings
        from mmd_tools.converters import MeshConverter, MorphConverter

        settings.set("import.model.create_mmd_shaders", False)
        settings.set("import.model.separate_meshes_by_material", False)
        parsed = parse_pmx_file(str(FIXTURE), use_native_pmx_parse=False)
        root = cmds.group(empty=True, name="focused_python_uv_weld_root")
        converter = MeshConverter(str(FIXTURE))
        _mesh_group, converted_mesh = converter.convert_pmx_mesh(parsed, root)
        converted_count = int(cmds.polyEvaluate(converted_mesh, vertex=True))
        if converted_count != actual_new:
            raise RuntimeError("Python import did not use the native morph-equivalent weld result")
        if int(converter.profile["uv_welded_vertex_count"]) != old_vertex_count - converted_count:
            raise RuntimeError("Python converter did not report the combined planned/native weld count")
        if not cmds.attributeQuery("mmd_source_to_local_indices", node=converted_mesh, exists=True):
            raise RuntimeError("Python/C++ weld did not preserve the complete source-to-local mapping")
        source_to_local = cmds.getAttr(f"{converted_mesh}.mmd_source_to_local_indices") or []
        if len(source_to_local) != len(parsed.vertices):
            raise RuntimeError("source-to-local mapping does not cover every PMX source vertex")
        if any(int(value) < 0 for value in source_to_local):
            raise RuntimeError("unified source-to-local mapping contains an unavailable source")
        mapped_locals = {int(value) for value in source_to_local if int(value) >= 0}
        if len(mapped_locals) != converted_count:
            raise RuntimeError("source-to-local mapping was not composed through the C++ weld")
        morph_result = MorphConverter().convert_pmx_morphs(parsed, converted_mesh)
        if not morph_result.get("success"):
            raise RuntimeError(f"morph import failed after native weld: {morph_result!r}")
        if parsed.morphs and int(morph_result.get("morphs_converted", 0)) == 0:
            raise RuntimeError("native-welded model did not import any PMX morphs")
        cmds.delete(root)

        print(
            "OK: C++ UV weld reduced "
            f"{old_vertex_count} -> {actual_new}; native import -> {converted_count} vertices"
        )
        return 0
    finally:
        maya.standalone.uninitialize()
        if dll_handle is not None:
            dll_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
