class PmdVertex:
    """PMDファイルの頂点データを保持するクラス。"""
    def __init__(self):
        self.position = (0.0, 0.0, 0.0)
        self.normal = (0.0, 0.0, 0.0)
        self.uv = (0.0, 0.0)
        self.bone_indices = (0, 0)
        self.bone_weight = 0
        self.edge_flag = 0

    def parse(self, file_handle):
        """
        ファイルハンドルからPMD頂点データを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: 頂点データの解析に失敗した場合。
        """
        # TODO: PMD頂点データのバイナリ解析ロジックを実装する。
        # Position (3 floats), Normal (3 floats), UV (2 floats)
        # Bone indices (2 shorts), Bone weight (1 byte), Edge flag (1 byte)
        pass
