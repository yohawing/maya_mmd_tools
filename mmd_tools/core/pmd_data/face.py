import struct


class PmdFace:
    """
    PMDファイルの面データを保持するクラス。
    """

    def __init__(self):
        self.indices = (0, 0, 0)

    def parse(self, f):
        """
        ファイルハンドルからPMD面データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.indices = struct.unpack("<HHH", f.read(6))

    def write(self, f):
        """
        PMD面データをバイナリファイルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(struct.pack("<HHH", *self.indices))
