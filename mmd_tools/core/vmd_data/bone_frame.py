import struct

from mmd_tools.core import utils


DEFAULT_BONE_INTERPOLATION = b"\x14" * 64


class VmdBoneFrame:
    """VMDファイルのボーンフレームデータを保持するクラス。"""

    def __init__(self):
        # ボーン名は15文字までの文字列で表現され、VMDファイル内のボーンを識別する。
        self.bone_name = ""
        # フレーム番号は整数で表現され、VMDファイル内のフレームの順序を示す。
        self.frame_number = 0
        # 位置は3次元座標で表現される。形式は (x, y, z)
        self.position = (0.0, 0.0, 0.0)
        # 回転はクォータニオンで表現される。形式は (x, y, z, w)
        self.rotation = (0.0, 0.0, 0.0, 0.0)  # Quaternion
        # 補間データ 	キーフレーム間のモーションを算出するためのパラメタ
        # 補間パラメータは4点のベジェ曲線(0,0),(x1,y1),(x2,y2),(127,127)で
        # 表している．各軸のパラメータを
        # X軸の補間パラメータ　(X_x1,X_y1),(X_x2,X_y2)
        # Y軸の補間パラメータ　(Y_x1,Y_y1),(Y_x2,Y_y2)
        # Z軸の補間パラメータ　(Z_x1,Z_y1),(Z_x2,Z_y2)
        # 回転の補間パラメータ (R_x1,R_y1),(R_x2,R_y2)
        # とした時、補間パラメータは以下の通り.
        # X_x1,Y_x1,Z_x1,R_x1,X_y1,Y_y1,Z_y1,R_y1,
        # X_x2,Y_x2,Z_x2,R_x2,X_y2,Y_y2,Z_y2,R_y2,
        # Y_x1,Z_x1,R_x1,X_y1,Y_y1,Z_y1,R_y1,X_x2,
        # Y_x2,Z_x2,R_x2,X_y2,Y_y2,Z_y2,R_y2, 01,
        # Z_x1,R_x1,X_y1,Y_y1,Z_y1,R_y1,X_x2,Y_x2,
        # Z_x2,R_x2,X_y2,Y_y2,Z_y2,R_y2, 01, 00,
        # R_x1,X_y1,Y_y1,Z_y1,R_y1,X_x2,Y_x2,Z_x2,
        # R_x2,X_y2,Y_y2,Z_y2,R_y2, 01, 00, 00
        self.interpolation = DEFAULT_BONE_INTERPOLATION

    @classmethod
    def size(cls):
        # ボーン名(15) + フレーム番号(4) + 位置(12) + 回転(16) + 補間データ(64)　合計 111
        return 15 + 4 + 12 + 16 + 64

    def parse(self, data):
        """
        バイトデータからVMDボーンフレームデータを解析し、自身の属性に格納する。

        Args:
            data (bytes): ボーンフレームデータ。
        """
        self.bone_name = utils.decodePMDString(data[:15])
        self.frame_number = struct.unpack_from("<I", data, 15)[0]
        self.position = struct.unpack_from("<fff", data, 19)
        self.rotation = struct.unpack_from("<ffff", data, 31)
        self.interpolation = data[47:111]

    def write(self):
        """
        VMDボーンフレームデータをバイトデータに変換する。

        Returns:
            bytes: ボーンフレームのバイナリデータ。
        """
        data = b""
        # ボーン名を15バイトの固定長でエンコード
        data += utils.encodePMDString(self.bone_name, 15)
        # フレーム番号を4バイトのunsigned intとしてパック
        data += struct.pack("<I", self.frame_number)
        # 位置を3つのfloatとしてパック
        data += struct.pack("<fff", *self.position)
        # 回転を4つのfloat（クォータニオン）としてパック
        data += struct.pack("<ffff", *self.rotation)
        # 補間データをそのまま追加（64バイト）
        if len(self.interpolation) == 64:
            data += self.interpolation
        else:
            # 補間データが不正な場合はデフォルト値で埋める
            data += DEFAULT_BONE_INTERPOLATION
        return data
