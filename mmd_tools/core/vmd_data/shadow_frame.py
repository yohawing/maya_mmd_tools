import struct


class VmdShadowFrame:
    """VMDファイルのセルフシャドウフレームデータを保持するクラス。"""

    def __init__(self):
        self.frame_number = 0  # フレーム番号
        self.mode = 0  # セルフシャドウ種類, 0:OFF, 1:mode1, 2:mode2
        self.distance = 0.0  # シャドウ距離

    @classmethod
    def size(cls):
        # フレーム番号(4) + モード(1) + 距離(4) 合計 9
        return 4 + 1 + 4

    def parse(self, data):
        """
        バイトデータからVMDシャドウフレームデータを解析し、自身の属性に格納する。

        Args:
            data (bytes): シャドウフレームデータ。
        """
        self.frame_number = struct.unpack_from("<I", data, 0)[0]
        self.mode = struct.unpack_from("<B", data, 4)[0]
        self.distance = struct.unpack_from("<f", data, 5)[0]

    def write(self):
        """
        VMDシャドウフレームデータをバイトデータに変換する。

        Returns:
            bytes: シャドウフレームのバイナリデータ。
        """
        data = b""
        # フレーム番号を4バイトのunsigned intとしてパック
        data += struct.pack("<I", self.frame_number)
        # モードを1バイトのunsigned byteとしてパック
        data += struct.pack("<B", self.mode)
        # 距離を4バイトのfloatとしてパック
        data += struct.pack("<f", self.distance)
        return data
