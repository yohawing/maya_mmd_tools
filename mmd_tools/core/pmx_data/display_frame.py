import struct
from typing import BinaryIO

from mmd_tools.core import utils


class PmxDisplayFrame:
    """
    PMXファイルの表示枠データを保持するクラス。
    """
    def __init__(self, bone_index_size: int, morph_index_size: int, encoding_flag: int = 1):
        self.bone_index_size = bone_index_size
        self.morph_index_size = morph_index_size
        self.encoding_flag = encoding_flag  # 0=UTF-16LE, 1=UTF-8
        self.encoding = utils.get_pmx_encoding_string(encoding_flag)  # "utf-16-le" or "utf-8"
        self.name = ''
        self.name_english = ''
        self.special_flag = 0
        self.elements = []

    def parse(self, f: BinaryIO) -> None:
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

    def write(self, f: BinaryIO) -> None:
        """
        PMX表示枠データをファイルハンドルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(utils.encodePMXString(self.name, self.encoding))
        f.write(utils.encodePMXString(self.name_english, self.encoding))

        f.write(struct.pack('<B', self.special_flag))

        f.write(struct.pack('<I', len(self.elements)))

        bone_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.bone_index_size]
        morph_index_format = {1: '<b', 2: '<h', 4: '<i'}[self.morph_index_size]

        for element in self.elements:
            f.write(struct.pack('<B', element['type']))
            if element['type'] == 0:  # Bone
                f.write(struct.pack(bone_index_format, element['index']))
            elif element['type'] == 1:  # Morph
                f.write(struct.pack(morph_index_format, element['index']))
            else:
                raise ValueError(f"Unknown display frame element type: {element['type']}")
