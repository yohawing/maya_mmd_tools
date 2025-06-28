import struct

class PmdBone:
    """PMDファイルのボーンデータを保持するクラス。"""
    def __init__(self):
        self.name = ''
        self.parent_bone_index = -1
        self.tail_pos_bone_index = -1
        self.bone_type = 0
        self.ik_parent_bone_index = -1
        self.head_position = (0.0, 0.0, 0.0)
        self.english_name = ''

    def parse(self, f):
        """
        ファイルハンドルからPMDボーンデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = f.read(20).decode('shift_jis').strip('\x00')
        self.parent_bone_index = struct.unpack('<h', f.read(2))[0]
        self.tail_pos_bone_index = struct.unpack('<h', f.read(2))[0]
        self.bone_type = struct.unpack('<B', f.read(1))[0]
        self.ik_parent_bone_index = struct.unpack('<h', f.read(2))[0]
        self.head_position = struct.unpack('<fff', f.read(12))

    def parse_english(self, f):
        """
        ファイルハンドルから英語のPMDボーン名を解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.english_name = f.read(20).decode('cp932').strip('\x00')
