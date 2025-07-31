import struct

from mmd_tools.core import utils

### トゥーンインデックス
# - **0xFF**: toon0.bmp
# - **0x00**: toon01.bmp
# - **0x01**: toon02.bmp
# - **...**: ...
# - **0x09**: toon10.bmp


class PmdMaterial:
    """PMDファイルの材質データを保持するクラス。"""

    def __init__(self, material_index=0):
        # デフォルト値を設定
        self.name = "PmdDefaultMaterial"
        self.diffuse = (1.0, 1.0, 1.0, 1.0)  # RGBA - 白色をデフォルトに
        self.specular_power = 5.0
        self.specular = (0.5, 0.5, 0.5)  # RGB
        self.ambient = (0.0, 0.0, 0.0)  # RGB
        self.toon_texture_index = 0
        self.edge_flag = 0
        self.face_count = 0
        self.texture_file_name = ""
        self.material_index = material_index

    def parse(self, f):
        """
        ファイルハンドルからPMD材質データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.diffuse = struct.unpack("<ffff", f.read(16))
        self.specular_power = struct.unpack("<f", f.read(4))[0]
        self.specular = struct.unpack("<fff", f.read(12))
        self.ambient = struct.unpack("<fff", f.read(12))
        self.toon_texture_index = struct.unpack("<B", f.read(1))[0]
        self.edge_flag = struct.unpack("<B", f.read(1))[0]
        self.face_count = struct.unpack("<I", f.read(4))[0]
        self.texture_file_name = utils.decodePMDString(f.read(20))

        # テクスチャファイル名から名前を生成
        if self.texture_file_name:
            self.name = self.texture_file_name
        else:
            # テクスチャがない場合はインデックスを使用したデフォルト名
            self.name = f"material_{self.material_index}"

    def get_name(self):
        """
        材質の名前を取得する。

        Returns:
            str: 材質の名前。
        """
        return self.name

    def write(self, f):
        """
        PMD材質データをバイナリファイルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(struct.pack("<ffff", *self.diffuse))
        f.write(struct.pack("<f", self.specular_power))
        f.write(struct.pack("<fff", *self.specular))
        f.write(struct.pack("<fff", *self.ambient))
        f.write(struct.pack("<B", self.toon_texture_index))
        f.write(struct.pack("<B", self.edge_flag))
        f.write(struct.pack("<I", self.face_count))
        f.write(utils.encodePMDString(self.texture_file_name, 20))
