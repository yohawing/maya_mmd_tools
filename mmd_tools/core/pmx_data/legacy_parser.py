"""Legacy Python PMX reader retained for migration-only PMX tooling."""

import os
import struct
from typing import TYPE_CHECKING, Optional

from mmd_tools.core import utils

from ..exceptions import MMDParseException
from .bone import PmxBone
from .display_frame import PmxDisplayFrame
from .face import PmxFace
from .header import is_pmx_21_or_later
from .joint import PmxJoint
from .material import PmxMaterial
from .morph import PmxMorph
from .rigid_body import PmxRigidBody
from .soft_body import PmxSoftBody
from .vertex import PmxVertex

if TYPE_CHECKING:
    from . import PmxData


def parse_pmx_file_legacy(file_path: str, target: Optional["PmxData"] = None) -> "PmxData":
    """Parse a PMX file with the legacy pure-Python reader.

    This helper exists for writer roundtrip tests and inspection tools that
    explicitly opt out of native parser policy. New import code should route
    through ``mmd_tools.core.mmd_parser.parse_pmx_file`` instead.

    Args:
        file_path: PMX file path to parse.
        target: Optional ``PmxData`` instance to populate.

    Returns:
        Parsed PMX data.

    Raises:
        FileNotFoundError: ファイルが見つからない場合。
        MMDParseException: ファイルの解析に失敗した場合。
    """
    if target is None:
        from . import PmxData

        target = PmxData()

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"PMX file not found: {file_path}")

    with open(file_path, "rb") as f:
        try:
            target.header.parse(f)

            encoding_flag = target.header.encoding
            encoding_text = utils.get_pmx_encoding_string(encoding_flag)
            vertex_size = target.header.vertex_index_size
            texture_size = target.header.texture_index_size
            material_size = target.header.material_index_size
            bone_size = target.header.bone_index_size
            morph_size = target.header.morph_index_size
            rigid_body_size = target.header.rigid_body_index_size

            vertex_count = struct.unpack("<I", f.read(4))[0]
            for _ in range(vertex_count):
                vertex = PmxVertex(bone_size, target.header.additional_uv)
                vertex.parse(f, target.header.version)
                target.vertices.append(vertex)

            face_count = struct.unpack("<I", f.read(4))[0]
            for _ in range(face_count // 3):
                face = PmxFace(vertex_size)
                face.parse(f)
                target.faces.append(face)

            texture_count = struct.unpack("<I", f.read(4))[0]
            for _ in range(texture_count):
                texture_path_length = struct.unpack("<I", f.read(4))[0]
                texture_path = f.read(texture_path_length).decode(encoding_text)
                target.textures.append(texture_path)

            material_count = struct.unpack("<I", f.read(4))[0]
            for i in range(material_count):
                material = PmxMaterial(texture_size, encoding_flag, material_index=i)
                material.parse(f)
                target.materials.append(material)

            bone_count = struct.unpack("<I", f.read(4))[0]
            for _ in range(bone_count):
                bone = PmxBone(bone_size, encoding_flag)
                bone.parse(f)
                target.bones.append(bone)

            morph_count = struct.unpack("<I", f.read(4))[0]
            for _ in range(morph_count):
                morph = PmxMorph(
                    vertex_size,
                    material_size,
                    bone_size,
                    morph_size,
                    rigid_body_size,
                    encoding_flag,
                )
                morph.parse(f)
                target.morphs.append(morph)

            display_frame_count = struct.unpack("<I", f.read(4))[0]
            for _ in range(display_frame_count):
                display_frame = PmxDisplayFrame(bone_size, morph_size, encoding_flag)
                display_frame.parse(f)
                target.display_frames.append(display_frame)

            rigid_body_count = struct.unpack("<I", f.read(4))[0]
            for _ in range(rigid_body_count):
                rigid_body = PmxRigidBody(bone_size, encoding_flag)
                rigid_body.parse(f)
                target.rigid_bodies.append(rigid_body)

            joint_count = struct.unpack("<I", f.read(4))[0]
            for _ in range(joint_count):
                joint = PmxJoint(rigid_body_size, encoding_flag)
                joint.parse(f)
                target.joints.append(joint)

            if is_pmx_21_or_later(target.header.version):
                soft_body_count = struct.unpack("<I", f.read(4))[0]
                for _ in range(soft_body_count):
                    soft_body = PmxSoftBody(
                        material_size,
                        rigid_body_size,
                        vertex_size,
                        encoding_flag,
                    )
                    soft_body.parse(f)
                    target.soft_bodies.append(soft_body)

        except struct.error as e:
            raise MMDParseException(f"Failed to parse PMX file: {file_path}") from e

    return target
