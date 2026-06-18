"""Minimum scene-data collector for PMX export.

Collects a single polygon mesh from the Maya scene and returns a dict
compatible with ``PmxExporter.export_pmx_model``.

Coordinate conventions
----------------------
This collector preserves Maya world-space coordinates without any basis
conversion (no Z-flip, no scale change).  Full MMD-basis conversion
(Z *= -1, skinCluster extraction) is out of scope for this minimum slice
and must be added in a later collector pass.
"""

from maya import cmds

from mmd_tools.core.constants import ATTR_MMD_MATERIAL_NAME, ATTR_MMD_MODEL_NAME


def _get_mesh_shape(node: str) -> str:
    """Return the mesh shape node, resolving from transform if needed.

    Args:
        node: Transform or mesh shape node name.

    Returns:
        Mesh shape node name.

    Raises:
        ValueError: If no mesh shape is found under *node*.
    """
    if cmds.nodeType(node) == "mesh":
        return node
    shapes = cmds.listRelatives(node, shapes=True, type="mesh", fullPath=True) or []
    if not shapes:
        raise ValueError(f"No mesh shape found under '{node}'")
    return shapes[0]


def _make_material_dict(mat_name: str) -> dict:
    """Return a minimal material dict with the given name (without ``face_count``)."""
    return {
        "name": mat_name,
        "diffuse": [0.8, 0.8, 0.8, 1.0],
        "specular": [0.5, 0.5, 0.5],
        "specular_coefficient": 5.0,
        "ambient": [0.3, 0.3, 0.3],
        "draw_flag": 0x01 | 0x02 | 0x10,
        "edge_color": [0.0, 0.0, 0.0, 1.0],
        "edge_size": 1.0,
        "texture_index": -1,
        "sphere_texture_index": -1,
        "sphere_mode": 0,
        "shared_toon_flag": 0,
        "toon_texture_index": -1,
        "memo": "",
    }


def _resolve_shader_name(sg_node_name: str) -> str:
    """Return the display name for the shader connected to a shadingEngine node.

    Reads ``mmd_material_name`` if the attribute is present; otherwise falls
    back to the shader node name.  Falls back to *sg_node_name* when no
    ``surfaceShader`` connection exists.
    """
    shaders = cmds.listConnections(f"{sg_node_name}.surfaceShader") or []
    if not shaders:
        return sg_node_name
    shader = shaders[0]
    if cmds.attributeQuery(ATTR_MMD_MATERIAL_NAME, node=shader, exists=True):
        val = cmds.getAttr(f"{shader}.{ATTR_MMD_MATERIAL_NAME}")
        if val:
            return val
    return shader


def _collect_materials_per_face(shape: str, fn) -> tuple:
    """Return ``(materials, faces)`` with polygons grouped by per-face material.

    Uses ``MFnMesh.getConnectedShaders`` to obtain the per-polygon shading-group
    assignment for instance 0.  Materials are ordered by first polygon
    occurrence so the output is deterministic.  Each material dict has
    ``face_count`` set to the fan-triangulated index count for its polygon group.

    Limitation: vertex indices are not remapped; all materials reference the
    same global vertex array.  Vertex deduplication or per-material re-indexing
    is out of scope for this minimum collector slice.

    Args:
        shape: Mesh shape node name.
        fn: ``MFnMesh`` handle for *shape*.

    Returns:
        Tuple ``(materials, faces)`` where *faces* is a flat list of polygon
        vertex-index lists ordered contiguously per material.
    """
    import maya.api.OpenMaya as om

    num_polys = fn.numPolygons
    shading_groups_obj, poly_shader_indices = fn.getConnectedShaders(0)

    if len(shading_groups_obj) == 0:
        # No shaders on this mesh — return one default material for all faces.
        faces = [list(fn.getPolygonVertices(i)) for i in range(num_polys)]
        mat = _make_material_dict("Default")
        mat["face_count"] = sum(max(0, len(f) - 2) * 3 for f in faces)
        return [mat], faces

    dep_fn = om.MFnDependencyNode()
    sg_names = []
    for sg_obj in shading_groups_obj:
        dep_fn.setObject(sg_obj)
        sg_names.append(dep_fn.name())

    # When the whole object shares one shader Maya returns an empty index array.
    if len(poly_shader_indices) == 0:
        poly_sg_keys = [sg_names[0]] * num_polys
    else:
        poly_sg_keys = []
        for poly_id in range(num_polys):
            idx = int(poly_shader_indices[poly_id])
            # idx == -1 or out of range means unassigned (rare but guard it).
            poly_sg_keys.append(sg_names[idx] if 0 <= idx < len(sg_names) else "__unassigned__")

    # Group polygons by SG in order of first occurrence.
    mat_order = []
    poly_by_sg = {}
    for poly_id, sg_key in enumerate(poly_sg_keys):
        if sg_key not in poly_by_sg:
            poly_by_sg[sg_key] = []
            mat_order.append(sg_key)
        poly_by_sg[sg_key].append(poly_id)

    materials = []
    faces = []
    for sg_key in mat_order:
        poly_ids = poly_by_sg[sg_key]
        group_faces = [list(fn.getPolygonVertices(i)) for i in poly_ids]
        mat_name = "Default" if sg_key == "__unassigned__" else _resolve_shader_name(sg_key)
        mat = _make_material_dict(mat_name)
        mat["face_count"] = sum(max(0, len(f) - 2) * 3 for f in group_faces)
        materials.append(mat)
        faces.extend(group_faces)

    return materials, faces


class ExportSceneCollector:
    """Collect minimum PMX-compatible data from a single Maya polygon mesh.

    This is a *minimum* collector: it captures per-vertex positions, normals,
    UVs (first-occurrence per vertex), polygon connectivity, and per-face
    material assignment.  It does **not** perform skinCluster extraction,
    blend-shape collection, or MMD-basis coordinate conversion — those belong
    to later collector slices.

    Example::

        collector = ExportSceneCollector()
        maya_data = collector.collect_from_mesh("pCubeTransform")
        exporter = PmxExporter()
        exporter.export_pmx_model("/tmp/out.pmx", maya_data)
    """

    def collect_from_mesh(self, transform_or_shape: str) -> dict:
        """Collect scene data from a single polygon mesh transform or shape.

        Coordinate system: Maya world-space (Y-up, right-handed, units in cm).
        No MMD basis conversion is applied.  All vertices receive ``bone_indices
        = [0]`` (BDEF1) so that the default root bone created by the exporter is
        valid.

        Per-face material assignment is preserved: polygons are grouped by their
        shading-group in first-occurrence order, and ``faces`` in the returned
        dict reflects that grouping so that each material's ``face_count`` covers
        a contiguous block of triangulated indices.

        Args:
            transform_or_shape: Transform or mesh shape node name.

        Returns:
            Dict with keys ``model_name``, ``vertices``, ``faces``,
            ``materials``, ``bones`` (``None`` → exporter creates root bone)
            suitable for ``PmxExporter.export_pmx_model``.

        Raises:
            ValueError: If no polygon mesh shape is found under the node.
        """
        import maya.api.OpenMaya as om

        shape = _get_mesh_shape(transform_or_shape)

        # Derive transform for model_name lookup
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        transform = parents[0] if parents else shape

        model_name = transform
        if cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=transform, exists=True):
            val = cmds.getAttr(f"{transform}.{ATTR_MMD_MODEL_NAME}")
            if val:
                model_name = val

        # Build MFnMesh handle
        sel = om.MSelectionList()
        sel.add(shape)
        dag = sel.getDagPath(0)
        fn = om.MFnMesh(dag)

        vertex_count = fn.numVertices
        points = fn.getPoints(om.MSpace.kWorld)
        # Angle-weighted=False gives evenly-weighted per-vertex normals.
        vertex_normals = fn.getVertexNormals(False, om.MSpace.kWorld)

        # Per-vertex UV: first polygon-corner occurrence wins.
        vertex_uvs = [(0.0, 0.0)] * vertex_count
        if fn.numUVs() > 0:
            u_arr, v_arr = fn.getUVs()
            for face_id in range(fn.numPolygons):
                poly_verts = fn.getPolygonVertices(face_id)
                for local_idx, vtx_id in enumerate(poly_verts):
                    try:
                        uv_id = fn.getPolygonUVid(face_id, local_idx)
                        if uv_id >= 0:
                            vertex_uvs[vtx_id] = (float(u_arr[uv_id]), float(v_arr[uv_id]))
                    except Exception:
                        pass

        vertices = []
        for i in range(vertex_count):
            p = points[i]
            n = vertex_normals[i]
            uv = vertex_uvs[i]
            vertices.append({
                "position": [p.x, p.y, p.z],
                "normal": [n.x, n.y, n.z],
                "uv": [uv[0], uv[1]],
                "bone_indices": [0],
            })

        # Collect per-face material assignments and group faces contiguously.
        materials, faces = _collect_materials_per_face(shape, fn)

        return {
            "model_name": model_name,
            "vertices": vertices,
            "faces": faces,
            "materials": materials,
            # None → PmxExporter auto-creates a default root bone at origin
            "bones": None,
        }
