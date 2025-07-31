import struct


class VmdCameraFrame:
    """VMDファイルのカメラフレームデータを保持するクラス。"""
    def __init__(self):
        self.frame_number = 0  # フレーム番号
        self.distance = 0.0  # 目標点とカメラの距離
        self.position = (0.0, 0.0, 0.0)
        self.rotation = (0.0, 0.0, 0.0) # Euler angles (X, Y, Z)
        # 補間パラメータは4点のベジェ曲線(0,0),(x1,y1),(x2,y2),(127,127)で
        # 表している.各軸のパラメータを
        # X軸の補間パラメータ　 (X_x1,X_y1),(X_x2,X_y2)
        # Y軸の補間パラメータ　 (Y_x1,Y_y1),(Y_x2,Y_y2)
        # Z軸の補間パラメータ　 (Z_x1,Z_y1),(Z_x2,Z_y2)
        # 回転の補間パラメータ　(R_x1,R_y1),(R_x2,R_y2)
        # 距離の補間パラメータ　(L_x1,L_y1),(L_x2,L_y2)
        # 視野角の補間パラメータ(V_x1,V_y1),(V_x2,V_y2)
        # とした時、補間パラメータは以下の通り.
        # X_x1 X_x2 X_y1 X_y2
        # Y_x1 Y_x2 Y_y1 Y_y2
        # Z_x1 Z_x2 Z_y1 Z_y2
        # R_x1 R_x2 R_y1 R_y2
        # L_x1 L_x2 L_y1 L_y2
        # V_x1 V_x2 V_y1 V_y2
        self.interpolation = b'' # 24 bytes
        self.viewing_angle = 0 # 視野角degrees
        self.perspective = 0 # 0: On, 1: Off


    @classmethod
    def size(cls):
        # フレーム番号(4) + 長さ(4) + 位置(12) + 回転(12) + 補間データ(24) + 視野角(4) + パースペクティブ(1)
        return 4 + 4 + 12 + 12 + 24 + 4 + 1

    def parse(self, data):
        """
        バイトデータからVMDカメラフレームデータを解析し、自身の属性に格納する。

        Args:
            data (bytes): カメラフレームデータ。
        """
        self.frame_number = struct.unpack_from('<I', data, 0)[0]
        self.distance = struct.unpack_from('<f', data, 4)[0]
        self.position = struct.unpack_from('<fff', data, 8)
        self.rotation = struct.unpack_from('<fff', data, 20)
        self.interpolation = data[32:56]
        self.viewing_angle = struct.unpack_from('<I', data, 56)[0]
        self.perspective = struct.unpack_from('<B', data, 60)[0]

    def write(self):
        """
        VMDカメラフレームデータをバイトデータに変換する。

        Returns:
            bytes: カメラフレームのバイナリデータ。
        """
        data = b''
        # フレーム番号を4バイトのunsigned intとしてパック
        data += struct.pack('<I', self.frame_number)
        # 距離を4バイトのfloatとしてパック
        data += struct.pack('<f', self.distance)
        # 位置を3つのfloatとしてパック
        data += struct.pack('<fff', *self.position)
        # 回転を3つのfloat（オイラー角）としてパック
        data += struct.pack('<fff', *self.rotation)
        # 補間データをそのまま追加（24バイト）
        if len(self.interpolation) == 24:
            data += self.interpolation
        else:
            # 補間データが不正な場合はデフォルト値で埋める
            data += b'\x00' * 24
        # 視野角を4バイトのunsigned intとしてパック
        data += struct.pack('<I', self.viewing_angle)
        # パースペクティブを1バイトのunsigned byteとしてパック
        data += struct.pack('<B', self.perspective)
        return data
