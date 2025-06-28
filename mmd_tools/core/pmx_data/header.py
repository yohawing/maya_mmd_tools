from ..exceptions import MMDParseException

class PmxHeader:
    """PMXファイルのヘッダ情報を保持するクラス。"""
    def __init__(self):
        self.magic = b''
        self.version = 0.0
        self.global_flags = 0
        self.text_encoding = 0
        self.num_uv_sets = 0
        self.vertex_index_size = 0
        self.texture_index_size = 0
        self.material_index_size = 0
        self.bone_index_size = 0
        self.morph_index_size = 0
        self.rigid_body_index_size = 0
        self.model_name_jp = ''
        self.model_name_en = ''
        self.comment_jp = ''
        self.comment_en = ''

    def parse(self, file_handle):
        """
        ファイルハンドルからPMXヘッダを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: ヘッダの解析に失敗した場合。
        """
        # TODO: PMXヘッダのバイナリ解析ロジックを実装する。
        # magic (4 bytes), version (4 bytes float)
        # global_flags (1 byte)
        # text_encoding (1 byte), num_uv_sets (1 byte), vertex_index_size (1 byte), etc.
        # model_name_jp (variable length string), model_name_en (variable length string)
        # comment_jp (variable length string), comment_en (variable length string)
        pass