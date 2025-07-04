import struct


class VmdLightFrame:
    """VMDファイルのライトフレームデータを保持するクラス。"""
    def __init__(self):
        self.frame_number = 0 # フレーム番号
        self.color = (0.0, 0.0, 0.0) # 色
        self.position = (0.0, 0.0, 0.0) # 位置

    @classmethod
    def size(cls):
        # フレーム番号(4) + 色(12) + 位置(12) 合計 28
        return 4 + 12 + 12

    def parse(self, data):
        """
        バイトデータからVMDライトフレームデータを解析し、自身の属性に格納する。

        Args:
            data (bytes): ライトフレームデータ。
        """
        self.frame_number = struct.unpack_from('<I', data, 0)[0]
        self.color = struct.unpack_from('<fff', data, 4)
        self.position = struct.unpack_from('<fff', data, 16)
