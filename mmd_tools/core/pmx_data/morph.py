import struct
import enum
from mmd_tools.core import utils

class PmxMorphType(enum.IntEnum):
    GroupMorph = 0
    VertexMorph = 1
    BoneMorph = 2
    UVMorph = 3
    AdditionalUVMorph1 = 4
    AdditionalUVMorph2 = 5
    AdditionalUVMorph3 = 6
    AdditionalUVMorph4 = 7
    MaterialMorph = 8
    FlipMorph = 9 # PMX 2.1以降
    ImpulseMorph = 10 # PMX 2.1以降

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

        self.type_formats = {
            "vertex": {1: '<B', 2: '<H', 4: '<I'}[vertex_index_size],
            "material": {1: '<b', 2: '<h', 4: '<i'}[material_index_size],
            "bone": {1: '<b', 2: '<h', 4: '<i'}[bone_index_size],
            "morph": {1: '<b', 2: '<h', 4: '<i'}[morph_index_size],
            "rigid_body": {1: '<b', 2: '<h', 4: '<i'}[rigid_body_index_size]
        }

        self.encoding = encoding

        self.name = ''
        self.name_english = ''
        self.panel = 0 # 操作パネル (1:眉, 2:目, 3:口, 4:その他, 0:システム予約)
        self.morph_type = PmxMorphType.GroupMorph # モーフ種類
        self.offset_count = 0
        self.offsets = []

    def parse(self, f):
        """
        ファイルハンドルからPMXモーフデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = utils.parsePMXString(f, self.encoding)  # Use utility function for PMX string parsing
        self.name_english = utils.parsePMXString(f, self.encoding)

        self.panel = struct.unpack('<B', f.read(1))[0]
        self.morph_type = PmxMorphType(struct.unpack('<B', f.read(1))[0]) # Convert to Enum
        self.offset_count = struct.unpack('<I', f.read(4))[0]

        for _ in range(self.offset_count):
            offset_data = {}

            if self.morph_type == PmxMorphType.GroupMorph:
                offset_data['morph_index'] = struct.unpack(self.type_formats["morph"], f.read(self.morph_index_size))[0]
                offset_data['morph_rate'] = struct.unpack('<f', f.read(4))[0]
            elif self.morph_type == PmxMorphType.VertexMorph:
                offset_data['vertex_index'] = struct.unpack(self.type_formats["vertex"], f.read(self.vertex_index_size))[0]
                offset_data['position_offset'] = struct.unpack('<fff', f.read(12))
            elif self.morph_type == PmxMorphType.BoneMorph:
                offset_data['bone_index'] = struct.unpack(self.type_formats["bone"], f.read(self.bone_index_size))[0]
                offset_data['translation'] = struct.unpack('<fff', f.read(12))
                offset_data['rotation'] = struct.unpack('<ffff', f.read(16))
            elif PmxMorphType.UVMorph <= self.morph_type <= PmxMorphType.AdditionalUVMorph4:
                offset_data['vertex_index'] = struct.unpack(self.type_formats["vertex"], f.read(self.vertex_index_size))[0]
                offset_data['uv_offset'] = struct.unpack('<ffff', f.read(16))
            elif self.morph_type == PmxMorphType.MaterialMorph:
                offset_data['material_index'] = struct.unpack(self.type_formats["material"], f.read(self.material_index_size))[0]
                offset_data['operation_type'] = struct.unpack('<B', f.read(1))[0]
                offset_data['diffuse'] = struct.unpack('<ffff', f.read(16))
                offset_data['specular'] = struct.unpack('<fff', f.read(12))
                offset_data['specular_coefficient'] = struct.unpack('<f', f.read(4))[0]
                offset_data['ambient'] = struct.unpack('<fff', f.read(12))
                offset_data['edge_color'] = struct.unpack('<ffff', f.read(16))
                offset_data['edge_size'] = struct.unpack('<f', f.read(4))[0]
                offset_data['texture_factor'] = struct.unpack('<ffff', f.read(16))
                offset_data['sphere_texture_factor'] = struct.unpack('<ffff', f.read(16))
                offset_data['toon_texture_factor'] = struct.unpack('<ffff', f.read(16))
            elif self.morph_type == PmxMorphType.FlipMorph:
                offset_data['morph_index'] = struct.unpack(self.type_formats["morph"], f.read(self.morph_index_size))[0]
                offset_data['flip_rate'] = struct.unpack('<f', f.read(4))[0]
            elif self.morph_type == PmxMorphType.ImpulseMorph:
                offset_data['rigid_body_index'] = struct.unpack(self.type_formats["rigid_body"], f.read(self.rigid_body_index_size))[0]
                offset_data['impulse'] = struct.unpack('<fff', f.read(12))
                offset_data['torque'] = struct.unpack('<fff', f.read(12))
            else:
                raise ValueError(f"Unknown morph type: {self.morph_type}")
            self.offsets.append(offset_data)
