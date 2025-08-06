import struct

from mmd_tools.core import utils


class VmdIKShowHideFrame:
    """VMDファイルのIK表示/非表示フレームデータを保持するクラス。"""

    def __init__(self):
        self.frame_number = 0
        self.visible = 0  # モデル表示 0: Off, 1: On
        self.ik_count = 0  # 記録するIKの数
        self.ik_states = []  # List of (ik_name, show_flag)

    @classmethod
    def size(cls):
        # This size is variable due to ik_states.
        # We need to read ik_count first to determine the full size.
        # For now, return a base size, and handle variable part in parse.
        return 4 + 1 + 4  # frame_number + visible + ik_count

    def parse(self, data):
        """
        バイトデータからVMD IK表示/非表示フレームデータを解析し、自身の属性に格納する。

        Args:
            data (bytes): IK表示/非表示フレームデータ。
        """
        self.frame_number = struct.unpack_from("<I", data, 0)[0]
        self.visible = struct.unpack_from("<B", data, 4)[0]
        self.ik_count = struct.unpack_from("<I", data, 5)[0]

        offset = 9
        for _ in range(self.ik_count):
            ik_name = utils.decodePMDString(data[offset : offset + 20])
            show_flag = struct.unpack_from("<B", data, offset + 20)[0]
            self.ik_states.append((ik_name, show_flag))
            offset += 21

    def write(self):
        """
        VMD IK表示/非表示フレームデータをバイトデータに変換する。

        Returns:
            bytes: IK表示/非表示フレームのバイナリデータ。
        """
        data = b""
        # フレーム番号を4バイトのunsigned intとしてパック
        data += struct.pack("<I", self.frame_number)
        # モデル表示を1バイトのunsigned byteとしてパック
        data += struct.pack("<B", self.visible)
        # IKの数を4バイトのunsigned intとしてパック
        data += struct.pack("<I", self.ik_count)

        # 各IKの状態を書き込む
        for ik_name, show_flag in self.ik_states:
            # IK名を20バイトの固定長でエンコード
            data += utils.encodePMDString(ik_name, 20)
            # 表示フラグを1バイトのunsigned byteとしてパック
            data += struct.pack("<B", show_flag)

        return data
