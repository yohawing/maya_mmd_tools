from ..mmd_parser import MMDParseException

class PmdMaterial:
    """PMDファイルの材質データを保持するクラス。"""
    def __init__(self):
        self.diffuse = (0.0, 0.0, 0.0, 0.0) # RGBA
        self.specular_power = 0.0
        self.specular = (0.0, 0.0, 0.0) # RGB
        self.ambient = (0.0, 0.0, 0.0) # RGB
        self.toon_index = 0
        self.edge_flag = 0
        self.face_count = 0
        self.texture_file_name = ''

    def parse(self, file_handle):
        """
        ファイルハンドルからPMD材質データを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: 材質データの解析に失敗した場合。
        """
        # TODO: PMD材質データのバイナリ解析ロジックを実装する。
        # Diffuse (4 floats), Specular Power (1 float), Specular (3 floats), Ambient (3 floats)
        # Toon Index (1 byte), Edge Flag (1 byte), Face Count (1 int)
        # Texture File Name (20 bytes, Shift-JIS)
        pass