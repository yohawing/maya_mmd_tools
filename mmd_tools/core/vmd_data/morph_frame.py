import struct

from mmd_tools.core import utils

class VmdMorphFrame:
    """VMDファイルのモーフフレームデータを保持するクラス。"""
    def __init__(self):
        self.morph_name = ''
        self.frame_number = 0
        self.value = 0.0

    @classmethod
    def size(cls):
        # モーフ名(15) + フレーム番号(4) + モーフ値(4)　合計 23
        return 15 + 4 + 4

    def parse(self, data):
        """
        バイトデータからVMDモーフフレームデータを解析し、自身の属性に格納する。

        Args:
            data (bytes): モーフフレームデータ。
        """
        self.morph_name = utils.decodePMDString(data[:15])
        self.frame_number = struct.unpack_from('<I', data, 15)[0]
        self.value = struct.unpack_from('<f', data, 19)[0]
