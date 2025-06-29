import os
import struct

from mmd_tools.core import utils

from .exceptions import MMDParseException
from .pmx_data.header import PmxHeader
from .pmx_data.vertex import PmxVertex
from .pmx_data.face import PmxFace
from .pmx_data.material import PmxMaterial
from .pmx_data.bone import PmxBone
from .pmx_data.morph import PmxMorph
from .pmx_data.display_frame import PmxDisplayFrame
from .pmx_data.rigid_body import PmxRigidBody
from .pmx_data.joint import PmxJoint
from .pmx_data.soft_body import PmxSoftBody


class PmxParser:
    """
    PMXファイルを解析し、そのデータをPythonオブジェクトとして保持するクラス。
    """
    def __init__(self):
        self.header = PmxHeader()
        self.vertices = []
        self.faces = []
        self.materials = []
        self.bones = []
        self.morphs = []
        self.display_frames = []
        self.rigid_bodies = []
        self.joints = []
        self.soft_bodies = []
        self.textures = []
        self.toon_textures = []

    def parse_file(self, file_path):
        """
        指定されたPMXファイルを読み込み、各セクションを解析してデータを格納する。

        Args:
            file_path (str): 解析するPMXファイルのパス。

        Raises:
            FileNotFoundError: ファイルが見つからない場合。
            MMDParseException: ファイルの解析に失敗した場合。
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PMX file not found: {file_path}")

        with open(file_path, 'rb') as f:
            try:
                # Header
                self.header.parse(f)

                # Get sizes from header
                encoding = self.header.text_encoding
                vertex_size = self.header.vertex_index_size
                texture_size = self.header.texture_index_size
                material_size = self.header.material_index_size
                bone_size = self.header.bone_index_size
                morph_size = self.header.morph_index_size
                rigid_body_size = self.header.rigid_body_index_size

                # Vertex
                vertex_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(vertex_count):
                    vertex = PmxVertex(self.header)
                    vertex.parse(f)
                    self.vertices.append(vertex)

                # Face
                face_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(face_count // 3):
                    face = PmxFace(vertex_size)
                    face.parse(f)
                    self.faces.append(face)

                # Textures
                texture_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(texture_count):
                    texture_path_length = struct.unpack('<I', f.read(4))[0]
                    texture_path = f.read(texture_path_length).decode(encoding)
                    self.textures.append(texture_path)

                # Material
                material_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(material_count):
                    material = PmxMaterial(texture_size, encoding)
                    material.parse(f)
                    self.materials.append(material)

                # Bone
                bone_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(bone_count):
                    bone = PmxBone(bone_size, encoding)
                    bone.parse(f)
                    self.bones.append(bone)

                # Morph
                morph_count = struct.unpack('<I', f.read(4))[0]
                for i in range(morph_count):
                    morph = PmxMorph(vertex_size, material_size, bone_size, morph_size, rigid_body_size, encoding)
                    morph.parse(f)
                    self.morphs.append(morph)

                # Display Frame
                display_frame_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(display_frame_count):
                    display_frame = PmxDisplayFrame(bone_size, morph_size, encoding)
                    display_frame.parse(f)
                    self.display_frames.append(display_frame)

                # Rigid Body
                rigid_body_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(rigid_body_count):
                    rigid_body = PmxRigidBody(bone_size, encoding)
                    rigid_body.parse(f)
                    self.rigid_bodies.append(rigid_body)

                # Joint
                joint_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(joint_count):
                    joint = PmxJoint(rigid_body_size, encoding)
                    joint.parse(f)
                    self.joints.append(joint)

                # SoftBody (Optional, PMX 2.1 only)
                if self.header.version >= 2.1:
                    soft_body_count = struct.unpack('<I', f.read(4))[0]
                    for _ in range(soft_body_count):
                        soft_body = PmxSoftBody(encoding)
                        soft_body.parse(f)
                        # self.soft_bodies.append(soft_body) # Need to add soft_bodies list to __init__

            except struct.error as e:
                raise MMDParseException(f"Failed to parse PMX file: {file_path}") from e