import os

from maya import cmds
from maya import mel
from maya.api import OpenMaya as om
from maya.api import OpenMayaAnim as oma


from mmd_tools.core.constants import ATTR_MMD_MODEL_NAME_EN, ATTR_MMD_MODEL_NAME
from mmd_tools.core.settings import settings

from . import utils
from .logger import get_logger

logger = get_logger(__name__)


def sanitize_text(name):
    """
    Maya用に名前をサニタイズする。
    日本語などのマルチバイト文字をASCII文字に変換し、Maya互換の名前にする。

    Args:
        name (str): 元の名前

    Returns:
        str: Maya互換の名前
    """
    if not name:
        return "unnamed"

    converted_name = utils.convert_utf8_to_ascii(name)
    return converted_name or "default_name"


def sanitize_texture_path(texture_path, texture_dir):
    """
    テクスチャパスをMaya用にサニタイズする。

    Args:
        texture_path (str): 元のテクスチャパス
        texture_dir (str): テクスチャディレクトリ

    Returns:
        str: Maya互換のテクスチャパス、またはNone
    """
    if not texture_path:
        return None

    # 絶対パスを構築
    if not os.path.isabs(texture_path):
        full_texture_path = os.path.join(texture_dir, texture_path)
    else:
        full_texture_path = texture_path

    # パスの正規化
    full_texture_path = os.path.normpath(full_texture_path)

    # ファイルの存在確認
    if not os.path.exists(full_texture_path):
        print(f"Warning: Texture file not found: {full_texture_path}")
        return None

    return full_texture_path


def create_mesh_with_uvs(name, vertices, face_counts, face_connects, uvs, face_uv_connects):
    """
    MayaシーンにUV付きのメッシュオブジェクトを作成します。
    OpenMaya APIを使用して高速化。

    Args:
        name (str): 作成するメッシュオブジェクトの名前。
        vertices (list[tuple[float, float, float]]): 頂点座標のリスト。
        face_counts (list[int]): 各面の頂点数のリスト。
        face_connects (list[int]): 面を構成する頂点インデックスのリスト。
        uvs (list[float]): UV座標のフラットなリスト (u1, v1, u2, v2, ...)。
        face_uv_connects (list[int]): 各面の各頂点に対応するUVのインデックスリスト。

    Returns:
        str: 作成されたメッシュのトランスフォームノード名。
    """
    # OpenMaya APIを使用してメッシュを作成
    mesh_fn = om.MFnMesh()

    # 頂点データをOpenMayaのMPointArrayに変換
    points = om.MPointArray()
    for vertex in vertices:
        points.append(om.MPoint(vertex[0], vertex[1], vertex[2]))

    # 面データをOpenMayaのMIntArrayに変換
    face_counts_array = om.MIntArray()
    for count in face_counts:
        face_counts_array.append(count)

    face_connects_array = om.MIntArray()
    for connect in face_connects:
        face_connects_array.append(connect)

    # メッシュを作成
    mesh_obj = mesh_fn.create(points, face_counts_array, face_connects_array)

    # UVセットを作成
    if uvs and face_uv_connects:
        # TODO: UVセットが複数ある場合に対応する。
        uv_set_name = settings.get("import.model.uv_set_name").replace("#", "1")
        mesh_fn.createUVSet(uv_set_name)

        # UV座標をMFloatArrayに変換
        u_array = om.MFloatArray()
        v_array = om.MFloatArray()
        for i in range(0, len(uvs), 2):
            u_array.append(uvs[i])
            v_array.append(uvs[i + 1])

        # UV接続をMIntArrayに変換
        uv_counts_array = om.MIntArray()
        for count in face_counts:
            uv_counts_array.append(count)

        uv_connects_array = om.MIntArray()
        for connect in face_uv_connects:
            uv_connects_array.append(connect)

        # UVを設定
        mesh_fn.setUVs(u_array, v_array, uv_set_name)
        mesh_fn.assignUVs(uv_counts_array, uv_connects_array, uv_set_name)

    # トランスフォームノードを作成
    dag_path = om.MDagPath.getAPathTo(mesh_obj)
    transform_fn = om.MFnTransform(dag_path.transform())
    transform_name = transform_fn.setName(name)

    # デフォルトのシェーディンググループに割り当て
    cmds.sets(transform_name, edit=True, forceElement="initialShadingGroup")
    cmds.select(clear=True)

    return transform_name


def split_mesh_by_material(mesh_name, materials):
    """
    メッシュをマテリアルごとに分割します。

    Args:
        mesh_name (str): 分割するメッシュの名前。
        materials (list): マテリアルのリスト。
    """
    for material in materials:
        # マテリアルごとに新しいメッシュを作成
        new_mesh = cmds.duplicate(mesh_name, name=f"{mesh_name}_{material.name}")[0]
        # マテリアルを割り当て
        cmds.select(new_mesh)
        cmds.hyperShade(assign=material.name)


def create_material(name, color, texture_path=None, texture_dir=""):
    """
    Mayaシーンにマテリアルを作成します。

    Args:
        name (str): マテリアルの名前。
        color (tuple[float, float, float, float]): RGBAカラー。
        texture_path (str, optional): テクスチャファイルのパス。
        texture_dir (str, optional): テクスチャファイルが置かれているディレクトリ。

    Returns:
        str: 作成されたシェーダーノード名。
    """
    sanitized_name = sanitize_text(name)
    shader = cmds.shadingNode("lambert", asShader=True, name=sanitized_name)
    set_attribute(shader, "color", color[:3], "double3")
    transparency = 1.0 - color[3]
    set_attribute(shader, "transparency", [transparency, transparency, transparency], "double3")

    # 元の名前を保持
    set_custom_attributes(shader, {"mmd_material_name": name})

    if texture_path:
        # テクスチャパスを解決
        full_texture_path = os.path.join(texture_dir, texture_path)
        if os.path.exists(full_texture_path):
            file_node = cmds.shadingNode("file", asTexture=True, name=sanitized_name + "_file")
            place_uv_node = cmds.shadingNode(
                "place2dTexture",
                asUtility=True,
                name=sanitized_name + "_place2dTexture",
            )
            # 標準的なUV接続
            cmds.connectAttr(place_uv_node + ".outUV", file_node + ".uvCoord")
            cmds.connectAttr(file_node + ".outColor", shader + ".color")

            cmds.setAttr(file_node + ".fileTextureName", full_texture_path, type="string")
        else:
            cmds.warning(f"Texture file not found: {full_texture_path}")

    return shader


def assign_material(mesh_name, shader_node):
    """
    メッシュにマテリアルを割り当てます。

    Args:
        mesh_name (str): マテリアルを割り当てるメッシュの名前。
        shader_node (str): 割り当てるシェーダーノード名。
    """
    # マテリアル専用のシェーディンググループを作成
    sanitized_shader_name = shader_node + "SG"
    sg_name = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sanitized_shader_name)
    # シェーダーをシェーディンググループに接続
    cmds.connectAttr(shader_node + ".outColor", f"{sg_name}.surfaceShader", force=True)
    # メッシュをシェーディンググループに割り当て
    cmds.sets(mesh_name, edit=True, forceElement=sg_name)


def assign_material_to_faces(mesh_name, shader_node, face_selection):
    """
    メッシュの特定の面にマテリアルを割り当てます。

    Args:
        mesh_name (str): マテリアルを割り当てるメッシュの名前。
        shader_node (str): 割り当てるシェーダーノード名。
        face_selection (str): 選択する面の指定。例: "mesh_name.f[1:10]"
    """
    # シェーダーノードの存在確認
    if not cmds.objExists(shader_node):
        logger.error(f"Shader node '{shader_node}' does not exist")
        return

    # マテリアル専用のシェーディンググループを作成
    sanitized_shader_name = shader_node + "SG"
    sg_name = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sanitized_shader_name)

    # シェーダーのタイプに応じて適切な接続を行う
    shader_type = cmds.nodeType(shader_node)

    if shader_type == "dx11Shader":
        # dx11Shaderは直接surfaceShaderに接続
        cmds.connectAttr(shader_node + ".message", f"{sg_name}.surfaceShader", force=True)
    else:
        # 標準シェーダーは.outColorを使用
        cmds.connectAttr(shader_node + ".outColor", f"{sg_name}.surfaceShader", force=True)

    # 指定した面をシェーディンググループに割り当て
    cmds.sets(face_selection, edit=True, forceElement=sg_name)


def set_custom_attributes(object_name, attributes):
    """
    Mayaオブジェクトにカスタムアトリビュートを設定します。
    存在しないアトリビュートは自動的に作成されます。
    cmds.setAttrの代わりにOpenMaya APIを使用して高速化します。

    Args:
        object_name (str): カスタムアトリビュートを設定するオブジェクトの名前。
        attributes (dict): 属性名と値の辞書。

    Example:
        set_custom_attributes("pCube1", {
            "customAttr1": 1.0,
            "customAttr2": "example",
            "customAttr3": [0.5, 0.5, 0.5],
        })
    """
    for attr_name, attr_value in attributes.items():
        attr_type = type(attr_value).__name__
        actual_attr_type = attr_type  # 実際に使用する型を保存

        # アトリビュートが存在しない場合は作成
        if not cmds.attributeQuery(attr_name, node=object_name, exists=True):
            if attr_type in ["int", "float", "bool"]:
                add_numeric_attribute(object_name, attr_name, attr_type)
            elif attr_type in ["str", "bytes"]:
                add_typed_attribute(object_name, attr_name, attr_type)
            elif attr_type in ["list", "tuple"]:
                # リストやタプルの場合は型を指定
                if len(attr_value) == 3 and all(isinstance(x, float) for x in attr_value):
                    actual_attr_type = "double3"
                elif len(attr_value) == 3 and all(isinstance(x, int) for x in attr_value):
                    actual_attr_type = "long3"
                elif len(attr_value) == 4 and all(isinstance(x, (float, int)) for x in attr_value):
                    actual_attr_type = "double4"
                elif all(isinstance(x, float) for x in attr_value):
                    actual_attr_type = "doubleArray"
                elif all(isinstance(x, int) for x in attr_value):
                    actual_attr_type = "longArray"
                add_typed_attribute(object_name, attr_name, actual_attr_type)

        # 値を設定（既存・新規両方に対応）
        try:
            if attr_type in ["int", "float", "bool", "str", "bytes"]:
                set_attribute(object_name, attr_name, attr_value, attr_type)
            elif attr_type in ["list", "tuple"]:
                set_attribute(object_name, attr_name, attr_value, actual_attr_type)
        except Exception as e:
            logger.warning(f"Failed to set attribute {attr_name} on {object_name}: {e}")


def add_numeric_attribute(object_name, attr_name, attr_type):
    """
    OpenMaya API 2.0を使用して数値型のアトリビュートを追加します。

    Args:
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名
        attr_type (str): アトリビュートタイプ (long, float, bool)
    """
    try:
        # オブジェクトのMObjectを取得
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        node_obj = selection_list.getDependNode(0)
        depend_fn = om.MFnDependencyNode(node_obj)

        # 数値アトリビュートを作成
        attr = om.MFnNumericAttribute()

        if attr_type == "int":
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.kInt)
        elif attr_type == "float":
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.kFloat)
        elif attr_type == "bool":
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.kBoolean)
        else:
            raise ValueError(f"Unsupported numeric attribute type: {attr_type}")

        # アトリビュートを追加
        depend_fn.addAttribute(attr_obj)

    except Exception as e:
        logger.error(f"Failed to add numeric attribute '{attr_name}' to '{object_name}': {e}")


def add_typed_attribute(object_name, attr_name, attr_type):
    """
    OpenMaya API 2.0を使用して型付きアトリビュートを追加します。

    Args:
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名
        attr_type (str): アトリビュートタイプ (string, double3, long3, double4, doubleArray, longArray)
    """
    try:
        # オブジェクトのMObjectを取得
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        node_obj = selection_list.getDependNode(0)
        depend_fn = om.MFnDependencyNode(node_obj)

        if attr_type == "str":
            # 文字列アトリビュート
            attr = om.MFnTypedAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnData.kString)
        elif attr_type == "bytes":
            # バイトアトリビュート
            attr = om.MFnTypedAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnData.kString)
        elif attr_type == "double3":
            # 3つのdouble値を持つアトリビュート
            attr = om.MFnNumericAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.k3Double)
        elif attr_type == "long3":
            # 3つのint値を持つアトリビュート
            attr = om.MFnNumericAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.k3Int)
        elif attr_type == "double4":
            # 4つのdouble値を持つアトリビュート
            attr = om.MFnNumericAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnNumericData.k4Double)
        elif attr_type == "doubleArray":
            # double配列アトリビュート
            attr = om.MFnTypedAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnData.kDoubleArray)
        elif attr_type == "longArray":
            # long配列アトリビュート
            attr = om.MFnTypedAttribute()
            attr_obj = attr.create(attr_name, attr_name, om.MFnData.kIntArray)
        else:
            raise ValueError(f"Unsupported typed attribute type: {attr_type}")

        # アトリビュートを追加
        depend_fn.addAttribute(attr_obj)

    except Exception as e:
        logger.error(f"Failed to add typed attribute '{attr_name}' to '{object_name}': {e}")


def set_attribute(object_name, attr_name, attr_value, attr_type):
    """
    OpenMaya API 2.0を使用してアトリビュート値を設定します。
    cmds.setAttrの代わりに使用します。

    Args:
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名
        attr_value: 設定する値
        attr_type (str, optional): アトリビュートタイプ（配列の場合に必要）

    Example:
        set_attribute("pCube1", "customAttr1", 1.0, "float")
        set_attribute("pCube1", "customAttr2", "example", "str")
        set_attribute("pCube1", "customAttr3", [0.5, 0.5, 0.5], "double3")
    """
    try:
        # オブジェクトのMObjectを取得
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        node_obj = selection_list.getDependNode(0)
        depend_fn = om.MFnDependencyNode(node_obj)

        # プラグを取得
        plug = depend_fn.findPlug(attr_name, False)

        # 値の型に応じて設定
        if attr_type == "bool":
            plug.setBool(attr_value)
        elif attr_type == "int" or attr_type == "long":
            plug.setInt(attr_value)
        elif attr_type == "float":
            plug.setFloat(attr_value)
        elif attr_type == "double":
            plug.setDouble(attr_value)
        elif attr_type == "str" or attr_type == "string":
            plug.setString(attr_value)
        elif attr_type == "bytes":
            # バイトデータは文字列として設定
            plug.setString(attr_value.decode("utf-8"))
        elif attr_type == "double3" and len(attr_value) == 3:
            # 3要素のベクトル値
            for i, value in enumerate(attr_value):
                child_plug = plug.child(i)
                child_plug.setDouble(value)
        elif attr_type == "long3" and len(attr_value) == 3:
            # 3要素の整数値
            for i, value in enumerate(attr_value):
                child_plug = plug.child(i)
                child_plug.setInt(value)
        elif attr_type == "double4" and len(attr_value) == 4:
            # 4要素のベクトル値
            for i, value in enumerate(attr_value):
                child_plug = plug.child(i)
                child_plug.setDouble(value)
        elif attr_type == "doubleArray":
            double_array_data = om.MFnDoubleArrayData()
            double_array_obj = double_array_data.create()
            double_array = om.MDoubleArray(attr_value)
            double_array_data.set(double_array)
            plug.setMObject(double_array_obj)
        elif attr_type == "longArray":
            int_array_data = om.MFnIntArrayData()
            int_array_obj = int_array_data.create()
            int_array = om.MIntArray(attr_value)
            int_array_data.set(int_array)
            plug.setMObject(int_array_obj)
        else:
            logger.warning(f"Unsupported attribute value type: {type(attr_value)}")

    except Exception as e:
        logger.error(f"Failed to set attribute value '{attr_name}' on '{object_name}': {e}")


def get_attribute(object_name, attr_name):
    """
    OpenMaya API 2.0を使用してアトリビュート値を取得します。
    cmds.getAttrの代わりに使用します。

    Args:
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名

    Returns:
        The value of the attribute, or None if it does not exist.
    """
    try:
        # オブジェクトのMObjectを取得
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        node_obj = selection_list.getDependNode(0)
        depend_fn = om.MFnDependencyNode(node_obj)

        # プラグを取得（存在しない場合は例外が発生）
        try:
            plug = depend_fn.findPlug(attr_name, False)
        except Exception:
            # アトリビュートが存在しない場合
            return None

        if plug.isNull:
            return None

        # 複合アトリビュートの場合
        if plug.isCompound:
            num_children = plug.numChildren()
            if num_children == 0:
                return None
            # 数値型の複合アトリビュート (double3, float3など)
            return tuple(plug.child(i).asDouble() for i in range(num_children))

        # 配列アトリビュートの場合
        if plug.isArray:
            return [plug.elementByLogicalIndex(i).asDouble() for i in range(plug.numElements)]

        # 単一アトリビュートの場合、タイプに応じて適切なメソッドを使用
        obj = plug.attribute()

        # 数値型チェック
        if obj.hasFn(om.MFn.kNumericAttribute):
            attr_fn = om.MFnNumericAttribute(obj)
            numeric_type = attr_fn.numericType()

            if numeric_type == om.MFnNumericData.kBoolean:
                return plug.asBool()
            elif numeric_type in [
                om.MFnNumericData.kInt,
                om.MFnNumericData.kLong,
                om.MFnNumericData.kByte,
                om.MFnNumericData.kShort,
            ]:
                return plug.asInt()
            elif numeric_type == om.MFnNumericData.kFloat:
                return plug.asFloat()
            elif numeric_type == om.MFnNumericData.kDouble:
                return plug.asDouble()

        # 型付きアトリビュート（文字列など）
        if obj.hasFn(om.MFn.kTypedAttribute):
            attr_fn = om.MFnTypedAttribute(obj)
            attr_type = attr_fn.attrType()

            if attr_type == om.MFnData.kString:
                return plug.asString()

        # その他の場合、型を推測して取得
        # まずdoubleとして取得を試みる
        try:
            return plug.asDouble()
        except Exception:
            # 失敗したら文字列として取得
            try:
                return plug.asString()
            except Exception:
                return None

    except Exception:
        # オブジェクトが存在しない、その他のエラー
        return None


def get_materials_from_mesh(mesh_name):
    """メッシュに割り当てられているマテリアルを取得

    Args:
        mesh_name (str): メッシュの名前
    Returns:
        list: メッシュに割り当てられているマテリアルのリスト

    """
    mesh_shapes = cmds.listRelatives(mesh_name, shapes=True, type="mesh") or []
    assigned_materials = []

    for shape in mesh_shapes:
        # シェーディングエンジンを取得
        shading_engines = cmds.listConnections(shape, type="shadingEngine") or []
        for sg in shading_engines:
            # シェーディングエンジンに接続されているマテリアルを取得
            materials = cmds.listConnections(f"{sg}.surfaceShader") or []
            assigned_materials.extend(materials)

    return assigned_materials


def apply_vertex_weights(
    skin_cluster,
    mesh_node,
    weights,
):
    """
    Mayaのメッシュに頂点ウェイトを適用します。

    Args:
        vertices (list): PMXの頂点データ。
        maya_joints (list): Mayaのジョイント名のリスト。
        skin_cluster (str): スキンクラスターの名前。
        mesh_node (str): メッシュノードの名前。
        weights (list[list[float]]): 頂点ごとのウェイトリスト。
        influences (list[list[int]]): 頂点ごとの影響ジョイントインデックスリスト。
    """

    # スキンクラスターのMObjectを取得
    selection_list = om.MSelectionList()
    selection_list.add(skin_cluster)
    skin_cluster_obj = selection_list.getDependNode(0)
    # https://help.autodesk.com/view/MAYAUL/2022/ENU/?guid=Maya_SDK_py_ref_class_open_maya_anim_1_1_m_fn_skin_cluster_html
    skin_fn = oma.MFnSkinCluster(skin_cluster_obj)

    influence_paths = skin_fn.influenceObjects()
    influence_count = len(influence_paths)

    # メッシュのDagPathを取得
    mesh_selection_list = om.MSelectionList()
    mesh_selection_list.add(mesh_node)
    shape_dag_path = mesh_selection_list.getDagPath(0)
    mesh_fn = om.MFnMesh(shape_dag_path)
    vertex_count = mesh_fn.numVertices

    # 全頂点のコンポーネントを作成
    vertex_component = om.MFnSingleIndexedComponent()
    vertex_component_obj = vertex_component.create(om.MFn.kMeshVertComponent)
    vertex_indices = list(range(vertex_count))
    vertex_component.addElements(vertex_indices)

    # infarray = list(range(influence_count))
    influence_indices = om.MIntArray(influence_count, 0)
    for ii in range(influence_count):
        influence_indices[ii] = ii

    # ウェイト配列を作成（頂点数 × 影響数）
    weight_array = om.MDoubleArray(vertex_count * influence_count, 0.0)
    # 各頂点のウェイトを設定
    for vertex_index in range(vertex_count):
        for influence_index in range(influence_count):
            array_index = vertex_index * influence_count + influence_index

            # influence_indexがweightsの範囲外の場合は0.0を設定
            in_range = vertex_index < len(weights) and influence_index < len(weights[vertex_index])
            weight_value = weights[vertex_index][influence_index] if in_range else 0.0
            weight_array[array_index] = weight_value

    # 一括で設定
    skin_fn.setWeights(shape_dag_path, vertex_component_obj, influence_indices, weight_array, False)


def find_or_create_blendshape_node(mesh_node):
    """既存のblendShapeノードを検索または新規作成"""
    # メッシュノードが存在するかチェック
    if not cmds.objExists(mesh_node):
        raise ValueError(f"Mesh node {mesh_node} does not exist")

    # シェイプノードを取得
    shape_nodes = cmds.listRelatives(mesh_node, shapes=True, type="mesh")
    if not shape_nodes:
        raise ValueError(f"No mesh shape found for {mesh_node}")

    shape_node = shape_nodes[0]

    history = cmds.listHistory(shape_node, il=2, pdo=False) or []
    blendshapes = [
        x for x in history if cmds.nodeType(x) == "blendShape" and cmds.blendShape(x, q=True, g=True)[0] == shape_node
    ]
    if blendshapes:
        return blendshapes[0]
    else:
        return cmds.blendShape(mesh_node)[0]


def find_or_create_nucleus_solver(name="mmd_nucleus"):
    """既存のNucleusソルバーを検索または新規作成"""
    nucleus_nodes = cmds.ls(type="nucleus")
    if nucleus_nodes:
        return nucleus_nodes[0]
    return cmds.createNode("nucleus", name=name)


def create_collision_primitive(shape_type, size, name="collision"):
    """
    形状タイプに応じたコリジョン用プリミティブを作成

    Args:
        shape_type (int): 0=箱, 1=球, 2=カプセル
        size (tuple): (x, y, z) サイズ
        name (str): オブジェクト名

    Returns:
        str: 作成されたオブジェクト名
    """
    if shape_type == 0:  # 箱
        obj = cmds.polyCube(name=name, width=size[0] * 2, height=size[1] * 2, depth=size[2] * 2)[0]
    elif shape_type == 1:  # 球
        obj = cmds.polySphere(name=name, radius=size[0])[0]
    elif shape_type == 2:  # カプセル（円柱で近似）
        obj = cmds.polyCylinder(name=name, radius=size[0], height=size[1] * 2)[0]
    else:
        raise ValueError(f"Unknown shape type: {shape_type}")

    return obj


def apply_ncloth_to_mesh(mesh, nucleus_solver=None):
    """
    メッシュにnClothを適用

    Args:
        mesh (str): メッシュ名
        nucleus_solver (str): Nucleusソルバー名（Noneの場合は新規作成）

    Returns:
        str: 作成されたnClothシェイプノード名
    """

    cmds.select(mesh)
    ncloth_shape = mel.eval("createNCloth 0;")

    if nucleus_solver and ncloth_shape:
        # Nucleusソルバーへの接続
        ncloth_nodes = cmds.ls(type="nCloth")
        if ncloth_nodes:
            index = len([i for i in cmds.listConnections(nucleus_solver + ".inputActive") or [] if i])
            cmds.connectAttr(
                f"{ncloth_shape[0]}.currentState",
                f"{nucleus_solver}.inputActive[{index}]",
            )
            cmds.connectAttr(
                f"{ncloth_shape[0]}.startState",
                f"{nucleus_solver}.inputActiveStart[{index}]",
            )

    return ncloth_shape[0] if ncloth_shape else None


def apply_nrigid_to_mesh(obj, is_dynamic=True):
    """
    オブジェクトにnRigidを適用

    Args:
        obj (str): オブジェクト名
        is_dynamic (bool): 動的かどうか

    Returns:
        str: 作成されたnRigidノード名
    """

    cmds.select(obj)
    nrigid = mel.eval("makeCollideNCloth;")

    if nrigid:
        cmds.setAttr(f"{nrigid[0]}.isDynamic", 1 if is_dynamic else 0)
        return nrigid[0]

    return None


def create_dynamic_curve(points, name="dynamic_curve"):
    """
    ダイナミックカーブを作成

    Args:
        points (list): カーブのポイントリスト
        name (str): カーブ名

    Returns:
        str: 作成されたカーブ名
    """
    curve = cmds.curve(d=1, p=points, name=name)
    return curve


def apply_nhair_to_curve(curve):
    """
    カーブにnHairシステムを適用

    Args:
        curve (str): カーブ名

    Returns:
        str: 作成されたhairSystemノード名
    """

    cmds.select(curve)
    mel.eval('makeCurvesDynamic 2 { "1", "0", "1", "1", "0"};')

    hair_systems = cmds.ls(type="hairSystem")
    if hair_systems:
        return hair_systems[-1]

    return None


def set_viewport_backface_culling(enabled=True, panel_name=None) -> bool:
    """
    ビューポートのバックフェイスカリングを設定する。

    Args:
        enabled (bool): バックフェイスカリングを有効にするかどうか
        panel_name (str): 対象のパネル名。Noneの場合はアクティブなパネルを使用

    Returns:
        bool: 設定が成功したかどうか
    """
    try:
        # パネル名が指定されていない場合、アクティブなパネルを取得
        if panel_name is None:
            panel_name = cmds.getPanel(withFocus=True)

            # アクティブパネルがモデルパネルでない場合、デフォルトのパネルを使用
            if not cmds.getPanel(typeOf=panel_name) == "modelPanel":
                panels = cmds.getPanel(type="modelPanel")
                if panels:
                    panel_name = panels[0]
                else:
                    logger.warning("No model panels found")
                    return False

        # バックフェイスカリングを設定
        cmds.modelEditor(panel_name, edit=True, backfaceCulling=enabled)

        logger.info(f"Backface culling {'enabled' if enabled else 'disabled'} for panel: {panel_name}")
        return True

    except Exception as e:
        logger.error(f"Failed to set backface culling: {e}")
        return False


def create_ik_handle(start_joint, end_joint, solver="ikRPsolver", name=None):
    """
    IKハンドルを作成する。

    Args:
        start_joint (str): IKチェーンの開始ジョイント名
        end_joint (str): IKチェーンの終了ジョイント名
        solver (str): 使用するIKソルバー ("ikRPsolver", "ikSCsolver", "ikSplineSolver")
        name (str): IKハンドルの名前（Noneの場合は自動生成）

    Returns:
        tuple: (ik_handle, effector) IKハンドル名とエフェクター名のタプル

    Raises:
        ValueError: ジョイントが存在しない場合やソルバーが無効な場合
    """
    # ジョイントの存在確認
    if not cmds.objExists(start_joint):
        raise ValueError(f"Start joint '{start_joint}' does not exist")
    if not cmds.objExists(end_joint):
        raise ValueError(f"End joint '{end_joint}' does not exist")

    # ソルバーの妥当性確認
    valid_solvers = ["ikRPsolver", "ikSCsolver", "ikSplineSolver"]
    if solver not in valid_solvers:
        raise ValueError(f"Invalid solver '{solver}'. Must be one of: {valid_solvers}")

    try:
        # IKハンドルの作成
        ik_handle_result = cmds.ikHandle(
            startJoint=start_joint,
            endEffector=end_joint,
            solver=solver,
            name=name if name else f"{end_joint}_ikHandle",
        )

        ik_handle = ik_handle_result[0]
        effector = ik_handle_result[1]

        logger.info(f"Created IK handle '{ik_handle}' from '{start_joint}' to '{end_joint}'")
        return ik_handle, effector

    except Exception as e:
        logger.error(f"Failed to create IK handle: {e}")
        raise


def set_joint_limits(joint, limit_min=None, limit_max=None, enable_limits=True):
    """
    ジョイントの回転制限を設定する。

    Args:
        joint (str): ジョイント名
        limit_min (list): 最小回転制限 [x, y, z] ラジアン単位
        limit_max (list): 最大回転制限 [x, y, z] ラジアン単位
        enable_limits (bool): 制限を有効にするかどうか
    """

    # 回転制限の設定（ラジアンから度数に変換）
    if limit_min:
        set_attribute(joint, "minRotLimit", limit_min, "double3")

    if limit_max:
        set_attribute(joint, "maxRotLimit", limit_max, "double3")

    # 制限の有効化/無効化
    if limit_min:
        cmds.setAttr(f"{joint}.minRotXLimitEnable", enable_limits)
        cmds.setAttr(f"{joint}.minRotYLimitEnable", enable_limits)
        cmds.setAttr(f"{joint}.minRotZLimitEnable", enable_limits)

    if limit_max:
        cmds.setAttr(f"{joint}.maxRotXLimitEnable", enable_limits)
        cmds.setAttr(f"{joint}.maxRotYLimitEnable", enable_limits)
        cmds.setAttr(f"{joint}.maxRotZLimitEnable", enable_limits)


def create_pole_vector_constraint(ik_handle, pole_vector_object, maintain_offset=True):
    """
    IKハンドルにポールベクターコンストレイントを作成する。

    Args:
        ik_handle (str): IKハンドル名
        pole_vector_object (str): ポールベクターコントロールオブジェクト名
        maintain_offset (bool): オフセットを維持するかどうか

    Returns:
        str: 作成されたコンストレイントノード名
    """
    if not cmds.objExists(ik_handle):
        raise ValueError(f"IK handle '{ik_handle}' does not exist")
    if not cmds.objExists(pole_vector_object):
        raise ValueError(f"Pole vector object '{pole_vector_object}' does not exist")

    try:
        constraint = cmds.poleVectorConstraint(pole_vector_object, ik_handle, maintainOffset=maintain_offset)[0]

        logger.info(f"Created pole vector constraint from '{pole_vector_object}' to '{ik_handle}'")
        return constraint

    except Exception as e:
        logger.error(f"Failed to create pole vector constraint: {e}")
        raise


def create_matrix_from_axes(x_axis, y_axis, z_axis):
    """
    3つの軸ベクトルから回転行列を作成する。

    Args:
        x_axis (list): X軸ベクトル [x, y, z]
        y_axis (list): Y軸ベクトル [x, y, z]
        z_axis (list): Z軸ベクトル [x, y, z]

    Returns:
        om.MMatrix: 回転行列
    """
    matrix = om.MMatrix()
    matrix.setElement(0, 0, x_axis[0])
    matrix.setElement(0, 1, x_axis[1])
    matrix.setElement(0, 2, x_axis[2])
    matrix.setElement(1, 0, y_axis[0])
    matrix.setElement(1, 1, y_axis[1])
    matrix.setElement(1, 2, y_axis[2])
    matrix.setElement(2, 0, z_axis[0])
    matrix.setElement(2, 1, z_axis[1])
    matrix.setElement(2, 2, z_axis[2])
    return matrix


def matrix_to_euler(matrix):
    """
    回転行列をオイラー角に変換する。

    Args:
        matrix (om.MMatrix): 回転行列

    Returns:
        list: オイラー角 [x, y, z] 度数法
    """
    transform_matrix = om.MTransformationMatrix(matrix)
    euler = transform_matrix.rotation(asQuaternion=False)
    # ラジアンから度に変換
    import math

    return [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]


def create_animation_curves(
    node_name,
    attributes,
    tangent_type=oma.MFnAnimCurve.kTangentLinear,
    animation_layer=None,
):
    """
    指定したノードの属性にアニメーションカーブを作成する。
    アニメーションレイヤーが指定されている場合は、レイヤー用のカーブを作成する。

    Args:
        node_name (str): ノード名
        attributes (list): アトリビュート名のリスト
        tangent_type: タンジェントタイプ（デフォルト: 線形）
        animation_layer (str, optional): アニメーションレイヤー名

    Returns:
        dict: アトリビュート名をキー、MFnAnimCurveオブジェクトを値とする辞書
    """
    # ノードを取得
    sel_list = om.MSelectionList()
    sel_list.add(node_name)
    node = sel_list.getDependNode(0)
    fn_depend = om.MFnDependencyNode(node)

    # アニメーションレイヤーが指定されている場合
    if animation_layer and cmds.animLayer(animation_layer, query=True, exists=True):
        # オブジェクトがレイヤーに含まれているか確認
        cmds.select(node_name, replace=True)
        affected_layers = cmds.animLayer([node_name], query=True, affectedLayers=True) or []
        if animation_layer not in affected_layers:
            # オブジェクトをレイヤーに追加
            current_selection = cmds.ls(selection=True)
            cmds.select(node_name, replace=True)
            cmds.animLayer(animation_layer, edit=True, addSelectedObjects=True)
            if current_selection:
                cmds.select(current_selection)
            else:
                cmds.select(clear=True)

    # 既存のアニメーションカーブをクリア（レイヤーモードでない場合のみ）
    if not animation_layer:
        for attr in attributes:
            connections = cmds.listConnections(f"{node_name}.{attr}", source=True, destination=False)
            if connections:
                cmds.delete(connections)

    # アニメーションカーブを作成
    curves = {}
    for attr in attributes:
        if animation_layer:
            # レイヤーが有効な場合は、cmds.setKeyframeを使って初期カーブを作成
            cmds.setKeyframe(node_name, attribute=attr, animLayer=animation_layer)
            # 作成されたカーブを取得
            blend_nodes = cmds.animLayer(animation_layer, query=True, blendNodes=True) or []
            for blend_node in blend_nodes:
                # ブレンドノードの入力カーブを探す
                input_curves = cmds.listConnections(blend_node, source=True, type="animCurve") or []
                for curve_name in input_curves:
                    # このカーブが目的の属性のものか確認
                    curve_connections = cmds.listConnections(curve_name, destination=True, plugs=True) or []
                    for conn in curve_connections:
                        if f"{node_name}.{attr}" in conn or attr in conn:
                            # Maya APIオブジェクトとして取得
                            curve_sel = om.MSelectionList()
                            curve_sel.add(curve_name)
                            curve_obj = curve_sel.getDependNode(0)
                            curves[attr] = oma.MFnAnimCurve(curve_obj)
                            break
        else:
            # 通常のアニメーションカーブ作成
            curve = oma.MFnAnimCurve()
            plug = fn_depend.findPlug(attr, False)
            curve.create(plug)
            curves[attr] = curve

    return curves


def set_keyframes_batch(
    curves,
    frame_data_list,
    value_generator_func,
    tangent_type=oma.MFnAnimCurve.kTangentLinear,
):
    """
    複数のアニメーションカーブに一括でキーフレームを設定する。
    アニメーションレイヤーのカーブも正しく処理される。

    Args:
        curves (dict): アトリビュート名をキー、MFnAnimCurveオブジェクトを値とする辞書
        frame_data_list (list): フレームデータのリスト
        value_generator_func: フレームデータから値を生成する関数
                             (frame_data) -> dict[attr_name, value]
        tangent_type: タンジェントタイプ（デフォルト: 線形）
    """
    import math

    # まず全てのキーを追加（高速化のため）
    for frame_data in frame_data_list:
        # 値を生成
        values = value_generator_func(frame_data)

        # フレーム番号を取得
        if hasattr(frame_data, "frame_number"):
            frame_num = frame_data.frame_number
        else:
            frame_num = frame_data.get("frame_number", 0)

        # MTimeオブジェクトを作成
        time = om.MTime(frame_num, om.MTime.uiUnit())

        # 各カーブにキーを設定
        for attr_name, curve in curves.items():
            if attr_name in values:
                value = values[attr_name]

                # 回転属性の場合はラジアンに変換
                if attr_name in ["rotateX", "rotateY", "rotateZ"]:
                    value = math.radians(value)

                try:
                    curve.addKey(time, value, tangent_type, tangent_type)
                except Exception:
                    # エラーが発生した場合はスキップ（レイヤーカーブの場合など）
                    logger.debug(f"Failed to add key for {attr_name} at frame {frame_num}")
                    pass


def find_all_mmd_models():
    """
    シーン内のすべてのMMDモデルのルートノードを検索します。

    Returns:
        list: MMDモデルのルートノード名のリスト
    """
    from ..core.constants import SCENE_ROOT_SUFFIX

    # *_rootという名前のトランスフォームノードを検索
    # namespace対応のため、ワイルドカードパターンを使用
    all_transforms = cmds.ls("*:*{}".format(SCENE_ROOT_SUFFIX), type="transform") + cmds.ls(
        "*{}".format(SCENE_ROOT_SUFFIX), type="transform"
    )

    # 重複を削除
    all_transforms = list(set(all_transforms))

    mmd_models = []
    for transform in all_transforms:
        # MMD関連のアトリビュートがあるか確認
        if cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=transform, exists=True) or cmds.attributeQuery(
            ATTR_MMD_MODEL_NAME_EN, node=transform, exists=True
        ):
            mmd_models.append(transform)

    return sorted(mmd_models)  # 名前順でソート


def get_parent_mmd_root(node_name):
    """
    指定されたノードの親階層からMMDモデルのルートノードを検索します。

    Args:
        node_name (str): 検索開始ノード名

    Returns:
        str: MMDモデルのルートノード名。見つからない場合はNone
    """
    from ..core.constants import SCENE_ROOT_SUFFIX

    try:
        # 現在のノードから親を辿る
        current = node_name
        while current:
            # ルートサフィックスを持ち、MMDアトリビュートがあるか確認
            if current.endswith(SCENE_ROOT_SUFFIX) and (
                cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=current, exists=True)
                or cmds.attributeQuery(ATTR_MMD_MODEL_NAME_EN, node=current, exists=True)
            ):
                return current

            # 親ノードを取得
            parents = cmds.listRelatives(current, parent=True, fullPath=True)
            if parents:
                current = parents[0]
            else:
                break

    except Exception as e:
        logger.warning(f"Failed to find parent MMD root for {node_name}: {e}")

    return None


def get_mmd_model_display_name(root_node):
    """
    MMDモデルの表示名を取得します。

    Args:
        root_node (str): MMDモデルのルートノード名

    Returns:
        str: 表示名（日本語名があれば優先、なければノード名）
    """
    try:
        if cmds.attributeQuery(ATTR_MMD_MODEL_NAME, node=root_node, exists=True):
            name_jp = cmds.getAttr(f"{root_node}.{ATTR_MMD_MODEL_NAME}")
            if name_jp:
                return name_jp

        if cmds.attributeQuery(ATTR_MMD_MODEL_NAME_EN, node=root_node, exists=True):
            name_en = cmds.getAttr(f"{root_node}.{ATTR_MMD_MODEL_NAME_EN}")
            if name_en:
                return name_en

    except Exception:
        pass

    # アトリビュートがない場合はノード名から_rootを除いて返す
    from ..core.constants import SCENE_ROOT_SUFFIX

    return root_node.replace(SCENE_ROOT_SUFFIX, "")


def select_objects(objects=None, clear=True, add=False, replace=True):
    """
    OpenMaya API 2.0を使用してオブジェクトを選択します。
    cmds.select()の代替実装です。

    Args:
        objects (str or list, optional): 選択するオブジェクト。Noneの場合はクリアのみ
        clear (bool): 選択をクリアするかどうか
        add (bool): 既存の選択に追加するかどうか
        replace (bool): 既存の選択を置き換えるかどうか

    Returns:
        bool: 成功したかどうか
    """
    try:
        # 現在の選択を取得
        current_selection = om.MGlobal.getActiveSelectionList()

        if clear or replace:
            # 選択をクリア
            om.MGlobal.setActiveSelectionList(om.MSelectionList())

        if objects is None:
            return True

        # 新しい選択リストを作成
        new_selection = om.MSelectionList()

        # 追加モードの場合は現在の選択を保持
        if add and not clear and not replace:
            new_selection = om.MSelectionList(current_selection)

        # オブジェクトを追加
        if isinstance(objects, str):
            objects = [objects]

        for obj in objects:
            try:
                new_selection.add(obj)
            except Exception:
                logger.warning(f"Could not add '{obj}' to selection")

        # 選択を設定
        om.MGlobal.setActiveSelectionList(new_selection)
        return True

    except Exception as e:
        logger.error(f"Failed to select objects: {e}")
        return False


def object_exists(object_name):
    """
    OpenMaya API 2.0を使用してオブジェクトの存在を確認します。
    cmds.objExists()の代替実装です。

    Args:
        object_name (str): 確認するオブジェクト名

    Returns:
        bool: オブジェクトが存在するかどうか
    """
    try:
        selection_list = om.MSelectionList()
        selection_list.add(object_name)
        return True
    except Exception:
        return False


def parent_objects(children, parent=None, world=False):
    """
    オブジェクトの親子関係を設定します。
    cmds.parent()のラッパー実装です。

    Args:
        children (str or list): 子オブジェクト
        parent (str, optional): 親オブジェクト。Noneまたはworld=Trueの場合はワールド空間へ
        world (bool): ワールド空間に親付けするかどうか

    Returns:
        list: 親付けされたオブジェクトのリスト
    """
    try:
        if isinstance(children, str):
            children = [children]

        if world or parent is None:
            # ワールド空間へ親付け
            result = cmds.parent(children, world=True)
        else:
            # 指定された親へ親付け
            result = cmds.parent(children, parent)

        return result if isinstance(result, list) else [result]

    except Exception as e:
        logger.error(f"Failed to parent objects: {e}")
        return []


def list_objects(object_filter=None, type=None, fullPath=False):
    """
    OpenMaya API 2.0を使用してシーン内のオブジェクトをリストします。
    cmds.ls()の簡易版実装です。

    Args:
        object_filter (str, optional): オブジェクト名のフィルター（ワイルドカード対応）
        type (str, optional): オブジェクトタイプフィルター
        fullPath (bool, optional): フルパスで返すかどうか（デフォルト: False）

    Returns:
        list: マッチしたオブジェクトのリスト
    """
    try:
        result = []

        # タイプに応じたイテレータを作成
        if type == "joint":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kJoint)
        elif type == "mesh":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kMesh)
        elif type == "transform":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kTransform)
        elif type == "camera":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kCamera)
        elif type == "blendShape":
            # blendShapeはDGノードなので別の方法で取得
            return _list_dg_nodes("blendShape", object_filter)
        elif type == "directionalLight":
            it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kDirectionalLight)
        else:
            # 全てのDAGオブジェクト
            it = om.MItDag(om.MItDag.kDepthFirst)

        while not it.isDone():
            try:
                dag_path = it.getPath()
                # fullPathフラグに応じて名前を取得
                if fullPath:
                    node_name = dag_path.fullPathName()
                else:
                    node_name = dag_path.partialPathName()

                # フィルターチェック
                if object_filter:
                    import fnmatch

                    if not fnmatch.fnmatch(node_name, object_filter):
                        it.next()
                        continue

                result.append(node_name)
            except Exception:
                pass

            it.next()

        return result

    except Exception as e:
        logger.error(f"Failed to list objects: {e}")
        return []


def _list_dg_nodes(node_type, object_filter=None):
    """
    DGノード（非DAGノード）をリストする内部ヘルパー関数

    Args:
        node_type (str): ノードタイプ
        object_filter (str, optional): オブジェクト名のフィルター

    Returns:
        list: マッチしたオブジェクトのリスト
    """
    try:
        result = []
        it = om.MItDependencyNodes(om.MFn.kBlendShape)

        while not it.isDone():
            try:
                node = it.thisNode()
                fn_node = om.MFnDependencyNode(node)
                node_name = fn_node.name()

                # フィルターチェック
                if object_filter:
                    import fnmatch

                    if not fnmatch.fnmatch(node_name, object_filter):
                        it.next()
                        continue

                result.append(node_name)
            except Exception:
                pass

            it.next()

        return result
    except Exception as e:
        logger.error(f"Failed to list DG nodes: {e}")
        return []
