import enum
import struct

from mmd_tools.core import utils
from mmd_tools.core.pmx_data.ik_link import PmxIKLink


class PmxBoneFlag(enum.IntFlag):
    """
    PMXファイルのボーンフラグを定義するクラス。
    各フラグはビットマスクで定義されており、ボーンの特性を示す。
    """

    CONNECT_BONE = 0x0001  # 接続先表示方法 (0:座標オフセット, 1:ボーン指定)
    GIVEN_PARENT_ROTATE = 0x0100  # 回転付与
    GIVEN_PARENT_MOVE = 0x0200  # 移動付与
    AXIS_FIXED = 0x0400  # 軸固定
    LOCAL_AXIS = 0x0800  # ローカル軸
    EXTERNAL_PARENT_DEFORM = 0x2000  # 外部親変形
    IK = 0x0020  # IK


class PmxBone:
    """
    PMXファイルのボーンデータを保持するクラス。
    """

    def __init__(self, bone_index_size, encoding):
        self.bone_index_size = bone_index_size
        self.encoding = encoding
        self.name = ""
        self.name_english = ""
        self.position = (0.0, 0.0, 0.0)
        self.parent_bone_index = -1
        self.transform_layer = 0
        self.bone_flag = 0

        # Flag-dependent data
        self.connect_bone_index = -1
        self.connect_position_offset = (0.0, 0.0, 0.0)
        self.given_parent_bone_index = -1
        self.given_rate = 0.0
        self.axis_direction = (0.0, 0.0, 0.0)
        self.x_axis_direction = (0.0, 0.0, 0.0)
        self.z_axis_direction = (0.0, 0.0, 0.0)
        self.key_value = 0
        self.ik_target_bone_index = -1
        self.ik_loop_count = 0
        self.ik_limit_angle = 0.0
        self.ik_links = []

    def parse(self, f):
        """
        ファイルハンドルからPMXボーンデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = utils.parsePMXString(f, self.encoding)
        self.name_english = utils.parsePMXString(f, self.encoding)

        self.position = struct.unpack("<fff", f.read(12))

        bone_index_format = {1: "<b", 2: "<h", 4: "<i"}[self.bone_index_size]
        self.parent_bone_index = struct.unpack(
            bone_index_format, f.read(self.bone_index_size)
        )[0]

        if self.parent_bone_index == 0xFFFFFFFF:
            self.parent_bone_index = -1

        self.transform_layer = struct.unpack("<i", f.read(4))[0]
        self.bone_flag = struct.unpack("<H", f.read(2))[0]

        # Flag-dependent data parsing
        # 0x0001: 接続先表示方法 (0:座標オフセット, 1:ボーン指定)
        if self.bone_flag & 0x0001:
            self.connect_bone_index = struct.unpack(
                bone_index_format, f.read(self.bone_index_size)
            )[0]
        else:
            self.connect_position_offset = struct.unpack("<fff", f.read(12))

        # 0x0100: 回転付与, 0x0200: 移動付与
        if self.bone_flag & 0x0100 or self.bone_flag & 0x0200:
            self.given_parent_bone_index = struct.unpack(
                bone_index_format, f.read(self.bone_index_size)
            )[0]
            self.given_rate = struct.unpack("<f", f.read(4))[0]

        # 0x0400: 軸固定
        if self.bone_flag & 0x0400:
            self.axis_direction = struct.unpack("<fff", f.read(12))

        # 0x0800: ローカル軸
        if self.bone_flag & 0x0800:
            self.x_axis_direction = struct.unpack("<fff", f.read(12))
            self.z_axis_direction = struct.unpack("<fff", f.read(12))

        # 0x2000: 外部親変形
        if self.bone_flag & 0x2000:
            self.key_value = struct.unpack("<i", f.read(4))[0]

        # 0x0020: IK
        if self.bone_flag & 0x0020:
            self.ik_target_bone_index = struct.unpack(
                bone_index_format, f.read(self.bone_index_size)
            )[0]
            self.ik_loop_count = struct.unpack("<i", f.read(4))[0]
            self.ik_limit_angle = struct.unpack("<f", f.read(4))[0]
            ik_link_count = struct.unpack("<i", f.read(4))[0]
            for _ in range(ik_link_count):
                ik_link = PmxIKLink(self.bone_index_size, self.encoding)
                ik_link.parse(f)
                self.ik_links.append(ik_link)

    def get_name(self):
        """
        ボーンの名前を取得する。英語名が設定されていればそれを返し、なければ日本語名を返す。

        Returns:
            str: ボーンの名前。
        """
        if self.name_english and self.name_english != "":
            return self.name_english

        return self.name
