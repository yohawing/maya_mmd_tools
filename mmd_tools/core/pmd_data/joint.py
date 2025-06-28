import struct

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
        self.name = f.read(20).decode('cp932').strip('\x00')
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
