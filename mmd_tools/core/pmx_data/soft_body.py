from typing import BinaryIO
import struct

from mmd_tools.core import utils

_SIGNED_INDEX_FORMATS = {1: "<b", 2: "<h", 4: "<i"}
_UNSIGNED_INDEX_FORMATS = {1: "<B", 2: "<H", 4: "<I"}
_UNSUPPORTED_DETAIL_SIZE = 4 + 12 * 4 + 6 * 4 + 4 * 4 + 3 * 4


class PmxSoftBody:
    """
    PMXファイルのSoftBodyデータを保持するクラス (PMX 2.1以降)。

    Maya importer は soft body を未対応として扱うが、PMX 2.1 の該当セクションを
    安全に読み飛ばし、writer roundtrip でバイナリ境界を壊さないための
    header-level parser/writer を提供する。
    """

    def __init__(
        self,
        material_index_size: int = 1,
        rigid_body_index_size: int = 1,
        vertex_index_size: int = 1,
        encoding_flag: int = 1,
    ):
        self.name = ""
        self.name_english = ""
        self.kind = 0  # 0: tri mesh, 1: rope
        self.material_index = -1
        self.collision_group = 0
        self.collision_mask = 0
        self.flags = 0
        self.bending_constraints_distance = 0
        self.cluster_count = 0
        self.total_mass = 0.0
        self.collision_margin = 0.0
        self.material_index_size = material_index_size
        self.rigid_body_index_size = rigid_body_index_size
        self.vertex_index_size = vertex_index_size
        self.encoding_flag = encoding_flag  # 0=UTF-16LE, 1=UTF-8
        self.encoding = utils.get_pmx_encoding_string(encoding_flag)  # "utf-16-le" or "utf-8"
        self._unsupported_detail = b"\x00" * _UNSUPPORTED_DETAIL_SIZE
        self.anchors = []
        self.pins = []

    def parse(self, f: BinaryIO) -> None:
        """
        ファイルハンドルからPMX SoftBodyデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = utils.parsePMXString(f, self.encoding_flag)
        self.name_english = utils.parsePMXString(f, self.encoding_flag)
        self.kind = struct.unpack("<B", f.read(1))[0]
        material_index_format = _SIGNED_INDEX_FORMATS[self.material_index_size]
        self.material_index = struct.unpack(material_index_format, f.read(self.material_index_size))[0]
        self.collision_group = struct.unpack("<B", f.read(1))[0]
        self.collision_mask = struct.unpack("<H", f.read(2))[0]
        self.flags = struct.unpack("<B", f.read(1))[0]
        self.bending_constraints_distance = struct.unpack("<i", f.read(4))[0]
        self.cluster_count = struct.unpack("<i", f.read(4))[0]
        self.total_mass = struct.unpack("<f", f.read(4))[0]
        self.collision_margin = struct.unpack("<f", f.read(4))[0]
        self._unsupported_detail = f.read(_UNSUPPORTED_DETAIL_SIZE)

        rigid_body_index_format = _SIGNED_INDEX_FORMATS[self.rigid_body_index_size]
        vertex_index_format = _UNSIGNED_INDEX_FORMATS[self.vertex_index_size]

        anchor_count = struct.unpack("<I", f.read(4))[0]
        self.anchors = []
        for _ in range(anchor_count):
            rigid_body_index = struct.unpack(rigid_body_index_format, f.read(self.rigid_body_index_size))[0]
            vertex_index = struct.unpack(vertex_index_format, f.read(self.vertex_index_size))[0]
            near_mode = struct.unpack("<B", f.read(1))[0]
            self.anchors.append((rigid_body_index, vertex_index, near_mode))

        pin_count = struct.unpack("<I", f.read(4))[0]
        self.pins = [struct.unpack(vertex_index_format, f.read(self.vertex_index_size))[0] for _ in range(pin_count)]

    def write(self, f: BinaryIO) -> None:
        """
        PMX SoftBodyデータをファイルハンドルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(utils.encodePMXString(self.name, self.encoding_flag))
        f.write(utils.encodePMXString(self.name_english, self.encoding_flag))
        f.write(struct.pack("<B", self.kind))
        material_index_format = _SIGNED_INDEX_FORMATS[self.material_index_size]
        f.write(struct.pack(material_index_format, self.material_index))
        f.write(struct.pack("<B", self.collision_group))
        f.write(struct.pack("<H", self.collision_mask))
        f.write(struct.pack("<B", self.flags))
        f.write(struct.pack("<i", self.bending_constraints_distance))
        f.write(struct.pack("<i", self.cluster_count))
        f.write(struct.pack("<f", self.total_mass))
        f.write(struct.pack("<f", self.collision_margin))
        detail = self._unsupported_detail[:_UNSUPPORTED_DETAIL_SIZE]
        f.write(detail + b"\x00" * (_UNSUPPORTED_DETAIL_SIZE - len(detail)))

        rigid_body_index_format = _SIGNED_INDEX_FORMATS[self.rigid_body_index_size]
        vertex_index_format = _UNSIGNED_INDEX_FORMATS[self.vertex_index_size]
        f.write(struct.pack("<I", len(self.anchors)))
        for rigid_body_index, vertex_index, near_mode in self.anchors:
            f.write(struct.pack(rigid_body_index_format, rigid_body_index))
            f.write(struct.pack(vertex_index_format, vertex_index))
            f.write(struct.pack("<B", near_mode))

        f.write(struct.pack("<I", len(self.pins)))
        for vertex_index in self.pins:
            f.write(struct.pack(vertex_index_format, vertex_index))
