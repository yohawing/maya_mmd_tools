import enum
import struct

from mmd_tools.core import utils


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

    SHARED = 0  # 共有Toon
    NOT_SHARED = 1  # 個別Toon


class PmxMaterial:
    """
    PMXファイルの材質データを保持するクラス。
    """

    def __init__(self, texture_index_size, encoding, material_index):
        self.texture_index_size = texture_index_size
        self.encoding = encoding
        self.name = ""
        self.name_english = ""
        self.diffuse = (0.0, 0.0, 0.0, 0.0)
        self.specular = (0.0, 0.0, 0.0)
        self.specular_coefficient = 0.0
        self.ambient = (0.0, 0.0, 0.0)
        self.draw_flag = PmxDrawFlag.NONE
        self.edge_color = (0.0, 0.0, 0.0, 0.0)
        self.edge_size = 0.0
        self.texture_index = -1
        self.sphere_texture_index = -1
        self.sphere_mode = PmxSphereMode.DISABLED
        self.shared_toon_flag = PmxSharedToonFlag.SHARED
        self.toon_texture_index = -1
        self.memo = ""
        self.face_count = 0
        self.material_index = material_index

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
