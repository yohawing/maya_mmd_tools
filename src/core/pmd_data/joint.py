from ..mmd_parser import MMDParseException

class PmdJoint:
    """PMDファイルのジョイントデータを保持するクラス。"""
    def __init__(self):
        self.name = ''
        self.rigid_body_index_a = -1
        self.rigid_body_index_b = -1
        self.position = (0.0, 0.0, 0.0)
        self.rotation = (0.0, 0.0, 0.0) # Euler angles
        self.translation_limit_min = (0.0, 0.0, 0.0)
        self.translation_limit_max = (0.0, 0.0, 0.0)
        self.rotation_limit_min = (0.0, 0.0, 0.0)
        self.rotation_limit_max = (0.0, 0.0, 0.0)
        self.spring_translation = (0.0, 0.0, 0.0)
        self.spring_rotation = (0.0, 0.0, 0.0)

    def parse(self, file_handle):
        """
        ファイルハンドルからPMDジョイントデータを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: ジョイントデータの解析に失敗した場合。
        """
        # TODO: PMDジョイントデータのバイナリ解析ロジックを実装する。
        # Name (20 bytes, Shift-JIS)
        # Rigid Body Index A (int), Rigid Body Index B (int)
        # Position (3 floats), Rotation (3 floats)
        # Translation Limit Min (3 floats), Translation Limit Max (3 floats)
        # Rotation Limit Min (3 floats), Rotation Limit Max (3 floats)
        # Spring Translation (3 floats), Spring Rotation (3 floats)
        pass