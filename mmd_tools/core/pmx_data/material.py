import struct

from mmd_tools.core import utils


class PmxMaterial:
    """
    PMXファイルの材質データを保持するクラス。
    """

    def __init__(self, texture_index_size, encoding):
        self.texture_index_size = texture_index_size
        self.encoding = encoding
        self.name = ""
        self.name_english = ""
        self.diffuse = (0.0, 0.0, 0.0, 0.0)
        self.specular = (0.0, 0.0, 0.0)
        self.specular_coefficient = 0.0
        self.ambient = (0.0, 0.0, 0.0)
        self.draw_flag = 0
        self.edge_color = (0.0, 0.0, 0.0, 0.0)
        self.edge_size = 0.0
        self.texture_index = -1
        self.sphere_texture_index = -1
        self.sphere_mode = 0
        self.shared_toon_flag = 0
        self.toon_texture_index = -1
        self.memo = ""
        self.face_count = 0

    def parse(self, f):
        """
        ファイルハンドルからPMX材質データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = utils.parsePMXString(f, self.encoding)
        self.name_english = utils.parsePMXString(f, self.encoding)

        self.diffuse = struct.unpack("<ffff", f.read(16))
        self.specular = struct.unpack("<fff", f.read(12))
        self.specular_coefficient = struct.unpack("<f", f.read(4))[0]
        self.ambient = struct.unpack("<fff", f.read(12))
        self.draw_flag = struct.unpack("<B", f.read(1))[0]
        self.edge_color = struct.unpack("<ffff", f.read(16))
        self.edge_size = struct.unpack("<f", f.read(4))[0]

        texture_index_format = {1: "<b", 2: "<h", 4: "<i"}[self.texture_index_size]
        self.texture_index = struct.unpack(
            texture_index_format, f.read(self.texture_index_size)
        )[0]
        self.sphere_texture_index = struct.unpack(
            texture_index_format, f.read(self.texture_index_size)
        )[0]

        self.sphere_mode = struct.unpack("<B", f.read(1))[0]
        self.shared_toon_flag = struct.unpack("<B", f.read(1))[0]

        if self.shared_toon_flag == 0:
            self.toon_texture_index = struct.unpack(
                texture_index_format, f.read(self.texture_index_size)
            )[0]
        else:
            self.toon_texture_index = struct.unpack("<B", f.read(1))[0]

        self.memo = utils.parsePMXString(f, self.encoding)

        self.face_count = struct.unpack("<I", f.read(4))[0]
