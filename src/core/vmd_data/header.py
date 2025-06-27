from ..mmd_parser import MMDParseException

class VmdHeader:
    """VMDファイルのヘッダ情報を保持するクラス。"""
    def __init__(self):
        self.magic = b''
        self.version = 0.0
        self.model_name = ''

    def parse(self, file_handle):
        """
        ファイルハンドルからVMDヘッダを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: ヘッダの解析に失敗した場合。
        """
        # TODO: VMDヘッダのバイナリ解析ロジックを実装する。
        # magic (30 bytes), version (4 bytes float)
        # model_name (20 bytes, Shift-JIS)
        pass