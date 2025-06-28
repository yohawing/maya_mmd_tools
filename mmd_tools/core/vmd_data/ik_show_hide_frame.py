from ..exceptions import MMDParseException

class VmdIKShowHideFrame:
    """VMDファイルのIK/表示フレームデータを保持するクラス。"""
    def __init__(self):
        self.frame_number = 0
        self.show_ik = 0 # 0: Hide, 1: Show
        self.ik_bones = [] # List of (bone_name, show_flag)

    def parse(self, file_handle):
        """
        ファイルハンドルからVMD IK/表示フレームデータを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: IK/表示フレームデータの解析に失敗した場合。
        """
        # TODO: VMD IK/表示フレームデータのバイナリ解析ロジックを実装する。
        # Frame Number (int)
        # Show IK (1 byte)
        # Number of IK Bones (int)
        # For each IK Bone: Bone Name (15 bytes, Shift-JIS), Show Flag (1 byte)
        pass