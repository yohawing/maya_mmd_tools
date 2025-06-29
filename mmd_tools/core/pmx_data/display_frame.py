import struct
from mmd_tools.core import utils

class PmxDisplayFrame:
    """
    PMXファイルの表示枠データを保持するクラス。
    """
    def __init__(self, bone_index_size, morph_index_size, encoding):
        self.bone_index_size = bone_index_size
        self.morph_index_size = morph_index_size
        self.encoding = encoding
        self.name = ''
        self.name_english = ''
        self.special_flag = 0
        self.elements = []

    def parse(self, f):
        """
        ファイルハンドルからPMX表示枠データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = utils.parsePMXString(f, self.encoding)
        self.name_english = utils.parsePMXString(f, self.encoding)

        self.special_flag = struct.unpack('<B', f.read(1))[0]

        element_count = struct.unpack('<I', f.read(4))[0]

        bone_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.bone_index_size]
        morph_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.morph_index_size]

        for _ in range(element_count):
            element_type = struct.unpack('<B', f.read(1))[0]
            index = -1
            if element_type == 0: # Bone
                index = struct.unpack(bone_index_format, f.read(self.bone_index_size))[0]
            elif element_type == 1: # Morph
                index = struct.unpack(morph_index_format, f.read(self.morph_index_size))[0]
            else:
                raise ValueError(f"Unknown display frame element type: {element_type}")
            self.elements.append({'type': element_type, 'index': index})
