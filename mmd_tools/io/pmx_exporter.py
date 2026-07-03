"""
PMXファイルにエクスポートするためのモジュール。

Mayaシーン直結ではなく、dictベースの geometry / material / bone /
VertexMorph / physics データをPmxDataに変換して書き出す。
"""

import os

from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.bone import PmxBone, PmxBoneFlag
from mmd_tools.core.pmx_data.display_frame import PmxDisplayFrame
from mmd_tools.core.pmx_data.face import PmxFace
from mmd_tools.core.pmx_data.header import PmxEncoding
from mmd_tools.core.pmx_data.joint import PmxJoint
from mmd_tools.core.pmx_data.material import PmxMaterial
from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType
from mmd_tools.core.pmx_data.rigid_body import PmxRigidBody
from mmd_tools.core.pmx_data.vertex import PmxVertex
from mmd_tools.core.display_frame_metadata import normalize_display_frame_dict
from mmd_tools.core.utils import (
    choose_index_size as _choose_index_size,
    choose_reference_index_size as _choose_reference_index_size,
    fan_triangulate as _fan_triangulate,
)


class PmxExporter:
    """
    MayaのシーンデータをPMXファイルフォーマットにエクスポートするクラス。
    dict入力から段階的にPMXの主要セクションを書き出す。
    """

    def __init__(self):
        pass

    def export_pmx_model(self, file_path: str, maya_data: dict) -> None:
        """
        dictベースのモデルデータをPMXファイルにエクスポートする。

        Args:
            file_path: エクスポート先のPMXファイルのパス。
            maya_data: vertices / faces を必須とするモデルデータ辞書。

        Raises:
            ValueError: 入力データがPMXとして書き出せない場合。
            IOError: ファイル書き込みに失敗した場合。
        """
        # --- validation ---
        vertices_raw = maya_data.get("vertices", [])
        faces_raw = maya_data.get("faces", [])

        if not vertices_raw:
            raise ValueError("vertices must not be empty")
        if not faces_raw:
            raise ValueError("faces must not be empty")

        # --- ensure parent dir exists ---
        parent_dir = os.path.dirname(file_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # --- build PmxData ---
        pmx = PmxData()

        # --- determine sizes (needed before header) ---
        vertex_count = len(vertices_raw)
        vertex_index_size = _choose_index_size(vertex_count)

        # --- determine bone info (needed before vertex creation for index size) ---
        bones_raw = maya_data.get("bones")
        if bones_raw is None:
            bone_count = 1
        else:
            if not bones_raw:
                raise ValueError("bones must not be empty when specified")
            bone_count = len(bones_raw)
        pmx.header.bone_index_size = _choose_reference_index_size(bone_count)

        morphs_raw = maya_data.get("morphs") or []
        display_frames_raw = maya_data.get("display_frames") or []
        rigid_bodies_raw = maya_data.get("rigid_bodies") or []
        joints_raw = maya_data.get("joints") or []
        rigid_body_count = len(rigid_bodies_raw)
        if joints_raw and rigid_body_count == 0:
            raise ValueError("joints require at least one rigid body")

        pmx.header.magic = b"PMX "
        pmx.header.version = 2.0
        pmx.header.header_size = 8
        pmx.header.encoding = PmxEncoding.UTF16LE
        pmx.header.additional_uv = 0
        pmx.header.vertex_index_size = vertex_index_size
        textures = maya_data.get("textures", [])
        materials_raw = maya_data.get("materials", [])
        pmx.header.texture_index_size = _choose_reference_index_size(len(textures))
        pmx.header.material_index_size = _choose_reference_index_size(len(materials_raw))
        pmx.header.morph_index_size = _choose_reference_index_size(len(morphs_raw))
        pmx.header.rigid_body_index_size = _choose_reference_index_size(rigid_body_count)

        model_name = maya_data.get("model_name", "Untitled")
        pmx.header.model_name = model_name
        if "model_name_english" in maya_data:
            pmx.header.model_name_english = maya_data["model_name_english"]
        else:
            pmx.header.model_name_english = model_name
        if "comment" in maya_data:
            pmx.header.comment = maya_data["comment"]
        else:
            pmx.header.comment = ""
        if "comment_english" in maya_data:
            pmx.header.comment_english = maya_data["comment_english"]
        else:
            pmx.header.comment_english = ""

        # --- vertices ---
        for v_raw in vertices_raw:
            pos = v_raw.get("position", [0.0, 0.0, 0.0])
            normal = v_raw.get("normal", [0.0, 0.0, 0.0])
            uv = v_raw.get("uv", [0.0, 0.0])
            bone_indices = v_raw.get("bone_indices", [0])
            bone_weights = v_raw.get("bone_weights", [])
            edge_mag = v_raw.get("edge_magnification", 1.0)

            v = PmxVertex(
                bone_index_size=pmx.header.bone_index_size,
                additional_uv_count=pmx.header.additional_uv,
            )
            v.position = tuple(pos)
            v.normal = tuple(normal)
            v.uv = tuple(uv)

            # Determine weight transform type from bone_indices length.
            if len(bone_indices) == 1:
                v.weight_transform_type = 0  # BDEF1
                v.bone_indices = [bone_indices[0]]
                v.bone_weights = []
            elif len(bone_indices) == 2:
                v.weight_transform_type = 1  # BDEF2
                v.bone_indices = [bone_indices[0], bone_indices[1]]
                v.bone_weights = [bone_weights[0] if bone_weights else 0.5]
            elif len(bone_indices) == 4:
                v.weight_transform_type = 2  # BDEF4
                v.bone_indices = list(bone_indices[:4])
                weights = list(bone_weights[:4]) if bone_weights else []
                while len(weights) < 4:
                    weights.append(0.0)
                v.bone_weights = weights
            else:
                raise ValueError(
                    f"Unsupported bone_indices length: {len(bone_indices)}. "
                    f"Must be 1 (BDEF1), 2 (BDEF2), or 4 (BDEF4)."
                )

            for bone_index in v.bone_indices:
                if bone_index < 0 or bone_index >= bone_count:
                    raise ValueError(
                        f"bone index out of range: {bone_index} "
                        f"(bone_count={bone_count})"
                    )

            v.edge_magnification = edge_mag
            pmx.vertices.append(v)

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
                face = PmxFace(pmx.header.vertex_index_size)
                face.indices = tuple(tri)
                pmx.faces.append(face)

        # --- textures ---
        pmx.textures = textures

        # --- materials ---
        total_index_count = len(pmx.faces) * 3

        if not materials_raw:
            mat = PmxMaterial(
                texture_index_size=pmx.header.texture_index_size,
                encoding=pmx.header.encoding,
            )
            mat.name = "Default"
            mat.name_english = "Default"
            mat.diffuse = (0.8, 0.8, 0.8, 1.0)
            mat.specular = (0.5, 0.5, 0.5)
            mat.specular_coefficient = 5.0
            mat.ambient = (0.3, 0.3, 0.3)
            mat.draw_flag = 0x01 | 0x02 | 0x10  # DOUBLE_SIDED | GROUND_SHADOW | EDGE_DRAWING
            mat.edge_color = (0.0, 0.0, 0.0, 1.0)
            mat.edge_size = 1.0
            mat.texture_index = -1
            mat.sphere_texture_index = -1
            mat.sphere_mode = 0
            mat.shared_toon_flag = 0
            mat.toon_texture_index = -1
            mat.memo = ""
            mat.face_count = total_index_count
            pmx.materials.append(mat)
        else:
            # Distribute face counts: if any material is missing face_count,
            # assign all remaining index count to the first unspecified material.
            specified_total = sum(m.get("face_count") or 0 for m in materials_raw)
            unspecified_indices = [
                i for i, m in enumerate(materials_raw) if m.get("face_count") is None
            ]

            for i, m_raw in enumerate(materials_raw):
                mat = PmxMaterial(
                    texture_index_size=pmx.header.texture_index_size,
                    encoding=pmx.header.encoding,
                )
                mat.name = m_raw.get("name", f"Material{i}")
                mat.name_english = m_raw.get("name_english", "")
                mat.diffuse = tuple(m_raw.get("diffuse", [0.8, 0.8, 0.8, 1.0]))
                mat.specular = tuple(m_raw.get("specular", [0.5, 0.5, 0.5]))
                mat.specular_coefficient = m_raw.get("specular_coefficient", 5.0)
                mat.ambient = tuple(m_raw.get("ambient", [0.3, 0.3, 0.3]))
                mat.draw_flag = m_raw.get("draw_flag", 0x01 | 0x02 | 0x10)
                mat.edge_color = tuple(m_raw.get("edge_color", [0.0, 0.0, 0.0, 1.0]))
                mat.edge_size = m_raw.get("edge_size", 1.0)
                mat.texture_index = m_raw.get("texture_index", -1)
                mat.sphere_texture_index = m_raw.get("sphere_texture_index", -1)
                mat.sphere_mode = m_raw.get("sphere_mode", 0)
                mat.shared_toon_flag = m_raw.get("shared_toon_flag", 0)
                mat.toon_texture_index = m_raw.get("toon_texture_index", -1)
                mat.memo = m_raw.get("memo", "")

                fc = m_raw.get("face_count")
                if fc is not None:
                    mat.face_count = fc
                elif (unspecified_indices and i == unspecified_indices[0]) or (
                    not unspecified_indices and i == 0 and specified_total == 0
                ):
                    mat.face_count = total_index_count - specified_total
                else:
                    mat.face_count = 0

                pmx.materials.append(mat)

            total_assigned = sum(m.face_count for m in pmx.materials)
            if total_assigned != total_index_count:
                pmx.materials[-1].face_count += total_index_count - total_assigned

        pmx.header.material_index_size = _choose_reference_index_size(len(pmx.materials))

        # --- bones ---
        if bones_raw is None:
            root_bone = PmxBone(
                bone_index_size=pmx.header.bone_index_size,
                encoding=pmx.header.encoding,
            )
            root_bone.name = "root"
            root_bone.name_english = "root"
            root_bone.position = (0.0, 0.0, 0.0)
            root_bone.parent_bone_index = -1
            root_bone.transform_layer = 0
            root_bone.bone_flag = (
                PmxBoneFlag.DISPLAY
                | PmxBoneFlag.OPERATABLE
                | PmxBoneFlag.ROTATABLE
                | PmxBoneFlag.MOVABLE
            )
            root_bone.connect_position_offset = (0.0, 0.0, 0.0)
            pmx.bones.append(root_bone)
        else:
            for b_raw in bones_raw:
                bone = PmxBone(
                    bone_index_size=pmx.header.bone_index_size,
                    encoding=pmx.header.encoding,
                )
                bone.name = b_raw.get("name", "Bone")
                bone.name_english = b_raw.get("name_english", "")
                bone.position = tuple(b_raw.get("position", [0.0, 0.0, 0.0]))
                bone.parent_bone_index = b_raw.get("parent_index", -1)
                bone.transform_layer = b_raw.get("transform_layer", 0)
                if "bone_flag" in b_raw:
                    bone.bone_flag = b_raw["bone_flag"]
                else:
                    bone.bone_flag = (
                        PmxBoneFlag.DISPLAY
                        | PmxBoneFlag.OPERATABLE
                        | PmxBoneFlag.ROTATABLE
                        | PmxBoneFlag.MOVABLE
                    )
                if int(bone.bone_flag) & int(PmxBoneFlag.CONNECT_BONE):
                    bone.connect_bone_index = b_raw.get("connect_bone_index", -1)
                    bone.connect_position_offset = (0.0, 0.0, 0.0)
                else:
                    bone.connect_position_offset = tuple(
                        b_raw.get("connect_position_offset", [0.0, 0.0, 0.0])
                    )
                    bone.connect_bone_index = -1

                bone.grant_parent_bone_index = b_raw.get("grant_parent_bone_index", -1)
                bone.grant_rate = b_raw.get("grant_rate", 0.0)
                bone.axis_direction = tuple(b_raw.get("axis_direction", [0.0, 0.0, 0.0]))
                bone.x_axis_direction = tuple(
                    b_raw.get("x_axis_direction", [1.0, 0.0, 0.0])
                )
                bone.z_axis_direction = tuple(
                    b_raw.get("z_axis_direction", [0.0, 0.0, 1.0])
                )
                bone.key_value = b_raw.get("key_value", 0)
                bone.ik_target_bone_index = b_raw.get("ik_target_bone_index", -1)
                bone.ik_loop_count = b_raw.get("ik_loop_count", 0)
                bone.ik_limit_angle = b_raw.get("ik_limit_angle", 0.0)
                pmx.bones.append(bone)

        # --- morphs ---
        for m_raw in morphs_raw:
            morph_type = m_raw.get("type", m_raw.get("morph_type", "vertex"))
            if isinstance(morph_type, str):
                normalized_type = morph_type.lower()
            elif morph_type == PmxMorphType.VertexMorph or morph_type == int(PmxMorphType.VertexMorph):
                normalized_type = "vertex"
            elif morph_type == PmxMorphType.BoneMorph or morph_type == int(PmxMorphType.BoneMorph):
                normalized_type = "bone"
            elif morph_type == PmxMorphType.MaterialMorph or morph_type == int(PmxMorphType.MaterialMorph):
                normalized_type = "material"
            else:
                normalized_type = morph_type

            morph = PmxMorph(
                vertex_index_size=pmx.header.vertex_index_size,
                material_index_size=pmx.header.material_index_size,
                bone_index_size=pmx.header.bone_index_size,
                morph_index_size=pmx.header.morph_index_size,
                rigid_body_index_size=pmx.header.rigid_body_index_size,
                encoding=pmx.header.encoding,
            )

            if normalized_type == "vertex":
                morph.name = m_raw.get("name", "VertexMorph")
                morph.name_english = m_raw.get("name_english", morph.name)
                morph.panel = m_raw.get("panel", 4)
                morph.morph_type = PmxMorphType.VertexMorph

                for offset in m_raw.get("offsets", []):
                    vertex_index = offset["vertex_index"]
                    if vertex_index < 0 or vertex_index >= vertex_count:
                        raise ValueError(
                            f"morph vertex index out of range: {vertex_index} "
                            f"(vertex_count={vertex_count})"
                        )
                    morph.offsets.append(
                        {
                            "vertex_index": vertex_index,
                            "position_offset": tuple(offset.get("position_offset", [0.0, 0.0, 0.0])),
                        }
                    )

            elif normalized_type == "bone":
                morph.name = m_raw.get("name", "BoneMorph")
                morph.name_english = m_raw.get("name_english", morph.name)
                morph.panel = m_raw.get("panel", 4)
                morph.morph_type = PmxMorphType.BoneMorph

                for offset in m_raw.get("offsets", []):
                    bone_index = offset["bone_index"]
                    if bone_index < 0 or bone_index >= len(pmx.bones):
                        raise ValueError(
                            f"morph bone index out of range: {bone_index} "
                            f"(bone_count={len(pmx.bones)})"
                        )
                    morph.offsets.append(
                        {
                            "bone_index": bone_index,
                            "translation": tuple(offset.get("translation", [0.0, 0.0, 0.0])),
                            "rotation": tuple(offset.get("rotation", [0.0, 0.0, 0.0, 1.0])),
                        }
                    )

            elif normalized_type == "material":
                material_count = len(pmx.materials)
                morph.name = m_raw.get("name", "MaterialMorph")
                morph.name_english = m_raw.get("name_english", morph.name)
                morph.panel = m_raw.get("panel", 4)
                morph.morph_type = PmxMorphType.MaterialMorph

                for offset in m_raw.get("offsets", []):
                    material_index = offset["material_index"]
                    if material_index != -1 and not (0 <= material_index < material_count):
                        raise ValueError(
                            f"morph material index out of range: {material_index} "
                            f"(material_count={material_count})"
                        )
                    morph.offsets.append(
                        {
                            "material_index": material_index,
                            "operation_type": offset.get("operation_type", 1),
                            "diffuse": tuple(offset.get("diffuse", [0.0, 0.0, 0.0, 0.0])),
                            "specular": tuple(offset.get("specular", [0.0, 0.0, 0.0])),
                            "specular_coefficient": offset.get("specular_coefficient", 0.0),
                            "ambient": tuple(offset.get("ambient", [0.0, 0.0, 0.0])),
                            "edge_color": tuple(offset.get("edge_color", [0.0, 0.0, 0.0, 0.0])),
                            "edge_size": offset.get("edge_size", 0.0),
                            "texture_factor": tuple(offset.get("texture_factor", [0.0, 0.0, 0.0, 0.0])),
                            "sphere_texture_factor": tuple(offset.get("sphere_texture_factor", [0.0, 0.0, 0.0, 0.0])),
                            "toon_texture_factor": tuple(offset.get("toon_texture_factor", [0.0, 0.0, 0.0, 0.0])),
                        }
                    )

            else:
                raise ValueError(f"Unsupported morph type: {morph_type!r}")

            pmx.morphs.append(morph)

        # --- rigid bodies ---
        for rb_raw in rigid_bodies_raw:
            rb = PmxRigidBody(
                bone_index_size=pmx.header.bone_index_size,
                encoding_flag=pmx.header.encoding_flag,
            )
            rb.name = rb_raw.get("name", "RigidBody")
            rb.name_english = rb_raw.get("name_english", "")
            rb.related_bone_index = rb_raw.get("related_bone_index", -1)
            if rb.related_bone_index != -1 and not (0 <= rb.related_bone_index < bone_count):
                raise ValueError(
                    f"rigid body related_bone_index out of range: {rb.related_bone_index} "
                    f"(bone_count={bone_count})"
                )
            rb.group = rb_raw.get("group", 0)
            rb.collision_mask = rb_raw.get("collision_mask", 0)
            rb.shape_type = rb_raw.get("shape_type", 0)
            rb.size = tuple(rb_raw.get("size", [0.0, 0.0, 0.0]))
            rb.position = tuple(rb_raw.get("position", [0.0, 0.0, 0.0]))
            rb.rotation = tuple(rb_raw.get("rotation", [0.0, 0.0, 0.0]))
            rb.mass = rb_raw.get("mass", 0.0)
            rb.velocity_attenuation = rb_raw.get("velocity_attenuation", 0.0)
            rb.rotation_attenuation = rb_raw.get("rotation_attenuation", 0.0)
            rb.elasticity = rb_raw.get("elasticity", 0.0)
            rb.friction = rb_raw.get("friction", 0.0)
            rb.physics_mode = rb_raw.get("physics_mode", 0)
            pmx.rigid_bodies.append(rb)

        # --- joints ---
        for j_raw in joints_raw:
            joint = PmxJoint(
                rigid_body_index_size=pmx.header.rigid_body_index_size,
                encoding=pmx.header.encoding,
            )
            joint.name = j_raw.get("name", "Joint")
            joint.name_english = j_raw.get("name_english", "")
            joint.joint_type = j_raw.get("joint_type", 0)
            joint.rigid_body_a_index = j_raw.get("rigid_body_a_index", -1)
            joint.rigid_body_b_index = j_raw.get("rigid_body_b_index", -1)
            for attr in ("rigid_body_a_index", "rigid_body_b_index"):
                rb_index = getattr(joint, attr)
                if rb_index != -1 and not (0 <= rb_index < rigid_body_count):
                    raise ValueError(
                        f"joint {attr} out of range: {rb_index} "
                        f"(rigid_body_count={rigid_body_count})"
                    )
            joint.position = tuple(j_raw.get("position", [0.0, 0.0, 0.0]))
            joint.rotation = tuple(j_raw.get("rotation", [0.0, 0.0, 0.0]))
            joint.translation_limit_min = tuple(j_raw.get("translation_limit_min", [0.0, 0.0, 0.0]))
            joint.translation_limit_max = tuple(j_raw.get("translation_limit_max", [0.0, 0.0, 0.0]))
            joint.rotation_limit_min = tuple(j_raw.get("rotation_limit_min", [0.0, 0.0, 0.0]))
            joint.rotation_limit_max = tuple(j_raw.get("rotation_limit_max", [0.0, 0.0, 0.0]))
            joint.spring_translation = tuple(j_raw.get("spring_translation", [0.0, 0.0, 0.0]))
            joint.spring_rotation = tuple(j_raw.get("spring_rotation", [0.0, 0.0, 0.0]))
            pmx.joints.append(joint)

        # --- display frames (required for valid PMX) ---
        def _append_display_frame(frame_raw):
            frame_data = normalize_display_frame_dict(frame_raw)
            frame = PmxDisplayFrame(
                pmx.header.bone_index_size,
                pmx.header.morph_index_size,
                pmx.header.encoding_flag,
            )
            frame.name = frame_data["name"]
            frame.name_english = frame_data["name_english"]
            frame.special_flag = frame_data["special_flag"]
            frame.elements = []
            for element in frame_data["elements"]:
                element_type = element["type"]
                index = element["index"]
                if element_type == 0:
                    if index < 0 or index >= bone_count:
                        raise ValueError(
                            f"display frame bone index out of range: {index} "
                            f"(bone_count={bone_count})"
                        )
                elif element_type == 1:
                    if index < 0 or index >= len(pmx.morphs):
                        raise ValueError(
                            f"display frame morph index out of range: {index} "
                            f"(morph_count={len(pmx.morphs)})"
                        )
                else:
                    raise ValueError(f"Unsupported display frame element type: {element_type}")
                frame.elements.append({"type": element_type, "index": index})
            pmx.display_frames.append(frame)

        if display_frames_raw:
            for frame_raw in display_frames_raw:
                _append_display_frame(frame_raw)
        else:
            _append_display_frame(
                {
                    "name": "Root",
                    "name_english": "Root",
                    "special_flag": 1,
                    "elements": [{"type": 0, "index": 0}],
                }
            )
            _append_display_frame(
                {
                    "name": "表情",
                    "name_english": "Exp",
                    "special_flag": 1,
                    "elements": [],
                }
            )

        # --- write ---
        pmx.write_file(file_path)


__all__ = [
    "PmxExporter",
    "_choose_index_size",
    "_choose_reference_index_size",
    "_fan_triangulate",
]
