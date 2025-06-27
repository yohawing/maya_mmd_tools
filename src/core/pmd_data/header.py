class PmdHeader:
    """PMDファイルのヘッダ情報を保持するクラス。"""
    def __init__(self):
        self.magic = b''
        self.version = 0.0
        self.model_name = ''
        self.comment = ''

    def parse(self, file_handle):
        """
        ファイルハンドルからPMDヘッダを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: ヘッダの解析に失敗した場合。
        """
        # TODO: PMDヘッダのバイナリ解析ロジックを実装する。
        # magic (3 bytes), version (4 bytes float)
        # model_name (20 bytes, Shift-JIS)
        # comment (256 bytes, Shift-JIS)
        pass
