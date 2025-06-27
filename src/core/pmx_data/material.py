from ..mmd_parser import MMDParseException

class PmxMaterial:
    """PMXファイルの材質データを保持するクラス。"""
    def __init__(self):
        self.name_jp = ''
        self.name_en = ''
        self.diffuse = (0.0, 0.0, 0.0, 0.0)
        self.specular = (0.0, 0.0, 0.0)
        self.specular_power = 0.0
        self.ambient = (0.0, 0.0, 0.0)
        self.draw_flags = 0
        self.edge_color = (0.0, 0.0, 0.0, 0.0)
        self.edge_size = 0.0
        self.texture_index = -1
        self.sphere_texture_index = -1
        self.sphere_mode = 0
        self.toon_tag = 0 # 0: toon_texture_index, 1: toon_color
        self.toon_texture_index = -1
        self.comment = ''
        self.face_count = 0

    def parse(self, file_handle, header):
        """
        ファイルハンドルからPMX材質データを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。
            header (PmxHeader): PMXヘッダ情報（テクスチャインデックスサイズなどに使用）。

        Raises:
            MMDParseException: 材質データの解析に失敗した場合。
        """
        # TODO: PMX材質データのバイナリ解析ロジックを実装する。
        # Name JP (variable length string), Name EN (variable length string)
        # Diffuse (4 floats), Specular (3 floats), Specular Power (1 float), Ambient (3 floats)
        # Draw Flags (1 byte), Edge Color (4 floats), Edge Size (1 float)
        # Texture Index (variable size), Sphere Texture Index (variable size), Sphere Mode (1 byte)
        # Toon Tag (1 byte), Toon Texture Index (variable size)
        # Comment (variable length string)
        # Face Count (int)
        pass