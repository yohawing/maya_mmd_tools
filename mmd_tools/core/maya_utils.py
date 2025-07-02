import maya.cmds as cmds
import maya.api.OpenMaya as om
import os

from . import utils


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
    
    converted_name = utils.convert_unicode_to_maya_safe(name)
    print(f"Sanitized name: {name} to {converted_name}")  # デバッグ用ログ
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
        uv_set_name = "map1"
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
    cmds.sets(transform_name, edit=True, forceElement='initialShadingGroup')
    cmds.select(clear=True)
    
    return transform_name

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
    shader = cmds.shadingNode('lambert', asShader=True, name=name)
    cmds.setAttr(shader + ".color", color[0], color[1], color[2], type='double3')
    # AlphaをTransparencyに変換
    cmds.setAttr(shader + ".transparency", 1.0 - color[3], 1.0 - color[3], 1.0 - color[3], type='double3')

    if texture_path:
        # テクスチャパスを解決
        full_texture_path = os.path.join(texture_dir, texture_path)
        if os.path.exists(full_texture_path):
            file_node = cmds.shadingNode('file', asTexture=True, name=name + "_file")
            place_uv_node = cmds.shadingNode('place2dTexture', asUtility=True, name=name + "_place2dTexture")

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
    cmds.connectAttr(shader_node + ".outColor", sg_name + ".surfaceShader", force=True)
    # メッシュをシェーディンググループに割り当て
    cmds.sets(mesh_name, edit=True, forceElement=sg_name)