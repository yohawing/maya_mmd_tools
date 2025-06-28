from ..exceptions import MMDParseException

class VmdLightFrame:
    """VMDファイルの照明フレームデータを保持するクラス。"""
    def __init__(self):
        self.frame_number = 0
        self.color = (0.0, 0.0, 0.0) # RGB
        self.position = (0.0, 0.0, 0.0)

    def parse(self, file_handle):
        """
        ファイルハンドルからVMD照明フレームデータを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: 照明フレームデータの解析に失敗した場合。
        """
        # TODO: VMD照明フレームデータのバイナリ解析ロジックを実装する。
        # Frame Number (int)
        # Color (3 floats)
        # Position (3 floats)
        pass