import struct

from mmd_tools.core import utils


class PmdHeader:
    """PMDファイルのヘッダ情報を保持するクラス。"""

    def __init__(self):
        self.magic = b""
        self.version = 0.0
        self.model_name = ""
        self.comment = ""
        self.model_name_english = ""
        self.comment_english = ""

    def parse(self, f):
        """
        ファイルハンドルからPMDヘッダを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.magic = f.read(3)
        if self.magic != b"Pmd":
            raise ValueError("Not a valid PMD file.")
        self.version = struct.unpack("<f", f.read(4))[0]
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

    def get_name(self):
        """
        モデルの名前を取得する。英語名が設定されていればそれを返し、なければ日本語名を返す。

        Returns:
            str: モデルの名前。
        """
        if self.model_name_english and self.model_name_english != "":
            return self.model_name_english

        return self.model_name

    def get_comment(self):
        """
        モデルのコメントを取得する。英語コメントが設定されていればそれを返し、なければ日本語コメントを返す。

        Returns:
            str: モデルのコメント。
        """
        if self.comment_english and self.comment_english != "":
            return self.comment_english

        return self.comment

    def write(self, f):
        """
        PMDヘッダをバイナリファイルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(b"Pmd")
        f.write(struct.pack("<f", self.version))
        f.write(utils.encodePMDString(self.model_name, 20))
        f.write(utils.encodePMDString(self.comment, 256))

    def write_english(self, f):
        """
        英語のPMDヘッダをバイナリファイルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        f.write(utils.encodePMDString(self.model_name_english, 20))
        f.write(utils.encodePMDString(self.comment_english, 256))
