import os

from maya import cmds
from maya.api import OpenMaya as om
from maya.api import OpenMayaAnim as oma

from mmd_tools.settings import settings

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


def create_mesh_with_uvs(
    name, vertices, face_counts, face_connects, uvs, face_uv_connects
):
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
    cmds.setAttr(shader + ".color", color[0], color[1], color[2], type="double3")
    # AlphaをTransparencyに変換
    cmds.setAttr(
        shader + ".transparency",
        1.0 - color[3],
        1.0 - color[3],
        1.0 - color[3],
        type="double3",
    )

    # 元の名前を保持
    set_custom_attributes(shader, {"mmd_material_name": name})

    if texture_path:
        # テクスチャパスを解決
        full_texture_path = os.path.join(texture_dir, texture_path)
        if os.path.exists(full_texture_path):
            file_node = cmds.shadingNode(
                "file", asTexture=True, name=sanitized_name + "_file"
            )
            place_uv_node = cmds.shadingNode(
                "place2dTexture",
                asUtility=True,
                name=sanitized_name + "_place2dTexture",
            )
            # 標準的なUV接続
            cmds.connectAttr(place_uv_node + ".outUV", file_node + ".uvCoord")
            cmds.connectAttr(file_node + ".outColor", shader + ".color")

            cmds.setAttr(
                file_node + ".fileTextureName", full_texture_path, type="string"
            )
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
    sg_name = cmds.sets(
        renderable=True, noSurfaceShader=True, empty=True, name=sanitized_shader_name
    )
    # シェーダーをシェーディンググループに接続
    cmds.connectAttr(shader_node + ".outColor", sg_name + ".surfaceShader", force=True)
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
    # マテリアル専用のシェーディンググループを作成
    sanitized_shader_name = shader_node + "SG"
    sg_name = cmds.sets(
        renderable=True, noSurfaceShader=True, empty=True, name=sanitized_shader_name
    )
    # シェーダーをシェーディンググループに接続
    cmds.connectAttr(shader_node + ".outColor", sg_name + ".surfaceShader", force=True)
    # 指定した面をシェーディンググループに割り当て
    cmds.sets(face_selection, edit=True, forceElement=sg_name)


def set_custom_attributes(object_name, attributes):
    """
    Mayaオブジェクトにカスタムアトリビュートを設定します。

    Args:
        object_name (str): カスタムアトリビュートを設定するオブジェクトの名前。
        attributes (dict): 属性名と値の辞書。
    """
    for attr_name, attr_value in attributes.items():
        attr_type = type(attr_value).__name__
        if not cmds.attributeQuery(attr_name, node=object_name, exists=True):
            if attr_type in ["int", "float", "bool"]:
                add_numeric_attribute(object_name, attr_name, attr_type)
                set_attribute_value_api(object_name, attr_name, attr_value, attr_type)
            if attr_type in ["str", "bytes"]:
                add_typed_attribute(object_name, attr_name, attr_type)
                set_attribute_value_api(object_name, attr_name, attr_value, attr_type)
            if attr_type in ["list", "tuple"]:
                # リストやタプルの場合は型を指定
                if len(attr_value) == 3 and all(
                    isinstance(x, float) for x in attr_value
                ):
                    attr_type = "double3"
                elif len(attr_value) == 3 and all(
                    isinstance(x, int) for x in attr_value
                ):
                    attr_type = "long3"
                elif all(isinstance(x, float) for x in attr_value):
                    attr_type = "doubleArray"
                elif all(isinstance(x, int) for x in attr_value):
                    attr_type = "longArray"

                add_typed_attribute(object_name, attr_name, attr_type)
                set_attribute_value_api(object_name, attr_name, attr_value, attr_type)


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
        logger.error(
            f"Failed to add numeric attribute '{attr_name}' to '{object_name}': {e}"
        )


def add_typed_attribute(object_name, attr_name, attr_type):
    """
    OpenMaya API 2.0を使用して型付きアトリビュートを追加します。

    Args:
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名
        attr_type (str): アトリビュートタイプ (string, double3, long3, doubleArray, longArray)
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
        logger.error(
            f"Failed to add typed attribute '{attr_name}' to '{object_name}': {e}"
        )


def set_attribute_value_api(object_name, attr_name, attr_value, attr_type):
    """
    OpenMaya API 2.0を使用してアトリビュート値を設定します。

    Args:
        object_name (str): オブジェクト名
        attr_name (str): アトリビュート名
        attr_value: 設定する値
        attr_type (str, optional): アトリビュートタイプ（配列の場合に必要）
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
        elif attr_type == "int":
            plug.setInt(attr_value)
        elif attr_type == "float":
            plug.setFloat(attr_value)
        elif attr_type == "str":
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
        logger.error(
            f"Failed to set attribute value '{attr_name}' on '{object_name}': {e}"
        )


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
            in_range = vertex_index < len(weights) and influence_index < len(
                weights[vertex_index]
            )
            weight_value = weights[vertex_index][influence_index] if in_range else 0.0
            weight_array[array_index] = weight_value

    # 一括で設定
    skin_fn.setWeights(
        shape_dag_path, vertex_component_obj, influence_indices, weight_array, False
    )


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
        x
        for x in history
        if cmds.nodeType(x) == "blendShape"
        and cmds.blendShape(x, q=True, g=True)[0] == shape_node
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
        obj = cmds.polyCube(
            name=name, width=size[0] * 2, height=size[1] * 2, depth=size[2] * 2
        )[0]
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
    from maya import mel

    cmds.select(mesh)
    ncloth_shape = mel.eval("createNCloth 0;")

    if nucleus_solver and ncloth_shape:
        # Nucleusソルバーへの接続
        ncloth_nodes = cmds.ls(type="nCloth")
        if ncloth_nodes:
            index = len(
                [
                    i
                    for i in cmds.listConnections(nucleus_solver + ".inputActive") or []
                    if i
                ]
            )
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
    from maya import mel

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
    from maya import mel

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

        logger.info(
            f"Backface culling {'enabled' if enabled else 'disabled'} for panel: {panel_name}"
        )
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
            name=name if name else f"{end_joint}_ikHandle"
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
        
    Returns:
        bool: 設定が成功したかどうか
    """
    if not cmds.objExists(joint):
        logger.error(f"Joint '{joint}' does not exist")
        return False
        
    if cmds.nodeType(joint) != "joint":
        logger.error(f"'{joint}' is not a joint node")
        return False
    
    try:
        import math
        
        # 回転制限の設定（ラジアンから度数に変換）
        if limit_min:
            cmds.setAttr(f"{joint}.minRotXLimit", math.degrees(limit_min[0]))
            cmds.setAttr(f"{joint}.minRotYLimit", math.degrees(limit_min[1]))
            cmds.setAttr(f"{joint}.minRotZLimit", math.degrees(limit_min[2]))
            
        if limit_max:
            cmds.setAttr(f"{joint}.maxRotXLimit", math.degrees(limit_max[0]))
            cmds.setAttr(f"{joint}.maxRotYLimit", math.degrees(limit_max[1]))
            cmds.setAttr(f"{joint}.maxRotZLimit", math.degrees(limit_max[2]))
        
        # 制限の有効化/無効化
        if limit_min:
            cmds.setAttr(f"{joint}.minRotXLimitEnable", enable_limits)
            cmds.setAttr(f"{joint}.minRotYLimitEnable", enable_limits)
            cmds.setAttr(f"{joint}.minRotZLimitEnable", enable_limits)
            
        if limit_max:
            cmds.setAttr(f"{joint}.maxRotXLimitEnable", enable_limits)
            cmds.setAttr(f"{joint}.maxRotYLimitEnable", enable_limits)
            cmds.setAttr(f"{joint}.maxRotZLimitEnable", enable_limits)
        
        logger.info(f"Set joint limits for '{joint}'")
        return True
        
    except Exception as e:
        logger.error(f"Failed to set joint limits for '{joint}': {e}")
        return False


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
        constraint = cmds.poleVectorConstraint(
            pole_vector_object, 
            ik_handle, 
            maintainOffset=maintain_offset
        )[0]
        
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
    return [
        math.degrees(euler.x),
        math.degrees(euler.y),
        math.degrees(euler.z)
    ]


def create_animation_curves(node_name, attributes, tangent_type=oma.MFnAnimCurve.kTangentLinear):
    """
    指定したノードの属性にアニメーションカーブを作成する。
    
    Args:
        node_name (str): ノード名
        attributes (list): アトリビュート名のリスト
        tangent_type: タンジェントタイプ（デフォルト: 線形）
        
    Returns:
        dict: アトリビュート名をキー、MFnAnimCurveオブジェクトを値とする辞書
    """
    # ノードを取得
    sel_list = om.MSelectionList()
    sel_list.add(node_name)
    node = sel_list.getDependNode(0)
    fn_depend = om.MFnDependencyNode(node)
    
    # 既存のアニメーションカーブをクリア
    for attr in attributes:
        connections = cmds.listConnections(
            f"{node_name}.{attr}", source=True, destination=False
        )
        if connections:
            cmds.delete(connections)
    
    # アニメーションカーブを作成
    curves = {}
    for attr in attributes:
        curve = oma.MFnAnimCurve()
        plug = fn_depend.findPlug(attr, False)
        curve.create(plug)
        curves[attr] = curve
        
    return curves


def set_keyframes_batch(curves, frame_data_list, value_generator_func, tangent_type=oma.MFnAnimCurve.kTangentLinear):
    """
    複数のアニメーションカーブに一括でキーフレームを設定する。
    
    Args:
        curves (dict): アトリビュート名をキー、MFnAnimCurveオブジェクトを値とする辞書
        frame_data_list (list): フレームデータのリスト
        value_generator_func: フレームデータから値を生成する関数
                             (frame_data) -> dict[attr_name, value]
        tangent_type: タンジェントタイプ（デフォルト: 線形）
    """
    import math
    
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
                    
                curve.addKey(time, value, tangent_type, tangent_type)


def set_quaternion_keyframe(node_name, quaternion, frame_number, tangent_type=oma.MFnAnimCurve.kTangentLinear):
    """
    クォータニオンを使用して回転キーフレームを設定する。
    
    Args:
        node_name (str): ノード名
        quaternion (list): クォータニオン [x, y, z, w]
        frame_number (int): フレーム番号
        tangent_type: タンジェントタイプ
        
    Returns:
        bool: 成功したかどうか
    """
    try:
        # MQuaternionオブジェクトを作成
        quat = om.MQuaternion(quaternion[0], quaternion[1], quaternion[2], quaternion[3])
        quat = quat.normal()  # 正規化
        
        # オイラー角に変換
        euler = quat.asEulerRotation()
        
        # MTimeオブジェクトを作成
        time = om.MTime(frame_number, om.MTime.uiUnit())
        
        # ノードを取得
        sel_list = om.MSelectionList()
        sel_list.add(node_name)
        node = sel_list.getDependNode(0)
        fn_depend = om.MFnDependencyNode(node)
        
        # 回転プラグを取得/作成
        for axis, value in [("rotateX", euler.x), ("rotateY", euler.y), ("rotateZ", euler.z)]:
            plug = fn_depend.findPlug(axis, False)
            
            # 既存のアニメーションカーブを取得または作成
            anim_curve = None
            if plug.isConnected():
                connections = plug.connectedTo(True, False)
                if connections:
                    curve_node = connections[0].node()
                    if curve_node.hasFn(om.MFn.kAnimCurve):
                        anim_curve = oma.MFnAnimCurve(curve_node)
            
            if not anim_curve:
                anim_curve = oma.MFnAnimCurve()
                anim_curve.create(plug)
            
            # キーフレームを追加
            anim_curve.addKey(time, value, tangent_type, tangent_type)
            
        return True
        
    except Exception as e:
        logger.error(f"Failed to set quaternion keyframe: {e}")
        return False


def set_transform_matrix_keyframe(node_name, matrix, frame_number):
    """
    変換行列を使用してキーフレームを設定する。
    クォータニオンを含む完全な変換を設定できる。
    
    Args:
        node_name (str): ノード名
        matrix (om.MMatrix): 変換行列
        frame_number (int): フレーム番号
        
    Returns:
        bool: 成功したかどうか
    """
    try:
        # MTransformationMatrixを作成
        transform_matrix = om.MTransformationMatrix(matrix)
        
        # 位置、回転、スケールを取得
        translation = transform_matrix.translation(om.MSpace.kWorld)
        rotation = transform_matrix.rotation(asQuaternion=False)
        scale = transform_matrix.scale(om.MSpace.kWorld)
        
        # キーフレーム設定
        attrs = {
            "translateX": translation.x,
            "translateY": translation.y,
            "translateZ": translation.z,
            "rotateX": rotation.x,
            "rotateY": rotation.y,
            "rotateZ": rotation.z,
            "scaleX": scale[0],
            "scaleY": scale[1],
            "scaleZ": scale[2]
        }
        
        time = om.MTime(frame_number, om.MTime.uiUnit())
        
        # ノードを取得
        sel_list = om.MSelectionList()
        sel_list.add(node_name)
        node = sel_list.getDependNode(0)
        fn_depend = om.MFnDependencyNode(node)
        
        for attr_name, value in attrs.items():
            plug = fn_depend.findPlug(attr_name, False)
            
            # アニメーションカーブを取得または作成
            anim_curve = None
            if plug.isConnected():
                connections = plug.connectedTo(True, False)
                if connections:
                    curve_node = connections[0].node()
                    if curve_node.hasFn(om.MFn.kAnimCurve):
                        anim_curve = oma.MFnAnimCurve(curve_node)
            
            if not anim_curve:
                anim_curve = oma.MFnAnimCurve()
                anim_curve.create(plug)
            
            anim_curve.addKey(time, value, oma.MFnAnimCurve.kTangentLinear, oma.MFnAnimCurve.kTangentLinear)
            
        return True
        
    except Exception as e:
        logger.error(f"Failed to set transform matrix keyframe: {e}")
        return False




