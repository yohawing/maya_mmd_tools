import enum
import struct
from enum import Enum

from mmd_tools.core import utils


class PmdBoneType(Enum):
    """PMDファイルのボーンタイプを定義する列挙型。"""

    ROTATE = 0  # 0:回転
    ROTATE_AND_MOVE = 1  # 1:回転と移動
    IK = 2  # 2:IK
    UNKNOWN = 3  # 3:不明
    IK_AFFECTED = 4  # 4:IK影響下
    ROTATE_AFFECTED = 5  # 5:回転影響下
    IK_CONNECTION = 6  # 6:IK接続先
    HIDDEN = 7  # 7:非表示
    TWIST = 8  # 8:捻り
    ROTATE_MOTION = 9  # 9:回転運動


class PmdBone:
    """PMDファイルのボーンデータを保持するクラス。"""

    name: str
    name_english: str
    parent_bone_index: int
    tail_pos_bone_index: int
    bone_type: PmdBoneType
    ik_parent_bone_index: int
    position: tuple[float, float, float]

    def __init__(self):
        self.name = ""
        self.name_english = ""
        self.parent_bone_index = -1
        self.tail_pos_bone_index = -1
        self.bone_type = PmdBoneType.ROTATE  # 初期値は0（回転ボーン）
        self.ik_parent_bone_index = -1
        self.position = (0.0, 0.0, 0.0)

    def parse(self, f):
        """
        ファイルハンドルからPMDボーンデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = utils.decodePMDString(f.read(20))
        self.parent_bone_index = struct.unpack("<H", f.read(2))[0]
        if self.parent_bone_index == 0xFFFF:
            self.parent_bone_index = -1
        self.tail_pos_bone_index = struct.unpack("<H", f.read(2))[0]
        bone_type = struct.unpack("<B", f.read(1))[0]
        self.bone_type = (
            PmdBoneType(bone_type)
            if bone_type in PmdBoneType._value2member_map_
            else PmdBoneType.UNKNOWN
        )
        self.ik_parent_bone_index = struct.unpack("<H", f.read(2))[0]
        self.position = struct.unpack("<fff", f.read(12))

    def parse_english(self, f):
        """
        ファイルハンドルから英語のPMDボーン名を解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name_english = utils.decodePMDString(f.read(20))

    def get_name(self):
        """
        ボーンの名前を取得する。英語名が設定されていればそれを返し、なければ日本語名を返す。

        Returns:
            str: ボーンの名前。
        """
        if self.name_english and self.name_english != "":
            return self.name_english

        return self.name
