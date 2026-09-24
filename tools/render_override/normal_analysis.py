"""Read-only evidence for avoiding redundant weighted vertex-normal averaging.

API timings use already evaluated DAG meshes through Python API 2.0. They do
not measure a replacement C++ implementation or the cost of first evaluation.
"""

import hashlib
import math
from pathlib import Path
import statistics
import time


def inspect_normals(meshes):
    """Compare weighted normals with uniform corner normals; never alter a mesh."""
    from maya.api import OpenMaya as om

    rows = []
    for mesh in meshes:
        selection = om.MSelectionList()
        selection.add(mesh)
        fn = om.MFnMesh(selection.getDagPath(0))

        def weighted():
            return fn.getVertexNormals(True, om.MSpace.kObject)

        def raw_bundle():
            return (fn.getNormals(om.MSpace.kObject), fn.getNormalIds(), fn.getVertices())

        # Both APIs see the same already evaluated mesh. Warm each once, then
        # alternate order to avoid always charging the first call to one API.
        weighted()
        raw_bundle()
        timings = {"weighted": [], "rawBundle": []}
        for repeat in range(7):
            operations = [("weighted", weighted), ("rawBundle", raw_bundle)]
            for name, operation in operations[::1 if repeat % 2 == 0 else -1]:
                started = time.perf_counter()
                value = operation()
                timings[name].append((time.perf_counter() - started) * 1000)
                del value

        normals, (normal_counts, normal_ids), (face_counts, vertices) = raw_bundle()
        reference = weighted()
        assert list(normal_counts) == list(face_counts)
        assert len(normal_ids) == len(vertices) == sum(face_counts)
        assert len(reference) == fn.numVertices
        per_vertex = [None] * fn.numVertices
        conflicts = set()
        for vertex, normal_id in zip(vertices, normal_ids):
            assert 0 <= vertex < fn.numVertices and 0 <= normal_id < len(normals)
            value = tuple(normals[normal_id])
            if per_vertex[vertex] is None:
                per_vertex[vertex] = value
            elif per_vertex[vertex] != value:
                conflicts.add(vertex)

        errors = []
        invalid = 0
        for vertex, value in enumerate(per_vertex):
            if value is None or vertex in conflicts:
                continue
            expected = tuple(reference[vertex])
            lengths = [math.sqrt(sum(x * x for x in vector)) for vector in (value, expected)]
            if any(not math.isfinite(x) or x <= 0 for x in lengths):
                invalid += 1
                continue
            # The renderer normalizes the API result before storing it.
            errors.append(max(abs(a / lengths[0] - b / lengths[1]) for a, b in zip(value, expected)))
        rows.append({"mesh": mesh, "vertices": fn.numVertices, "polygons": fn.numPolygons,
                     "normalCount": len(normals), "conflictingCornerVertices": len(conflicts),
                     "unreferencedVertices": sum(value is None for value in per_vertex),
                     "invalidUniformVertices": invalid, "comparedUniformVertices": len(errors),
                     "maxNormalizedComponentError": max(errors, default=None),
                     "uniformVerticesAboveTolerance": sum(error > 1e-6 for error in errors),
                     "apiSamplesMs": timings,
                     "apiMedianMs": {name: statistics.median(values) for name, values in timings.items()}})
    return {"scope": "restored, already evaluated DAG meshes; warm API calls only; mapping cost excluded",
            "samples": 7, "componentTolerance": 1e-6,
            "analysisSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "meshes": rows}
