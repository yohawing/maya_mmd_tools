import enum
import struct
from typing import BinaryIO

from mmd_tools.core import utils
from mmd_tools.core.pmx_data.header import PmxEncoding


class PmxMorphType(enum.IntEnum):
    GroupMorph = 0
    VertexMorph = 1
    BoneMorph = 2
    UVMorph = 3
    AdditionalUVMorph1 = 4
    AdditionalUVMorph2 = 5
    AdditionalUVMorph3 = 6
    AdditionalUVMorph4 = 7
    MaterialMorph = 8
    FlipMorph = 9  # PMX 2.1以降
    ImpulseMorph = 10  # PMX 2.1以降


class PmxMorph:
    """
    PMXファイルのモーフデータを保持するクラス。
    """

    def __init__(
        self,
        vertex_index_size: int,
        material_index_size: int,
        bone_index_size: int,
        morph_index_size: int,
        rigid_body_index_size: int,
        encoding: PmxEncoding = PmxEncoding.UTF16LE,
    ):
        self.vertex_index_size = vertex_index_size
        self.material_index_size = material_index_size
        self.bone_index_size = bone_index_size
        self.morph_index_size = morph_index_size
        self.rigid_body_index_size = rigid_body_index_size

        self.type_formats = {
            "vertex": {1: "<B", 2: "<H", 4: "<I"}[vertex_index_size],
            "material": {1: "<b", 2: "<h", 4: "<i"}[material_index_size],
            "bone": {1: "<b", 2: "<h", 4: "<i"}[bone_index_size],
            "morph": {1: "<b", 2: "<h", 4: "<i"}[morph_index_size],
            "rigid_body": {1: "<b", 2: "<h", 4: "<i"}[rigid_body_index_size],
        }

        self.encoding = encoding

        self.name = ""
        self.name_english = ""
        self.panel = 0  # 操作パネル (1:眉, 2:目, 3:口, 4:その他, 0:システム予約)
        self.morph_type = PmxMorphType.GroupMorph  # モーフ種類
        self.offset_count = 0
        self.offsets = []

    def parse(self, f: BinaryIO) -> None:
        """
        ファイルハンドルからPMXモーフデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = utils.parsePMXString(
            f, self.encoding
        )  # Use utility function for PMX string parsing
        self.name_english = utils.parsePMXString(f, self.encoding)

        self.panel = struct.unpack("<B", f.read(1))[0]
        self.morph_type = PmxMorphType(
            struct.unpack("<B", f.read(1))[0]
        )  # Convert to Enum
        self.offset_count = struct.unpack("<I", f.read(4))[0]

        for _ in range(self.offset_count):
            offset_data = {}

            if self.morph_type == PmxMorphType.GroupMorph:
                offset_data["morph_index"] = struct.unpack(
                    self.type_formats["morph"], f.read(self.morph_index_size)
                )[0]
                offset_data["morph_rate"] = struct.unpack("<f", f.read(4))[0]
            elif self.morph_type == PmxMorphType.VertexMorph:
                offset_data["vertex_index"] = struct.unpack(
                    self.type_formats["vertex"], f.read(self.vertex_index_size)
                )[0]
                offset_data["position_offset"] = struct.unpack("<fff", f.read(12))
            elif self.morph_type == PmxMorphType.BoneMorph:
                offset_data["bone_index"] = struct.unpack(
                    self.type_formats["bone"], f.read(self.bone_index_size)
                )[0]
                offset_data["translation"] = struct.unpack("<fff", f.read(12))
                offset_data["rotation"] = struct.unpack("<ffff", f.read(16))
            elif (
                PmxMorphType.UVMorph
                <= self.morph_type
                <= PmxMorphType.AdditionalUVMorph4
            ):
                offset_data["vertex_index"] = struct.unpack(
                    self.type_formats["vertex"], f.read(self.vertex_index_size)
                )[0]
                offset_data["uv_offset"] = struct.unpack("<ffff", f.read(16))
            elif self.morph_type == PmxMorphType.MaterialMorph:
                offset_data["material_index"] = struct.unpack(
                    self.type_formats["material"], f.read(self.material_index_size)
                )[0]
                offset_data["operation_type"] = struct.unpack("<B", f.read(1))[0]
                offset_data["diffuse"] = struct.unpack("<ffff", f.read(16))
                offset_data["specular"] = struct.unpack("<fff", f.read(12))
                offset_data["specular_coefficient"] = struct.unpack("<f", f.read(4))[0]
                offset_data["ambient"] = struct.unpack("<fff", f.read(12))
                offset_data["edge_color"] = struct.unpack("<ffff", f.read(16))
                offset_data["edge_size"] = struct.unpack("<f", f.read(4))[0]
                offset_data["texture_factor"] = struct.unpack("<ffff", f.read(16))
                offset_data["sphere_texture_factor"] = struct.unpack(
                    "<ffff", f.read(16)
                )
                offset_data["toon_texture_factor"] = struct.unpack("<ffff", f.read(16))
            elif self.morph_type == PmxMorphType.FlipMorph:
                offset_data["morph_index"] = struct.unpack(
                    self.type_formats["morph"], f.read(self.morph_index_size)
                )[0]
                offset_data["flip_rate"] = struct.unpack("<f", f.read(4))[0]
            elif self.morph_type == PmxMorphType.ImpulseMorph:
                offset_data["rigid_body_index"] = struct.unpack(
                    self.type_formats["rigid_body"], f.read(self.rigid_body_index_size)
                )[0]
                offset_data["impulse"] = struct.unpack("<fff", f.read(12))
                offset_data["torque"] = struct.unpack("<fff", f.read(12))
            else:
                raise ValueError(f"Unknown morph type: {self.morph_type}")
            self.offsets.append(offset_data)

    def get_name(self) -> str:
        """
        モーフの名前を取得する。英語名が設定されている場合は英語名を返す。

        Returns:
            str: モーフの名前。
        """
        if self.name_english and self.name_english != "":
            return self.name_english
        return self.name

    def write(self, f: BinaryIO) -> None:
        """
        PMXモーフデータをファイルハンドルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(utils.encodePMXString(self.name, self.encoding))
        f.write(utils.encodePMXString(self.name_english, self.encoding))

        f.write(struct.pack("<B", self.panel))
        f.write(
            struct.pack(
                "<B",
                self.morph_type.value
                if isinstance(self.morph_type, PmxMorphType)
                else self.morph_type,
            )
        )
        f.write(struct.pack("<I", len(self.offsets)))

        for offset_data in self.offsets:
            if self.morph_type == PmxMorphType.GroupMorph:
                f.write(
                    struct.pack(self.type_formats["morph"], offset_data["morph_index"])
                )
                f.write(struct.pack("<f", offset_data["morph_rate"]))
            elif self.morph_type == PmxMorphType.VertexMorph:
                f.write(
                    struct.pack(
                        self.type_formats["vertex"], offset_data["vertex_index"]
                    )
                )
                f.write(struct.pack("<fff", *offset_data["position_offset"]))
            elif self.morph_type == PmxMorphType.BoneMorph:
                f.write(
                    struct.pack(self.type_formats["bone"], offset_data["bone_index"])
                )
                f.write(struct.pack("<fff", *offset_data["translation"]))
                f.write(struct.pack("<ffff", *offset_data["rotation"]))
            elif (
                PmxMorphType.UVMorph
                <= self.morph_type
                <= PmxMorphType.AdditionalUVMorph4
            ):
                f.write(
                    struct.pack(
                        self.type_formats["vertex"], offset_data["vertex_index"]
                    )
                )
                f.write(struct.pack("<ffff", *offset_data["uv_offset"]))
            elif self.morph_type == PmxMorphType.MaterialMorph:
                f.write(
                    struct.pack(
                        self.type_formats["material"], offset_data["material_index"]
                    )
                )
                f.write(struct.pack("<B", offset_data["operation_type"]))
                f.write(struct.pack("<ffff", *offset_data["diffuse"]))
                f.write(struct.pack("<fff", *offset_data["specular"]))
                f.write(struct.pack("<f", offset_data["specular_coefficient"]))
                f.write(struct.pack("<fff", *offset_data["ambient"]))
                f.write(struct.pack("<ffff", *offset_data["edge_color"]))
                f.write(struct.pack("<f", offset_data["edge_size"]))
                f.write(struct.pack("<ffff", *offset_data["texture_factor"]))
                f.write(struct.pack("<ffff", *offset_data["sphere_texture_factor"]))
                f.write(struct.pack("<ffff", *offset_data["toon_texture_factor"]))
            elif self.morph_type == PmxMorphType.FlipMorph:
                f.write(
                    struct.pack(self.type_formats["morph"], offset_data["morph_index"])
                )
                f.write(struct.pack("<f", offset_data["flip_rate"]))
            elif self.morph_type == PmxMorphType.ImpulseMorph:
                f.write(
                    struct.pack(
                        self.type_formats["rigid_body"], offset_data["rigid_body_index"]
                    )
                )
                f.write(struct.pack("<fff", *offset_data["impulse"]))
                f.write(struct.pack("<fff", *offset_data["torque"]))
            else:
                raise ValueError(f"Unknown morph type: {self.morph_type}")
