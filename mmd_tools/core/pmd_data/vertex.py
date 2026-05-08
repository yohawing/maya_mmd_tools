import struct


class PmdVertex:
    """PMDファイルの頂点データを保持するクラス。"""

    def __init__(self):
        self.position = (0.0, 0.0, 0.0)
        self.normal = (0.0, 0.0, 0.0)
        self.uv = (0.0, 0.0)
        self.bone_indices = (0, 0)
        self.bone_weight = 0
        self.edge_flag = 0

    def parse(self, f):
        """
        ファイルハンドルからPMD頂点データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.position = struct.unpack("<fff", f.read(12))
        self.normal = struct.unpack("<fff", f.read(12))
        self.uv = struct.unpack("<ff", f.read(8))
        self.bone_indices = struct.unpack("<HH", f.read(4))
        self.bone_weight = struct.unpack("<B", f.read(1))[0]
        self.edge_flag = struct.unpack("<B", f.read(1))[0]

    def write(self, f):
        """
        PMD頂点データをバイナリファイルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(struct.pack("<fff", *self.position))
        f.write(struct.pack("<fff", *self.normal))
        f.write(struct.pack("<ff", *self.uv))
        f.write(struct.pack("<HH", *self.bone_indices))
        f.write(struct.pack("<B", self.bone_weight))
        f.write(struct.pack("<B", self.edge_flag))
