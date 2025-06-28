import struct

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

    def parse(self, f):
        """
        ファイルハンドルからPMD剛体データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = f.read(20).decode('cp932').strip('\x00')
        self.bone_index = struct.unpack('<h', f.read(2))[0]
        self.collision_group = struct.unpack('<B', f.read(1))[0]
        self.collision_mask = struct.unpack('<H', f.read(2))[0]
        self.shape_type = struct.unpack('<B', f.read(1))[0]
        self.size = struct.unpack('<fff', f.read(12))
        self.position = struct.unpack('<fff', f.read(12))
        self.rotation = struct.unpack('<fff', f.read(12))
        self.mass = struct.unpack('<f', f.read(4))[0]
        self.friction = struct.unpack('<f', f.read(4))[0]
        self.elasticity = struct.unpack('<f', f.read(4))[0]
        self.physics_mode = struct.unpack('<B', f.read(1))[0]
