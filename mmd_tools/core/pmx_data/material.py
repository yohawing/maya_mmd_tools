import enum
import struct
from typing import BinaryIO

from mmd_tools.core import utils
from mmd_tools.core.pmx_data.header import PmxEncoding


# bitFlag: 描画フラグ
#   0x01:両面描画, 0x02:地面影, 0x04:セルフシャドウマップ描画
#   0x08:セルフシャドウ描画, 0x10:エッジ描画
#   0x20:頂点カラー(v2.1), 0x40:Point描画(v2.1), 0x80:Line描画(v2.1)
class PmxDrawFlag(enum.IntFlag):
    """
    PMXファイルのボーンフラグを定義するクラス。
    各フラグはビットマスクで定義されており、ボーンの特性を示す。
    """

    NONE = 0x00  # 描画しない
    DOUBLE_SIDED = 0x01  # 両面描画
    GROUND_SHADOW = 0x02  # 地面影
    SELF_SHADOW_MAP = 0x04  # セルフシャドウマップ描画
    SELF_SHADOW = 0x08  # セルフシャドウ描画
    EDGE_DRAWING = 0x10  # エッジ描画
    VERTEX_COLOR = 0x20  # 頂点カラー (v2.1)
    POINT_DRAWING = 0x40  # Point描画 (v2.1)
    LINE_DRAWING = 0x80  # Line描画 (v2.1)


# byte: スフィアモード (0:無効, 1:乗算sph, 2:加算spa, 3:サブテクスチャ)
class PmxSphereMode(enum.IntEnum):
    """
    PMXファイルのスフィアモードを定義する列挙型。
    スフィアモードは、マテリアルのスフィアテクスチャの使用方法を示す。
    """

    DISABLED = 0  # 無効
    MULTIPLY = 1  # 乗算 (sph)
    ADDITIVE = 2  # 加算 (spa)
    SUB_TEXTURE = 3  # サブテクスチャ


# 共有Toonフラグ
class PmxSharedToonFlag(enum.IntEnum):
    """
    PMXファイルの共有Toonフラグを定義する列挙型。
    Toonテクスチャの共有方法を示す。
    """

    NOT_SHARED = 0  # 個別Toon（テクスチャテーブル参照）
    SHARED = 1  # 共有Toon（toon01～toon10）


class PmxMaterial:
    """
    PMXファイルの材質データを保持するクラス。
    """

    def __init__(
        self,
        texture_index_size: int = 1,
        encoding: PmxEncoding = PmxEncoding.UTF16LE,
        material_index: int = 0,
    ):
        # デフォルト値を設定
        self.texture_index_size = texture_index_size
        self.encoding = encoding
        self.name = ""
        self.name_english = ""
        self.diffuse = (1.0, 1.0, 1.0, 1.0)  # 白色をデフォルトに
        self.specular = (0.5, 0.5, 0.5)
        self.specular_coefficient = 5.0
        self.ambient = (0.3, 0.3, 0.3)
        self.draw_flag = PmxDrawFlag.DOUBLE_SIDED | PmxDrawFlag.GROUND_SHADOW | PmxDrawFlag.EDGE_DRAWING
        self.edge_color = (0.0, 0.0, 0.0, 1.0)
        self.edge_size = 1.0
        self.texture_index = -1
        self.sphere_texture_index = -1
        self.sphere_mode = PmxSphereMode.DISABLED
        self.shared_toon_flag = PmxSharedToonFlag.NOT_SHARED
        self.toon_texture_index = -1
        self.memo = ""
        self.face_count = 0
        self.material_index = material_index

    def parse(self, f: BinaryIO) -> None:
        """
        ファイルハンドルからPMX材質データを解析し、自身の属性に格納する。

        Args:
            f: バイナリ読み込みモードで開かれたファイルハンドル。
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
        self.texture_index = struct.unpack(texture_index_format, f.read(self.texture_index_size))[0]
        self.sphere_texture_index = struct.unpack(texture_index_format, f.read(self.texture_index_size))[0]

        self.sphere_mode = struct.unpack("<B", f.read(1))[0]
        self.shared_toon_flag = struct.unpack("<B", f.read(1))[0]

        if self.shared_toon_flag == 0:
            self.toon_texture_index = struct.unpack(texture_index_format, f.read(self.texture_index_size))[0]
        else:
            self.toon_texture_index = struct.unpack("<B", f.read(1))[0]

        self.memo = utils.parsePMXString(f, self.encoding)

        self.face_count = struct.unpack("<I", f.read(4))[0]

    def get_name(self) -> str:
        """
        マテリアルの名前を取得します。英語名が設定されていればそれを返し、なければ日本語名を返す。

        Returns:
            str: マテリアルの名前
        """
        if self.name_english and self.name_english != "":
            return self.name_english
        return self.name

    def write(self, f: BinaryIO) -> None:
        """
        PMX材質データをファイルハンドルに書き込む。

        Args:
            f: バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(utils.encodePMXString(self.name, self.encoding))
        f.write(utils.encodePMXString(self.name_english, self.encoding))

        f.write(struct.pack("<ffff", *self.diffuse))
        f.write(struct.pack("<fff", *self.specular))
        f.write(struct.pack("<f", self.specular_coefficient))
        f.write(struct.pack("<fff", *self.ambient))
        f.write(struct.pack("<B", self.draw_flag))
        f.write(struct.pack("<ffff", *self.edge_color))
        f.write(struct.pack("<f", self.edge_size))

        texture_index_format = {1: "<b", 2: "<h", 4: "<i"}[self.texture_index_size]
        f.write(struct.pack(texture_index_format, self.texture_index))
        f.write(struct.pack(texture_index_format, self.sphere_texture_index))

        f.write(struct.pack("<B", self.sphere_mode))
        f.write(struct.pack("<B", self.shared_toon_flag))

        if self.shared_toon_flag == 0:
            f.write(struct.pack(texture_index_format, self.toon_texture_index))
        else:
            f.write(struct.pack("<B", self.toon_texture_index))

        f.write(utils.encodePMXString(self.memo, self.encoding))

        f.write(struct.pack("<I", self.face_count))
