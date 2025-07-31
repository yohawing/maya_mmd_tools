import struct

from mmd_tools.core import utils


class VmdHeader:
    """VMDファイルのヘッダ情報を保持するクラス。"""

    SIGNATURE = b"Vocaloid Motion Data"

    def __init__(self):
        self.magic = b''
        self.model_name = ''

    def parse(self, f):
        """
        VMDファイルのヘッダを解析し、属性に格納する。

        Args:
            f (file-like object): VMDファイルのバイナリデータを読み込むためのファイルオブジェクト。
        """
        self.magic = struct.unpack("<30s", f.read(30))[0]
        if not self.magic.startswith(self.SIGNATURE):
            raise ValueError("Unsupported MMD file format: Invalid magic number")
        self.model_name = utils.decodePMDString(struct.unpack("<20s", f.read(20))[0])

    def write(self, f):
        """
        VMDファイルのヘッダをバイナリファイルに書き込む。

        Args:
            f (file-like object): VMDファイルのバイナリデータを書き込むためのファイルオブジェクト。
        """
        # Magic (30バイト固定、残りは0でパディング)
        magic_data = self.SIGNATURE + b"\x00" * (30 - len(self.SIGNATURE))
        f.write(struct.pack("<30s", magic_data))
        
        # Model name (20バイト固定)
        f.write(utils.encodePMDString(self.model_name, 20))

