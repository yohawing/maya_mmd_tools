class PmdMorph:
    """PMDファイルのモーフデータを保持するクラス。"""
    def __init__(self):
        self.name = ''
        self.num_vertices = 0
        self.morph_type = 0 # 0: Base, 1: Eyebrow, 2: Eye, 3: Mouth, 4: Other
        self.vertices = [] # List of (vertex_index, position_offset_x, position_offset_y, position_offset_z)

    def parse(self, file_handle):
        """
        ファイルハンドルからPMDモーフデータを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: モーフデータの解析に失敗した場合。
        """
        # TODO: PMDモーフデータのバイナリ解析ロジックを実装する。
        # Name (20 bytes, Shift-JIS)
        # Number of Vertices (int)
        # For each vertex: Vertex Index (int), Position Offset (3 floats)
        pass
