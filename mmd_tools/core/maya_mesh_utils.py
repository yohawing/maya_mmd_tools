"""Maya mesh helpers used by MMD import and morph conversion."""

from maya import cmds
from maya.api import OpenMaya as om
from maya.api import OpenMayaAnim as oma

from mmd_tools.core import settings_keys as setting_keys
from mmd_tools.core.settings import settings


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

    if normals and len(normals) == len(vertices):
        normal_array = om.MVectorArray()
        normal_vertex_ids = om.MIntArray()
        for vertex_id, normal in enumerate(normals):
            normal_array.append(om.MVector(normal[0], normal[1], normal[2]))
            normal_vertex_ids.append(vertex_id)
        mesh_fn.setVertexNormals(normal_array, normal_vertex_ids)
    elif normals:
        normal_array = om.MVectorArray()
        normal_face_ids = om.MIntArray()
        normal_vertex_ids = om.MIntArray()
        face_id = 0
        cursor = 0
        for count in face_counts:
            for _ in range(count):
                vertex_id = face_connects[cursor]
                normal = normals[cursor]
                normal_array.append(om.MVector(normal[0], normal[1], normal[2]))
                normal_face_ids.append(face_id)
                normal_vertex_ids.append(vertex_id)
                cursor += 1
            face_id += 1
        mesh_fn.setFaceVertexNormals(normal_array, normal_face_ids, normal_vertex_ids)

    if uvs and face_uv_connects:
        uv_set_name = settings.get(setting_keys.IMPORT_MODEL_UV_SET_NAME).replace("#", "1")
        mesh_fn.createUVSet(uv_set_name)

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
