"""
PMDファイルにエクスポートするためのモジュール。

Mayaシーン直結ではなく、dictベースの geometry / material / bone データを
PmdDataに変換して書き出す。PMXエクスポータの最小スライスに対応するサブセット
（header / vertices / faces / materials / bones）のみを扱う。
"""

import os

from mmd_tools.core.pmd_data import PmdData
from mmd_tools.core.pmd_data.bone import PmdBone, PmdBoneType
from mmd_tools.core.pmd_data.face import PmdFace
from mmd_tools.core.pmd_data.material import PmdMaterial
from mmd_tools.core.pmd_data.vertex import PmdVertex
from mmd_tools.core.utils import fan_triangulate as _fan_triangulate

# PMDの頂点インデックスは符号なし16bit。
PMD_MAX_VERTEX_INDEX = 0xFFFF
# tail / IK 親が無いことを表す番兵。
PMD_NO_BONE = 0xFFFF


def _normalize_bone_weight(v_raw: dict) -> int:
    """頂点の重みをPMDの 0-100 (bone0 の割合) に正規化する。

    `bone_weight` が与えられていればそれを 0-100 にクランプして使う。
    無ければ `bone_weights`（PMX風の float リスト）の先頭要素を百分率へ変換する。
    どちらも無ければ bone0 へ全乗せ（100）をデフォルトとする。

    Args:
        v_raw: 頂点を表すdict。

    Returns:
        bone_indices[0] に乗る重み（0-100の整数）。
    """
    if "bone_weight" in v_raw:
        weight = int(round(v_raw["bone_weight"]))
    else:
        bone_weights = v_raw.get("bone_weights")
        if bone_weights:
            weight = int(round(float(bone_weights[0]) * 100))
        else:
            weight = 100
    return max(0, min(100, weight))


class PmdExporter:
    """
    MayaのシーンデータをPMDファイルフォーマットにエクスポートするクラス。
    dict入力から最小限のPMDセクションを書き出す。
    """

    def __init__(self):
        pass

    def export_pmd_model(self, file_path: str, maya_data: dict) -> None:
        """
        dictベースのモデルデータをPMDファイルにエクスポートする。

        Args:
            file_path: エクスポート先のPMDファイルのパス。
            maya_data: vertices / faces を必須とするモデルデータ辞書。

        Raises:
            ValueError: 入力データがPMDとして書き出せない場合。
            IOError: ファイル書き込みに失敗した場合。
        """
        # --- validation ---
        vertices_raw = maya_data.get("vertices", [])
        faces_raw = maya_data.get("faces", [])

        if not vertices_raw:
            raise ValueError("vertices must not be empty")
        if not faces_raw:
            raise ValueError("faces must not be empty")

        vertex_count = len(vertices_raw)
        max_vertex_count = PMD_MAX_VERTEX_INDEX + 1
        if vertex_count > max_vertex_count:
            raise ValueError(
                f"PMD supports at most {max_vertex_count} vertices, got {vertex_count}"
            )

        # bones の事前確定（頂点の bone index 検証に必要）。
        bones_raw = maya_data.get("bones")
        if bones_raw is None:
            bone_count = 1
        else:
            if not bones_raw:
                raise ValueError("bones must not be empty when specified")
            bone_count = len(bones_raw)

        # --- ensure parent dir exists ---
        parent_dir = os.path.dirname(file_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # --- build PmdData ---
        pmd = PmdData()

        # --- header ---
        pmd.header.magic = b"Pmd"
        pmd.header.version = 1.0
        pmd.header.model_name = maya_data.get("model_name", "Untitled")
        pmd.header.comment = maya_data.get("comment", "")
        pmd.header.model_name_english = maya_data.get("model_name_english", "")
        pmd.header.comment_english = maya_data.get("comment_english", "")

        # --- vertices ---
        for v_raw in vertices_raw:
            vertex = PmdVertex()
            vertex.position = tuple(v_raw.get("position", [0.0, 0.0, 0.0]))
            vertex.normal = tuple(v_raw.get("normal", [0.0, 0.0, 0.0]))
            vertex.uv = tuple(v_raw.get("uv", [0.0, 0.0]))

            # bone_indices は常に2要素へ正規化する（不足は0埋め、超過は切り詰め）。
            bone_indices = (list(v_raw.get("bone_indices", [0, 0])) + [0, 0])[:2]
            for bone_index in bone_indices:
                if bone_index < 0 or bone_index >= bone_count:
                    raise ValueError(
                        f"bone index out of range: {bone_index} (bone_count={bone_count})"
                    )
            vertex.bone_indices = tuple(bone_indices)
            vertex.bone_weight = _normalize_bone_weight(v_raw)
            vertex.edge_flag = v_raw.get("edge_flag", 0)
            pmd.vertices.append(vertex)

        # --- faces (triangulate if needed) ---
        for f_raw in faces_raw:
            triangles = [f_raw] if len(f_raw) == 3 else _fan_triangulate(f_raw)
            for tri in triangles:
                for vertex_index in tri:
                    if vertex_index < 0 or vertex_index >= vertex_count:
                        raise ValueError(
                            f"face vertex index out of range: {vertex_index} "
                            f"(vertex_count={vertex_count})"
                        )
                face = PmdFace()
                face.indices = tuple(tri)
                pmd.faces.append(face)

        # --- materials ---
        # PMD の face_count は「面頂点数（インデックス数）」であり面数ではない。
        total_index_count = len(pmd.faces) * 3
        materials_raw = maya_data.get("materials")

        if not materials_raw:
            material = PmdMaterial(material_index=0)
            material.name = "Default"
            material.diffuse = (0.8, 0.8, 0.8, 1.0)
            material.specular_power = 5.0
            material.specular = (0.5, 0.5, 0.5)
            material.ambient = (0.3, 0.3, 0.3)
            material.toon_texture_index = 0
            material.edge_flag = 0
            material.face_count = total_index_count
            material.texture_file_name = ""
            pmd.materials.append(material)
        else:
            # face_count 未指定のマテリアルがあれば、残りインデックス数を最初の
            # 未指定マテリアルへ割り当てる（PMXエクスポータと同じ分配挙動）。
            specified_total = sum(m.get("face_count") or 0 for m in materials_raw)
            unspecified_indices = [
                i for i, m in enumerate(materials_raw) if m.get("face_count") is None
            ]

            for i, m_raw in enumerate(materials_raw):
                material = PmdMaterial(material_index=i)
                material.name = m_raw.get("name", f"Material{i}")
                material.diffuse = tuple(m_raw.get("diffuse", [0.8, 0.8, 0.8, 1.0]))
                material.specular_power = m_raw.get("specular_power", 5.0)
                material.specular = tuple(m_raw.get("specular", [0.5, 0.5, 0.5]))
                material.ambient = tuple(m_raw.get("ambient", [0.3, 0.3, 0.3]))
                toon_index = int(m_raw.get("toon_texture_index", 0))
                material.toon_texture_index = min(255, max(0, toon_index))
                material.edge_flag = m_raw.get("edge_flag", 0)
                material.texture_file_name = m_raw.get("texture_file_name", "")

                fc = m_raw.get("face_count")
                if fc is not None:
                    material.face_count = fc
                elif (unspecified_indices and i == unspecified_indices[0]) or (
                    not unspecified_indices and i == 0 and specified_total == 0
                ):
                    material.face_count = total_index_count - specified_total
                else:
                    material.face_count = 0

                pmd.materials.append(material)

            total_assigned = sum(m.face_count for m in pmd.materials)
            if total_assigned != total_index_count:
                pmd.materials[-1].face_count += total_index_count - total_assigned

        # --- bones ---
        if bones_raw is None:
            root_bone = PmdBone()
            root_bone.name = "root"
            root_bone.name_english = "root"
            root_bone.parent_bone_index = -1
            root_bone.tail_pos_bone_index = PMD_NO_BONE
            root_bone.bone_type = PmdBoneType.ROTATE_AND_MOVE
            root_bone.ik_parent_bone_index = 0
            root_bone.position = (0.0, 0.0, 0.0)
            pmd.bones.append(root_bone)
        else:
            for b_raw in bones_raw:
                bone = PmdBone()
                bone.name = b_raw.get("name", "Bone")
                bone.name_english = b_raw.get("name_english", "")
                bone.parent_bone_index = b_raw.get("parent_index", -1)
                bone.tail_pos_bone_index = b_raw.get("tail_pos_bone_index", PMD_NO_BONE)
                bone_type = b_raw.get("bone_type", PmdBoneType.ROTATE_AND_MOVE)
                bone.bone_type = bone_type if isinstance(bone_type, PmdBoneType) else PmdBoneType(bone_type)
                bone.ik_parent_bone_index = b_raw.get("ik_parent_bone_index", 0)
                bone.position = tuple(b_raw.get("position", [0.0, 0.0, 0.0]))
                pmd.bones.append(bone)

        # --- write ---
        pmd.write_file(file_path)


__all__ = [
    "PmdExporter",
    "_fan_triangulate",
    "_normalize_bone_weight",
]
