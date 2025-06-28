from ..exceptions import MMDParseException

class PmxIKLink:
    """PMXファイルのIKリンクデータを保持するクラス。"""
    def __init__(self):
        self.bone_index = -1
        self.has_angle_limit = False
        self.limit_min = (0.0, 0.0, 0.0)
        self.limit_max = (0.0, 0.0, 0.0)

    def parse(self, file_handle, header):
        """
        ファイルハンドルからPMX IKリンクデータを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。
            header (PmxHeader): PMXヘッダ情報（ボーンインデックスサイズなどに使用）。

        Raises:
            MMDParseException: IKリンクデータの解析に失敗した場合。
        """
        # TODO: PMX IKリンクデータのバイナリ解析ロジックを実装する。
        # Bone Index (variable size)
        # Has Angle Limit (1 byte)
        # If Has Angle Limit: Limit Min (3 floats), Limit Max (3 floats)
        pass