import struct

from mmd_tools.core import utils


class PmdHeader:
    """PMDファイルのヘッダ情報を保持するクラス。"""
    def __init__(self):
        self.magic = b''
        self.version = 0.0
        self.model_name = ''
        self.comment = ''
        self.model_name_english = ''
        self.comment_english = ''

    def parse(self, f):
        """
        ファイルハンドルからPMDヘッダを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.magic = f.read(3)
        if self.magic != b'Pmd':
            raise ValueError("Not a valid PMD file.")
        self.version = struct.unpack('<f', f.read(4))[0]
        self.model_name = utils.decodePMDString(f.read(20))
        self.comment = utils.decodePMDString(f.read(256))

    def parse_english(self, f):
        """
        ファイルハンドルから英語のPMDヘッダを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.model_name_english = utils.decodePMDString(f.read(20))
        self.comment_english = utils.decodePMDString(f.read(256))
