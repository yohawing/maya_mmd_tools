import os
import struct

from mmd_tools.core import utils

from .exceptions import MMDParseException
from .pmd_data.header import PmdHeader
from .pmd_data.vertex import PmdVertex
from .pmd_data.material import PmdMaterial
from .pmd_data.bone import PmdBone
from .pmd_data.ik import PmdIK
from .pmd_data.morph import PmdMorph
from .pmd_data.display_frame import PmdDisplayFrame
from .pmd_data.rigid_body import PmdRigidBody
from .pmd_data.joint import PmdJoint
from .pmd_data.face import PmdFace

class PmdParser:
    """
    PMDファイルを解析し、そのデータをPythonオブジェクトとして保持するクラス。
    """
    def __init__(self):
        self.header: PmdHeader = PmdHeader()
        self.vertices: list[PmdVertex] = []
        self.faces: list[PmdFace] = []
        self.materials: list[PmdMaterial] = []
        self.bones: list[PmdBone] = []
        self.ik_data: list[PmdIK] = []
        self.morphs: list[PmdMorph] = []
        self.display_frames: list[PmdDisplayFrame] = []
        self.rigid_bodies: list[PmdRigidBody] = []
        self.joints: list[PmdJoint] = []

    def parse_file(self, file_path) -> 'PmdParser':
        """
        指定されたPMDファイルを読み込み、各セクションを解析してデータを格納する。

        Args:
            file_path (str): 解析するPMDファイルのパス。

        Returns:
            PmdParser: メソッドチェーニングをサポートするための自身のインスタンス。

        Raises:
            FileNotFoundError: ファイルが見つからない場合。
            MMDParseException: ファイルの解析に失敗した場合。
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PMD file not found: {file_path}")

        with open(file_path, 'rb') as f:
            try:
                # Header
                self.header.parse(f)

                # Vertex
                vertex_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(vertex_count):
                    vertex = PmdVertex()
                    vertex.parse(f)
                    self.vertices.append(vertex)

                # Face
                face_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(face_count // 3):
                    face = PmdFace()
                    face.parse(f)
                    self.faces.append(face)

                # Material
                material_count = struct.unpack('<I', f.read(4))[0]
                for _ in range(material_count):
                    material = PmdMaterial()
                    material.parse(f)
                    self.materials.append(material)

                # Bone
                bone_count = struct.unpack('<H', f.read(2))[0]
                for _ in range(bone_count):
                    bone = PmdBone()
                    bone.parse(f)
                    self.bones.append(bone)

                # IK
                ik_count = struct.unpack('<H', f.read(2))[0]
                for _ in range(ik_count):
                    ik = PmdIK()
                    ik.parse(f)
                    self.ik_data.append(ik)

                # Morph
                morph_count = struct.unpack('<H', f.read(2))[0]
                for _ in range(morph_count):
                    morph = PmdMorph()
                    morph.parse(f)
                    self.morphs.append(morph)

                # Display Frame
                self.display_frame = PmdDisplayFrame()
                self.display_frame.parse(f)
                # display_frame_count = struct.unpack('<B', f.read(1))[0]
                # for _ in range(display_frame_count):
                #     frame = PmdDisplayFrame()
                #     frame.parse(f)
                #     self.display_frames.append(frame)


                # この先はPMDファイルの拡張データを解析する部分です。
                # 拡張データがない場合はここで処理を終了します。

                try:

                    # English Header
                    has_english_header = struct.unpack('<B', f.read(1))[0]
                    if has_english_header:
                        self.header.parse_english(f)

                    # English Bone Names
                    if has_english_header:
                        for bone in self.bones:
                            bone.parse_english(f)

                    # English Morph Names
                    if has_english_header:
                        for morph in self.morphs:
                            morph.parse_english(f)

                    # English Display Frame Names
                    if has_english_header:
                        self.display_frame.parse_english(f)

                    # Toon Textures
                    toon_texture_count = 10
                    toon_textures = []
                    for _ in range(toon_texture_count):
                        toon_texture_name = utils.decodePMDString(f.read(100))
                        toon_texture_name = toon_texture_name.replace("\\", os.path.sep)
                        toon_textures.append(toon_texture_name)
                    # This part of the data is not stored in a specific class yet.
                    # It might be better to store it in the material data.

                    # Physics
                    rigid_body_count = struct.unpack('<I', f.read(4))[0]
                    for _ in range(rigid_body_count):
                        rigid_body = PmdRigidBody()
                        rigid_body.parse(f)
                        self.rigid_bodies.append(rigid_body)

                    joint_count = struct.unpack('<I', f.read(4))[0]
                    for _ in range(joint_count):
                        joint = PmdJoint()
                        joint.parse(f)
                        self.joints.append(joint)

                except Exception:
                    return self

            except struct.error as e:
                raise MMDParseException(f"Failed to parse PMD file: {file_path}") from e
        
        return self
