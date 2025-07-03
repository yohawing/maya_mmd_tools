import os
import maya.cmds as cmds
from ..core import maya_utils
from .. import settings

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

        Args:
            pmx_data (pmx_parser.PmxParser): 解析されたPMXデータオブジェクト。

        Returns:
            str: 作成されたMayaメッシュをまとめるグループノードの名前。
        """
        model_name = pmx_data.header.model_name
        all_vertices = pmx_data.vertices
        all_faces = pmx_data.faces
        all_materials = pmx_data.materials
        all_textures = pmx_data.textures

        # モデル名のグループを作成
        model_group = cmds.group(empty=True, name=model_name)
        
        # 設定からマテリアルごとのメッシュ分割設定を取得
        separate_by_material = settings.get("import.model.separate_meshes_by_material", False)
        
        if separate_by_material:
            # マテリアルごとにメッシュを分割する場合
            self._create_separated_meshes(model_name, all_vertices, all_faces, 
                                         all_materials, all_textures, model_group)
        else:
            # 全てのメッシュを統合する場合
            self._create_unified_mesh(model_name, all_vertices, all_faces, 
                                     all_materials, all_textures, model_group)

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

        model_name = pmd_data.header.model_name
        all_vertices = pmd_data.vertices
        all_faces = pmd_data.faces
        all_materials = pmd_data.materials

        # モデル名のグループを作成
        model_group = cmds.group(empty=True, name=model_name)
        
        # 設定からマテリアルごとのメッシュ分割設定を取得
        separate_by_material = settings.get("import.model.separate_meshes_by_material", False)
        
        if separate_by_material:
            # マテリアルごとにメッシュを分割する場合
            self._create_separated_meshes(model_name, all_vertices, all_faces, 
                                         all_materials, None, model_group)
        else:
            # 全てのメッシュを統合する場合
            self._create_unified_mesh(model_name, all_vertices, all_faces, 
                                     all_materials, None, model_group)

        cmds.select(model_group)
        return model_group

        return None

    def _create_separated_meshes(self, model_name, all_vertices, all_faces, all_materials, all_textures, model_group):
        """
        マテリアルごとに分割されたメッシュを作成する。

        Args:
            model_name (str): モデル名
            all_vertices (list): 全ての頂点データ
            all_faces (list): 全ての面データ
            all_materials (list): 全てのマテリアルデータ
            all_textures (list): 全てのテクスチャデータ
            model_group (str): 親グループの名前
        """
        face_offset = 0
        for i, material in enumerate(all_materials):
            # 材質名をサニタイズして一意な名前を生成
            raw_material_name = material.name or f"material_{i}"
            material_name = maya_utils.sanitize_text(raw_material_name)
            mesh_name = maya_utils.sanitize_text(f"{model_name}_{material_name}")
            
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

            # メッシュを作成
            created_mesh = maya_utils.create_mesh_with_uvs(
                name=mesh_name,
                vertices=vertices,
                face_counts=face_counts,
                face_connects=face_connects,
                uvs=uvs,
                face_uv_connects=face_uv_connects
            )

            # マテリアルを作成して割り当て
            if all_textures is None:
                texture_path = None
            else:
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

    def _create_unified_mesh(self, model_name, all_vertices, all_faces, all_materials, all_textures, model_group):
        """
        全てのメッシュを統合した単一のメッシュを作成する。

        Args:
            model_name (str): モデル名
            all_vertices (list): 全ての頂点データ
            all_faces (list): 全ての面データ
            all_materials (list): 全てのマテリアルデータ
            all_textures (list): 全てのテクスチャデータ
            model_group (str): 親グループの名前
        """
        # 統合メッシュの名前を設定
        mesh_name = maya_utils.sanitize_text(model_name)
        
        # 全ての頂点と面を直接使用
        vertices = [v.position for v in all_vertices]
        uvs = []
        for vertex in all_vertices:
            uvs.extend(vertex.uv)  # UVデータをフラットなリストとして追加
        
        # 面データを作成
        face_connects = []
        face_counts = []
        face_uv_connects = []
        material_face_ranges = []
        
        # 全ての面を収集
        face_offset = 0
        for i, material in enumerate(all_materials):
            num_material_faces = material.face_count // 3
            if num_material_faces == 0:
                continue
            
            start_face = len(face_counts)
            for j in range(face_offset, face_offset + num_material_faces):
                face = all_faces[j]
                face_connects.extend(face.indices)
                face_counts.append(len(face.indices))
                # UVインデックスは頂点インデックスと同じ
                face_uv_connects.extend(face.indices)
            
            end_face = len(face_counts)
            material_face_ranges.append((material, start_face, end_face))
            face_offset += num_material_faces

        # 統合メッシュを作成
        created_mesh = maya_utils.create_mesh_with_uvs(
            name=mesh_name,
            vertices=vertices,
            face_counts=face_counts,
            face_connects=face_connects,
            uvs=uvs,
            face_uv_connects=face_uv_connects
        )
        
        # マテリアルを作成して、適切な面に割り当てる
        for material, start_face, end_face in material_face_ranges:
            if start_face == end_face:
                continue
                
            # マテリアル名をサニタイズ
            material_name = maya_utils.sanitize_text(material.name)
            
            # テクスチャパスを取得
            texture_path = None
            if all_textures:
                if material.texture_index != -1:
                    raw_texture_path = all_textures[material.texture_index]
                    texture_path = maya_utils.sanitize_texture_path(raw_texture_path, self.texture_dir)
            
            # マテリアルを作成
            shader = maya_utils.create_material(
                name=material_name,
                color=material.diffuse,
                texture_path=texture_path,
                texture_dir=self.texture_dir
            )
            
            # 面の範囲を選択してマテリアルを割り当て
            face_selection = f"{created_mesh}.f[{start_face}:{end_face-1}]"
            maya_utils.assign_material_to_faces(created_mesh, shader, face_selection)
        
        # 作成したメッシュをグループに追加
        cmds.parent(created_mesh, model_group)
