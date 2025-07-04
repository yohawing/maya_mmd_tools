import struct
from mmd_tools.core import utils

class PmdMaterial:
    """PMDファイルの材質データを保持するクラス。"""
    def __init__(self):
        self.name = "PmdDefaultMaterial"
        self.diffuse = (0.0, 0.0, 0.0, 0.0) # RGBA
        self.specular_power = 0.0
        self.specular = (0.0, 0.0, 0.0) # RGB
        self.ambient = (0.0, 0.0, 0.0) # RGB
        self.toon_index = 0
        self.edge_flag = 0
        self.face_count = 0
        self.texture_file_name = ''

    def parse(self, f):
        """
        ファイルハンドルからPMD材質データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.diffuse = struct.unpack('<ffff', f.read(16))
        self.specular_power = struct.unpack('<f', f.read(4))[0]
        self.specular = struct.unpack('<fff', f.read(12))
        self.ambient = struct.unpack('<fff', f.read(12))
        self.toon_index = struct.unpack('<B', f.read(1))[0]
        self.edge_flag = struct.unpack('<B', f.read(1))[0]
        self.face_count = struct.unpack('<I', f.read(4))[0]
        self.texture_file_name = utils.decodePMDString(f.read(20))

        # テクスチャファイル名から名前を生成
        self.name = self.texture_file_name
