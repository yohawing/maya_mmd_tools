class PmdRigidBody:
    """PMDファイルの剛体データを保持するクラス。"""
    def __init__(self):
        self.name = ''
        self.bone_index = -1
        self.collision_group = 0
        self.collision_mask = 0
        self.shape_type = 0 # 0: Sphere, 1: Box, 2: Capsule
        self.size = (0.0, 0.0, 0.0)
        self.position = (0.0, 0.0, 0.0)
        self.rotation = (0.0, 0.0, 0.0) # Euler angles
        self.mass = 0.0
        self.friction = 0.0
        self.elasticity = 0.0
        self.physics_mode = 0 # 0: Static, 1: Dynamic, 2: Dynamic (Bone Alignment)

    def parse(self, file_handle):
        """
        ファイルハンドルからPMD剛体データを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: 剛体データの解析に失敗した場合。
        """
        # TODO: PMD剛体データのバイナリ解析ロジックを実装する。
        # Name (20 bytes, Shift-JIS)
        # Bone Index (short)
        # Collision Group (byte), Collision Mask (short)
        # Shape Type (byte), Size (3 floats), Position (3 floats), Rotation (3 floats)
        # Mass (float), Friction (float), Elasticity (float), Physics Mode (byte)
        pass
