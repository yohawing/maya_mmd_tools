import struct

class PmdHeader:
    """PMDファイルのヘッダ情報を保持するクラス。"""
    def __init__(self):
        self.magic = b''
        self.version = 0.0
        self.model_name = ''
        self.comment = ''
        self.english_model_name = ''
        self.english_comment = ''

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
        self.model_name = f.read(20).decode('cp932').strip('\x00')
        self.comment = f.read(256).decode('cp932').strip('\x00')

    def parse_english(self, f):
        """
        ファイルハンドルから英語のPMDヘッダを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.english_model_name = f.read(20).decode('shift_jis').strip('\x00')
        self.english_comment = f.read(256).decode('cp932').strip('\x00')
