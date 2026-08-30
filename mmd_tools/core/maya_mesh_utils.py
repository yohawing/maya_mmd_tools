"""Maya mesh helpers used by MMD import and morph conversion."""

import math

from maya import cmds
from maya.api import OpenMaya as om
from maya.api import OpenMayaAnim as oma

from mmd_tools.core.logger import get_logger

logger = get_logger(__name__)


# A dot product below this bound represents a meaningful authored-vs-geometric
# normal difference (approximately 0.8 degrees for unit vectors).
_AUTHORED_NORMAL_DOT_TOLERANCE = 1.0e-4


def _normalize_normal(normal):
    """Return a finite unit normal, or ``None`` when the input is invalid."""
    try:
        x = float(normal[0])
        y = float(normal[1])
        z = float(normal[2])
    except (IndexError, TypeError, ValueError, OverflowError):
        return None

    if not all(math.isfinite(component) for component in (x, y, z)):
        return None

    length = math.hypot(math.hypot(x, y), z)
    if not math.isfinite(length) or length <= 0.0:
        return None

    normalized = (x / length, y / length, z / length)
    if not all(math.isfinite(component) for component in normalized):
        return None
    return normalized


def has_materially_different_authored_normals(mesh_node):
    """Return whether a mesh has locked authored normals that differ materially.

    The comparison uses Maya's locked face-vertex normal pool against its
    unweighted geometric per-vertex normals. Missing, malformed, or invalid
    mesh data fails closed so callers can safely leave GPU deformation enabled.

    Args:
        mesh_node: Mesh shape or transform name/path.

    Returns:
        ``True`` when at least one locked authored face-vertex normal differs
        beyond the documented dot-product tolerance; otherwise ``False``.
    """
    try:
        selection = om.MSelectionList()
        selection.add(mesh_node)
        dag_path = selection.getDagPath(0)
        if dag_path.node().hasFn(om.MFn.kTransform):
            shape_nodes = cmds.listRelatives(mesh_node, shapes=True, type="mesh", fullPath=True) or []
            if not shape_nodes:
                return False
            shape_selection = om.MSelectionList()
            shape_selection.add(shape_nodes[0])
            dag_path = shape_selection.getDagPath(0)
        if not dag_path.node().hasFn(om.MFn.kMesh):
            return False

        mesh_fn = om.MFnMesh(dag_path)
        normal_counts, normal_ids = mesh_fn.getNormalIds()
        vertex_counts, vertex_ids = mesh_fn.getVertices()
        authored_normals = mesh_fn.getNormals(om.MSpace.kObject)
        geometric_normals = mesh_fn.getVertexNormals(False, om.MSpace.kObject)

        if len(normal_counts) != len(vertex_counts) or len(normal_ids) != len(vertex_ids):
            return False
        if len(authored_normals) == 0 or len(geometric_normals) == 0:
            return False

        for normal_id, vertex_id in zip(normal_ids, vertex_ids):
            if normal_id < 0 or normal_id >= len(authored_normals):
                return False
            if vertex_id < 0 or vertex_id >= len(geometric_normals):
                return False
            if not mesh_fn.isNormalLocked(normal_id):
                continue

            authored = _normalize_normal(authored_normals[normal_id])
            geometric = _normalize_normal(geometric_normals[vertex_id])
            if authored is None or geometric is None:
                return False
            dot = sum(authored[index] * geometric[index] for index in range(3))
            if not math.isfinite(dot):
                return False
            if dot < 1.0 - _AUTHORED_NORMAL_DOT_TOLERANCE:
                return True

        return False
    except (RuntimeError, TypeError, ValueError, IndexError, AttributeError):
        return False


def configure_authored_normal_skin_policy(
    skin_cluster,
    has_authored_normal_difference,
    cmds_module=None,
):
    """Preserve authored normals and selectively opt a skinCluster out of GPU.

    Args:
        skin_cluster: Maya skinCluster node name.
        has_authored_normal_difference: Whether locked authored normals differ
            materially from the mesh's geometric normals.
        cmds_module: Optional maya.cmds-compatible adapter used by tests.
    """
    maya_cmds = cmds if cmds_module is None else cmds_module
    attributes = [("deformUserNormals", True)]
    if has_authored_normal_difference:
        attributes.append(("blockGPU", True))

    for attribute, value in attributes:
        if not maya_cmds.attributeQuery(attribute, node=skin_cluster, exists=True):
            continue
        try:
            maya_cmds.setAttr(f"{skin_cluster}.{attribute}", value)
        except (RuntimeError, TypeError, ValueError):
            logger.warning(
                "Failed to set %s on skinCluster '%s'",
                attribute,
                skin_cluster,
                exc_info=True,
            )


def create_mesh_with_uvs(name, vertices, face_counts, face_connects, uvs, face_uv_connects, normals=None):
    """MayaシーンにUV付きのメッシュオブジェクトを作成します。"""
    mesh_fn = om.MFnMesh()

    points = om.MPointArray()
    for vertex in vertices:
        points.append(om.MPoint(vertex[0], vertex[1], vertex[2]))

    face_counts_array = om.MIntArray()
    for count in face_counts:
        face_counts_array.append(count)

    face_connects_array = om.MIntArray()
    for connect in face_connects:
        face_connects_array.append(connect)

    mesh_obj = mesh_fn.create(points, face_counts_array, face_connects_array)

    if normals:
        normal_array = om.MVectorArray()
        normal_vertex_ids = om.MIntArray()
        if len(normals) == len(vertices):
            for vertex_id, normal in enumerate(normals):
                normalized = _normalize_normal(normal)
                if normalized is None:
                    continue
                normal_array.append(om.MVector(*normalized))
                normal_vertex_ids.append(vertex_id)
            if len(normal_vertex_ids):
                # The bulk setter creates locked user normals. A second lock
                # can recompute mixed valid/fallback entries in Maya 2024.
                mesh_fn.setVertexNormals(normal_array, normal_vertex_ids)
        else:
            normal_face_ids = om.MIntArray()
            cursor = 0
            for face_id, count in enumerate(face_counts):
                for _ in range(count):
                    normalized = _normalize_normal(normals[cursor]) if cursor < len(normals) else None
                    if normalized is not None:
                        normal_array.append(om.MVector(*normalized))
                        normal_face_ids.append(face_id)
                        normal_vertex_ids.append(face_connects[cursor])
                    cursor += 1
            if len(normal_face_ids):
                # setFaceVertexNormals creates locked user normals. Calling
                # lockFaceVertexNormals again resets their values in Maya 2024.
                mesh_fn.setFaceVertexNormals(normal_array, normal_face_ids, normal_vertex_ids)

    if uvs and face_uv_connects:
        uv_set_name = "map1"
        # MFnMesh.create() already provides an empty ``map1`` set.  Calling
        # createUVSet("map1") again makes Maya silently create ``map11``;
        # TEXCOORD0 still reads the current empty ``map1`` and DX11 textures
        # therefore sample a constant texel.  Reuse Maya's existing canonical
        # set and make it current for hardware shaders.
        if uv_set_name not in mesh_fn.getUVSetNames():
            mesh_fn.createUVSet(uv_set_name)
        mesh_fn.setCurrentUVSetName(uv_set_name)

        u_array = om.MFloatArray()
        v_array = om.MFloatArray()
        for i in range(0, len(uvs), 2):
            u_array.append(uvs[i])
            v_array.append(uvs[i + 1])

        uv_counts_array = om.MIntArray()
        for count in face_counts:
            uv_counts_array.append(count)

        uv_connects_array = om.MIntArray()
        for connect in face_uv_connects:
            uv_connects_array.append(connect)

        mesh_fn.setUVs(u_array, v_array, uv_set_name)
        mesh_fn.assignUVs(uv_counts_array, uv_connects_array, uv_set_name)

    dag_path = om.MDagPath.getAPathTo(mesh_obj)
    transform_fn = om.MFnTransform(dag_path.transform())
    transform_fn.setName(name)
    transform_name = transform_fn.fullPathName()

    cmds.sets(transform_name, edit=True, forceElement="initialShadingGroup")
    cmds.select(clear=True)

    return transform_name


def split_mesh_by_material(mesh_name, materials):
    """メッシュをマテリアルごとに分割します。"""
    for material in materials:
        new_mesh = cmds.duplicate(mesh_name, name=f"{mesh_name}_{material.name}")[0]
        cmds.select(new_mesh)
        cmds.hyperShade(assign=material.name)


def get_materials_from_mesh(mesh_name):
    """メッシュに割り当てられているマテリアルを取得"""
    mesh_shapes = cmds.listRelatives(mesh_name, shapes=True, type="mesh") or []
    assigned_materials = []

    for shape in mesh_shapes:
        shading_engines = cmds.listConnections(shape, type="shadingEngine") or []
        for sg in shading_engines:
            materials = cmds.listConnections(f"{sg}.surfaceShader") or []
            assigned_materials.extend(materials)

    return assigned_materials


def apply_vertex_weights(
    skin_cluster,
    mesh_node,
    weights,
):
    """Mayaのメッシュに頂点ウェイトを適用します。"""
    selection_list = om.MSelectionList()
    selection_list.add(skin_cluster)
    skin_cluster_obj = selection_list.getDependNode(0)
    skin_fn = oma.MFnSkinCluster(skin_cluster_obj)

    influence_paths = skin_fn.influenceObjects()
    influence_count = len(influence_paths)

    mesh_selection_list = om.MSelectionList()
    mesh_selection_list.add(mesh_node)
    shape_dag_path = mesh_selection_list.getDagPath(0)
    mesh_fn = om.MFnMesh(shape_dag_path)
    vertex_count = mesh_fn.numVertices

    vertex_component = om.MFnSingleIndexedComponent()
    vertex_component_obj = vertex_component.create(om.MFn.kMeshVertComponent)
    vertex_indices = list(range(vertex_count))
    vertex_component.addElements(vertex_indices)

    influence_indices = list(range(influence_count))

    zero_row = [0.0] * influence_count
    n_weights = len(weights)
    flat = []
    flat_extend = flat.extend
    for vi in range(vertex_count):
        if vi < n_weights:
            row = weights[vi]
            row_len = len(row)
            if row_len >= influence_count:
                flat_extend(row[:influence_count])
            else:
                flat_extend(row)
                flat_extend(zero_row[: influence_count - row_len])
        else:
            flat_extend(zero_row)
    weight_array = om.MDoubleArray(flat)

    try:
        skin_fn.setWeights(
            shape_dag_path,
            vertex_component_obj,
            om.MIntArray(influence_indices),
            weight_array,
            False,
        )
    except TypeError:
        # Maya 2027's Python 3.13 binding can select only the single-influence
        # overload here. Keep the API fast path for older Maya versions and
        # use the command fallback only when that binding defect is observed.
        influence_names = [path.fullPathName() for path in influence_paths]
        for vertex_index in range(vertex_count):
            row_start = vertex_index * influence_count
            row = flat[row_start : row_start + influence_count]
            cmds.skinPercent(
                skin_cluster,
                f"{mesh_node}.vtx[{vertex_index}]",
                transformValue=list(zip(influence_names, row)),
                normalize=False,
            )


def find_or_create_blendshape_node(mesh_node):
    """既存のblendShapeノードを検索または新規作成"""
    if not cmds.objExists(mesh_node):
        raise ValueError(f"Mesh node {mesh_node} does not exist")

    shape_nodes = cmds.listRelatives(mesh_node, shapes=True, type="mesh", fullPath=True)
    if not shape_nodes:
        raise ValueError(f"No mesh shape found for {mesh_node}")

    shape_node = shape_nodes[0]

    history = cmds.listHistory(shape_node, il=2, pdo=False) or []
    blendshapes = []
    for node in history:
        if cmds.nodeType(node) != "blendShape":
            continue
        geometry_paths = set()
        for geometry in cmds.blendShape(node, query=True, geometry=True) or []:
            geometry_paths.update(cmds.ls(geometry, long=True) or [geometry])
        if shape_node in geometry_paths:
            blendshapes.append(node)
    if blendshapes:
        return blendshapes[0]
    return cmds.blendShape(mesh_node)[0]
