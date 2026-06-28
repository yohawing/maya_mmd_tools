"""Convert parsed PMD data into PMX data for unified import handling."""

from mmd_tools.core.pmd_data import PmdData
from mmd_tools.core.pmd_data.bone import PmdBoneType
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmx_data.bone import PmxBone, PmxBoneFlag
from mmd_tools.core.pmx_data.display_frame import PmxDisplayFrame
from mmd_tools.core.pmx_data.face import PmxFace
from mmd_tools.core.pmx_data.header import PmxEncoding
from mmd_tools.core.pmx_data.ik_link import PmxIKLink
from mmd_tools.core.pmx_data.joint import PmxJoint
from mmd_tools.core.pmx_data.material import PmxDrawFlag, PmxMaterial, PmxSharedToonFlag, PmxSphereMode
from mmd_tools.core.pmx_data.morph import PmxMorph, PmxMorphType
from mmd_tools.core.pmx_data.rigid_body import PmxRigidBody
from mmd_tools.core.pmx_data.vertex import PmxVertex


def _choose_index_size(count: int) -> int:
    if count <= 0xFF:
        return 1
    if count <= 0xFFFF:
        return 2
    return 4


def _choose_reference_index_size(count: int) -> int:
    if count <= 0x7F:
        return 1
    if count <= 0x7FFF:
        return 2
    return 4


def _valid_index(index: int, count: int) -> int:
    if 0 <= index < count:
        return index
    return -1


def _split_pmd_texture(texture_name: str) -> tuple:
    parts = [part for part in (texture_name or "").replace("\\", "/").split("*") if part]
    texture = ""
    sphere = ""
    for part in parts:
        lower = part.lower()
        if lower.endswith((".sph", ".spa")) and not sphere:
            sphere = part
        elif not texture:
            texture = part
    return texture, sphere


def _texture_index(textures: list, texture_name: str) -> int:
    if not texture_name:
        return -1
    if texture_name not in textures:
        textures.append(texture_name)
    return textures.index(texture_name)


def _build_vertex(pmd_vertex, bone_count: int, bone_index_size: int) -> PmxVertex:
    vertex = PmxVertex(bone_index_size=bone_index_size, additional_uv_count=0)
    vertex.position = tuple(pmd_vertex.position)
    vertex.normal = tuple(pmd_vertex.normal)
    vertex.uv = tuple(pmd_vertex.uv)
    bone0 = _valid_index(int(pmd_vertex.bone_indices[0]), bone_count)
    bone1 = _valid_index(int(pmd_vertex.bone_indices[1]), bone_count)
    weight0 = max(0.0, min(1.0, float(pmd_vertex.bone_weight) / 100.0))
    if bone1 == -1 or bone0 == bone1 or weight0 >= 1.0:
        vertex.weight_transform_type = 0
        vertex.bone_indices = [bone0 if bone0 != -1 else 0]
        vertex.bone_weights = []
    elif weight0 <= 0.0:
        vertex.weight_transform_type = 0
        vertex.bone_indices = [bone1]
        vertex.bone_weights = []
    else:
        vertex.weight_transform_type = 1
        vertex.bone_indices = [bone0 if bone0 != -1 else 0, bone1]
        vertex.bone_weights = [weight0]
    vertex.edge_magnification = 0.0 if pmd_vertex.edge_flag else 1.0
    return vertex


def _build_material(pmd_material, material_index: int, textures: list, texture_index_size: int) -> PmxMaterial:
    material = PmxMaterial(texture_index_size=texture_index_size, encoding=PmxEncoding.UTF16LE, material_index=material_index)
    material.name = pmd_material.get_name() or f"material_{material_index}"
    material.name_english = material.name
    material.diffuse = tuple(pmd_material.diffuse)
    material.specular = tuple(pmd_material.specular)
    material.specular_coefficient = pmd_material.specular_power
    material.ambient = tuple(pmd_material.ambient)
    material.draw_flag = PmxDrawFlag.DOUBLE_SIDED | PmxDrawFlag.GROUND_SHADOW | PmxDrawFlag.SELF_SHADOW
    if pmd_material.edge_flag:
        material.draw_flag |= PmxDrawFlag.EDGE_DRAWING
    material.edge_color = (0.0, 0.0, 0.0, 1.0)
    material.edge_size = 1.0
    texture_name, sphere_name = _split_pmd_texture(pmd_material.texture_file_name)
    material.texture_index = _texture_index(textures, texture_name)
    material.sphere_texture_index = _texture_index(textures, sphere_name)
    if sphere_name.lower().endswith(".sph"):
        material.sphere_mode = PmxSphereMode.MULTIPLY
    elif sphere_name.lower().endswith(".spa"):
        material.sphere_mode = PmxSphereMode.ADDITIVE
    else:
        material.sphere_mode = PmxSphereMode.DISABLED
    if int(pmd_material.toon_texture_index) == 0xFF:
        material.shared_toon_flag = PmxSharedToonFlag.NOT_SHARED
        material.toon_texture_index = -1
    else:
        material.shared_toon_flag = PmxSharedToonFlag.SHARED
        material.toon_texture_index = max(0, min(9, int(pmd_material.toon_texture_index)))
    material.face_count = pmd_material.face_count
    return material


def _bone_flag_for_pmd_bone(pmd_bone) -> PmxBoneFlag:
    flag = PmxBoneFlag.ROTATABLE
    if pmd_bone.bone_type == PmdBoneType.ROTATE_AND_MOVE:
        flag |= PmxBoneFlag.MOVABLE
    if pmd_bone.bone_type != PmdBoneType.HIDDEN:
        flag |= PmxBoneFlag.DISPLAY | PmxBoneFlag.OPERATABLE
    if pmd_bone.bone_type == PmdBoneType.IK:
        flag |= PmxBoneFlag.IK | PmxBoneFlag.MOVABLE
    return flag


def _build_bone(pmd_bone, bone_index_size: int, bone_count: int, ik_by_bone: dict) -> PmxBone:
    bone = PmxBone(bone_index_size=bone_index_size, encoding=PmxEncoding.UTF16LE)
    bone.name = pmd_bone.name
    bone.name_english = pmd_bone.name_english
    bone.position = tuple(pmd_bone.position)
    bone.parent_bone_index = _valid_index(int(pmd_bone.parent_bone_index), bone_count)
    bone.transform_layer = 0
    bone.bone_flag = _bone_flag_for_pmd_bone(pmd_bone)
    tail_index = _valid_index(int(pmd_bone.tail_pos_bone_index), bone_count)
    if tail_index != -1:
        bone.bone_flag |= PmxBoneFlag.CONNECT_BONE
        bone.connect_bone_index = tail_index
    else:
        bone.connect_position_offset = (0.0, 1.0, 0.0)

    ik = ik_by_bone.get(id(pmd_bone))
    if ik is not None:
        bone.bone_flag |= PmxBoneFlag.IK
        bone.ik_target_bone_index = _valid_index(int(ik.target_bone_index), bone_count)
        bone.ik_loop_count = int(ik.iterations)
        bone.ik_limit_angle = float(ik.control_weight)
        bone.ik_links = []
        for link_index in ik.link_bones:
            link = PmxIKLink(bone_index_size)
            link.ik_bone_index = _valid_index(int(link_index), bone_count)
            bone.ik_links.append(link)
    return bone


def _build_morph(pmd_morph, base_vertices: list, vertex_count: int, pmx: PmxData) -> PmxMorph:
    morph = PmxMorph(
        vertex_index_size=pmx.header.vertex_index_size,
        material_index_size=pmx.header.material_index_size,
        bone_index_size=pmx.header.bone_index_size,
        morph_index_size=pmx.header.morph_index_size,
        rigid_body_index_size=pmx.header.rigid_body_index_size,
        encoding=pmx.header.encoding,
    )
    morph.name = pmd_morph.name
    morph.name_english = pmd_morph.english_name
    morph.panel = int(pmd_morph.morph_type) if 1 <= int(pmd_morph.morph_type) <= 4 else 4
    morph.morph_type = PmxMorphType.VertexMorph
    morph.offsets = []
    for pmd_vertex_index, position_offset in pmd_morph.vertices:
        vertex_index = int(pmd_vertex_index)
        if base_vertices and 0 <= vertex_index < len(base_vertices):
            vertex_index = int(base_vertices[vertex_index][0])
        if 0 <= vertex_index < vertex_count:
            morph.offsets.append(
                {
                    "vertex_index": vertex_index,
                    "position_offset": tuple(position_offset),
                }
            )
    morph.offset_count = len(morph.offsets)
    return morph


def _build_display_frames(pmd_data: PmdData, pmx: PmxData) -> list:
    frames = []
    root_frame = PmxDisplayFrame(pmx.header.bone_index_size, pmx.header.morph_index_size, pmx.header.encoding_flag)
    root_frame.name = "Root"
    root_frame.name_english = "Root"
    root_frame.special_flag = 1
    root_frame.elements = [{"type": 0, "index": 0}] if pmx.bones else []
    frames.append(root_frame)

    morph_frame = PmxDisplayFrame(pmx.header.bone_index_size, pmx.header.morph_index_size, pmx.header.encoding_flag)
    morph_frame.name = "表情"
    morph_frame.name_english = "Exp"
    morph_frame.special_flag = 1
    morph_frame.elements = [{"type": 1, "index": index} for index in range(len(pmx.morphs))]
    frames.append(morph_frame)

    display_frame = getattr(pmd_data, "display_frame", None)
    if display_frame is not None:
        for name, bone_indices in display_frame.bone_display_lists.items():
            frame = PmxDisplayFrame(pmx.header.bone_index_size, pmx.header.morph_index_size, pmx.header.encoding_flag)
            frame.name = name
            frame.name_english = name
            frame.special_flag = 0
            frame.elements = [{"type": 0, "index": index} for index in bone_indices if 0 <= index < len(pmx.bones)]
            frames.append(frame)
    return frames


def convert_pmd_to_pmx_data(pmd_data: PmdData) -> PmxData:
    """Convert parsed PMD data to PMX data."""
    pmx = PmxData()
    pmx.header.magic = b"PMX "
    pmx.header.version = 2.0
    pmx.header.header_size = 8
    pmx.header.encoding = PmxEncoding.UTF16LE
    pmx.header.additional_uv = 0
    pmx.header.vertex_index_size = _choose_index_size(len(pmd_data.vertices))
    pmx.header.bone_index_size = _choose_reference_index_size(len(pmd_data.bones))
    pmx.header.morph_index_size = _choose_reference_index_size(max(1, len(pmd_data.morphs)))
    pmx.header.rigid_body_index_size = _choose_reference_index_size(len(pmd_data.rigid_bodies))
    pmx.header.model_name = pmd_data.header.model_name
    pmx.header.model_name_english = pmd_data.header.model_name_english
    pmx.header.comment = pmd_data.header.comment
    pmx.header.comment_english = pmd_data.header.comment_english

    pmx.vertices = [_build_vertex(vertex, len(pmd_data.bones), pmx.header.bone_index_size) for vertex in pmd_data.vertices]
    for pmd_face in pmd_data.faces:
        face = PmxFace(pmx.header.vertex_index_size)
        face.indices = tuple(pmd_face.indices)
        pmx.faces.append(face)

    textures = []
    pmx.materials = [
        _build_material(material, index, textures, 4)
        for index, material in enumerate(pmd_data.materials)
    ]
    pmx.textures = textures
    pmx.header.texture_index_size = _choose_reference_index_size(len(pmx.textures))
    for material in pmx.materials:
        material.texture_index_size = pmx.header.texture_index_size
    pmx.header.material_index_size = _choose_reference_index_size(len(pmx.materials))

    ik_by_bone_index = {ik.ik_bone_index: ik for ik in pmd_data.ik_data}
    ik_by_bone = {
        id(pmd_data.bones[index]): ik
        for index, ik in ik_by_bone_index.items()
        if 0 <= index < len(pmd_data.bones)
    }
    pmx.bones = [
        _build_bone(bone, pmx.header.bone_index_size, len(pmd_data.bones), ik_by_bone)
        for bone in pmd_data.bones
    ]

    base_vertices = []
    for morph in pmd_data.morphs:
        if int(morph.morph_type) == 0:
            base_vertices = list(morph.vertices)
            break
    for morph in pmd_data.morphs:
        if int(morph.morph_type) == 0:
            continue
        pmx.morphs.append(_build_morph(morph, base_vertices, len(pmd_data.vertices), pmx))

    pmx.rigid_bodies = []
    for pmd_rigid_body in pmd_data.rigid_bodies:
        rigid_body = PmxRigidBody(pmx.header.bone_index_size, pmx.header.encoding_flag)
        rigid_body.name = pmd_rigid_body.name
        rigid_body.name_english = pmd_rigid_body.name
        rigid_body.related_bone_index = _valid_index(int(pmd_rigid_body.bone_index), len(pmd_data.bones))
        rigid_body.group = pmd_rigid_body.collision_group
        rigid_body.collision_mask = pmd_rigid_body.collision_mask
        rigid_body.shape_type = pmd_rigid_body.shape_type
        rigid_body.size = tuple(pmd_rigid_body.size)
        rigid_body.position = tuple(pmd_rigid_body.position)
        rigid_body.rotation = tuple(pmd_rigid_body.rotation)
        rigid_body.mass = pmd_rigid_body.mass
        rigid_body.velocity_attenuation = pmd_rigid_body.velocity_attenuation
        rigid_body.rotation_attenuation = pmd_rigid_body.rotation_attenuation
        rigid_body.elasticity = pmd_rigid_body.elasticity
        rigid_body.friction = pmd_rigid_body.friction
        rigid_body.physics_mode = pmd_rigid_body.physics_mode
        pmx.rigid_bodies.append(rigid_body)

    pmx.joints = []
    for pmd_joint in pmd_data.joints:
        joint = PmxJoint(pmx.header.rigid_body_index_size, pmx.header.encoding)
        joint.name = pmd_joint.name
        joint.name_english = pmd_joint.name
        joint.joint_type = 0
        joint.rigid_body_a_index = _valid_index(int(pmd_joint.rigid_body_index_a), len(pmd_data.rigid_bodies))
        joint.rigid_body_b_index = _valid_index(int(pmd_joint.rigid_body_index_b), len(pmd_data.rigid_bodies))
        joint.position = tuple(pmd_joint.position)
        joint.rotation = tuple(pmd_joint.rotation)
        joint.translation_limit_min = tuple(pmd_joint.translation_limit_min)
        joint.translation_limit_max = tuple(pmd_joint.translation_limit_max)
        joint.rotation_limit_min = tuple(pmd_joint.rotation_limit_min)
        joint.rotation_limit_max = tuple(pmd_joint.rotation_limit_max)
        joint.spring_translation = tuple(pmd_joint.spring_translation)
        joint.spring_rotation = tuple(pmd_joint.spring_rotation)
        pmx.joints.append(joint)

    pmx.display_frames = _build_display_frames(pmd_data, pmx)
    return pmx
