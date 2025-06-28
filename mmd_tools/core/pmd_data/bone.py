import struct
from mmd_tools.core import utils

class PmdBone:
    """PMDファイルのボーンデータを保持するクラス。"""
    def __init__(self):
        self.name = ''
        self.english_name = ''
        self.parent_bone_index = -1
        self.tail_pos_bone_index = -1
        self.bone_type = 0
        self.ik_parent_bone_index = -1
        self.head_position = (0.0, 0.0, 0.0)
  

    def parse(self, f):
        """
        ファイルハンドルからPMDボーンデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = utils.decodePMDString(f.read(20))
        self.parent_bone_index = struct.unpack('<H', f.read(2))[0]
        self.tail_pos_bone_index = struct.unpack('<H', f.read(2))[0]
        self.bone_type = struct.unpack('<B', f.read(1))[0]
        self.ik_parent_bone_index = struct.unpack('<H', f.read(2))[0]
        self.head_position = struct.unpack('<fff', f.read(12))

    def parse_english(self, f):
        """
        ファイルハンドルから英語のPMDボーン名を解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.english_name = utils.decodePMDString(f.read(20))
