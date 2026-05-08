from typing import BinaryIO

from mmd_tools.core import utils


class PmxSoftBody:
    """
    PMXファイルのSoftBodyデータを保持するクラス (PMX 2.1以降)。
    現在、このクラスはプレースホルダーであり、詳細な解析は実装されていません。
    """

    def __init__(self, encoding_flag: int = 1):
        self.name = ""
        self.name_english = ""
        self.encoding_flag = encoding_flag  # 0=UTF-16LE, 1=UTF-8
        self.encoding = utils.get_pmx_encoding_string(encoding_flag)  # "utf-16-le" or "utf-8"
        # TODO: Implement detailed SoftBody parsing based on PMX 2.1 specification

    def parse(self, f: BinaryIO) -> None:
        """
        ファイルハンドルからPMX SoftBodyデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        # Placeholder for SoftBody parsing
        # This section needs to be implemented according to the full PMX 2.1 specification.
        # For now, it just reads the name lengths and skips the data.
        self.name = utils.parsePMXString(f, self.encoding)
        self.name_english = utils.parsePMXString(f, self.encoding)

        # Skip the rest of the SoftBody data for now
        # This needs to be replaced with actual parsing logic
        # based on the PMX 2.1 specification for SoftBody.
        # For example, reading shape type, material index, group, collision flags, etc.
        # For demonstration, let's assume a fixed size for now, but this is incorrect.
        # f.read(some_fixed_size_for_softbody_data)

    def write(self, f: BinaryIO) -> None:
        """
        PMX SoftBodyデータをファイルハンドルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        # Placeholder for SoftBody writing
        # This section needs to be implemented according to the full PMX 2.1 specification.
        f.write(utils.encodePMXString(self.name, self.encoding))
        f.write(utils.encodePMXString(self.name_english, self.encoding))

        # TODO: Write the rest of the SoftBody data based on PMX 2.1 specification
        # For example, shape type, material index, group, collision flags, etc.
