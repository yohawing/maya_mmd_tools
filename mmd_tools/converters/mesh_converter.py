import os
import time
import json

from maya import cmds
from typing import Tuple, Union, List

from mmd_tools.core.settings import settings
from mmd_tools.core import maya_utils
from mmd_tools.core.logger import get_logger
from mmd_tools.core.pmd_data import PmdData
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.material import PmxDrawFlag
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.constants import (
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_SPHERE_TEXTURE_INDEX,
    ATTR_MMD_TEXTURE_INDEX,
    ATTR_MMD_TOON_TEXTURE_INDEX,
    GEOMETRY_GROUP,
    ATTR_MMD_FILE_TYPE,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_FILE_VERSION,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_MEMO,
    ATTR_MMD_EDGE_FLAG,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_MATERIAL_INDEX,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
    ATTR_MMD_MORPH_GROUP_SPLIT_MESH,
    ATTR_MMD_VERTEX_MORPH_NAMES_JSON,
)

LOGGER = get_logger(__name__)

_ALPHA_CAPABLE_TEXTURE_EXTENSIONS = {".png", ".tga", ".tif", ".tiff", ".dds"}
_STANDARD_TOON_TEXTURE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "shaders", "toon_textures")
)


def _material_uses_transparency(material, texture_path=None) -> bool:
    """Return whether the material should use the alpha-blended DX11 technique."""
    opacity = material.diffuse[3] if hasattr(material, "diffuse") and len(material.diffuse) > 3 else 1.0
    if opacity < 0.999:
        return True

    if texture_path:
        return os.path.splitext(str(texture_path))[1].lower() in _ALPHA_CAPABLE_TEXTURE_EXTENSIONS

    return False


def _resolve_pmx_toon_texture_path(texture_dir, material, all_textures):
    """Resolve a PMX custom/shared toon texture to an absolute file path."""
    if not hasattr(material, "shared_toon_flag") or not hasattr(material, "toon_texture_index"):
        return None

    toon_index = int(material.toon_texture_index)
    if toon_index < 0:
        return None

    # PMX shared_toon_flag: 0 = regular texture table, 1 = shared toon01..toon10.
    if int(material.shared_toon_flag) == 0:
        if not all_textures or toon_index >= len(all_textures):
            return None
        return os.path.normpath(os.path.join(texture_dir, all_textures[toon_index]))

    if toon_index > 9:
        return None
    return os.path.join(_STANDARD_TOON_TEXTURE_DIR, f"toon{toon_index + 1:02d}.bmp")


def _ensure_mmd_shader_uniform_attributes(shader_node):
    """MMD シェーダーで uniform 属性がない場合に補完する。

    Maya の standalone 環境では dx11Shader / GLSLShader が OGSFX/uniform を
    自動生成しないことがあるため、事前に属性を作成しておく。
    """
    import maya.api.OpenMaya as om

    uniforms = [
        ("DiffuseColor", om.MFnNumericData.kDouble, 4, True, (0.8, 0.8, 0.8, 1.0)),
        ("SpecularColor", om.MFnNumericData.kDouble, 3, True, (0.5, 0.5, 0.5)),
        ("AmbientColor", om.MFnNumericData.kDouble, 3, True, (0.3, 0.3, 0.3)),
        ("EdgeColor", om.MFnNumericData.kDouble, 4, True, (0.0, 0.0, 0.0, 1.0)),
        ("Shininess", om.MFnNumericData.kDouble, 1, False, 20.0),
        ("EdgeSize", om.MFnNumericData.kDouble, 1, False, 1.0),
        ("SphereMode", om.MFnNumericData.kLong, 1, False, 0),
        ("Opacity", om.MFnNumericData.kDouble, 1, False, 1.0),
        ("HasMainTexture", om.MFnNumericData.kLong, 1, False, 0),
        ("HasSphereTexture", om.MFnNumericData.kLong, 1, False, 0),
        ("HasToonTexture", om.MFnNumericData.kLong, 1, False, 0),
        # MMD ライト（コントローラ駆動の唯一の光源）。GUI では dx11Shader が .fx
        # から自動生成するが、standalone/テストでは生成されないため補完しておき、
        # 後段のコントローラ結線が失敗しないようにする。
        ("MMDLightDirection", om.MFnNumericData.kDouble, 3, False, (0.5, -1.0, 0.5)),
        ("MMDLightColor", om.MFnNumericData.kDouble, 3, True, (1.0, 1.0, 1.0)),
    ]

    try:
        sel = om.MSelectionList()
        sel.add(shader_node)
        node = sel.getDependNode(0)
    except Exception as e:
        LOGGER.warning(
            "Failed to setup fallback uniform attributes for shader '%s': %s",
            shader_node,
            e,
            exc_info=True,
        )
        return

    dep_fn = om.MFnDependencyNode(node)
    for name, child_type, child_count, is_color, default_value in uniforms:
        if dep_fn.hasAttribute(name):
            continue

        try:
            if child_count == 1:
                attr_fn = om.MFnNumericAttribute()
                attr = attr_fn.create(name, name, child_type, default_value)
                attr_fn.keyable = True
                attr_fn.storable = True
                attr_fn.writable = True
                dep_fn.addAttribute(attr)
            elif child_count <= 3:
                children = []
                for i in range(child_count):
                    child_fn = om.MFnNumericAttribute()
                    child = child_fn.create(
                        f"{name}{i}",
                        f"{name}{i}",
                        child_type,
                        default_value[i],
                    )
                    child_fn.keyable = True
                    child_fn.storable = True
                    child_fn.writable = True
                    children.append(child)

                parent_fn = om.MFnNumericAttribute()
                parent = parent_fn.create(name, name, *children)
                parent_fn.keyable = True
                parent_fn.storable = True
                parent_fn.writable = True
                if is_color:
                    parent_fn.usedAsColor = True
                dep_fn.addAttribute(parent)
            else:
                parent_fn = om.MFnCompoundAttribute()
                parent = parent_fn.create(name, name)
                parent_fn.keyable = True
                parent_fn.storable = True
                parent_fn.writable = True
                if is_color:
                    parent_fn.usedAsColor = True

                for i in range(child_count):
                    child_fn = om.MFnNumericAttribute()
                    child = child_fn.create(f"{name}{i}", f"{name}{i}", child_type, default_value[i])
                    child_fn.keyable = True
                    child_fn.storable = True
                    child_fn.writable = True
                    parent_fn.addChild(child)
                dep_fn.addAttribute(parent)
        except Exception as e:
            # 属性作成に失敗しても後続の set_attribute が失敗しないように継続するが、
            # 失敗は必ずログに残す。
            LOGGER.warning(
                "Failed to create fallback uniform attribute '%s' on shader '%s': %s",
                name,
                shader_node,
                e,
                exc_info=True,
            )


def _ensure_dx11_uniform_attributes(shader_node):
    """Backward-compatible alias for dynamic uniform attr creation."""
    _ensure_mmd_shader_uniform_attributes(shader_node)


def _set_dx11_color_uniform(shader_node, attr_name, values):
    """Set dx11Shader color uniforms across Maya's generated child attrs.

    Maya GUI dx11Shader exposes HLSL float4 colors as a mixed compound plus
    generated attrs such as DiffuseColorRGB/DiffuseColorA.  Setting only the
    parent compound can leave the generated RGB attr at black on real VP2.
    """
    color = list(values)
    if len(color) < 3:
        return

    rgb = color[:3]
    alpha = color[3] if len(color) > 3 else None
    attr_type = "double4" if alpha is not None else "double3"

    if cmds.attributeQuery(attr_name, node=shader_node, exists=True):
        maya_utils.set_attribute(shader_node, attr_name, color, attr_type)

    rgb_attr = f"{attr_name}RGB"
    if cmds.attributeQuery(rgb_attr, node=shader_node, exists=True):
        try:
            cmds.setAttr(f"{shader_node}.{rgb_attr}", rgb[0], rgb[1], rgb[2], type="double3")
        except Exception:
            LOGGER.warning(
                "Failed to set dx11Shader RGB uniform '%s.%s'",
                shader_node,
                rgb_attr,
                exc_info=True,
            )

    for suffix, value in zip(("R", "G", "B"), rgb):
        child_attr = f"{attr_name}{suffix}"
        if cmds.attributeQuery(child_attr, node=shader_node, exists=True):
            try:
                cmds.setAttr(f"{shader_node}.{child_attr}", value)
            except Exception:
                LOGGER.debug("Failed to set dx11Shader child uniform '%s.%s'", shader_node, child_attr, exc_info=True)

    alpha_attr = f"{attr_name}A"
    if alpha is not None and cmds.attributeQuery(alpha_attr, node=shader_node, exists=True):
        try:
            cmds.setAttr(f"{shader_node}.{alpha_attr}", alpha)
        except Exception:
            LOGGER.debug("Failed to set dx11Shader alpha uniform '%s.%s'", shader_node, alpha_attr, exc_info=True)


def sync_dx11_generated_uniforms(shader_nodes=None):
    """Synchronize generated dx11Shader effect attrs after import.

    In Maya GUI, dx11Shader creates attrs like DiffuseColorRGB only after the
    .fx file has been evaluated by VP2.  During material construction those
    attrs may not exist yet, so this post-import pass copies the MMD custom
    attributes into the generated effect attrs once they are present.
    """
    synced = 0
    shaders = list(shader_nodes) if shader_nodes is not None else (cmds.ls(type="dx11Shader") or [])
    for shader in shaders:
        if not shader or not cmds.objExists(shader) or cmds.nodeType(shader) != "dx11Shader":
            continue
        if cmds.attributeQuery(ATTR_MMD_DIFFUSE_COLOR, node=shader, exists=True):
            try:
                diffuse = list(cmds.getAttr(f"{shader}.{ATTR_MMD_DIFFUSE_COLOR}")[0])
                opacity = 1.0
                if cmds.attributeQuery("Opacity", node=shader, exists=True):
                    opacity = float(cmds.getAttr(f"{shader}.Opacity"))
                _set_dx11_color_uniform(shader, "DiffuseColor", diffuse + [opacity])
                synced += 1
            except Exception:
                LOGGER.warning("Failed to sync dx11 DiffuseColor uniforms for '%s'", shader, exc_info=True)

        if cmds.attributeQuery(ATTR_MMD_EDGE_COLOR, node=shader, exists=True):
            try:
                edge_color = list(cmds.getAttr(f"{shader}.{ATTR_MMD_EDGE_COLOR}")[0])
                edge_alpha = 1.0
                if cmds.attributeQuery("EdgeColorA", node=shader, exists=True):
                    edge_alpha = float(cmds.getAttr(f"{shader}.EdgeColorA"))
                _set_dx11_color_uniform(shader, "EdgeColor", edge_color + [edge_alpha])
            except Exception:
                LOGGER.warning("Failed to sync dx11 EdgeColor uniforms for '%s'", shader, exc_info=True)

    return synced


class MeshConverter:
    """
    MMDのメッシュデータをMayaのメッシュノードに変換するクラス。
    """

    def __init__(self, pmx_filepath=""):
        """
        コンストラクタ。

        Args:
            pmx_filepath (str): 読み込むPMXファイルのパス。
        """
        self.logger = get_logger(__name__)
        self.created_shaders = []
        self.profile = {
            "mesh_create_sec": 0.0,
            "material_create_sec": 0.0,
            "material_assign_sec": 0.0,
            "parent_sec": 0.0,
            "created_mesh_count": 0,
            "source_vertex_count": 0,
            "mesh_vertex_slots_estimated": 0,
            "face_count": 0,
            "material_count_processed": 0,
        }
        if pmx_filepath:
            self.texture_dir = os.path.dirname(pmx_filepath)

    def _add_profile_time(self, key: str, start: float) -> None:
        """Accumulate timing in the converter profile."""
        self.profile[key] = round(float(self.profile.get(key, 0.0)) + time.perf_counter() - start, 6)

    def convert_pmx_mesh(self, pmx_data: PmxData, root_group: str) -> Tuple[str, Union[str, List[str]]]:
        """
        PMXのメッシュデータをMayaのメッシュノードに変換する。

        Args:
            pmx_data (pmx_parser.PmxParser): 解析されたPMXデータオブジェクト。
            root_group (str): ルートグループの名前。

        Returns:
            str: 作成されたMayaメッシュをまとめるグループノードの名前。
            str: 作成されたMayaメッシュノードの名前（分割時はリスト）。
        """
        model_name = pmx_data.header.get_name()
        all_vertices = pmx_data.vertices
        all_faces = pmx_data.faces
        all_materials = pmx_data.materials
        all_textures = pmx_data.textures

        # ジオメトリグループを作成
        geo_group = cmds.group(empty=True, name=GEOMETRY_GROUP, parent=root_group)

        # 設定からマテリアルごとのメッシュ分割設定を取得
        separate_by_material = settings.get("import.model.separate_meshes_by_material", False)
        split_by_morph_groups = settings.get("import.model.split_meshes_by_morph_groups", False)

        if separate_by_material:
            created_mesh = self._create_material_split_meshes(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                geo_group,
                is_pmd=False,
            )
        elif split_by_morph_groups:
            created_mesh = self._create_morph_group_split_meshes(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                pmx_data.morphs,
                geo_group,
            )
        else:
            created_mesh = self._create_unified_mesh(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                geo_group,
            )

        maya_utils.select_objects(geo_group)
        return geo_group, created_mesh

    def convert_pmd_mesh(self, pmd_data: PmdData, root_group: str) -> Tuple[str, Union[str, List[str]]]:
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
        separate_by_material = settings.get("import.model.separate_meshes_by_material", False)

        if separate_by_material:
            created_mesh = self._create_material_split_meshes(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                None,
                geo_group,
                is_pmd=True,
            )
        else:
            created_mesh = self._create_unified_mesh(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                None,
                geo_group,
                is_pmd=True,
            )

        maya_utils.select_objects(geo_group)
        return geo_group, created_mesh

    def _create_unified_mesh(
        self,
        model_name,
        all_vertices,
        all_faces,
        all_materials,
        all_textures,
        model_group,
        is_pmd=False,
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

        # 頂点数がゼロの場合は警告を出して処理を中断
        if not all_vertices or len(all_vertices) == 0:
            self.logger.warning(f"頂点数がゼロのためメッシュを作成しません: {model_name}")
            return None

        # 統合メッシュの名前を設定
        mesh_name = maya_utils.sanitize_text(model_name) + "_mesh"

        # 全ての頂点と面を直接使用 z*= -1
        vertices = [v.position for v in all_vertices]
        vertices = [(v[0], v[1], -v[2]) for v in vertices]
        normals = [v.normal for v in all_vertices]
        normals = [(n[0], n[1], -n[2]) for n in normals]

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
        create_start = time.perf_counter()
        created_mesh = maya_utils.create_mesh_with_uvs(
            name=mesh_name,
            vertices=vertices,
            face_counts=face_counts,
            face_connects=face_connects,
            uvs=uvs,
            face_uv_connects=face_uv_connects,
            normals=normals,
        )
        self._add_profile_time("mesh_create_sec", create_start)
        self.profile["created_mesh_count"] += 1
        self.profile["source_vertex_count"] = len(vertices)
        self.profile["mesh_vertex_slots_estimated"] += len(vertices)
        self.profile["face_count"] += len(face_counts)

        # マテリアルを作成して、適切な面に割り当てる
        for i, (material, start_face, end_face) in enumerate(material_face_ranges):
            if start_face == end_face:
                continue

            # マテリアル名をサニタイズ
            # material_name = maya_utils.sanitize_text(material.name)

            # テクスチャパスを取得
            texture_path = None
            if all_textures:
                if material.texture_index != -1:
                    raw_texture_path = all_textures[material.texture_index]
                    texture_path = maya_utils.sanitize_texture_path(raw_texture_path, self.texture_dir)

            # マテリアルを作成
            material_start = time.perf_counter()
            shader = self._create_material(
                material=material,
                texture_path=texture_path,
                all_textures=all_textures,
                is_pmd=is_pmd,
                material_index=i,
            )
            self._add_profile_time("material_create_sec", material_start)
            self.created_shaders.append(shader)
            self.profile["material_count_processed"] += 1

            # 面の範囲を選択してマテリアルを割り当て
            face_selection = f"{created_mesh}.f[{start_face}:{end_face - 1}]"
            assign_start = time.perf_counter()
            maya_utils.assign_material_to_faces(created_mesh, shader, face_selection)
            self._add_profile_time("material_assign_sec", assign_start)

        # 作成したメッシュをグループに追加
        parent_start = time.perf_counter()
        maya_utils.parent_objects(created_mesh, model_group)
        self._add_profile_time("parent_sec", parent_start)

        # MMDモデル表示用にバックフェイスカリングを無効化（設定に応じて）
        disable_backface_culling = settings.get("import.model.disable_backface_culling", True)
        if disable_backface_culling:
            maya_utils.set_viewport_backface_culling(False)

        return created_mesh

    def _create_material_split_meshes(
        self,
        model_name,
        all_vertices,
        all_faces,
        all_materials,
        all_textures,
        geo_group,
        is_pmd=False,
    ):
        """
        マテリアルごとに分割したメッシュを作成する。
        PMX の sub-mesh は該当 material の face が参照する頂点だけを保持し、
        mmd_source_vertex_indices に元 PMX vertex index を保存する。PMD は既存互換のため全頂点を保持する。

        Args:
            model_name (str): モデル名
            all_vertices (list): 全ての頂点データ
            all_faces (list): 全ての面データ
            all_materials (list): 全てのマテリアルデータ
            all_textures (list): 全てのテクスチャデータ
            geo_group (str): 親グループの名前
            is_pmd (bool): PMDファイルかどうか

        Returns:
            list: 作成されたメッシュノードの名前のリスト
        """
        if not all_vertices or len(all_vertices) == 0:
            self.logger.warning(f"頂点数がゼロのためメッシュを作成しません: {model_name}")
            return []

        # 全頂点座標 (z*= -1)
        vertices = [v.position for v in all_vertices]
        vertices = [(v[0], v[1], -v[2]) for v in vertices]
        normals = [v.normal for v in all_vertices]
        normals = [(n[0], n[1], -n[2]) for n in normals]

        # 全UV (flip V)
        uvs = []
        for vertex in all_vertices:
            vertex.uv = (vertex.uv[0], 1.0 - vertex.uv[1])
            uvs.extend(vertex.uv)

        mesh_names = []
        face_offset = 0

        for i, material in enumerate(all_materials):
            num_material_faces = material.face_count // 3
            if num_material_faces == 0:
                continue

            # この material の face 範囲だけ切り出す
            sub_face_connects = []
            sub_face_counts = []
            sub_face_uv_connects = []
            source_vertex_indices = []
            source_to_local = {}

            def get_local_vertex_index(source_index):
                source_index = int(source_index)
                if is_pmd:
                    return source_index
                if source_index not in source_to_local:
                    source_to_local[source_index] = len(source_vertex_indices)
                    source_vertex_indices.append(source_index)
                return source_to_local[source_index]

            for j in range(face_offset, face_offset + num_material_faces):
                face = all_faces[j]
                reverced_indices = face.indices[::-1]
                local_indices = [get_local_vertex_index(index) for index in reverced_indices]
                sub_face_connects.extend(local_indices)
                sub_face_counts.append(len(reverced_indices))
                sub_face_uv_connects.extend(local_indices)

            if is_pmd:
                mesh_vertices = vertices
                mesh_normals = normals
                mesh_uvs = uvs
            else:
                mesh_vertices = [vertices[index] for index in source_vertex_indices]
                mesh_normals = [normals[index] for index in source_vertex_indices]
                mesh_uvs = []
                for index in source_vertex_indices:
                    mesh_uvs.extend(uvs[index * 2 : index * 2 + 2])

            # マテリアル名からメッシュ名生成
            mat_name = material.get_name() if material.get_name() else f"material_{i}"
            sub_mesh_name = maya_utils.sanitize_text(f"{model_name}_{mat_name}_mesh")

            create_start = time.perf_counter()
            created_mesh = maya_utils.create_mesh_with_uvs(
                name=sub_mesh_name,
                vertices=mesh_vertices,
                face_counts=sub_face_counts,
                face_connects=sub_face_connects,
                uvs=mesh_uvs,
                face_uv_connects=sub_face_uv_connects,
                normals=mesh_normals,
            )
            self._add_profile_time("mesh_create_sec", create_start)
            self.profile["created_mesh_count"] += 1
            self.profile["source_vertex_count"] = len(vertices)
            self.profile["mesh_vertex_slots_estimated"] += len(mesh_vertices)
            self.profile["face_count"] += len(sub_face_counts)
            maya_utils.set_custom_attributes(
                created_mesh,
                {
                    ATTR_MMD_MATERIAL_INDEX: i,
                    "mmd_material_split_mesh": True,
                },
            )
            if not is_pmd:
                maya_utils.add_typed_attribute(created_mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, "longArray")
                maya_utils.set_attribute(
                    created_mesh,
                    ATTR_MMD_SOURCE_VERTEX_INDICES,
                    source_vertex_indices,
                    "longArray",
                )

            # テクスチャパスを取得
            texture_path = None
            if all_textures:
                if material.texture_index != -1:
                    raw_texture_path = all_textures[material.texture_index]
                    texture_path = maya_utils.sanitize_texture_path(raw_texture_path, self.texture_dir)

            # マテリアルを作成 (全体割当)
            material_start = time.perf_counter()
            shader = self._create_material(
                material=material,
                texture_path=texture_path,
                all_textures=all_textures,
                is_pmd=is_pmd,
                material_index=i,
            )
            self._add_profile_time("material_create_sec", material_start)
            self.created_shaders.append(shader)
            self.profile["material_count_processed"] += 1

            # 全 face にマテリアルを割り当て
            assign_start = time.perf_counter()
            maya_utils.assign_material_to_faces(
                created_mesh, shader, f"{created_mesh}.f[0:{num_material_faces - 1}]"
            )
            self._add_profile_time("material_assign_sec", assign_start)

            # グループに追加
            parent_start = time.perf_counter()
            maya_utils.parent_objects(created_mesh, geo_group)
            self._add_profile_time("parent_sec", parent_start)
            mesh_names.append(created_mesh)

            face_offset += num_material_faces

        # MMDモデル表示用にバックフェイスカリングを無効化（設定に応じて）
        disable_backface_culling = settings.get("import.model.disable_backface_culling", True)
        if disable_backface_culling:
            maya_utils.set_viewport_backface_culling(False)

        return mesh_names

    def _create_morph_group_split_meshes(
        self,
        model_name,
        all_vertices,
        all_faces,
        all_materials,
        all_textures,
        all_morphs,
        geo_group,
    ):
        """Create compact PMX meshes grouped by identical vertex morph material sets."""
        if not all_vertices or len(all_vertices) == 0:
            self.logger.warning(f"頂点数がゼロのためメッシュを作成しません: {model_name}")
            return []

        material_vertex_sets = self._build_material_vertex_sets(all_faces, all_materials)
        morph_names_by_material_set = {}
        touched_materials = set()

        for morph in all_morphs or []:
            if getattr(morph, "morph_type", None) != PmxMorphType.VertexMorph:
                continue

            morph_materials = set()
            for offset in getattr(morph, "offsets", []) or []:
                try:
                    vertex_index = int(offset.get("vertex_index"))
                except Exception:
                    continue
                for material_index, vertex_indices in material_vertex_sets.items():
                    if vertex_index in vertex_indices:
                        morph_materials.add(material_index)

            if not morph_materials:
                continue

            key = tuple(sorted(morph_materials))
            morph_names_by_material_set.setdefault(key, []).append(morph.get_name())
            touched_materials.update(morph_materials)

        if not morph_names_by_material_set:
            self.logger.info("No vertex morph material groups found; falling back to unified mesh import.")
            return self._create_unified_mesh(
                model_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                geo_group,
            )

        material_sets = [(key, morph_names) for key, morph_names in sorted(morph_names_by_material_set.items())]
        static_materials = tuple(
            i for i, material in enumerate(all_materials) if material.face_count > 0 and i not in touched_materials
        )
        if static_materials:
            material_sets.append((static_materials, []))

        mesh_names = []
        for group_index, (material_indices, vertex_morph_names) in enumerate(material_sets):
            suffix = f"morphGroup_{group_index}" if vertex_morph_names else "morphGroup_static"
            mesh_name = maya_utils.sanitize_text(f"{model_name}_{suffix}_mesh")
            created_mesh = self._create_compact_material_subset_mesh(
                mesh_name,
                all_vertices,
                all_faces,
                all_materials,
                all_textures,
                geo_group,
                material_indices,
                material_index_attr=None,
                extra_attrs={
                    ATTR_MMD_MORPH_GROUP_SPLIT_MESH: True,
                    ATTR_MMD_VERTEX_MORPH_NAMES_JSON: json.dumps(
                        vertex_morph_names,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            )
            if created_mesh:
                mesh_names.append(created_mesh)

        disable_backface_culling = settings.get("import.model.disable_backface_culling", True)
        if disable_backface_culling:
            maya_utils.set_viewport_backface_culling(False)

        return mesh_names

    def _build_material_vertex_sets(self, all_faces, all_materials):
        """Return source vertex indices referenced by each material face range."""
        material_vertex_sets = {}
        face_offset = 0
        for i, material in enumerate(all_materials):
            num_material_faces = material.face_count // 3
            vertices = set()
            for j in range(face_offset, face_offset + num_material_faces):
                face = all_faces[j]
                vertices.update(int(index) for index in face.indices)
            material_vertex_sets[i] = vertices
            face_offset += num_material_faces
        return material_vertex_sets

    def _create_compact_material_subset_mesh(
        self,
        mesh_name,
        all_vertices,
        all_faces,
        all_materials,
        all_textures,
        geo_group,
        material_indices,
        material_index_attr=None,
        extra_attrs=None,
    ):
        """Create a compact PMX mesh from one or more material face ranges."""
        vertices = [(v.position[0], v.position[1], -v.position[2]) for v in all_vertices]
        normals = [(v.normal[0], v.normal[1], -v.normal[2]) for v in all_vertices]
        uvs = []
        for vertex in all_vertices:
            flipped_uv = (vertex.uv[0], 1.0 - vertex.uv[1])
            uvs.extend(flipped_uv)

        material_face_offsets = []
        face_offset = 0
        for material in all_materials:
            num_material_faces = material.face_count // 3
            material_face_offsets.append((face_offset, num_material_faces))
            face_offset += num_material_faces

        source_vertex_indices = []
        source_to_local = {}
        face_counts = []
        face_connects = []
        face_uv_connects = []
        local_material_ranges = []

        def get_local_vertex_index(source_index):
            source_index = int(source_index)
            if source_index not in source_to_local:
                source_to_local[source_index] = len(source_vertex_indices)
                source_vertex_indices.append(source_index)
            return source_to_local[source_index]

        for material_index in material_indices:
            material = all_materials[material_index]
            source_face_start, num_material_faces = material_face_offsets[material_index]
            if num_material_faces == 0:
                continue

            local_face_start = len(face_counts)
            for j in range(source_face_start, source_face_start + num_material_faces):
                face = all_faces[j]
                reversed_indices = face.indices[::-1]
                local_indices = [get_local_vertex_index(index) for index in reversed_indices]
                face_connects.extend(local_indices)
                face_counts.append(len(reversed_indices))
                face_uv_connects.extend(local_indices)
            local_face_end = len(face_counts)
            if local_face_start != local_face_end:
                local_material_ranges.append((material_index, material, local_face_start, local_face_end))

        if not face_counts:
            return None

        mesh_vertices = [vertices[index] for index in source_vertex_indices]
        mesh_normals = [normals[index] for index in source_vertex_indices]
        mesh_uvs = []
        for index in source_vertex_indices:
            mesh_uvs.extend(uvs[index * 2 : index * 2 + 2])

        create_start = time.perf_counter()
        created_mesh = maya_utils.create_mesh_with_uvs(
            name=mesh_name,
            vertices=mesh_vertices,
            face_counts=face_counts,
            face_connects=face_connects,
            uvs=mesh_uvs,
            face_uv_connects=face_uv_connects,
            normals=mesh_normals,
        )
        self._add_profile_time("mesh_create_sec", create_start)
        self.profile["created_mesh_count"] += 1
        self.profile["source_vertex_count"] = len(vertices)
        self.profile["mesh_vertex_slots_estimated"] += len(mesh_vertices)
        self.profile["face_count"] += len(face_counts)

        attrs = dict(extra_attrs or {})
        if material_index_attr is not None:
            attrs[ATTR_MMD_MATERIAL_INDEX] = material_index_attr
            attrs["mmd_material_split_mesh"] = True
        if attrs:
            maya_utils.set_custom_attributes(created_mesh, attrs)
        maya_utils.add_typed_attribute(created_mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, "longArray")
        maya_utils.set_attribute(created_mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, source_vertex_indices, "longArray")

        for material_index, material, start_face, end_face in local_material_ranges:
            texture_path = None
            if all_textures and material.texture_index != -1:
                raw_texture_path = all_textures[material.texture_index]
                texture_path = maya_utils.sanitize_texture_path(raw_texture_path, self.texture_dir)

            material_start = time.perf_counter()
            shader = self._create_material(
                material=material,
                texture_path=texture_path,
                all_textures=all_textures,
                is_pmd=False,
                material_index=material_index,
            )
            self._add_profile_time("material_create_sec", material_start)
            self.created_shaders.append(shader)
            self.profile["material_count_processed"] += 1

            assign_start = time.perf_counter()
            maya_utils.assign_material_to_faces(created_mesh, shader, f"{created_mesh}.f[{start_face}:{end_face - 1}]")
            self._add_profile_time("material_assign_sec", assign_start)

        parent_start = time.perf_counter()
        maya_utils.parent_objects(created_mesh, geo_group)
        self._add_profile_time("parent_sec", parent_start)
        return created_mesh

    def _create_material(
        self,
        material,
        texture_path=None,
        all_textures=None,
        is_pmd=False,
        material_index=None,
    ):
        """
        MMDマテリアルデータからMayaマテリアルを作成します。

        Args:
            material: MMDマテリアルオブジェクト（PMX/PMD）
            texture_path (str, optional): テクスチャファイルのパス。
            all_textures (list, optional): 全てのテクスチャパスリスト（PMXのみ）
            is_pmd (bool): PMDファイルかどうか

        Returns:
            str: 作成されたシェーダーノード名。
        """
        sanitized_name = maya_utils.sanitize_text(material.get_name())
        # 名前が空の場合はデフォルト名を使用
        if not sanitized_name:
            sanitized_name = f"material_{material_index if material_index is not None else 0}"

        # create_mmd_shaders設定を確認
        create_mmd_shaders = settings.get("import.model.create_mmd_shaders")

        if create_mmd_shaders:
            backend = str(settings.get("import.model.mmd_shader_backend", "auto")).lower()
            if backend not in {"auto", "dx11", "glsl", "standard"}:
                cmds.warning(f"Unknown mmd_shader_backend '{backend}', fallback to auto.")
                backend = "auto"

            if backend != "standard":
                backend_order = ["dx11", "glsl"] if backend == "auto" else [backend]

                for target in backend_order:
                    shader = None
                    if target == "dx11":
                        try:
                            shader = cmds.shadingNode("dx11Shader", asShader=True, name=sanitized_name)
                            self._setup_dx11_shader(
                                shader,
                                material,
                                texture_path,
                                all_textures,
                                is_pmd,
                                material_index,
                            )
                            return shader
                        except (RuntimeError, Exception) as e:
                            cmds.warning(f"Failed to create dx11Shader: {e}. Trying next backend.")
                            if shader and cmds.objExists(shader):
                                cmds.delete(shader)
                            if backend != "auto":
                                break

                    if target == "glsl":
                        try:
                            shader = cmds.shadingNode("GLSLShader", asShader=True, name=sanitized_name)
                            self._setup_glsl_shader(
                                shader,
                                material,
                                texture_path,
                                all_textures,
                                is_pmd,
                                material_index,
                            )
                            return shader
                        except (RuntimeError, Exception) as e:
                            cmds.warning(f"Failed to create GLSLShader: {e}. Falling back.")
                            if shader and cmds.objExists(shader):
                                cmds.delete(shader)
                            if backend != "auto":
                                break

            if backend != "standard":
                cmds.warning("Falling back to standardSurface for material.")

        # 標準のstandardSurfaceを使用
        shader = cmds.shadingNode("standardSurface", asShader=True, name=sanitized_name)
        self._setup_standard_shader(shader, material, texture_path, all_textures, is_pmd, material_index)

        return shader

    def _apply_custom_attributes(
        self,
        shader,
        material,
        all_textures,
        is_pmd,
        material_index=None,
        texture_path=None,
        sphere_texture_path=None,
    ):
        """カスタムアトリビュートを適用する共通処理"""
        # mmd_materialフラグを追加（このマテリアルがMMDマテリアルであることを示す）

        custom_attrs = {
            ATTR_MMD_MATERIAL: 1,  # MMDマテリアルであることを示すフラグ
            ATTR_MMD_MATERIAL_INDEX: material.material_index,
            ATTR_MMD_MATERIAL_NAME: material.name,
            ATTR_MMD_DIFFUSE_COLOR: material.diffuse[:3],
            ATTR_MMD_AMBIENT_COLOR: material.ambient[:3],
            ATTR_MMD_TOON_TEXTURE_INDEX: material.toon_texture_index,
        }

        # テクスチャパスを保存
        if texture_path:
            custom_attrs["mmd_texture_path"] = texture_path
        if sphere_texture_path:
            custom_attrs["mmd_sphere_path"] = sphere_texture_path

        # スペキュラー関連の属性
        if hasattr(material, "specular"):
            custom_attrs[ATTR_MMD_SPECULAR_COLOR] = material.specular[:3]

        # 光沢度（PMDとPMXで属性名が異なる）
        if hasattr(material, "specular_power"):
            custom_attrs[ATTR_MMD_SHININESS] = material.specular_power
        elif hasattr(material, "specular_coefficient"):
            custom_attrs[ATTR_MMD_SHININESS] = material.specular_coefficient

        # PMDとPMXで異なる属性への対応
        if hasattr(material, "name_english"):
            custom_attrs[ATTR_MMD_MATERIAL_NAME_EN] = material.name_english
        else:
            custom_attrs[ATTR_MMD_MATERIAL_NAME_EN] = ""

        if is_pmd:
            custom_attrs[ATTR_MMD_EDGE_FLAG] = int(material.edge_flag)
        else:
            custom_attrs[ATTR_MMD_SPHERE_MODE] = int(material.sphere_mode)
            custom_attrs[ATTR_MMD_SPHERE_TEXTURE_INDEX] = material.sphere_texture_index
            custom_attrs[ATTR_MMD_TEXTURE_INDEX] = material.texture_index
            custom_attrs[ATTR_MMD_DRAW_FLAGS] = int(material.draw_flag)
            custom_attrs[ATTR_MMD_EDGE_COLOR] = material.edge_color[:3]
            custom_attrs[ATTR_MMD_EDGE_SIZE] = material.edge_size
            custom_attrs[ATTR_MMD_MEMO] = material.memo
            custom_attrs[ATTR_MMD_SHARED_TOON_FLAG] = int(material.shared_toon_flag)

        maya_utils.set_custom_attributes(
            shader,
            custom_attrs,
        )

    def _setup_standard_shader(self, shader, material, texture_path, all_textures, is_pmd, material_index=None):
        """標準のstandardSurfaceシェーダーを設定"""

        # マテリアル名をサニタイズ（テクスチャノード名に使用）
        sanitized_name = maya_utils.sanitize_text(material.name if material.name else "material")

        # 基本色設定（Diffuse）
        maya_utils.set_attribute(shader, "baseColor", material.diffuse[:3], "double3")

        # AlphaをOpacityに変換（StandardSurfaceではopacityを使用）
        maya_utils.set_attribute(
            shader,
            "opacity",
            (material.diffuse[3], material.diffuse[3], material.diffuse[3]),
            "double3",
        )

        # スペキュラー設定（MMDのspecularをStandardSurfaceにマッピング）
        if hasattr(material, "specular"):
            # スペキュラー色
            maya_utils.set_attribute(shader, "specularColor", material.specular[:3], "double3")

            # スペキュラー係数の取得（PMDとPMXで異なる）
            specular_coef = None
            if hasattr(material, "specular_coefficient"):
                specular_coef = material.specular_coefficient
            elif hasattr(material, "specular_power"):
                specular_coef = material.specular_power

            if specular_coef is not None:
                maya_utils.set_attribute(shader, "specularColor", material.specular[:3], "double3")

        # アンビエント設定（StandardSurfaceでは間接光の強度として使用）
        if hasattr(material, "ambient"):
            # アンビエント色の平均値を間接光の強度として使用
            ambient_intensity = (material.ambient[0] + material.ambient[1] + material.ambient[2]) / 3.0
            # エミッションとして微弱に設定（アンビエント光の表現）
            maya_utils.set_attribute(shader, "emission", ambient_intensity * 0.1, "float")
            maya_utils.set_attribute(shader, "emissionColor", material.ambient[:3], "double3")

        # 非金属マテリアルとして設定（MMDは基本的に非金属）
        maya_utils.set_attribute(shader, "metalness", 0.0, "float")

        # カスタムアトリビュートを適用
        self._apply_custom_attributes(shader, material, all_textures, is_pmd, material_index, texture_path)

        # テクスチャの設定
        if texture_path:
            # テクスチャパスを解決
            full_texture_path = os.path.join(self.texture_dir, texture_path)
            if os.path.exists(full_texture_path):
                file_node = cmds.shadingNode("file", asTexture=True, name=sanitized_name + "_file")
                place_uv_node = cmds.shadingNode(
                    "place2dTexture",
                    asUtility=True,
                    name=sanitized_name + "_place2dTexture",
                )
                # 標準的なUV接続
                cmds.connectAttr(place_uv_node + ".outUV", file_node + ".uvCoord")
                cmds.connectAttr(file_node + ".outColor", shader + ".baseColor")

                maya_utils.set_attribute(file_node, "fileTextureName", full_texture_path, "string")
            else:
                cmds.warning(f"Texture file not found: {full_texture_path}")

    def _setup_glsl_shader(self, shader, material, texture_path, all_textures, is_pmd, material_index=None):
        """GLSLShader を設定する。"""
        shader_ogsfx_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "shaders",
            "MMDShader.ogsfx",
        )
        shader_ogsfx_path = os.path.normpath(shader_ogsfx_path)

        maya_utils.set_attribute(shader, "shader", shader_ogsfx_path, "string")
        maya_utils.set_attribute(shader, "technique", "Main", "string")
        _ensure_mmd_shader_uniform_attributes(shader)

        edge_enabled = is_pmd
        if not is_pmd and hasattr(material, "draw_flag"):
            edge_enabled = bool(material.draw_flag & PmxDrawFlag.EDGE_DRAWING)

        maya_utils.set_attribute(shader, "DiffuseColor", material.diffuse, "double4")

        if hasattr(material, "specular"):
            maya_utils.set_attribute(shader, "SpecularColor", material.specular[:3], "double3")

        if hasattr(material, "ambient"):
            maya_utils.set_attribute(shader, "AmbientColor", material.ambient[:3], "double3")

        shininess = None
        if hasattr(material, "specular_coefficient"):
            shininess = material.specular_coefficient
        elif hasattr(material, "specular_power"):
            shininess = material.specular_power
        if shininess is not None:
            maya_utils.set_attribute(shader, "Shininess", shininess, "float")

        edge_color = [0.0, 0.0, 0.0, 1.0]
        if hasattr(material, "edge_color"):
            edge_color = list(material.edge_color)
            if len(edge_color) == 3:
                edge_color.append(1.0)
        maya_utils.set_attribute(shader, "EdgeColor", edge_color, "double4")
        edge_size = 0.0 if not edge_enabled else getattr(material, "edge_size", 1.0)
        maya_utils.set_attribute(shader, "EdgeSize", edge_size, "float")

        sphere_mode = getattr(material, "sphere_mode", 0)
        maya_utils.set_attribute(shader, "SphereMode", int(sphere_mode), "long")

        opacity = material.diffuse[3] if hasattr(material, "diffuse") and len(material.diffuse) > 3 else 1.0
        maya_utils.set_attribute(shader, "Opacity", opacity, "float")

        self._apply_custom_attributes(
            shader,
            material,
            all_textures,
            is_pmd,
            material_index,
            texture_path,
            None,
        )

    def _setup_dx11_shader(self, shader, material, texture_path, all_textures, is_pmd, material_index=None):
        """dx11Shaderを設定"""

        # シェーダーファイルのパスを設定
        shader_fx_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shaders", "MMDShader.fx")
        shader_fx_path = os.path.normpath(shader_fx_path)

        # dx11Shaderにエフェクトファイルを設定
        maya_utils.set_attribute(shader, "shader", shader_fx_path, "string")

        # mayapy standalone では dx11Shader が .fx ファイルから uniform 属性を
        # 自動生成しないため、事前に動的アトリビュートとして作成しておく
        _ensure_dx11_uniform_attributes(shader)

        # テクニックを設定（PMX draw_flag の EDGE_DRAWING で分岐）
        edge_enabled = is_pmd  # PMD は常に edge enabled として扱う
        if not is_pmd and hasattr(material, "draw_flag"):
            edge_enabled = bool(material.draw_flag & PmxDrawFlag.EDGE_DRAWING)

        transparent = _material_uses_transparency(material, texture_path)
        if edge_enabled:
            technique = "MMDTechniqueTransparent" if transparent else "MMDTechnique"
        else:
            technique = "MMDTechniqueNoEdgeTransparent" if transparent else "MMDTechniqueNoEdge"
        cmds.setAttr(f"{shader}.technique", technique, type="string")

        # 基本色設定（Diffuse）
        _set_dx11_color_uniform(shader, "DiffuseColor", material.diffuse)
        opacity = material.diffuse[3] if hasattr(material, "diffuse") and len(material.diffuse) > 3 else 1.0
        maya_utils.set_attribute(shader, "Opacity", opacity, "float")

        # スペキュラー設定
        if hasattr(material, "specular"):
            maya_utils.set_attribute(
                shader,
                "SpecularColor",
                material.specular[:3],
                "double3",
            )

        # スペキュラー係数の設定（PMDとPMXで異なる）
        specular_coef = None
        if hasattr(material, "specular_coefficient"):
            specular_coef = material.specular_coefficient
        elif hasattr(material, "specular_power"):
            specular_coef = material.specular_power

        if specular_coef is not None:
            maya_utils.set_attribute(shader, "Shininess", specular_coef, "float")

        # アンビエント設定
        if hasattr(material, "ambient"):
            maya_utils.set_attribute(
                shader,
                "AmbientColor",
                material.ambient[:3],
                "double3",
            )

        # エッジ設定（PMXのみ）
        if not is_pmd:
            # エッジ色
            _set_dx11_color_uniform(shader, "EdgeColor", material.edge_color)
            # エッジサイズ（EDGE_DRAWING 無効時は 0.0）
            edge_size = material.edge_size if edge_enabled else 0.0
            maya_utils.set_attribute(shader, "EdgeSize", edge_size, "float")

        # スフィアモード設定
        sphere_mode = getattr(material, "sphere_mode", 0)
        maya_utils.set_attribute(shader, "SphereMode", int(sphere_mode), "long")

        for texture_flag in ("HasMainTexture", "HasSphereTexture", "HasToonTexture"):
            if cmds.attributeQuery(texture_flag, node=shader, exists=True):
                maya_utils.set_attribute(shader, texture_flag, 0, "long")

        # テクスチャ設定
        if texture_path:
            full_texture_path = os.path.join(self.texture_dir, texture_path)
            full_texture_path = os.path.normpath(full_texture_path)

            # ファイルが存在するかチェック
            if os.path.exists(full_texture_path) and cmds.attributeQuery("MainTexture", node=shader, exists=True):
                # ファイルテクスチャノードを作成
                file_node = cmds.shadingNode("file", asTexture=True, name=shader + "_texture")
                # ファイルパスを設定
                maya_utils.set_attribute(file_node, "fileTextureName", full_texture_path, "string")
                # dx11ShaderのMainTextureに接続
                try:
                    cmds.connectAttr(file_node + ".outColor", shader + ".MainTexture", force=True)
                    if cmds.attributeQuery("HasMainTexture", node=shader, exists=True):
                        maya_utils.set_attribute(shader, "HasMainTexture", 1, "long")
                except Exception:
                    cmds.warning("Failed to connect texture to dx11Shader")
            else:
                cmds.warning(f"Texture file not found: {full_texture_path}")

        # スフィアテクスチャ設定（PMXのみ）
        sphere_texture_path = None
        if not is_pmd and hasattr(material, "sphere_texture_index") and material.sphere_texture_index >= 0:
            if all_textures and material.sphere_texture_index < len(all_textures):
                sphere_texture_path = all_textures[material.sphere_texture_index]
                full_sphere_path = os.path.join(self.texture_dir, sphere_texture_path)
                full_sphere_path = os.path.normpath(full_sphere_path)

                if os.path.exists(full_sphere_path) and cmds.attributeQuery("SphereTexture", node=shader, exists=True):
                    sphere_file_node = cmds.shadingNode("file", asTexture=True, name=shader + "_sphere_texture")
                    maya_utils.set_attribute(sphere_file_node, "fileTextureName", full_sphere_path, "string")
                    try:
                        cmds.connectAttr(
                            sphere_file_node + ".outColor",
                            shader + ".SphereTexture",
                            force=True,
                        )
                        if cmds.attributeQuery("HasSphereTexture", node=shader, exists=True):
                            maya_utils.set_attribute(shader, "HasSphereTexture", 1, "long")
                    except Exception:
                        cmds.warning("Failed to connect sphere texture to dx11Shader")

        # Toon texture setting. PMX custom toon uses the regular texture table;
        # shared toon uses bundled toon01.bmp..toon10.bmp assets.
        if not is_pmd:
            full_toon_path = _resolve_pmx_toon_texture_path(self.texture_dir, material, all_textures)
            if full_toon_path and os.path.exists(full_toon_path) and cmds.attributeQuery("ToonTexture", node=shader, exists=True):
                toon_file_node = cmds.shadingNode("file", asTexture=True, name=shader + "_toon_texture")
                maya_utils.set_attribute(toon_file_node, "fileTextureName", full_toon_path, "string")
                try:
                    cmds.connectAttr(
                        toon_file_node + ".outColor",
                        shader + ".ToonTexture",
                        force=True,
                    )
                    if cmds.attributeQuery("HasToonTexture", node=shader, exists=True):
                        maya_utils.set_attribute(shader, "HasToonTexture", 1, "long")
                except Exception:
                    cmds.warning("Failed to connect toon texture to dx11Shader")
            elif full_toon_path:
                cmds.warning(f"Toon texture file not found: {full_toon_path}")

        # カスタムアトリビュートを適用
        self._apply_custom_attributes(
            shader,
            material,
            all_textures,
            is_pmd,
            material_index,
            texture_path,
            sphere_texture_path,
        )
