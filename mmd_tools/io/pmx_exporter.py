"""
PMXファイルにエクスポートするためのモジュール。

Mayaシーン直結ではなく、dictベースの geometry / material / bone /
VertexMorph / physics データをPmxDataに変換して書き出す。
"""

import os

from mmd_tools.core.exceptions import MMDExportException
from mmd_tools.core.native import export_pmx_from_parts
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.bone import PmxBone, PmxBoneFlag
from mmd_tools.core.pmx_data.display_frame import PmxDisplayFrame
from mmd_tools.core.pmx_data.face import PmxFace
from mmd_tools.core.pmx_data.header import PmxEncoding
from mmd_tools.core.pmx_data.joint import PmxJoint
from mmd_tools.core.pmx_data.material import PmxDrawFlag, PmxMaterial, PmxSharedToonFlag, PmxSphereMode
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

    def __init__(self, native_parts_exporter=export_pmx_from_parts):
        self._native_parts_exporter = native_parts_exporter

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
        try:
            self._export_pmx_model_impl(file_path, maya_data)
        except (ValueError, TypeError) as e:
            raise MMDExportException(f"Failed to export PMX file {file_path}: {e}") from e

    def _export_pmx_model_impl(self, file_path: str, maya_data: dict) -> None:
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
        native_bytes = self._try_native_parts_export(pmx)
        if native_bytes is not None:
            with open(file_path, "wb") as handle:
                handle.write(native_bytes)
        else:
            pmx.write_file(file_path)

    def to_native_parts(self, pmx: PmxData):
        """basic PMX data を mmd-anim parts exporter の入力へ変換する。"""
        if self.native_parts_export_blocker(pmx) is not None:
            return None

        positions = []
        normals = []
        uvs = []
        skin_indices = []
        skin_weights = []
        edge_scale = []
        for vertex in pmx.vertices:
            skinning = _pmx_vertex_skinning(vertex)
            indices4, weights4 = skinning
            positions.extend(_float_list(vertex.position, 3, "vertex position"))
            normals.extend(_float_list(vertex.normal, 3, "vertex normal"))
            uvs.extend(_float_list(vertex.uv, 2, "vertex uv"))
            skin_indices.extend(indices4)
            skin_weights.extend(weights4)
            edge_scale.append(float(vertex.edge_magnification))

        indices = [int(index) for face in pmx.faces for index in face.indices]

        descriptor = {
            "version": float(pmx.header.version),
            "encoding": "utf-16le" if pmx.header.encoding == PmxEncoding.UTF16LE else "utf-8",
            "name": pmx.header.model_name,
            "englishName": pmx.header.model_name_english,
            "comment": pmx.header.comment,
            "englishComment": pmx.header.comment_english,
            "materials": [self._native_material_descriptor(pmx, material) for material in pmx.materials],
            "bones": [self._native_bone_descriptor(bone) for bone in pmx.bones],
            "morphs": [],
            "displayFrames": [
                {
                    "name": frame.name,
                    "englishName": frame.name_english,
                    "special": bool(frame.special_flag),
                    "frames": [
                        {
                            "kind": "bone" if int(element["type"]) == 0 else "morph",
                            "index": int(element["index"]),
                        }
                        for element in frame.elements
                    ],
                }
                for frame in pmx.display_frames
            ],
            "rigidBodies": [self._native_rigid_body_descriptor(body) for body in pmx.rigid_bodies],
            "joints": [self._native_joint_descriptor(joint) for joint in pmx.joints],
            "indexSizes": {
                "vertex": int(pmx.header.vertex_index_size),
                "texture": int(pmx.header.texture_index_size),
                "material": int(pmx.header.material_index_size),
                "bone": int(pmx.header.bone_index_size),
                "morph": int(pmx.header.morph_index_size),
                "rigidBody": int(pmx.header.rigid_body_index_size),
            },
        }
        for morph in pmx.morphs:
            morph_descriptor = self._native_morph_descriptor(morph)
            descriptor["morphs"].append(morph_descriptor)
        return descriptor, positions, normals, uvs, indices, skin_indices, skin_weights, edge_scale

    def native_parts_export_blocker(self, pmx: PmxData) -> str | None:
        """Return why PMX must use the Python writer instead of native parts export."""
        if pmx.header.additional_uv != 0:
            return "additional_uv_header"
        if any(vertex.additional_uvs for vertex in pmx.vertices):
            return "additional_uv_vertices"
        for vertex in pmx.vertices:
            if _pmx_vertex_skinning(vertex) is None:
                return f"unsupported_skinning_type:{int(vertex.weight_transform_type)}"
        if any(material.face_count % 3 != 0 for material in pmx.materials):
            return "non_triangle_material_face_count"
        for morph in pmx.morphs:
            if self._native_morph_descriptor(morph) is None:
                return f"unsupported_morph_type:{int(morph.morph_type)}"
        return None

    def _try_native_parts_export(self, pmx: PmxData):
        if self._native_parts_exporter is None:
            return None
        native_parts = self.to_native_parts(pmx)
        if native_parts is None:
            return None
        descriptor, positions, normals, uvs, indices, skin_indices, skin_weights, edge_scale = native_parts
        return self._native_parts_exporter(
            descriptor,
            positions,
            normals,
            uvs,
            indices=indices,
            skin_indices=skin_indices,
            skin_weights=skin_weights,
            edge_scale=edge_scale,
        )

    def _native_material_descriptor(self, pmx: PmxData, material: PmxMaterial) -> dict:
        draw_flag = int(material.draw_flag)
        return {
            "name": material.name,
            "englishName": material.name_english,
            "texturePath": _texture_path(pmx.textures, material.texture_index),
            "sphereTexturePath": _texture_path(pmx.textures, material.sphere_texture_index),
            "sphereMode": _sphere_mode_name(material.sphere_mode),
            "toonTexturePath": (
                ""
                if material.shared_toon_flag == PmxSharedToonFlag.SHARED
                else _texture_path(pmx.textures, material.toon_texture_index)
            ),
            "sharedToonIndex": (
                int(material.toon_texture_index)
                if material.shared_toon_flag == PmxSharedToonFlag.SHARED
                else None
            ),
            "diffuse": _float_list(material.diffuse, 4, "material diffuse"),
            "specular": _float_list(material.specular, 3, "material specular"),
            "specularPower": float(material.specular_coefficient),
            "ambient": _float_list(material.ambient, 3, "material ambient"),
            "edgeColor": _float_list(material.edge_color, 4, "material edge color"),
            "edgeSize": float(material.edge_size),
            "flags": {
                "doubleSided": bool(draw_flag & int(PmxDrawFlag.DOUBLE_SIDED)),
                "groundShadow": bool(draw_flag & int(PmxDrawFlag.GROUND_SHADOW)),
                "selfShadowMap": bool(draw_flag & int(PmxDrawFlag.SELF_SHADOW_MAP)),
                "selfShadow": bool(draw_flag & int(PmxDrawFlag.SELF_SHADOW)),
                "edge": bool(draw_flag & int(PmxDrawFlag.EDGE_DRAWING)),
                "vertexColor": bool(draw_flag & int(PmxDrawFlag.VERTEX_COLOR)),
                "pointDraw": bool(draw_flag & int(PmxDrawFlag.POINT_DRAWING)),
                "lineDraw": bool(draw_flag & int(PmxDrawFlag.LINE_DRAWING)),
            },
            "faceCount": int(material.face_count) // 3,
        }

    @staticmethod
    def _native_bone_descriptor(bone: PmxBone) -> dict:
        flags = int(bone.bone_flag)
        descriptor = {
            "name": bone.name,
            "englishName": bone.name_english,
            "parentIndex": int(bone.parent_bone_index),
            "layer": int(bone.transform_layer),
            "position": _float_list(bone.position, 3, "bone position"),
            "rotatable": bool(flags & int(PmxBoneFlag.ROTATABLE)),
            "translatable": bool(flags & int(PmxBoneFlag.MOVABLE)),
            "visible": bool(flags & int(PmxBoneFlag.DISPLAY)),
            "enabled": bool(flags & int(PmxBoneFlag.OPERATABLE)),
        }
        if flags & int(PmxBoneFlag.CONNECT_BONE):
            descriptor["tailIndex"] = int(bone.connect_bone_index)
        else:
            descriptor["tailPosition"] = _float_list(bone.connect_position_offset, 3, "bone tail position")
        return descriptor

    @staticmethod
    def _native_morph_descriptor(morph: PmxMorph):
        if morph.morph_type == PmxMorphType.VertexMorph:
            return {
                "name": morph.name,
                "englishName": morph.name_english,
                "kind": "vertex",
                "vertexOffsets": [
                    {
                        "vertexIndex": int(offset["vertex_index"]),
                        "position": _float_list(offset["position_offset"], 3, "vertex morph position"),
                    }
                    for offset in morph.offsets
                ],
            }
        if morph.morph_type == PmxMorphType.GroupMorph:
            return {
                "name": morph.name,
                "englishName": morph.name_english,
                "kind": "group",
                "groupOffsets": [
                    {
                        "morphIndex": int(offset["morph_index"]),
                        "weight": float(offset["morph_rate"]),
                    }
                    for offset in morph.offsets
                ],
            }
        return None

    @staticmethod
    def _native_rigid_body_descriptor(body: PmxRigidBody) -> dict:
        return {
            "name": body.name,
            "englishName": body.name_english,
            "boneIndex": int(body.related_bone_index),
            "group": int(body.group),
            "mask": int(body.collision_mask),
            "shape": _rigid_body_shape_name(body.shape_type),
            "size": _float_list(body.size, 3, "rigid body size"),
            "position": _float_list(body.position, 3, "rigid body position"),
            "rotation": _float_list(body.rotation, 3, "rigid body rotation"),
            "mass": float(body.mass),
            "linearDamping": float(body.velocity_attenuation),
            "angularDamping": float(body.rotation_attenuation),
            "restitution": float(body.elasticity),
            "friction": float(body.friction),
            "mode": _rigid_body_mode_name(body.physics_mode),
        }

    @staticmethod
    def _native_joint_descriptor(joint: PmxJoint) -> dict:
        return {
            "name": joint.name,
            "englishName": joint.name_english,
            "type": _joint_type_name(joint.joint_type),
            "rigidBodyIndexA": int(joint.rigid_body_a_index),
            "rigidBodyIndexB": int(joint.rigid_body_b_index),
            "position": _float_list(joint.position, 3, "joint position"),
            "rotation": _float_list(joint.rotation, 3, "joint rotation"),
            "translationLowerLimit": _float_list(joint.translation_limit_min, 3, "joint translation lower limit"),
            "translationUpperLimit": _float_list(joint.translation_limit_max, 3, "joint translation upper limit"),
            "rotationLowerLimit": _float_list(joint.rotation_limit_min, 3, "joint rotation lower limit"),
            "rotationUpperLimit": _float_list(joint.rotation_limit_max, 3, "joint rotation upper limit"),
            "springTranslationFactor": _float_list(joint.spring_translation, 3, "joint spring translation"),
            "springRotationFactor": _float_list(joint.spring_rotation, 3, "joint spring rotation"),
        }


def _float_list(value, length: int, label: str) -> list:
    try:
        result = [float(item) for item in value]
    except TypeError as exc:
        raise TypeError(f"{label} must be an iterable of {length} numbers") from exc
    if len(result) != length:
        raise ValueError(f"{label} must contain {length} numbers")
    return result


def _texture_path(textures, index: int) -> str:
    if index < 0 or index >= len(textures):
        return ""
    return str(textures[index])


def _sphere_mode_name(value) -> str:
    mode = int(value)
    if mode == int(PmxSphereMode.MULTIPLY):
        return "multiply"
    if mode == int(PmxSphereMode.ADDITIVE):
        return "add"
    if mode == int(PmxSphereMode.SUB_TEXTURE):
        return "subTexture"
    return "disabled"


def _rigid_body_shape_name(value) -> str:
    shape = int(value)
    if shape == 1:
        return "box"
    if shape == 2:
        return "capsule"
    return "sphere"


def _rigid_body_mode_name(value) -> str:
    mode = int(value)
    if mode == 1:
        return "dynamic"
    if mode == 2:
        return "dynamicBone"
    return "static"


def _joint_type_name(value) -> str:
    joint_type = int(value)
    if joint_type == 1:
        return "generic6dof"
    if joint_type == 2:
        return "point2point"
    if joint_type == 3:
        return "coneTwist"
    if joint_type == 4:
        return "slider"
    if joint_type == 5:
        return "hinge"
    return "generic6dofSpring"


def _pmx_vertex_skinning(vertex: PmxVertex):
    if vertex.weight_transform_type == 0:
        indices = (list(vertex.bone_indices) + [0, 0, 0, 0])[:4]
        return [int(index) for index in indices], [1.0, 0.0, 0.0, 0.0]
    if vertex.weight_transform_type == 1:
        indices = (list(vertex.bone_indices) + [0, 0, 0, 0])[:4]
        weight0 = float(vertex.bone_weights[0]) if vertex.bone_weights else 0.5
        return [int(index) for index in indices], [weight0, 1.0 - weight0, 0.0, 0.0]
    if vertex.weight_transform_type == 2:
        indices = (list(vertex.bone_indices) + [0, 0, 0, 0])[:4]
        weights = (list(vertex.bone_weights) + [0.0, 0.0, 0.0, 0.0])[:4]
        return [int(index) for index in indices], [float(weight) for weight in weights]
    return None


__all__ = [
    "PmxExporter",
    "_choose_index_size",
    "_choose_reference_index_size",
    "_fan_triangulate",
]
