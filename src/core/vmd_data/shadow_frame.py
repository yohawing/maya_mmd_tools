class VmdShadowFrame:
    """VMDファイルのセルフシャドウフレームデータを保持するクラス。"""
    def __init__(self):
        self.frame_number = 0
        self.mode = 0
        self.distance = 0.0

    def parse(self, file_handle):
        """
        ファイルハンドルからVMDセルフシャドウフレームデータを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: セルフシャドウフレームデータの解析に失敗した場合。
        """
        # TODO: VMDセルフシャドウフレームデータのバイナリ解析ロジックを実装する。
        # Frame Number (int)
        # Mode (1 byte)
        # Distance (float)
        pass
