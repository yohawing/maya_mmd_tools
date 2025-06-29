import struct
from mmd_tools.core import utils

class PmxRigidBody:
    """
    PMXファイルの剛体データを保持するクラス。
    """
    def __init__(self, bone_index_size, encoding):
        self.bone_index_size = bone_index_size
        self.encoding = encoding
        self.name = ''
        self.name_english = ''
        self.related_bone_index = -1
        self.group = 0
        self.collision_mask = 0
        self.shape_type = 0 # 0: Sphere, 1: Box, 2: Capsule
        self.size = (0.0, 0.0, 0.0)
        self.position = (0.0, 0.0, 0.0)
        self.rotation = (0.0, 0.0, 0.0) # Euler angles
        self.mass = 0.0
        self.velocity_attenuation = 0.0 # 移動減衰
        self.rotation_attenuation = 0.0 # 回転減衰
        self.elasticity = 0.0 # 反発力
        self.friction = 0.0 # 摩擦力
        self.physics_mode = 0 # 0: ボーン追従, 1: 物理演算, 2: 物理+位置合わせ

    def parse(self, f):
        """
        ファイルハンドルからPMX剛体データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = utils.parsePMXString(f, self.encoding)
        self.name_english = utils.parsePMXString(f, self.encoding)

        bone_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.bone_index_size]
        self.related_bone_index = struct.unpack(bone_index_format, f.read(self.bone_index_size))[0]

        self.group = struct.unpack('<B', f.read(1))[0]
        self.collision_mask = struct.unpack('<H', f.read(2))[0]
        self.shape_type = struct.unpack('<B', f.read(1))[0]
        self.size = struct.unpack('<fff', f.read(12))
        self.position = struct.unpack('<fff', f.read(12))
        self.rotation = struct.unpack('<fff', f.read(12))
        self.mass = struct.unpack('<f', f.read(4))[0]
        self.velocity_attenuation = struct.unpack('<f', f.read(4))[0]
        self.rotation_attenuation = struct.unpack('<f', f.read(4))[0]
        self.elasticity = struct.unpack('<f', f.read(4))[0]
        self.friction = struct.unpack('<f', f.read(4))[0]
        self.physics_mode = struct.unpack('<B', f.read(1))[0]
