class PmxIK:
    """PMXファイルのIKデータを保持するクラス。"""
    def __init__(self):
        self.target_bone_index = -1
        self.loop_count = 0
        self.rotation_limit = 0.0
        self.links = [] # List of PmxIKLink objects

    def parse(self, file_handle, header):
        """
        ファイルハンドルからPMX IKデータを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。
            header (PmxHeader): PMXヘッダ情報（ボーンインデックスサイズなどに使用）。

        Raises:
            MMDParseException: IKデータの解析に失敗した場合。
        """
        # TODO: PMX IKデータのバイナリ解析ロジックを実装する。
        # Target Bone Index (variable size)
        # Loop Count (int), Rotation Limit (float)
        # Number of IK Links (int)
        # For each IK Link: Bone Index (variable size), Has Angle Limit (1 byte), Limit Min (3 floats), Limit Max (3 floats)
        pass
