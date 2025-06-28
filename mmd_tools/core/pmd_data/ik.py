import struct

class PmdIK:
    """PMDファイルのIKデータを保持するクラス。"""
    def __init__(self):
        self.ik_bone_index = -1
        self.target_bone_index = -1
        self.chain_length = 0
        self.iterations = 0
        self.rotation_constraint = 0.0
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
        print(f"  IK: ik_bone_index={self.ik_bone_index}, target_bone_index={self.target_bone_index}, chain_length={self.chain_length}")
        self.iterations = struct.unpack('<H', f.read(2))[0]
        self.rotation_constraint = struct.unpack('<f', f.read(4))[0]

        num_links = struct.unpack('<B', f.read(1))[0]
        print(f"  IK: num_links={num_links}")
        for i in range(num_links):
            link_bone_index = struct.unpack('<H', f.read(2))[0]
            self.link_bones.append(link_bone_index)
            print(f"    IK Link {i}: bone_index={link_bone_index}")
