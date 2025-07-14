import os

from maya import cmds
from typing import Tuple

from mmd_tools import settings
from mmd_tools.core import maya_utils
from mmd_tools.core.pmd_parser import PmdParser
from mmd_tools.core.pmx_parser import PmxParser
from mmd_tools.core.constants import GEOMETRY_GROUP, ATTR_MMD_FILE_TYPE, ATTR_MMD_MODEL_NAME, ATTR_MMD_MODEL_NAME_EN, ATTR_MMD_COMMENT, ATTR_MMD_COMMENT_EN, ATTR_MMD_FILE_VERSION


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

    def convert_pmx_mesh(self, pmx_data: PmxParser, root_group: str) -> Tuple[str, str]:
        """
        PMXのメッシュデータをMayaのメッシュノードに変換する。

        Args:
            pmx_data (pmx_parser.PmxParser): 解析されたPMXデータオブジェクト。
            root_group (str): ルートグループの名前。

        Returns:
            str: 作成されたMayaメッシュをまとめるグループノードの名前。
            str: 作成されたMayaメッシュノードの名前。
        """
        model_name = pmx_data.header.model_name
        all_vertices = pmx_data.vertices
        all_faces = pmx_data.faces
        all_materials = pmx_data.materials
        all_textures = pmx_data.textures

        # ジオメトリグループを作成
        geo_group = cmds.group(empty=True, name=GEOMETRY_GROUP, parent=root_group)
        
        # カスタムアトリビュートをルートグループに追加
        maya_utils.set_custom_attributes(
            root_group,
            {
                ATTR_MMD_FILE_TYPE: pmx_data.header.magic,
                ATTR_MMD_MODEL_NAME: pmx_data.header.model_name,
                ATTR_MMD_MODEL_NAME_EN: pmx_data.header.model_name_english,
                ATTR_MMD_COMMENT: pmx_data.header.comment,
                ATTR_MMD_COMMENT_EN: pmx_data.header.comment_english,
            },
        )

        # メッシュのマテリアル分割は、まずは統合メッシュを作った後にSplitする処理をすればいいの

        created_mesh = self._create_unified_mesh(
            model_name,
            all_vertices,
            all_faces,
            all_materials,
            all_textures,
            geo_group,
        )

        # 設定からマテリアルごとのメッシュ分割設定を取得
        # separate_by_material = settings.get(
        #     "import.model.separate_meshes_by_material", False
        # )
        # if separate_by_material:
        #     maya_utils.split_mesh_by_material(model_group, all_materials)

        cmds.select(geo_group)
        return geo_group, created_mesh

    def convert_pmd_mesh(self, pmd_data: PmdParser, root_group: str):
        """
        PMDのメッシュデータをMayaのメッシュノードに変換する。

        Args:
            pmd_data (PmdParser): 解析されたPMDデータオブジェクト。
            root_group (str): ルートグループの名前。

        Returns:
            str: 作成されたMayaメッシュノードの名前。
            str: 作成されたMayaメッシュをまとめるグループノードの名前。
        """

        model_name = pmd_data.header.get_name()
        all_vertices = pmd_data.vertices
        all_faces = pmd_data.faces
        all_materials = pmd_data.materials

        # ジオメトリグループを作成
        geo_group = cmds.group(empty=True, name=GEOMETRY_GROUP, parent=root_group)
        
        # カスタムアトリビュートをルートグループに追加
        maya_utils.set_custom_attributes(
            root_group,
            {
                ATTR_MMD_FILE_TYPE: pmd_data.header.magic,
                ATTR_MMD_FILE_VERSION: pmd_data.header.version,
                ATTR_MMD_MODEL_NAME: pmd_data.header.model_name,
                ATTR_MMD_MODEL_NAME_EN: pmd_data.header.model_name_english,
                ATTR_MMD_COMMENT: pmd_data.header.comment,
                ATTR_MMD_COMMENT_EN: pmd_data.header.comment_english,
            },
        )

        # 設定からマテリアルごとのメッシュ分割設定を取得
        separate_by_material = settings.get(
            "import.model.separate_meshes_by_material", False
        )

        created_mesh = self._create_unified_mesh(
            model_name, all_vertices, all_faces, all_materials, None, geo_group
        )

        if separate_by_material:
            maya_utils.split_mesh_by_material(geo_group, all_materials)

        cmds.select(geo_group)
        return geo_group, created_mesh

    def _create_unified_mesh(
        self,
        model_name,
        all_vertices,
        all_faces,
        all_materials,
        all_textures,
        model_group,
    ):
        """
        全てのメッシュを統合した単一のメッシュを作成する。

        Args:
            model_name (str): モデル名
            all_vertices (list): 全ての頂点データ
            all_faces (list): 全ての面データ
            all_materials (list): 全てのマテリアルデータ
            all_textures (list): 全てのテクスチャデータ
            model_group (str): 親グループの名前
        Returns:
            str: 作成されたメッシュノードの名前
        """
        # 統合メッシュの名前を設定
        mesh_name = maya_utils.sanitize_text(model_name) + "_mesh"

        # 全ての頂点と面を直接使用 z*= -1
        vertices = [v.position for v in all_vertices]
        vertices = [(v[0], v[1], -v[2]) for v in vertices]

        uvs = []
        for vertex in all_vertices:
            # flip V
            vertex.uv = (vertex.uv[0], 1.0 - vertex.uv[1])
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
                reverced_indices = face.indices[::-1]  # PMXの面は逆順なので反転
                face_connects.extend(reverced_indices)
                face_counts.append(len(reverced_indices))
                # UVインデックスは頂点インデックスと同じ
                face_uv_connects.extend(reverced_indices)

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
            face_uv_connects=face_uv_connects,
        )

        # マテリアルを作成して、適切な面に割り当てる
        for material, start_face, end_face in material_face_ranges:
            if start_face == end_face:
                continue

            # マテリアル名をサニタイズ
            # material_name = maya_utils.sanitize_text(material.name)

            # テクスチャパスを取得
            texture_path = None
            if all_textures:
                if material.texture_index != -1:
                    raw_texture_path = all_textures[material.texture_index]
                    texture_path = maya_utils.sanitize_texture_path(
                        raw_texture_path, self.texture_dir
                    )

            # マテリアルを作成
            shader = maya_utils.create_material(
                name=material.name,
                color=material.diffuse,
                texture_path=texture_path,
                texture_dir=self.texture_dir,
            )

            # 面の範囲を選択してマテリアルを割り当て
            face_selection = f"{created_mesh}.f[{start_face}:{end_face - 1}]"
            maya_utils.assign_material_to_faces(created_mesh, shader, face_selection)

        # 作成したメッシュをグループに追加
        cmds.parent(created_mesh, model_group)

        # MMDモデル表示用にバックフェイスカリングを無効化（設定に応じて）
        disable_backface_culling = settings.get(
            "import.model.disable_backface_culling", True
        )
        if disable_backface_culling:
            maya_utils.set_viewport_backface_culling(False)

        return created_mesh
