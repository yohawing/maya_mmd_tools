from ..mmd_parser import MMDParseException

class PmxRigidBody:
    """PMXファイルの剛体データを保持するクラス。"""
    def __init__(self):
        self.name_jp = ''
        self.name_en = ''
        self.bone_index = -1
        self.collision_group = 0
        self.collision_mask = 0
        self.shape_type = 0
        self.size = (0.0, 0.0, 0.0)
        self.position = (0.0, 0.0, 0.0)
        self.rotation = (0.0, 0.0, 0.0)
        self.mass = 0.0
        self.friction = 0.0
        self.elasticity = 0.0
        self.physics_mode = 0

    def parse(self, file_handle, header):
        """
        ファイルハンドルからPMX剛体データを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。
            header (PmxHeader): PMXヘッダ情報（ボーンインデックスサイズなどに使用）。

        Raises:
            MMDParseException: 剛体データの解析に失敗した場合。
        """
        # TODO: PMX剛体データのバイナリ解析ロジックを実装する。
        # Name JP (variable length string), Name EN (variable length string)
        # Bone Index (variable size)
        # Collision Group (1 byte), Collision Mask (2 bytes)
        # Shape Type (1 byte), Size (3 floats), Position (3 floats), Rotation (3 floats)
        # Mass (1 float), Friction (1 float), Elasticity (1 float), Physics Mode (1 byte)
        pass