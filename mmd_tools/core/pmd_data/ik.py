import struct


class PmdIK:
    """PMDファイルのIKデータを保持するクラス。"""
    def __init__(self):
        self.ik_bone_index = -1
        self.target_bone_index = -1
        self.chain_length = 0
        self.iterations = 0
        self.control_weight = 0.0
        self.link_bones = [] # List of (bone_index, limit_angle_flag, limit_min, limit_max)

    def parse(self, f):
        """
        ファイルハンドルからPMD IKデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.ik_bone_index = struct.unpack('<H', f.read(2))[0]
        self.target_bone_index = struct.unpack('<H', f.read(2))[0]
        self.chain_length = struct.unpack('<B', f.read(1))[0]
        self.iterations = struct.unpack('<H', f.read(2))[0]
        self.control_weight = struct.unpack('<f', f.read(4))[0]

        # num_links = struct.unpack('<B', f.read(1))[0]
        for i in range(self.chain_length):
            link_bone_index = struct.unpack('<H', f.read(2))[0]
            self.link_bones.append(link_bone_index)

    def write(self, f):
        """
        PMD IKデータをファイルハンドルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(struct.pack('<H', self.ik_bone_index))
        f.write(struct.pack('<H', self.target_bone_index))
        f.write(struct.pack('<B', self.chain_length))
        f.write(struct.pack('<H', self.iterations))
        f.write(struct.pack('<f', self.control_weight))

        # Link bones
        for link_bone_index in self.link_bones:
            f.write(struct.pack('<H', link_bone_index))
