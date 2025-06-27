from ..mmd_parser import MMDParseException

class PmdIK:
    """PMDファイルのIKデータを保持するクラス。"""
    def __init__(self):
        self.ik_bone_index = -1
        self.target_bone_index = -1
        self.chain_length = 0
        self.iterations = 0
        self.rotation_constraint = 0.0
        self.link_bones = [] # List of (bone_index, limit_angle_flag, limit_min, limit_max)

    def parse(self, file_handle):
        """
        ファイルハンドルからPMD IKデータを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: IKデータの解析に失敗した場合。
        """
        # TODO: PMD IKデータのバイナリ解析ロジックを実装する。
        # IK Bone Index (short)
        # Target Bone Index (short)
        # Chain Length (byte)
        # Iterations (short)
        # Rotation Constraint (float)
        # Number of IK Links (byte)
        # For each IK Link: Bone Index (short), Limit Angle Flag (byte), Limit Min (3 floats), Limit Max (3 floats)
        pass