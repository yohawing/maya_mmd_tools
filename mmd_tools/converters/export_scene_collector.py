"""Minimum scene-data collector for PMX export.

Collects one or more polygon meshes from the Maya scene and returns a dict
compatible with ``PmxExporter.export_pmx_model``.

Coordinate conventions
----------------------
This collector exports Maya world-space geometry back into MMD basis by
flipping Z for positions/normals and reversing face winding. Scale
normalization is out of scope for this minimum slice and must be added in a
later collector pass.
"""

import json
from typing import Optional

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
from maya import cmds

from mmd_tools.converters.morph_converter import MorphConverter
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MODEL_NAME,
)
from mmd_tools.core.coordinate_transform import maya_point_to_mmd
from mmd_tools.core.display_frame_metadata import display_frames_from_json
from mmd_tools.core.morph_metadata_reader import parse_blendshape_morph_names


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


def _get_model_name(node: str) -> str:
    """Return the MMD model name on *node*, falling back to its short DAG name."""
    if cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=node, exists=True):
        val = cmds.getAttr(f"{node}.{ATTR_MMD_MODEL_NAME}")
        if val:
            return val
    return node.rsplit("|", 1)[-1]


def _get_attr(node: str, attr: str, default=None):
    """Return attr value if it exists, otherwise *default*."""
    if cmds.attributeQuery(attr, node=node, exists=True):
        value = cmds.getAttr(f"{node}.{attr}")
        if value is not None:
            return value
    return default


def _collect_display_frames(root: str) -> list[dict]:
    """Return root-level PMX display-frame metadata collected during import."""
    return display_frames_from_json(_get_attr(root, ATTR_MMD_DISPLAY_FRAMES_JSON, ""))


def _maya_to_mmd_vector(values) -> list[float]:
    """Convert a Maya XYZ vector to MMD basis by flipping Z."""
    return list(maya_point_to_mmd(values))


def _list_export_mesh_shapes(root: str) -> list:
    """Return non-intermediate mesh shapes under *root* in deterministic DAG order."""
    if cmds.nodeType(root) == "mesh":
        shapes = [root]
    else:
        direct_shapes = cmds.listRelatives(root, shapes=True, type="mesh", fullPath=True) or []
        child_shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
        shapes = direct_shapes + child_shapes

    unique_shapes = []
    seen = set()
    for shape in sorted(shapes):
        if shape in seen:
            continue
        seen.add(shape)
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        unique_shapes.append(shape)
    return unique_shapes


def _find_skin_cluster(shape: str) -> str | None:
    """Return the first skinCluster in the mesh history, if any."""
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    for node in history:
        if cmds.nodeType(node) == "skinCluster":
            return node
    return None


def _find_blend_shapes(shape: str) -> list[str]:
    """Return blendShape nodes in the mesh history."""
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    return [node for node in history if cmds.nodeType(node) == "blendShape"]


def _collect_skin_bones(skin_cluster: str) -> tuple[list[dict], dict[str, int]]:
    """Collect exporter bone dicts from skinCluster influences."""
    influences = cmds.skinCluster(skin_cluster, query=True, influence=True) or []

    def sort_key(joint):
        stored_index = _get_attr(joint, ATTR_MMD_BONE_INDEX)
        if stored_index is None:
            return (1, joint)
        return (0, int(stored_index))

    ordered = sorted(influences, key=sort_key)
    export_index_by_joint = {joint: index for index, joint in enumerate(ordered)}
    stored_to_export = {}
    for joint, index in export_index_by_joint.items():
        stored_index = _get_attr(joint, ATTR_MMD_BONE_INDEX)
        if stored_index is not None:
            stored_to_export[int(stored_index)] = index

    bones = []
    for joint in ordered:
        parent_index = -1
        stored_parent = _get_attr(joint, ATTR_MMD_BONE_PARENT_INDEX)
        if stored_parent is not None:
            parent_index = stored_to_export.get(int(stored_parent), -1)
        else:
            parent = (cmds.listRelatives(joint, parent=True, type="joint") or [None])[0]
            if parent in export_index_by_joint:
                parent_index = export_index_by_joint[parent]

        position = cmds.xform(joint, query=True, worldSpace=True, translation=True)
        bones.append({
            "name": _get_attr(joint, ATTR_MMD_BONE_NAME, joint.rsplit("|", 1)[-1]),
            "name_english": _get_attr(joint, ATTR_MMD_BONE_NAME_EN, ""),
            "position": _maya_to_mmd_vector(position),
            "parent_index": parent_index,
            "source_joint": joint,
        })
    return bones, export_index_by_joint


def _joint_export_index_from_dag_path(path: om.MDagPath, export_index_by_joint: dict[str, int]) -> int:
    """Return exporter bone index for an influence dag path."""
    candidates = [
        path.fullPathName(),
        path.partialPathName(),
        path.fullPathName().rsplit("|", 1)[-1],
        path.partialPathName().rsplit("|", 1)[-1],
    ]
    for candidate in candidates:
        if candidate in export_index_by_joint:
            return export_index_by_joint[candidate]
    raise KeyError(path.fullPathName())


def _normalize_export_skin_pairs(pairs: list[tuple[int, float]]) -> dict:
    """Normalize, trim, and format one vertex's exporter skin weights."""
    pairs = [(bone_index, float(weight)) for bone_index, weight in pairs if float(weight) > 1e-8]
    if not pairs:
        pairs = [(0, 1.0)]
    pairs.sort(key=lambda pair: pair[1], reverse=True)
    pairs = pairs[:4]
    total = sum(weight for _bone_index, weight in pairs)
    if total > 0.0:
        pairs = [(bone_index, weight / total) for bone_index, weight in pairs]
    if len(pairs) == 3:
        pairs.append((pairs[-1][0], 0.0))
    return {
        "bone_indices": [bone_index for bone_index, _weight in pairs],
        "bone_weights": [weight for _bone_index, weight in pairs],
    }


def _collect_vertex_skin_weights_api(
    skin_cluster: str,
    shape: str,
    vertex_count: int,
    export_index_by_joint: dict[str, int],
) -> Optional[list]:
    """Collect all skin weights in one MFnSkinCluster.getWeights call."""
    try:
        selection = om.MSelectionList()
        selection.add(skin_cluster)
        skin_obj = selection.getDependNode(0)
        skin_fn = oma.MFnSkinCluster(skin_obj)

        shape_selection = om.MSelectionList()
        shape_selection.add(shape)
        shape_path = shape_selection.getDagPath(0)

        component_fn = om.MFnSingleIndexedComponent()
        component = component_fn.create(om.MFn.kMeshVertComponent)
        component_fn.addElements(list(range(vertex_count)))

        weights, influence_count = skin_fn.getWeights(shape_path, component)
        influence_count = int(influence_count)
        if influence_count <= 0 or len(weights) < vertex_count * influence_count:
            return None

        influence_export_indices = [
            _joint_export_index_from_dag_path(path, export_index_by_joint)
            for path in skin_fn.influenceObjects()
        ]
        if len(influence_export_indices) < influence_count:
            return None

        vertex_weights = []
        for vertex_index in range(vertex_count):
            offset = vertex_index * influence_count
            pairs = [
                (influence_export_indices[influence_index], float(weights[offset + influence_index]))
                for influence_index in range(influence_count)
            ]
            vertex_weights.append(_normalize_export_skin_pairs(pairs))
        return vertex_weights
    except Exception:
        return None


def _collect_vertex_skin_weights_cmds(
    skin_cluster: str,
    shape: str,
    vertex_count: int,
    export_index_by_joint: dict[str, int],
) -> list:
    """Collect skin weights via cmds.skinPercent fallback."""
    influences = cmds.skinCluster(skin_cluster, query=True, influence=True) or []
    influence_export_indices = [export_index_by_joint[joint] for joint in influences]
    vertex_weights = []
    for vertex_index in range(vertex_count):
        weights = cmds.skinPercent(
            skin_cluster,
            f"{shape}.vtx[{vertex_index}]",
            query=True,
            value=True,
        ) or []
        vertex_weights.append(_normalize_export_skin_pairs(list(zip(influence_export_indices, weights))))
    return vertex_weights


def _collect_vertex_skin_weights(skin_cluster: str, shape: str, vertex_count: int, export_index_by_joint: dict[str, int]) -> list:
    """Collect per-vertex exporter skinning fields from a skinCluster."""
    api_weights = _collect_vertex_skin_weights_api(skin_cluster, shape, vertex_count, export_index_by_joint)
    if api_weights is not None:
        return api_weights
    return _collect_vertex_skin_weights_cmds(skin_cluster, shape, vertex_count, export_index_by_joint)


def _blendshape_aliases_by_index(blend_shape: str) -> dict[int, str]:
    """Return target index -> alias name for a blendShape node."""
    aliases = {}
    flat = cmds.aliasAttr(blend_shape, query=True) or []
    for alias, attr in zip(flat[0::2], flat[1::2]):
        if "[" not in attr or "]" not in attr:
            continue
        try:
            index = int(attr.rsplit("[", 1)[1].split("]", 1)[0])
        except ValueError:
            continue
        aliases[index] = alias
    return aliases


def _blendshape_stored_names(blend_shape: str) -> dict[int, str]:
    """Return target index -> raw PMX morph name stored by MorphConverter."""
    if not cmds.attributeQuery(ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, node=blend_shape, exists=True):
        return {}
    try:
        raw = cmds.getAttr(f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}") or "{}"
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parse_blendshape_morph_names(parsed)


def _blendshape_target_indices(blend_shape: str) -> list[int]:
    """Return deterministic target indices from aliases, stored names, and weight plugs."""
    indices = set(_blendshape_aliases_by_index(blend_shape))
    indices.update(_blendshape_stored_names(blend_shape))
    try:
        indices.update(int(index) for index in (cmds.getAttr(f"{blend_shape}.w", multiIndices=True) or []))
    except Exception:
        pass
    return sorted(indices)


def _blendshape_geometry_index(blend_shape: str, shape: str) -> int:
    """Return the blendShape logical geometry index for *shape*."""
    geometries = cmds.blendShape(blend_shape, query=True, geometry=True) or []
    geometry_indices = cmds.blendShape(blend_shape, query=True, geometryIndices=True) or []
    if len(geometries) != len(geometry_indices):
        raise ValueError(
            f"blendShape '{blend_shape}' returned mismatched geometry names and indices"
        )

    shape_paths = set(cmds.ls(shape, long=True) or [shape])
    for geometry, geometry_index in zip(geometries, geometry_indices):
        if shape_paths.intersection(cmds.ls(geometry, long=True) or [geometry]):
            return int(geometry_index)
    raise ValueError(f"blendShape '{blend_shape}' has no geometry entry for shape '{shape}'")


def _stored_blendshape_target_offsets(
    blend_shape: str,
    shape: str,
    geometry_index: int,
    target_index: int,
    vertex_count: int,
    vertex_offset: int,
) -> list[dict]:
    """Read one full-weight target's sparse saved deltas without evaluating the DG."""
    group = f"{blend_shape}.inputTarget[{geometry_index}].inputTargetGroup[{target_index}]"
    item_indices = cmds.getAttr(f"{group}.inputTargetItem", multiIndices=True) or []
    if not item_indices and target_index in _blendshape_target_indices(blend_shape):
        # Weight indices are shared by all geometries on a blendShape, but a
        # target group need not exist for every geometry.
        return []
    if 6000 not in item_indices:
        raise ValueError(
            f"blendShape '{blend_shape}' target {target_index} geometry {geometry_index} "
            "has no full-weight inputTargetItem[6000]"
        )

    item = f"{group}.inputTargetItem[6000]"
    points = cmds.getAttr(f"{item}.inputPointsTarget")
    components = cmds.getAttr(f"{item}.inputComponentsTarget")
    if points is None and components is None:
        return []
    points = points or []
    components = components or []
    qualified_components = [
        component if ".vtx[" in component else f"{shape}.{component}"
        for component in components
    ]
    flattened = cmds.ls(qualified_components, flatten=True) or []
    if len(points) != len(flattened):
        raise ValueError(
            f"blendShape '{blend_shape}' target {target_index} geometry {geometry_index} "
            f"has {len(points)} points but {len(flattened)} components"
        )

    offsets = []
    for point, component in zip(points, flattened):
        try:
            vertex_index = int(component.rsplit(".vtx[", 1)[1].split("]", 1)[0])
        except (IndexError, ValueError) as exc:
            raise ValueError(
                f"blendShape '{blend_shape}' target {target_index} geometry {geometry_index} "
                f"has invalid vertex component '{component}'"
            ) from exc
        if not 0 <= vertex_index < vertex_count:
            raise ValueError(
                f"blendShape '{blend_shape}' target {target_index} geometry {geometry_index} "
                f"references out-of-range vertex {vertex_index}"
            )
        try:
            delta = [float(point[axis]) for axis in range(3)]
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError(
                f"blendShape '{blend_shape}' target {target_index} geometry {geometry_index} "
                f"has invalid point data {point!r}"
            ) from exc
        if all(abs(value) <= 1e-8 for value in delta):
            continue
        offsets.append({
            "vertex_index": vertex_index + vertex_offset,
            "position_offset": _maya_to_mmd_vector(delta),
        })
    return offsets


def _collect_vertex_morphs(shape: str, vertex_offset: int = 0) -> list[dict]:
    """Collect PMX VertexMorph dicts from blendShape targets on *shape*.

    The importer applies PMX vertex offsets in Maya mesh space by negating Z.
    Export inverts that local target delta back to PMX offset space.
    """
    vertex_count = int(cmds.polyEvaluate(shape, vertex=True))
    morphs = []

    for blend_shape in _find_blend_shapes(shape):
        target_indices = _blendshape_target_indices(blend_shape)
        if not target_indices:
            continue

        aliases = _blendshape_aliases_by_index(blend_shape)
        stored_names = _blendshape_stored_names(blend_shape)
        geometry_index = _blendshape_geometry_index(blend_shape, shape)
        for target_index in target_indices:
            offsets = _stored_blendshape_target_offsets(
                blend_shape,
                shape,
                geometry_index,
                target_index,
                vertex_count,
                vertex_offset,
            )
            if not offsets:
                continue

            morph_name = stored_names.get(target_index) or aliases.get(target_index) or f"VertexMorph{target_index}"
            morphs.append({
                "type": "vertex",
                "name": morph_name,
                "name_english": morph_name,
                "panel": 4,
                "offsets": offsets,
            })

    return morphs


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
        faces = [list(reversed(fn.getPolygonVertices(i))) for i in range(num_polys)]
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
        group_faces = [list(reversed(face)) for face in group_faces]
        mat["face_count"] = sum(max(0, len(f) - 2) * 3 for f in group_faces)
        materials.append(mat)
        faces.extend(group_faces)

    return materials, faces


class ExportSceneCollector:
    """Collect minimum PMX-compatible data from Maya polygon meshes.

    This is a *minimum* collector: it captures per-vertex positions, normals,
    UVs (first-occurrence per vertex), polygon connectivity, per-face
    material assignment, skinCluster weights, vertex blendShape morphs, and
    root-level bone/material morph metadata.  It converts positions, normals,
    and face winding back to MMD basis; scale normalization belongs to a later
    collector slice.

    Example::

        collector = ExportSceneCollector()
        maya_data = collector.collect_from_mesh("pCubeTransform")
        exporter = PmxExporter()
        exporter.export_pmx_model("/tmp/out.pmx", maya_data)
    """

    def collect(self, options: dict) -> dict:
        """Collect model data from common export options.

        ``target_model`` / ``model_root`` collect all descendant meshes.
        ``target_mesh`` / ``export_mesh`` / ``mesh`` collect a single mesh.
        """
        model_root = options.get("target_model") or options.get("model_root")
        if model_root:
            return self.collect_from_model_root(model_root)

        target_mesh = options.get("target_mesh") or options.get("export_mesh") or options.get("mesh")
        if target_mesh:
            return self.collect_from_mesh(target_mesh)

        raise ValueError("ExportSceneCollector requires target_model, model_root, or target_mesh")

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

        model_name = _get_model_name(transform)

        # Build MFnMesh handle
        sel = om.MSelectionList()
        sel.add(shape)
        dag = sel.getDagPath(0)
        fn = om.MFnMesh(dag)

        vertex_count = fn.numVertices
        skin_cluster = _find_skin_cluster(shape)
        bones = None
        skin_weights = None
        if skin_cluster:
            bones, export_index_by_joint = _collect_skin_bones(skin_cluster)
            skin_weights = _collect_vertex_skin_weights(skin_cluster, shape, vertex_count, export_index_by_joint)

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
                "position": _maya_to_mmd_vector([p.x, p.y, p.z]),
                "normal": _maya_to_mmd_vector([n.x, n.y, n.z]),
                "uv": [uv[0], uv[1]],
                "bone_indices": skin_weights[i]["bone_indices"] if skin_weights else [0],
                "bone_weights": skin_weights[i]["bone_weights"] if skin_weights else [],
            })

        # Collect per-face material assignments and group faces contiguously.
        materials, faces = _collect_materials_per_face(shape, fn)

        return {
            "model_name": model_name,
            "vertices": vertices,
            "faces": faces,
            "materials": materials,
            # None → exporters auto-create a default root bone at origin
            "bones": bones,
            "morphs": _collect_vertex_morphs(shape),
        }

    def collect_from_model_root(self, root: str) -> dict:
        """Collect and merge all polygon meshes below an MMD model root.

        This keeps the same minimum-slice limitations as ``collect_from_mesh``:
        world-space geometry is converted back to MMD basis, but scale
        normalization is still out of scope.
        """
        shapes = _list_export_mesh_shapes(root)
        if not shapes:
            raise ValueError(f"No mesh shapes found under model root '{root}'")

        merged_vertices = []
        merged_faces = []
        merged_materials = []
        merged_bones = []
        vertex_morphs_by_name = {}
        global_bone_by_key = {}
        vertex_offset = 0

        for shape in shapes:
            mesh_data = self.collect_from_mesh(shape)
            local_bones = mesh_data["bones"] or []
            bone_index_map = {}
            for local_index, bone in enumerate(local_bones):
                key = bone.get("source_joint") or f"{bone.get('name')}:{bone.get('name_english')}:{bone.get('position')}"
                if key not in global_bone_by_key:
                    global_bone_by_key[key] = len(merged_bones)
                    merged_bones.append(dict(bone))
                bone_index_map[local_index] = global_bone_by_key[key]
            for local_index, bone in enumerate(local_bones):
                parent_index = bone.get("parent_index", -1)
                merged_bones[bone_index_map[local_index]]["parent_index"] = bone_index_map.get(parent_index, -1)

            mesh_vertices = []
            for vertex in mesh_data["vertices"]:
                merged_vertex = dict(vertex)
                if local_bones:
                    merged_vertex["bone_indices"] = [
                        bone_index_map[index] for index in vertex.get("bone_indices", [0])
                    ]
                mesh_vertices.append(merged_vertex)

            merged_vertices.extend(mesh_vertices)
            for face in mesh_data["faces"]:
                merged_faces.append([index + vertex_offset for index in face])
            merged_materials.extend(mesh_data["materials"])
            for morph in mesh_data.get("morphs", []):
                key = (morph.get("type"), morph.get("name"))
                if key not in vertex_morphs_by_name:
                    vertex_morphs_by_name[key] = dict(morph)
                    vertex_morphs_by_name[key]["offsets"] = []
                vertex_morphs_by_name[key]["offsets"].extend(
                    {
                        **offset,
                        "vertex_index": int(offset["vertex_index"]) + vertex_offset,
                    }
                    for offset in morph.get("offsets", [])
                )
            vertex_offset += len(mesh_vertices)

        morphs = list(vertex_morphs_by_name.values())
        morphs.extend(MorphConverter().collect_morphs_from_scene_for_export())

        bone_index_by_joint: dict[str, int] = {}
        for key, index in global_bone_by_key.items():
            bone = merged_bones[index]
            source_joint = bone.get("source_joint")
            if source_joint:
                bone_index_by_joint[source_joint] = index
                for long_name in cmds.ls(source_joint, long=True) or []:
                    bone_index_by_joint[long_name] = index
                bone_index_by_joint[source_joint.rsplit("|", 1)[-1]] = index

        from .physics_export_collector import collect_physics_from_scene
        rigid_bodies, joints = collect_physics_from_scene(root, bone_index_by_joint)

        return {
            "model_name": _get_model_name(root),
            "vertices": merged_vertices,
            "faces": merged_faces,
            "materials": merged_materials,
            "bones": merged_bones or None,
            "morphs": morphs,
            "display_frames": _collect_display_frames(root),
            "rigid_bodies": rigid_bodies,
            "joints": joints,
        }
