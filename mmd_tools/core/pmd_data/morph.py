import struct

class PmdMorph:
    """PMDファイルのモーフデータを保持するクラス。"""
    def __init__(self):
        self.name = ''
        self.num_vertices = 0
        self.morph_type = 0 # 0: Base, 1: Eyebrow, 2: Eye, 3: Mouth, 4: Other
        self.vertices = [] # List of (vertex_index, position_offset_x, position_offset_y, position_offset_z)
        self.english_name = ''

    def parse(self, f):
        """
        ファイルハンドルからPMDモーフデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = f.read(20).decode('cp932').strip('\x00')
        self.num_vertices = struct.unpack('<I', f.read(4))[0]
        self.morph_type = struct.unpack('<B', f.read(1))[0]

        for _ in range(self.num_vertices):
            vertex_index = struct.unpack('<I', f.read(4))[0]
            position_offset = struct.unpack('<fff', f.read(12))
            self.vertices.append((vertex_index, position_offset))

    def parse_english(self, f):
        """
        ファイルハンドルから英語のPMDモーフ名を解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.english_name = f.read(20).decode('cp932').strip('\x00')
