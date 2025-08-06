import os
import struct
from typing import List

from mmd_tools.core import utils

from ..exceptions import MMDParseException
from ..logger import get_logger
from .bone import PmdBone
from .display_frame import PmdDisplayFrame
from .face import PmdFace
from .header import PmdHeader
from .ik import PmdIK
from .joint import PmdJoint
from .material import PmdMaterial
from .morph import PmdMorph
from .rigid_body import PmdRigidBody
from .vertex import PmdVertex

# ロガー取得
logger = get_logger("mmd_tools.core.pmd_parser")


class PmdData:
    """
    PMDファイルを解析し、そのデータをPythonオブジェクトとして保持するクラス。
    """

    header: PmdHeader
    vertices: List[PmdVertex]
    faces: List[PmdFace]
    materials: List[PmdMaterial]
    bones: List[PmdBone]
    ik_data: List[PmdIK]
    morphs: List[PmdMorph]
    display_frames: List[PmdDisplayFrame]
    rigid_bodies: List[PmdRigidBody]
    joints: List[PmdJoint]

    def __init__(self):
        self.header = PmdHeader()
        self.vertices = []
        self.faces = []
        self.materials = []
        self.bones = []
        self.ik_data = []
        self.morphs = []
        self.display_frames = []
        self.rigid_bodies = []
        self.joints = []

    def parse_file(self, file_path) -> "PmdData":
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
        logger.info(f"PMDファイルの解析を開始: {file_path}")

        if not os.path.exists(file_path):
            logger.error(f"PMDファイルが見つかりません: {file_path}")
            raise FileNotFoundError(f"PMD file not found: {file_path}")

        with open(file_path, "rb") as f:
            try:
                # Header
                logger.debug("ヘッダー情報を解析中")
                self.header.parse(f)

                # Vertex
                vertex_count = struct.unpack("<I", f.read(4))[0]
                logger.debug(f"頂点数: {vertex_count}")
                for _ in range(vertex_count):
                    vertex = PmdVertex()
                    vertex.parse(f)
                    self.vertices.append(vertex)

                # Face
                face_count = struct.unpack("<I", f.read(4))[0]
                logger.debug(f"面数: {face_count // 3}")
                for _ in range(face_count // 3):
                    face = PmdFace()
                    face.parse(f)
                    self.faces.append(face)

                # Material
                material_count = struct.unpack("<I", f.read(4))[0]
                logger.debug(f"マテリアル数: {material_count}")
                for i in range(material_count):
                    material = PmdMaterial(material_index=i)
                    material.parse(f)
                    self.materials.append(material)

                # Bone
                bone_count = struct.unpack("<H", f.read(2))[0]
                logger.debug(f"ボーン数: {bone_count}")
                for _ in range(bone_count):
                    bone = PmdBone()
                    bone.parse(f)
                    self.bones.append(bone)

                # IK
                ik_count = struct.unpack("<H", f.read(2))[0]
                logger.debug(f"IK数: {ik_count}")
                for _ in range(ik_count):
                    ik = PmdIK()
                    ik.parse(f)
                    self.ik_data.append(ik)

                # Morph
                morph_count = struct.unpack("<H", f.read(2))[0]
                logger.debug(f"モーフ数: {morph_count}")
                for _ in range(morph_count):
                    morph = PmdMorph()
                    morph.parse(f)
                    self.morphs.append(morph)

                # Display Frame
                logger.debug("表示枠を解析中")
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
                    logger.debug("拡張データを解析中")
                    has_english_header = struct.unpack("<B", f.read(1))[0]
                    if has_english_header:
                        logger.debug("英語ヘッダーを解析中")
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
                    rigid_body_count = struct.unpack("<I", f.read(4))[0]
                    logger.debug(f"剛体数: {rigid_body_count}")
                    for _ in range(rigid_body_count):
                        rigid_body = PmdRigidBody()
                        rigid_body.parse(f)
                        self.rigid_bodies.append(rigid_body)

                    joint_count = struct.unpack("<I", f.read(4))[0]
                    logger.debug(f"ジョイント数: {joint_count}")
                    for _ in range(joint_count):
                        joint = PmdJoint()
                        joint.parse(f)
                        self.joints.append(joint)

                except Exception:
                    logger.info("拡張データなし、または拡張データの解析を終了")
                    return self

            except struct.error as e:
                logger.error(f"PMDファイルの解析に失敗しました: {file_path}")
                raise MMDParseException(f"Failed to parse PMD file: {file_path}") from e

        logger.info("PMDファイルの解析が完了しました")
        return self

    def write_file(self, file_path):
        """
        PMDデータをファイルに書き込む。

        Args:
            file_path (str): 書き込むPMDファイルのパス。

        Raises:
            IOError: ファイル書き込みに失敗した場合。
        """
        logger.info(f"PMDファイルの書き込みを開始: {file_path}")

        try:
            with open(file_path, "wb") as f:
                # Header
                logger.debug("ヘッダー情報を書き込み中")
                self.header.write(f)

                # Vertex
                vertex_count = len(self.vertices)
                logger.debug(f"頂点数: {vertex_count}")
                f.write(struct.pack("<I", vertex_count))
                for vertex in self.vertices:
                    vertex.write(f)

                # Face
                face_count = len(self.faces) * 3
                logger.debug(f"面数: {len(self.faces)}")
                f.write(struct.pack("<I", face_count))
                for face in self.faces:
                    face.write(f)

                # Material
                material_count = len(self.materials)
                logger.debug(f"マテリアル数: {material_count}")
                f.write(struct.pack("<I", material_count))
                for material in self.materials:
                    material.write(f)

                # Bone
                bone_count = len(self.bones)
                logger.debug(f"ボーン数: {bone_count}")
                f.write(struct.pack("<H", bone_count))
                for bone in self.bones:
                    bone.write(f)

                # IK
                ik_count = len(self.ik_data)
                logger.debug(f"IK数: {ik_count}")
                f.write(struct.pack("<H", ik_count))
                for ik in self.ik_data:
                    ik.write(f)

                # Morph
                morph_count = len(self.morphs)
                logger.debug(f"モーフ数: {morph_count}")
                f.write(struct.pack("<H", morph_count))
                for morph in self.morphs:
                    morph.write(f)

                # Morph Display List
                morph_display_count = sum(len(frame.morphs) for frame in self.display_frames)
                logger.debug(f"モーフ表示数: {morph_display_count}")
                f.write(struct.pack("<B", morph_display_count))
                for frame in self.display_frames:
                    frame.write_morphs(f)

                # Display Frame Names
                display_frame_count = len(self.display_frames)
                logger.debug(f"表示枠数: {display_frame_count}")
                f.write(struct.pack("<B", display_frame_count))
                for frame in self.display_frames:
                    frame.write(f)

                # Bone Display List
                bone_display_count = sum(len(frame.bones) for frame in self.display_frames)
                logger.debug(f"ボーン表示数: {bone_display_count}")
                f.write(struct.pack("<I", bone_display_count))
                for frame in self.display_frames:
                    frame.write_bones(f)

                # Extended Data
                # English Header
                has_english = self.header.model_name_english != "" or self.header.comment_english != ""
                f.write(struct.pack("<B", 1 if has_english else 0))

                if has_english:
                    # English Model Name and Comment
                    self.header.write_english(f)

                    # English Bone Names
                    for bone in self.bones:
                        bone.write_english(f)

                    # English Morph Names
                    for morph in self.morphs:
                        morph.write_english(f)

                    # English Display Frame Names
                    for frame in self.display_frames:
                        frame.write_english(f)

                # Toon Textures (10個固定、未実装部分は空文字列)
                logger.debug("トゥーンテクスチャ情報を書き込み中")
                for i in range(10):
                    f.write(utils.encodePMDString("", 100))

                # Physics
                rigid_body_count = len(self.rigid_bodies)
                logger.debug(f"剛体数: {rigid_body_count}")
                f.write(struct.pack("<I", rigid_body_count))
                for rigid_body in self.rigid_bodies:
                    rigid_body.write(f)

                joint_count = len(self.joints)
                logger.debug(f"ジョイント数: {joint_count}")
                f.write(struct.pack("<I", joint_count))
                for joint in self.joints:
                    joint.write(f)

        except Exception as e:
            logger.error(f"PMDファイルの書き込みに失敗しました: {file_path}")
            raise IOError(f"Failed to write PMD file: {file_path}") from e

        logger.info("PMDファイルの書き込みが完了しました")
