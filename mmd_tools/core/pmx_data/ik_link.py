import struct

class PmxIKLink:
    """
    PMXファイルのIKリンクデータを保持するクラス。
    """
    def __init__(self, bone_index_size, encoding):
        self.bone_index_size = bone_index_size
        self.encoding = encoding
        self.ik_bone_index = -1
        self.angle_limit = 0
        self.limit_min = (0.0, 0.0, 0.0)
        self.limit_max = (0.0, 0.0, 0.0)

    def parse(self, f):
        """
        ファイルハンドルからPMX IKリンクデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        bone_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.bone_index_size]
        self.ik_bone_index = struct.unpack(bone_index_format, f.read(self.bone_index_size))[0]
        self.angle_limit = struct.unpack('<B', f.read(1))[0]
        if self.angle_limit == 1:
            self.limit_min = struct.unpack('<fff', f.read(12))
            self.limit_max = struct.unpack('<fff', f.read(12))
