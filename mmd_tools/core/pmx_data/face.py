import struct


class PmxFace:
    """
    PMXファイルの面データを保持するクラス。
    """
    def __init__(self, vertex_index_size):
        self.vertex_index_size = vertex_index_size
        self.indices = (0, 0, 0)

    def parse(self, f):
        """
        ファイルハンドルからPMX面データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        if self.vertex_index_size == 1:
            self.indices = struct.unpack('<BBB', f.read(3))
        elif self.vertex_index_size == 2:
            self.indices = struct.unpack('<HHH', f.read(6))
        elif self.vertex_index_size == 4:
            self.indices = struct.unpack('<III', f.read(12))
        else:
            raise ValueError(f"Unsupported vertex index size: {self.vertex_index_size}")
