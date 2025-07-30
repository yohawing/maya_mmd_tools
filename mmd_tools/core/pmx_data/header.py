import enum
import struct

from mmd_tools.core.settings import settings
from mmd_tools.core import utils


class PmxEncoding(enum.IntEnum):
    UTF16LE = 0
    UTF8 = 1


class PmxHeader:
    """
    PMXファイルのヘッダ情報を保持するクラス。
    """

    def __init__(self):
        self.magic = b""
        self.version = 0.0
        self.header_size = 0
        self.encoding = PmxEncoding.UTF16LE
        self.additional_uv = 0
        self.vertex_index_size = 0
        self.texture_index_size = 0
        self.material_index_size = 0
        self.bone_index_size = 0
        self.morph_index_size = 0
        self.rigid_body_index_size = 0
        self.model_name = ""
        self.model_name_english = ""
        self.comment = ""
        self.comment_english = ""

    def parse(self, f):
        """
        ファイルハンドルからPMXヘッダを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.magic = f.read(4)
        if self.magic != b"PMX ":
            raise ValueError("Not a valid PMX file.")
        self.version = struct.unpack("<f", f.read(4))[0]

        self.header_size = struct.unpack("<B", f.read(1))[0]
        if self.header_size != 8:
            raise ValueError(f"Unsupported PMX header size: {self.header_size}")

        self.encoding = PmxEncoding(struct.unpack("<B", f.read(1))[0])
        self.additional_uv = struct.unpack("<B", f.read(1))[0]
        self.vertex_index_size = struct.unpack("<B", f.read(1))[0]
        self.texture_index_size = struct.unpack("<B", f.read(1))[0]
        self.material_index_size = struct.unpack("<B", f.read(1))[0]
        self.bone_index_size = struct.unpack("<B", f.read(1))[0]
        self.morph_index_size = struct.unpack("<B", f.read(1))[0]
        self.rigid_body_index_size = struct.unpack("<B", f.read(1))[0]

        # Model Info
        self.model_name = utils.parsePMXString(f, self.encoding)
        self.model_name_english = utils.parsePMXString(f, self.encoding)
        self.comment = utils.parsePMXString(f, self.encoding)
        self.comment_english = utils.parsePMXString(f, self.encoding)

    def get_name(self):
        """
        モデルの名前を取得する。英語名が設定されていればそれを返し、なければ日本語名を返す。

        Returns:
            str: モデルの名前。
        """
        use_en = settings.get("import.model.joint_name_conversion_with_english")
        if use_en and self.model_name_english and self.model_name_english != "":
            return self.model_name_english

        return self.model_name

    def write(self, f):
        """
        PMXヘッダをバイナリファイルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        # Magic and version
        f.write(b"PMX ")
        f.write(struct.pack("<f", self.version))

        # Header size (always 8 for PMX 2.0/2.1)
        f.write(struct.pack("<B", 8))

        # Encoding flag (0: UTF-16LE, 1: UTF-8)
        encoding_flag = 0 if self.encoding == "utf-16-le" else 1
        f.write(struct.pack("<B", encoding_flag))

        # Additional data sizes
        f.write(struct.pack("<B", self.additional_uv))
        f.write(struct.pack("<B", self.vertex_index_size))
        f.write(struct.pack("<B", self.texture_index_size))
        f.write(struct.pack("<B", self.material_index_size))
        f.write(struct.pack("<B", self.bone_index_size))
        f.write(struct.pack("<B", self.morph_index_size))
        f.write(struct.pack("<B", self.rigid_body_index_size))

        # Model Info
        f.write(utils.encodePMXString(self.model_name, self.encoding))
        f.write(utils.encodePMXString(self.model_name_english, self.encoding))
        f.write(utils.encodePMXString(self.comment, self.encoding))
        f.write(utils.encodePMXString(self.comment_english, self.encoding))

    @property
    def encoding_flag(self):
        """エンコーディングフラグ（0=UTF-16LE, 1=UTF-8）を返す"""
        return 0 if self.encoding == "utf-16-le" else 1
