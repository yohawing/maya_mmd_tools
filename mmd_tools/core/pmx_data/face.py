from ..exceptions import MMDParseException

class PmxFace:
    """PMXファイルの面データを保持するクラス。"""
    def __init__(self):
        self.indices = (0, 0, 0)

    def parse(self, file_handle, header):
        """
        ファイルハンドルからPMX面データを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。
            header (PmxHeader): PMXヘッダ情報（頂点インデックスサイズなどに使用）。

        Raises:
            MMDParseException: 面データの解析に失敗した場合。
        """
        # TODO: PMX面データのバイナリ解析ロジックを実装する。
        # Indices (3 * variable size, based on header.vertex_index_size)
        pass