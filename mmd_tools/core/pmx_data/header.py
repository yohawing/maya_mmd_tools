import struct
from mmd_tools.core import utils

class PmxHeader:
    """
    PMXファイルのヘッダ情報を保持するクラス。
    """
    def __init__(self):
        self.magic = b''
        self.version = 0.0
        self.header_size = 0
        self.text_encoding = 'utf-16-le'  # デフォルトはUTF-16LE
        self.additional_uv = 0
        self.vertex_index_size = 0
        self.texture_index_size = 0
        self.material_index_size = 0
        self.bone_index_size = 0
        self.morph_index_size = 0
        self.rigid_body_index_size = 0
        self.model_name = ''
        self.model_name_english = ''
        self.comment = ''
        self.comment_english = ''

    def parse(self, f):
        """
        ファイルハンドルからPMXヘッダを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.magic = f.read(4)
        if self.magic != b'PMX ':
            raise ValueError("Not a valid PMX file.")
        self.version = struct.unpack('<f', f.read(4))[0]

        self.header_size = struct.unpack('<B', f.read(1))[0]
        if self.header_size != 8:
            raise ValueError(f"Unsupported PMX header size: {self.header_size}")

        text_encoding = struct.unpack('<B', f.read(1))[0]
        self.additional_uv = struct.unpack('<B', f.read(1))[0]
        self.vertex_index_size = struct.unpack('<B', f.read(1))[0]
        self.texture_index_size = struct.unpack('<B', f.read(1))[0]
        self.material_index_size = struct.unpack('<B', f.read(1))[0]
        self.bone_index_size = struct.unpack('<B', f.read(1))[0]
        self.morph_index_size = struct.unpack('<B', f.read(1))[0]
        self.rigid_body_index_size = struct.unpack('<B', f.read(1))[0]

        self.encoding = 'utf-16-le' if text_encoding == 0 else 'utf-8'

        # Model Info
        self.model_name = utils.parsePMXString(f, self.encoding)
        self.model_name_english = utils.parsePMXString(f, self.encoding)
        self.comment = utils.parsePMXString(f, self.encoding)
        self.comment_english = utils.parsePMXString(f, self.encoding)
