import struct


class PmxJoint:
    """
    PMXファイルのJointデータを保持するクラス。
    """
    def __init__(self, rigid_body_index_size, encoding):
        self.rigid_body_index_size = rigid_body_index_size
        self.encoding = encoding
        self.name = ''
        self.name_english = ''
        self.joint_type = 0
        self.rigid_body_a_index = -1
        self.rigid_body_b_index = -1
        self.position = (0.0, 0.0, 0.0)
        self.rotation = (0.0, 0.0, 0.0)
        self.translation_limit_min = (0.0, 0.0, 0.0)
        self.translation_limit_max = (0.0, 0.0, 0.0)
        self.rotation_limit_min = (0.0, 0.0, 0.0)
        self.rotation_limit_max = (0.0, 0.0, 0.0)
        self.spring_translation = (0.0, 0.0, 0.0)
        self.spring_rotation = (0.0, 0.0, 0.0)

    def parse(self, f):
        """
        ファイルハンドルからPMX Jointデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        name_length = struct.unpack('<I', f.read(4))[0]
        self.name = f.read(name_length).decode(self.encoding)

        name_english_length = struct.unpack('<I', f.read(4))[0]
        self.name_english = f.read(name_english_length).decode(self.encoding)

        self.joint_type = struct.unpack('<B', f.read(1))[0]

        rigid_body_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.rigid_body_index_size]
        self.rigid_body_a_index = struct.unpack(rigid_body_index_format, f.read(self.rigid_body_index_size))[0]
        self.rigid_body_b_index = struct.unpack(rigid_body_index_format, f.read(self.rigid_body_index_size))[0]

        self.position = struct.unpack('<fff', f.read(12))
        self.rotation = struct.unpack('<fff', f.read(12))
        self.translation_limit_min = struct.unpack('<fff', f.read(12))
        self.translation_limit_max = struct.unpack('<fff', f.read(12))
        self.rotation_limit_min = struct.unpack('<fff', f.read(12))
        self.rotation_limit_max = struct.unpack('<fff', f.read(12))
        self.spring_translation = struct.unpack('<fff', f.read(12))
        self.spring_rotation = struct.unpack('<fff', f.read(12))
