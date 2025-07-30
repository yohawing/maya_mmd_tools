import struct

from mmd_tools.core import utils


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
        self.name = utils.decodePMDString(f.read(20))
        self.num_vertices = struct.unpack('<I', f.read(4))[0]
        self.morph_type = struct.unpack('<B', f.read(1))[0]

        for i in range(self.num_vertices):
            vertex_index = struct.unpack('<I', f.read(4))[0]

            # ファイルの末尾に到達して12バイト読み取れない
            position_offset_data = f.read(12)
            if len(position_offset_data) < 12:
                raise ValueError(f"Invalid morph vertex data length on index {i}/{self.num_vertices}")
            position_offset = struct.unpack('<fff', position_offset_data)
            self.vertices.append((vertex_index, position_offset))

    def parse_english(self, f):
        """
        ファイルハンドルから英語のPMDモーフ名を解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.english_name = utils.decodeCp932String(f.read(20))

    def write(self, f):
        """
        PMDモーフデータをファイルハンドルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(utils.encodePMDString(self.name, 20))
        f.write(struct.pack('<I', self.num_vertices))
        f.write(struct.pack('<B', self.morph_type))

        for vertex_index, position_offset in self.vertices:
            f.write(struct.pack('<I', vertex_index))
            f.write(struct.pack('<fff', *position_offset))

    def write_english(self, f):
        """
        英語のPMDモーフ名をファイルハンドルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(utils.encodePMDString(self.english_name, 20))
