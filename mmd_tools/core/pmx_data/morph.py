import struct
from mmd_tools.core import utils

class PmxMorph:
    """
    PMXファイルのモーフデータを保持するクラス。
    """
    def __init__(self, vertex_index_size, material_index_size, bone_index_size, morph_index_size, rigid_body_index_size, encoding):
        self.vertex_index_size = vertex_index_size
        self.material_index_size = material_index_size
        self.bone_index_size = bone_index_size
        self.morph_index_size = morph_index_size
        self.rigid_body_index_size = rigid_body_index_size
        self.encoding = encoding

        self.name = ''
        self.name_english = ''
        self.panel = 0
        self.morph_type = 0
        self.offset_count = 0
        self.offsets = []

    def parse(self, f):
        """
        ファイルハンドルからPMXモーフデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        name_length = struct.unpack('<I', f.read(4))[0]
        self.name = f.read(name_length).decode(self.encoding)

        name_english_length = struct.unpack('<I', f.read(4))[0]
        self.name_english = f.read(name_english_length).decode(self.encoding)

        self.panel = struct.unpack('<B', f.read(1))[0]
        self.morph_type = struct.unpack('<B', f.read(1))[0]
        self.offset_count = struct.unpack('<I', f.read(4))[0]

        vertex_index_format = {1: '<B', 2: '<H', 4: '<I'}[self.vertex_index_size]
        material_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.material_index_size]
        bone_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.bone_index_size]
        morph_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.morph_index_size]
        rigid_body_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.rigid_body_index_size]

        for _ in range(self.offset_count):
            offset_data = {}
            if self.morph_type == 0: # Group Morph
                offset_data['morph_index'] = struct.unpack(morph_index_format, f.read(self.morph_index_size))[0]
                offset_data['morph_rate'] = struct.unpack('<f', f.read(4))[0]
            elif self.morph_type == 1: # Vertex Morph
                offset_data['vertex_index'] = struct.unpack(vertex_index_format, f.read(self.vertex_index_size))[0]
                offset_data['position_offset'] = struct.unpack('<fff', f.read(12))
            elif self.morph_type == 2: # Bone Morph
                offset_data['bone_index'] = struct.unpack(bone_index_format, f.read(self.bone_index_size))[0]
                offset_data['translation'] = struct.unpack('<fff', f.read(12))
                offset_data['rotation'] = struct.unpack('<ffff', f.read(16))
            elif self.morph_type >= 3 and self.morph_type <= 7: # UV Morph (UV, Additional UV1-4)
                offset_data['vertex_index'] = struct.unpack(vertex_index_format, f.read(self.vertex_index_size))[0]
                offset_data['uv_offset'] = struct.unpack('<ffff', f.read(16))
            elif self.morph_type == 8: # Material Morph
                offset_data['material_index'] = struct.unpack(material_index_format, f.read(self.material_index_size))[0]
                offset_data['operation_type'] = struct.unpack('<B', f.read(1))[0]
                offset_data['diffuse'] = struct.unpack('<ffff', f.read(16))
                offset_data['specular'] = struct.unpack('<fff', f.read(12))
                offset_data['specular_coefficient'] = struct.unpack('<f', f.read(4))[0]
                offset_data['ambient'] = struct.unpack('<fff', f.read(12))
                offset_data['edge_color'] = struct.unpack('<ffff', f.read(16))
                offset_data['edge_size'] = struct.unpack('<f', f.read(4))[0]
                offset_data['texture_index'] = struct.unpack(material_index_format, f.read(self.material_index_size))[0]
                offset_data['sphere_texture_index'] = struct.unpack(material_index_format, f.read(self.material_index_size))[0]
                offset_data['sphere_mode'] = struct.unpack('<B', f.read(1))[0]
                offset_data['toon_texture_index'] = struct.unpack(material_index_format, f.read(self.material_index_size))[0]
            elif self.morph_type == 9: # Flip Morph (PMX 2.1)
                offset_data['morph_index'] = struct.unpack(morph_index_format, f.read(self.morph_index_size))[0]
                offset_data['morph_rate'] = struct.unpack('<f', f.read(4))[0]
            elif self.morph_type == 10: # Impulse Morph (PMX 2.1)
                offset_data['rigid_body_index'] = struct.unpack(rigid_body_index_format, f.read(self.rigid_body_index_size))[0]
                offset_data['is_local'] = struct.unpack('<B', f.read(1))[0]
                offset_data['velocity'] = struct.unpack('<fff', f.read(12))
                offset_data['rotation_torque'] = struct.unpack('<fff', f.read(12))
            else:
                raise ValueError(f"Unknown morph type: {self.morph_type}")
            self.offsets.append(offset_data)
