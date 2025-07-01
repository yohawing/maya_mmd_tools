import os
import maya.cmds as cmds
from ..core import maya_utils

class MeshConverter:
    """
    MMDのメッシュデータをMayaのメッシュノードに変換するクラス。
    """

    def __init__(self, pmx_filepath):
        """
        コンストラクタ。

        Args:
            pmx_filepath (str): 読み込むPMXファイルのパス。
        """
        self.pmx_filepath = pmx_filepath
        self.texture_dir = os.path.dirname(pmx_filepath)

    def convert_pmx_mesh(self, pmx_data):
        """
        PMXのメッシュデータをMayaのメッシュノードに変換する。
        材質ごとにメッシュを分割して作成する。

        Args:
            pmx_data (pmx_parser.PmxParser): 解析されたPMXデータオブジェクト。

        Returns:
            str: 作成されたMayaメッシュをまとめるグループノードの名前。
        """
        model_name = maya_utils.get_unique_maya_name(pmx_data.header.model_name)
        all_vertices = pmx_data.vertices
        all_faces = pmx_data.faces
        all_materials = pmx_data.materials
        all_textures = pmx_data.textures

        # モデル名のグループを作成
        model_group = cmds.group(empty=True, name=model_name)
        
        face_offset = 0
        for i, material in enumerate(all_materials):
            # 材質名をサニタイズして一意な名前を生成
            raw_material_name = material.name or f"material_{i}"
            material_name = maya_utils.get_unique_maya_name(raw_material_name)
            mesh_name = maya_utils.get_unique_maya_name(f"{model_name}_{material_name}")
            
            # この材質が使用する面の数を計算
            num_material_faces = material.face_count // 3
            if num_material_faces == 0:
                face_offset += num_material_faces
                continue

            # この材質が使用する面と頂点を抽出
            material_faces = all_faces[face_offset : face_offset + num_material_faces]
            face_offset += num_material_faces

            # この材質が使用する頂点のインデックスセットを取得
            vert_indices_set = set()
            for face in material_faces:
                vert_indices_set.update(face.indices)
            
            # 頂点インデックスをソートして、新しいインデックスマッピングを作成
            sorted_vert_indices = sorted(list(vert_indices_set))
            vert_map = {old_idx: new_idx for new_idx, old_idx in enumerate(sorted_vert_indices)}

            # 新しい頂点リストとUVリストを作成
            vertices = []
            uvs = []
            for old_idx in sorted_vert_indices:
                vertex = all_vertices[old_idx]
                vertices.append(vertex.position)
                uvs.extend(vertex.uv) # extendで (u,v) をフラットリストに追加

            # 新しい面リストを作成
            face_connects = []
            face_counts = []
            face_uv_connects = []
            for face in material_faces:
                new_face = [vert_map[v_idx] for v_idx in face.indices]
                face_connects.extend(new_face)
                face_counts.append(len(new_face))
                # UVインデックスは頂点インデックスと1対1対応
                face_uv_connects.extend(new_face)

            # UV座標は既にフラット化されているのでそのまま使用
            flat_uvs = uvs

            # メッシュを作成
            created_mesh = maya_utils.create_mesh_with_uvs(
                name=mesh_name,
                vertices=vertices,
                face_counts=face_counts,
                face_connects=face_connects,
                uvs=flat_uvs,
                face_uv_connects=face_uv_connects
            )

            # マテリアルを作成して割り当て
            texture_path = None
            if material.texture_index != -1:
                raw_texture_path = all_textures[material.texture_index]
                texture_path = maya_utils.sanitize_texture_path(raw_texture_path, self.texture_dir)
            
            shader = maya_utils.create_material(
                name=material_name,
                color=material.diffuse,
                texture_path=texture_path,
                texture_dir=self.texture_dir
            )
            maya_utils.assign_material(created_mesh, shader)

            # 作成したメッシュをグループに追加
            cmds.parent(created_mesh, model_group)

        cmds.select(model_group)
        return model_group

    def convert_pmd_mesh(self, pmd_data):
        """
        PMDのメッシュデータをMayaのメッシュノードに変換する。

        Args:
            pmd_data (PmdParser): 解析されたPMDデータオブジェクト。

        Returns:
            str: 作成されたMayaメッシュノードの名前。
        """
        # TODO: PMDの頂点、面、UV、法線データをMayaのmeshノードに変換するロジックを実装する。
        # TODO: 材質データに基づいてMayaのシェーダーを作成し、テクスチャを適用する。
        # TODO: 頂点カラーが存在する場合は、MayaのcolorSetに変換する。
        # PMXと同様の実装が必要になるが、データ構造が異なるため注意
        cmds.warning("PMD mesh conversion is not yet implemented.")
        return None
