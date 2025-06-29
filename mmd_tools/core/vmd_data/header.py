import struct

from mmd_tools.core import utils

class VmdHeader:
    """VMDファイルのヘッダ情報を保持するクラス。"""

    SIGNATURE = b"Vocaloid Motion Data 0002"

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

