import struct

from mmd_tools.core import utils


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

    def parse(self, f):
        """
        ファイルハンドルからPMDジョイントデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = utils.decodePMDString(f.read(20))
        self.rigid_body_index_a = struct.unpack('<I', f.read(4))[0]
        self.rigid_body_index_b = struct.unpack('<I', f.read(4))[0]
        self.position = struct.unpack('<fff', f.read(12))
        self.rotation = struct.unpack('<fff', f.read(12))
        self.translation_limit_min = struct.unpack('<fff', f.read(12))
        self.translation_limit_max = struct.unpack('<fff', f.read(12))
        self.rotation_limit_min = struct.unpack('<fff', f.read(12))
        self.rotation_limit_max = struct.unpack('<fff', f.read(12))
        self.spring_translation = struct.unpack('<fff', f.read(12))
        self.spring_rotation = struct.unpack('<fff', f.read(12))

    def write(self, f):
        """
        PMDジョイントデータをファイルハンドルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(utils.encodePMDString(self.name, 20))
        f.write(struct.pack('<I', self.rigid_body_index_a))
        f.write(struct.pack('<I', self.rigid_body_index_b))
        f.write(struct.pack('<fff', *self.position))
        f.write(struct.pack('<fff', *self.rotation))
        f.write(struct.pack('<fff', *self.translation_limit_min))
        f.write(struct.pack('<fff', *self.translation_limit_max))
        f.write(struct.pack('<fff', *self.rotation_limit_min))
        f.write(struct.pack('<fff', *self.rotation_limit_max))
        f.write(struct.pack('<fff', *self.spring_translation))
        f.write(struct.pack('<fff', *self.spring_rotation))
