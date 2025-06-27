from ..mmd_parser import MMDParseException

class PmdBone:
    """PMDファイルのボーンデータを保持するクラス。"""
    def __init__(self):
        self.name = ''
        self.parent_bone_index = -1
        self.tail_pos_bone_index = -1
        self.bone_type = 0
        self.ik_parent_bone_index = -1
        self.head_position = (0.0, 0.0, 0.0)

    def parse(self, file_handle):
        """
        ファイルハンドルからPMDボーンデータを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: ボーンデータの解析に失敗した場合。
        """
        # TODO: PMDボーンデータのバイナリ解析ロジックを実装する。
        # Name (20 bytes, Shift-JIS)
        # Parent Bone Index (short)
        # Tail Pos Bone Index (short)
        # Bone Type (byte)
        # IK Parent Bone Index (short)
        # Head Position (3 floats)
        pass