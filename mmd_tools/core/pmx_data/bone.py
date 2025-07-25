import enum
import struct

from mmd_tools.core import utils
from mmd_tools.core.pmx_data.ik_link import PmxIKLink
from mmd_tools.core.settings import get_settings

settings = get_settings()


class PmxBoneFlag(enum.IntFlag):
    """
    PMXファイルのボーンフラグを定義するクラス。
    各フラグはビットマスクで定義されており、ボーンの特性を示す。
    """

    CONNECT_BONE = 0x0001  # 接続先表示方法 (0:相対座標オフセット, 1:ボーン指定)
    ROTATABLE = 0x0002  # 回転可能
    MOVABLE = 0x0004  # 移動可能
    DISPLAY = 0x0008  # 表示
    OPERATABLE = 0x0010  # 操作可能
    IK = 0x0020  # IK
    LOCAL = 0x0080  # ローカル付与 (付与対象 0:ユーザー変形値／IKリンク／多重付与 1:親のローカル変形量)
    GIVEN_PARENT_ROTATE = 0x0100  # 回転付与
    GIVEN_PARENT_MOVE = 0x0200  # 移動付与
    AXIS_FIXED = 0x0400  # 軸固定
    LOCAL_AXIS = 0x0800  # ローカル軸
    DEFORM_AFTER_PHYSICS = 0x1000  # 物理演算後変形
    EXTERNAL_PARENT_DEFORM = 0x2000  # 外部親変形


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
        self.x_axis_direction = (1.0, 0.0, 0.0)
        self.z_axis_direction = (0.0, 0.0, 1.0)
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
        if self.get_flag(PmxBoneFlag.CONNECT_BONE):
            self.connect_bone_index = struct.unpack(
                bone_index_format, f.read(self.bone_index_size)
            )[0]
        else:
            self.connect_position_offset = struct.unpack("<fff", f.read(12))

        # 0x0100: 回転付与, 0x0200: 移動付与
        if self.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE) or self.get_flag(
            PmxBoneFlag.GIVEN_PARENT_MOVE
        ):
            self.given_parent_bone_index = struct.unpack(
                bone_index_format, f.read(self.bone_index_size)
            )[0]
            self.given_rate = struct.unpack("<f", f.read(4))[0]

        # 0x0400: 軸固定
        if self.get_flag(PmxBoneFlag.AXIS_FIXED):
            self.axis_direction = struct.unpack("<fff", f.read(12))

        # 0x0800: ローカル軸
        if self.get_flag(PmxBoneFlag.LOCAL_AXIS):
            self.x_axis_direction = struct.unpack("<fff", f.read(12))
            self.z_axis_direction = struct.unpack("<fff", f.read(12))

        # 0x2000: 外部親変形
        if self.get_flag(PmxBoneFlag.EXTERNAL_PARENT_DEFORM):
            self.key_value = struct.unpack("<i", f.read(4))[0]

        # 0x0020: IK
        if self.get_flag(PmxBoneFlag.IK):
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
        if settings.get("import.model.joint_name_conversion_with_english", True):
            # 英語名があればそれを使用
            if self.name_english and self.name_english != "":
                return self.name_english

            return self.name
        else:
            return self.name

    def get_flag(self, PmxBoneFlag) -> int:
        """
        ボーンのフラグを取得する。

        Returns:
            int: ボーンのフラグ。
        """
        return self.bone_flag & PmxBoneFlag
