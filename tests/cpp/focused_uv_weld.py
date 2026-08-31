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


def _mesh_snapshot(cmds, om, mesh: str) -> dict:
    """Capture face-corner data that a topology rebuild must preserve."""
    mesh_fn = _mesh_fn(om, mesh)
    uv_sets = {}
    for uv_set in mesh_fn.getUVSetNames():
        u, v = mesh_fn.getUVs(uv_set)
        counts, ids = mesh_fn.getAssignedUVs(uv_set)
        uv_sets[str(uv_set)] = {
            "u": list(u),
            "v": list(v),
            "counts": list(counts),
            "ids": list(ids),
        }
    normals = mesh_fn.getNormals(om.MSpace.kObject)
    _, normal_ids = mesh_fn.getNormalIds()
    face_vertex_normals = [
        (float(normals[index].x), float(normals[index].y), float(normals[index].z))
        for index in normal_ids
    ]
    shaders, shader_indices = mesh_fn.getConnectedShaders(0)
    shader_names = [om.MFnDependencyNode(shader).name() for shader in shaders]
    return {
        "vertices": int(cmds.polyEvaluate(mesh, vertex=True)),
        "faces": int(cmds.polyEvaluate(mesh, face=True)),
        "uv_sets": uv_sets,
        "current_uv_set": str(mesh_fn.currentUVSetName()),
        "face_vertex_normals": face_vertex_normals,
        "shader_names": shader_names,
        "shader_indices": list(shader_indices),
    }


def _sequence_close(left, right, tolerance=1.0e-6) -> bool:
    """Compare authored floating-point payloads within Maya API precision."""
    if len(left) != len(right):
        return False
    return all(
        abs(float(a) - float(b)) <= tolerance
        for left_item, right_item in zip(left, right)
        for a, b in zip(left_item, right_item)
    )


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
            "materials": [
                {"name": "MorphA", "face_count": 6},
                {"name": "MorphB", "face_count": 6},
            ],
            "morphs": [
                {
                    "type": "vertex",
                    "name": "vertex_delta",
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


def _write_skin_split_fixture(path: Path) -> None:
    """Write two material meshes whose seam copies carry skin weights."""
    from mmd_tools.io.pmx_exporter import PmxExporter

    vertices = []
    for source_index, bone_index in enumerate((0, 0, 1, 1, 0, 0, 1, 1)):
        position = (
            [0.0, 0.0, 0.0]
            if source_index % 4 in (0, 1)
            else [1.0, 0.0, 0.0]
        )
        vertices.append(
            {
                "position": position,
                "normal": [0.0, 0.0, 1.0],
                "uv": [float(source_index % 2), 0.0],
                "bone_indices": [bone_index],
                "bone_weights": [1.0],
            }
        )
    PmxExporter(native_parts_exporter=None).export_pmx_model(
        str(path),
        {
            "model_name": "NativeSkinSplit",
            "vertices": vertices,
            "faces": [[0, 2, 3], [1, 2, 3], [4, 6, 7], [5, 6, 7]],
            "materials": [
                {"name": "SkinA", "face_count": 6},
                {"name": "SkinB", "face_count": 6},
            ],
            "bones": [
                {"name": "Root", "position": [0.0, 0.0, 0.0]},
                {"name": "Bone", "position": [0.0, 1.0, 0.0], "parent_index": 0},
            ],
        },
    )


def _skin_snapshot(cmds, mesh: str) -> dict:
    """Read production skin weights keyed by PMX source vertex."""
    shapes = [
        shape
        for shape in (cmds.listRelatives(mesh, shapes=True, type="mesh", fullPath=True) or [])
        if not cmds.getAttr(f"{shape}.intermediateObject")
    ]
    if len(shapes) != 1:
        raise RuntimeError(f"skin fixture has unexpected shape list for {mesh}: {shapes!r}")
    history = cmds.listHistory(shapes[0], pruneDagObjects=True) or []
    skin_clusters = [node for node in history if cmds.nodeType(node) == "skinCluster"]
    if len(skin_clusters) != 1:
        raise RuntimeError(f"skin fixture has unexpected skin history for {mesh}: {skin_clusters!r}")
    skin_cluster = skin_clusters[0]
    influences = cmds.skinCluster(skin_cluster, query=True, influence=True) or []
    from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX

    influence_indices = [int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}")) for joint in influences]
    source_indices = [
        int(value) for value in (cmds.getAttr(f"{mesh}.mmd_source_vertex_indices") or [])
    ]
    weights = []
    for local_index, source_index in enumerate(source_indices):
        values = cmds.skinPercent(
            skin_cluster,
            f"{shapes[0]}.vtx[{local_index}]",
            query=True,
            value=True,
        ) or []
        weights.append(
            {
                source_index: {
                    bone_index: round(float(value), 6)
                    for bone_index, value in zip(influence_indices, values)
                    if float(value) > 1.0e-7
                }
            }
        )
    return {
        "faces": int(cmds.polyEvaluate(mesh, face=True)),
        "source_indices": source_indices,
        "weights": weights,
    }


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
        for capability in ("sourceToLocalV1", "morphEquivalentV1", "batchV1", "profileV1"):
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
        if actual_new != old_vertex_count:
            raise RuntimeError("already-welded FastLoad mesh changed vertex count")
        if actual_faces != old_face_count:
            raise RuntimeError("UV weld changed the polygon count")

        welded_fn = _mesh_fn(om, transform)
        new_uv_counts, new_uv_ids = welded_fn.getAssignedUVs()
        if list(new_uv_counts) != list(old_uv_counts) or len(new_uv_ids) != len(old_uv_ids):
            raise RuntimeError("UV face-corner assignments changed during weld")

        if not cmds.attributeQuery("mmd_source_vertex_indices", node=transform, exists=True):
            raise RuntimeError("FastLoad pre-weld omitted mmd_source_vertex_indices")
        source_indices = [
            int(value) for value in (cmds.getAttr(f"{transform}.mmd_source_vertex_indices") or [])
        ]
        if len(source_indices) != actual_new or len(set(source_indices)) != actual_new:
            raise RuntimeError("FastLoad source-vertex mapping does not match welded topology")
        if not cmds.attributeQuery("mmd_source_to_local_indices", node=transform, exists=True):
            raise RuntimeError("FastLoad pre-weld omitted mmd_source_to_local_indices")
        fast_source_to_local = [
            int(value)
            for value in (cmds.getAttr(f"{transform}.mmd_source_to_local_indices") or [])
        ]
        if not fast_source_to_local or any(
            value < 0 or value >= actual_new for value in fast_source_to_local
        ):
            raise RuntimeError("FastLoad source-to-local mapping is incomplete")
        if max(source_indices) >= len(fast_source_to_local):
            raise RuntimeError("FastLoad source indices exceed source-to-local mapping")
        expected_welded_vertex_count = len(fast_source_to_local) - actual_new
        if expected_welded_vertex_count <= 0:
            raise RuntimeError("focused fixture did not exercise FastLoad pre-weld")

        cmds.delete(transform)
        if cmds.objExists(transform):
            raise RuntimeError("weld smoke cleanup failed")

        batch_loaded = [
            cmds.mmdFastLoad(f=str(FIXTURE), n=name, s=1.0)
            for name in ("focused_batch_uv_a", "focused_batch_uv_b")
        ]
        batch_meshes = [loaded_mesh[0] for loaded_mesh in batch_loaded]
        before_batch = {
            mesh: _mesh_snapshot(cmds, om, mesh) for mesh in batch_meshes
        }
        batch_result = cmds.mmdWeldUvSeamVertices(
            m=batch_meshes,
            f=str(FIXTURE),
            batch=True,
            profile=True,
        )
        if len(batch_result) != len(batch_meshes) * 4 + 6:
            raise RuntimeError(f"batch weld result has unexpected shape: {batch_result!r}")
        batch_records = {
            str(batch_result[offset]): {
                "status": str(batch_result[offset + 1]),
                "old": int(batch_result[offset + 2]),
                "new": int(batch_result[offset + 3]),
            }
            for offset in range(0, len(batch_meshes) * 4, 4)
        }
        if set(batch_records) != set(batch_meshes) or any(
            record["status"] != "ok" for record in batch_records.values()
        ):
            raise RuntimeError(f"batch weld did not report every mesh as ok: {batch_result!r}")
        if batch_result[len(batch_meshes) * 4] != "__profile__":
            raise RuntimeError(f"batch weld omitted profile marker: {batch_result!r}")
        if [int(value) for value in batch_result[-3:]] != [1, 1, 1]:
            raise RuntimeError(f"batch weld repeated PMX preparation: {batch_result!r}")
        for mesh in batch_meshes:
            after = _mesh_snapshot(cmds, om, mesh)
            before = before_batch[mesh]
            if after["faces"] != before["faces"]:
                raise RuntimeError(f"batch weld changed face count for {mesh}")
            if after["uv_sets"] != before["uv_sets"]:
                raise RuntimeError(f"batch weld changed UV data for {mesh}")
            if after["current_uv_set"] != before["current_uv_set"]:
                raise RuntimeError(f"batch weld changed current UV set for {mesh}")
            if not _sequence_close(
                after["face_vertex_normals"], before["face_vertex_normals"]
            ):
                raise RuntimeError(f"batch weld changed authored normals for {mesh}")
            if after["shader_names"] != before["shader_names"] or after["shader_indices"] != before["shader_indices"]:
                raise RuntimeError(f"batch weld changed shading assignments for {mesh}")
            if batch_records[mesh]["new"] != after["vertices"]:
                raise RuntimeError(f"batch weld vertex count mismatch for {mesh}: {batch_records[mesh]!r}")
            if after["vertices"] != before["vertices"]:
                raise RuntimeError(f"batch changed already-welded FastLoad mesh topology for {mesh}")
            if not cmds.attributeQuery("mmd_source_vertex_indices", node=mesh, exists=True):
                raise RuntimeError(f"FastLoad batch mesh omitted source indices for {mesh}")
            source_indices = [
                int(value) for value in (cmds.getAttr(f"{mesh}.mmd_source_vertex_indices") or [])
            ]
            if len(source_indices) != after["vertices"] or len(set(source_indices)) != after["vertices"]:
                raise RuntimeError(f"batch source indices do not match welded topology for {mesh}")
            if not cmds.attributeQuery("mmd_source_to_local_indices", node=mesh, exists=True):
                raise RuntimeError(f"FastLoad batch mesh omitted source-to-local mapping for {mesh}")
            source_to_local = [
                int(value) for value in (cmds.getAttr(f"{mesh}.mmd_source_to_local_indices") or [])
            ]
            if not source_to_local or any(
                value < 0 or value >= after["vertices"] for value in source_to_local
            ):
                raise RuntimeError(f"batch source-to-local mapping is incomplete for {mesh}")
            if max(source_indices) >= len(source_to_local):
                raise RuntimeError(f"batch source indices exceed source-to-local mapping for {mesh}")

            # A second invocation on an already-normalized mesh is a valid
            # no-op, not a failed batch item.
            noop_result = cmds.mmdWeldUvSeamVertices(
                m=[mesh], f=str(FIXTURE), batch=True, profile=True
            )
            if (
                len(noop_result) != 10
                or str(noop_result[1]) != "ok"
                or int(noop_result[2]) != after["vertices"]
                or int(noop_result[3]) != after["vertices"]
            ):
                raise RuntimeError(f"normalized mesh was not reported as a successful no-op: {noop_result!r}")
            cmds.delete(mesh)

        # Preflight all targets before mutation.  A missing later target must
        # leave an earlier valid target unchanged while retaining diagnostics.
        failure_loaded = cmds.mmdFastLoad(f=str(FIXTURE), n="focused_batch_fail_closed", s=1.0)
        failure_mesh = failure_loaded[0]
        failure_before = _mesh_snapshot(cmds, om, failure_mesh)
        missing_mesh = "focused_batch_missing_transform"
        failure_result = cmds.mmdWeldUvSeamVertices(
            m=[failure_mesh, missing_mesh], f=str(FIXTURE), batch=True, profile=True
        )
        failure_records = {
            str(failure_result[offset]): str(failure_result[offset + 1])
            for offset in range(0, len(failure_result) - 6, 4)
        }
        if failure_records.get(failure_mesh) != "blocked" or failure_records.get(missing_mesh) != "preflight_failed":
            raise RuntimeError(f"batch preflight failure status was not fail-closed: {failure_result!r}")
        if _mesh_snapshot(cmds, om, failure_mesh) != failure_before:
            raise RuntimeError("batch preflight failure partially mutated an earlier mesh")
        cmds.delete(failure_mesh)

        with tempfile.TemporaryDirectory() as directory:
            morph_fixture = Path(directory) / "morph_equivalence.pmx"
            _write_morph_equivalence_fixture(morph_fixture)
            morph_loaded = cmds.mmdFastLoad(
                f=str(morph_fixture), n="morph_equivalence", s=1.0, mo=True
            )
            morph_transform = morph_loaded[0]
            morph_result = cmds.mmdWeldUvSeamVertices(
                m=morph_transform, f=str(morph_fixture)
            )
            if [int(morph_result[1]), int(morph_result[2])] != [5, 5]:
                raise RuntimeError(
                    "FastLoad did not pre-weld only the equal seam pair: "
                    f"{morph_result!r}"
                )
            morph_map = cmds.getAttr(
                f"{morph_transform}.mmd_source_to_local_indices"
            ) or []
            if int(morph_map[0]) != int(morph_map[1]):
                raise RuntimeError("equivalent Vertex/UV/Additional-UV sources were not welded")
            if int(morph_map[2]) == int(morph_map[3]):
                raise RuntimeError("conflicting vertex morph sources were welded")
            morph_shape = (cmds.listRelatives(morph_transform, shapes=True, type="mesh") or [None])[0]
            blend_shapes = [
                node for node in (cmds.listHistory(morph_shape) or [])
                if cmds.nodeType(node) == "blendShape"
            ]
            if len(blend_shapes) != 1:
                raise RuntimeError(f"FastLoad did not create one vertex blendShape: {blend_shapes!r}")
            equivalent_local = int(morph_map[0])
            before_morph = cmds.xform(
                f"{morph_transform}.vtx[{equivalent_local}]", query=True,
                objectSpace=True, translation=True,
            )
            cmds.setAttr(f"{blend_shapes[0]}.vertex_delta", 1.0)
            after_morph = cmds.xform(
                f"{morph_transform}.vtx[{equivalent_local}]", query=True,
                objectSpace=True, translation=True,
            )
            if abs(float(after_morph[1]) - float(before_morph[1]) - 0.5) > 1.0e-6:
                raise RuntimeError(
                    "equivalent welded morph sources were double-applied or lost: "
                    f"before={before_morph!r}, after={after_morph!r}"
                )
            cmds.delete(morph_transform)

            # Exercise the production material-split route with the same
            # morph fixture.  Equal Vertex/UV/Additional-UV signatures must
            # weld in material A while the conflicting pair remains distinct
            # in material B.
            cmds.file(new=True, force=True)
            from mmd_tools.core.mmd_parser import parse_pmx_file
            from mmd_tools.core.settings import settings
            from mmd_tools.converters import BoneConverter, MeshConverter, MorphConverter

            settings.set("import.model.create_mmd_shaders", False)
            settings.set("import.model.separate_meshes_by_material", True)
            production_morph = parse_pmx_file(str(morph_fixture), use_native_pmx_parse=False)
            production_root = cmds.group(empty=True, name="production_morph_baseline_root")
            production_baseline_converter = MeshConverter(str(morph_fixture))
            production_baseline_converter._cpp_uv_weld_batch_command_available = lambda: False
            _production_group, production_baseline_meshes = production_baseline_converter.convert_pmx_mesh(
                production_morph, production_root
            )
            baseline_snapshots = {
                mesh.rsplit("|", 1)[-1]: _mesh_snapshot(cmds, om, mesh)
                for mesh in production_baseline_meshes
            }
            baseline_provenance = {
                mesh.rsplit("|", 1)[-1]: {
                    "source_indices": list(
                        cmds.getAttr(f"{mesh}.mmd_source_vertex_indices") or []
                    ),
                    "source_to_local": list(
                        cmds.getAttr(f"{mesh}.mmd_source_to_local_indices") or []
                    ),
                }
                for mesh in production_baseline_meshes
            }
            cmds.delete(production_root)

            # A native application failure must not leave a partially
            # converted material set in the production importer.
            cmds.file(new=True, force=True)
            failure_root = cmds.group(empty=True, name="production_batch_failure_root")
            failure_converter = MeshConverter(str(morph_fixture))
            failure_targets = []

            def _raise_after_partial_mutation(payloads):
                failure_targets.extend(payload["mesh"] for payload in payloads)
                cmds.setAttr(f"{failure_targets[0]}.visibility", False)
                raise RuntimeError("injected native batch application failure")

            failure_converter._run_cpp_uv_weld_batch = _raise_after_partial_mutation
            try:
                failure_converter.convert_pmx_mesh(production_morph, failure_root)
            except RuntimeError as exc:
                if "injected native batch application failure" not in str(exc):
                    raise
            else:
                raise RuntimeError("production batch failure did not propagate")
            if not failure_targets or any(cmds.objExists(mesh) for mesh in failure_targets):
                raise RuntimeError("production batch failure left partial material meshes")

            # Failures while persisting post-weld provenance are part of the
            # same importer transaction and must remove every material mesh.
            cmds.file(new=True, force=True)
            persistence_root = cmds.group(empty=True, name="production_persistence_failure_root")
            persistence_converter = MeshConverter(str(morph_fixture))
            persistence_targets = []
            original_batch = persistence_converter._run_cpp_uv_weld_batch

            def _record_batch_targets(payloads):
                persistence_targets.extend(payload["mesh"] for payload in payloads)
                return original_batch(payloads)

            def _raise_during_provenance(*_args, **_kwargs):
                raise RuntimeError("injected post-weld provenance failure")

            persistence_converter._run_cpp_uv_weld_batch = _record_batch_targets
            persistence_converter._post_weld_source_indices = _raise_during_provenance
            try:
                persistence_converter.convert_pmx_mesh(production_morph, persistence_root)
            except RuntimeError as exc:
                if "injected post-weld provenance failure" not in str(exc):
                    raise
            else:
                raise RuntimeError("post-weld provenance failure did not propagate")
            if not persistence_targets or any(cmds.objExists(mesh) for mesh in persistence_targets):
                raise RuntimeError("post-weld provenance failure left partial material meshes")

            cmds.file(new=True, force=True)
            settings.set("import.model.create_mmd_shaders", False)
            settings.set("import.model.separate_meshes_by_material", True)
            production_root = cmds.group(empty=True, name="production_morph_batch_root")
            production_converter = MeshConverter(str(morph_fixture))
            _production_group, production_meshes = production_converter.convert_pmx_mesh(
                production_morph, production_root
            )
            if set(baseline_snapshots) != {mesh.rsplit("|", 1)[-1] for mesh in production_meshes}:
                raise RuntimeError("production material split changed its mesh set")
            production_counts = [
                int(cmds.polyEvaluate(mesh, vertex=True)) for mesh in production_meshes
            ]
            if production_counts != [3, 4]:
                raise RuntimeError(
                    f"production morph split did not preserve weld parity: {production_counts!r}"
                )
            if production_converter.profile["native_uv_weld_command_calls"] != 1:
                raise RuntimeError("production material split did not use one native batch call")
            for mesh in production_meshes:
                leaf = mesh.rsplit("|", 1)[-1]
                if _mesh_snapshot(cmds, om, mesh) != baseline_snapshots[leaf]:
                    raise RuntimeError(f"production batch changed mesh attributes for {mesh}")
                provenance = {
                    "source_indices": list(
                        cmds.getAttr(f"{mesh}.mmd_source_vertex_indices") or []
                    ),
                    "source_to_local": list(
                        cmds.getAttr(f"{mesh}.mmd_source_to_local_indices") or []
                    ),
                }
                if provenance != baseline_provenance[leaf]:
                    raise RuntimeError(f"production batch changed source mapping for {mesh}")
            production_morph_result = MorphConverter().convert_pmx_morphs(
                production_morph, production_meshes
            )
            if not production_morph_result.get("success") or production_morph_result.get("morphs_converted", 0) < 3:
                raise RuntimeError(
                    f"production split morph conversion lost parity: {production_morph_result!r}"
                )
            cmds.delete(production_root)

            # Repeat the baseline/batch comparison through production skin
            # binding.  The native topology route must not change which PMX
            # source vertex receives each influence.
            skin_fixture = Path(directory) / "skin_split.pmx"
            _write_skin_split_fixture(skin_fixture)
            skin_data = parse_pmx_file(str(skin_fixture), use_native_pmx_parse=False)

            cmds.file(new=True, force=True)
            settings.set("import.model.create_mmd_shaders", False)
            settings.set("import.model.separate_meshes_by_material", True)
            skin_baseline_root = cmds.group(empty=True, name="production_skin_baseline_root")
            skin_baseline_converter = MeshConverter(str(skin_fixture))
            skin_baseline_converter._cpp_uv_weld_batch_command_available = lambda: False
            _skin_group, skin_baseline_meshes = skin_baseline_converter.convert_pmx_mesh(
                skin_data, skin_baseline_root
            )
            BoneConverter().convert_pmx_bones(
                skin_data,
                skin_baseline_meshes,
                skin_baseline_root,
                setup_rig=False,
                setup_bone_orientation=False,
                pmx_filepath=str(skin_fixture),
            )
            skin_baseline = {
                mesh.rsplit("|", 1)[-1]: {
                    "mesh": _mesh_snapshot(cmds, om, mesh),
                    "skin": _skin_snapshot(cmds, mesh),
                }
                for mesh in skin_baseline_meshes
            }
            cmds.file(new=True, force=True)
            settings.set("import.model.create_mmd_shaders", False)
            settings.set("import.model.separate_meshes_by_material", True)
            skin_batch_root = cmds.group(empty=True, name="production_skin_batch_root")
            skin_batch_converter = MeshConverter(str(skin_fixture))
            _skin_group, skin_batch_meshes = skin_batch_converter.convert_pmx_mesh(
                skin_data, skin_batch_root
            )
            BoneConverter().convert_pmx_bones(
                skin_data,
                skin_batch_meshes,
                skin_batch_root,
                setup_rig=False,
                setup_bone_orientation=False,
                pmx_filepath=str(skin_fixture),
            )
            if skin_batch_converter.profile["native_uv_weld_command_calls"] != 1:
                raise RuntimeError("production skin split did not use one native batch call")
            skin_batch = {
                mesh.rsplit("|", 1)[-1]: {
                    "mesh": _mesh_snapshot(cmds, om, mesh),
                    "skin": _skin_snapshot(cmds, mesh),
                }
                for mesh in skin_batch_meshes
            }
            if skin_batch != skin_baseline:
                raise RuntimeError(
                    f"production batch changed skin parity: baseline={skin_baseline!r}, batch={skin_batch!r}"
                )
            cmds.delete(skin_batch_root)

        # Exercise the actual normal Python PMX mesh path as well.  The
        # converter must keep the source topology intact until it invokes the
        # same C++ command, otherwise this would silently test only the
        # standalone command API.
        cmds.file(new=True, force=True)
        cmds.loadPlugin(str(PYTHON_PLUGIN), quiet=True)
        settings.set("import.model.create_mmd_shaders", False)
        settings.set("import.model.separate_meshes_by_material", False)
        parsed = parse_pmx_file(str(FIXTURE), use_native_pmx_parse=False)
        root = cmds.group(empty=True, name="focused_python_uv_weld_root")
        converter = MeshConverter(str(FIXTURE))
        _mesh_group, converted_mesh = converter.convert_pmx_mesh(parsed, root)
        converted_count = int(cmds.polyEvaluate(converted_mesh, vertex=True))
        if converted_count != actual_new:
            raise RuntimeError("Python import did not use the native morph-equivalent weld result")
        if int(converter.profile["uv_welded_vertex_count"]) != expected_welded_vertex_count:
            raise RuntimeError(
                "Python converter did not report the same weld reduction as FastLoad"
            )
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
            "OK: FastLoad pre-weld and native Python weld agree on "
            f"{converted_count} vertices"
        )
        return 0
    finally:
        maya.standalone.uninitialize()
        if dll_handle is not None:
            dll_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
