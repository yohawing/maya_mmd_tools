import struct

from .header import PmxHeader, PmxEncoding


class PmxData:
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

    def parse_file(self, file_path: str) -> "PmxData":
        """Legacy Python PMX parser compatibility entry point.

        PMX import code should use ``mmd_tools.core.mmd_parser.parse_pmx_file``
        so native parser policy is enforced in one place. This method remains
        only for old callers that intentionally opt in to the Python reader.

        Args:
            file_path: 解析するPMXファイルのパス。

        Returns:
            メソッドチェーニングをサポートするための自身のインスタンス。
        """
        from .legacy_parser import parse_pmx_file_legacy

        return parse_pmx_file_legacy(file_path, target=self)

    def write_file(self, file_path):
        """
        PMXデータをファイルに書き込む。

        Args:
            file_path (str): 書き込むPMXファイルのパス。

        Raises:
            IOError: ファイル書き込みに失敗した場合。
        """
        try:
            with open(file_path, "wb") as f:
                # Header
                self.header.write(f)

                # Get sizes from header
                encoding = self.header.encoding

                # Vertex
                vertex_count = len(self.vertices)
                f.write(struct.pack("<I", vertex_count))
                for vertex in self.vertices:
                    vertex.write(f, self.header.version)

                # Face
                face_count = len(self.faces) * 3
                f.write(struct.pack("<I", face_count))
                for face in self.faces:
                    face.write(f)

                # Textures
                texture_count = len(self.textures)
                f.write(struct.pack("<I", texture_count))
                # Convert PmxEncoding to actual encoding string
                encoding_text = "utf-16-le" if encoding == PmxEncoding.UTF16LE else "utf-8"
                for texture_path in self.textures:
                    texture_bytes = texture_path.encode(encoding_text)
                    f.write(struct.pack("<I", len(texture_bytes)))
                    f.write(texture_bytes)

                # Material
                material_count = len(self.materials)
                f.write(struct.pack("<I", material_count))
                for material in self.materials:
                    material.write(f)

                # Bone
                bone_count = len(self.bones)
                f.write(struct.pack("<I", bone_count))
                for bone in self.bones:
                    bone.write(f)

                # Morph
                morph_count = len(self.morphs)
                f.write(struct.pack("<I", morph_count))
                for morph in self.morphs:
                    morph.write(f)

                # Display Frame
                display_frame_count = len(self.display_frames)
                f.write(struct.pack("<I", display_frame_count))
                for display_frame in self.display_frames:
                    display_frame.write(f)

                # Rigid Body
                rigid_body_count = len(self.rigid_bodies)
                f.write(struct.pack("<I", rigid_body_count))
                for rigid_body in self.rigid_bodies:
                    rigid_body.write(f)

                # Joint
                joint_count = len(self.joints)
                f.write(struct.pack("<I", joint_count))
                for joint in self.joints:
                    joint.write(f)

                # SoftBody (Optional, PMX 2.1 only)
                if self.header.version >= 2.1:
                    soft_body_count = len(self.soft_bodies)
                    f.write(struct.pack("<I", soft_body_count))
                    for soft_body in self.soft_bodies:
                        soft_body.write(f)

        except Exception as e:
            raise IOError(f"Failed to write PMX file: {file_path}") from e
