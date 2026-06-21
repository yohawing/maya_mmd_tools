"""
VMDファイルのテストモック機能を提供するモジュール
"""

import struct
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core.vmd_data import VmdData


class VmdMock:
    """VMDパーサーのユニットテスト用バイナリデータを提供するモッククラス"""

    @staticmethod
    def create_minimal_vmd() -> bytes:
        """最小限のVMDファイルバイナリデータを生成

        Returns:
            bytes: 最小限のVMDファイルバイナリデータ
        """
        data = bytearray()

        # ヘッダー
        data.extend(b"Vocaloid Motion Data 0002\x00\x00\x00\x00\x00")  # 識別子
        data.extend(b"TestModel" + b"\x00" * (20 - len(b"TestModel")))  # モデル名

        # ボーンフレーム（10フレーム）
        data.extend(struct.pack("<L", 10))  # ボーンフレーム数
        for i in range(10):
            data.extend(b"center" + b"\x00" * (15 - len(b"center")))  # ボーン名
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 位置
            data.extend(struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0))  # 回転
            data.extend(b"\x00" * 64)  # 補間データ

        # モーフフレーム（5フレーム）
        data.extend(struct.pack("<L", 5))  # モーフフレーム数
        for i in range(5):
            data.extend(b"smile" + b"\x00" * (15 - len(b"smile")))  # モーフ名
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<f", 0.0))  # モーフ値

        # カメラフレーム（なし）
        data.extend(struct.pack("<L", 0))  # カメラフレーム数

        # ライトフレーム（なし）
        data.extend(struct.pack("<L", 0))  # ライトフレーム数

        # セルフシャドウフレーム（なし）
        data.extend(struct.pack("<L", 0))  # セルフシャドウフレーム数

        # IK表示フレーム（なし）
        data.extend(struct.pack("<L", 0))  # IK表示フレーム数

        return bytes(data)

    @staticmethod
    def create_full_vmd() -> bytes:
        """全機能を含むVMDファイルバイナリデータを生成

        ボーンフレーム・モーフフレーム・カメラフレーム・ライトフレーム・
        セルフシャドウフレームを含む。

        Returns:
            bytes: 全機能を含むVMDファイルバイナリデータ
        """
        return VmdMock.create_custom_vmd(
            model_name="FullTestModel",
            bone_frame_count=5,
            morph_frame_count=3,
            camera_frame_count=2,
            light_frame_count=2,
            shadow_frame_count=2,
        )

    @staticmethod
    def create_camera_vmd() -> bytes:
        """カメラアニメーション用VMDファイルバイナリデータを生成

        Returns:
            bytes: カメラアニメーション用VMDファイルバイナリデータ
        """
        data = bytearray()

        # ヘッダー
        data.extend(b"Vocaloid Motion Data 0002\x00\x00\x00\x00\x00")  # 識別子
        data.extend(b"Camera\x00" + b"\x00" * (20 - len(b"Camera\x00")))  # カメラ名

        # ボーンフレーム（なし）
        data.extend(struct.pack("<L", 0))  # ボーンフレーム数

        # モーフフレーム（なし）
        data.extend(struct.pack("<L", 0))  # モーフフレーム数

        # カメラフレーム（10フレーム）
        data.extend(struct.pack("<L", 10))  # カメラフレーム数
        for i in range(10):
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<f", 30.0))  # 距離
            data.extend(struct.pack("<fff", 0.0, 10.0, 0.0))  # 位置
            data.extend(struct.pack("<fff", 0.0, 0.0, 0.0))  # 回転
            data.extend(b"\x00" * 24)  # 補間データ
            data.extend(struct.pack("<L", 30))  # 視野角
            data.extend(struct.pack("<B", 0))  # パースペクティブ

        # ライトフレーム（なし）
        data.extend(struct.pack("<L", 0))  # ライトフレーム数

        # セルフシャドウフレーム（なし）
        data.extend(struct.pack("<L", 0))  # セルフシャドウフレーム数

        # IK表示フレーム（なし）
        data.extend(struct.pack("<L", 0))  # IK表示フレーム数

        return bytes(data)

    @staticmethod
    def create_invalid_vmd() -> bytes:
        """不正なVMDファイルバイナリデータを生成（エラーテスト用）

        Returns:
            bytes: 不正なVMDファイルバイナリデータ
        """
        # 不正なヘッダー
        return b"InvalidVmd"

    @staticmethod
    def create_custom_vmd(
        model_name: str = "TestModel",
        morph_name: str = "smile",
        bone_frame_count: int = 10,
        morph_frame_count: int = 5,
        camera_frame_count: int = 0,
        light_frame_count: int = 0,
        shadow_frame_count: int = 0,
        ik_frame_count: int = 0,
    ) -> bytes:
        """カスタムパラメータでVMDファイルバイナリデータを生成

        Args:
            model_name: モデル名
            morph_name: モーフフレーム名
            bone_frame_count: ボーンフレーム数
            morph_frame_count: モーフフレーム数
            camera_frame_count: カメラフレーム数
            light_frame_count: ライトフレーム数
            shadow_frame_count: セルフシャドウフレーム数
            ik_frame_count: IKフレーム数

        Returns:
            bytes: カスタムパラメータのVMDファイルバイナリデータ
        """
        data = bytearray()

        # ヘッダー
        data.extend(b"Vocaloid Motion Data 0002\x00\x00\x00\x00\x00")  # 識別子
        model_name_bytes = model_name.encode("shift-jis", errors="ignore")[:20]
        data.extend(model_name_bytes + b"\x00" * (20 - len(model_name_bytes)))  # モデル名

        # ボーンフレーム
        data.extend(struct.pack("<L", bone_frame_count))
        for i in range(bone_frame_count):
            data.extend(b"center" + b"\x00" * (15 - len(b"center")))  # ボーン名
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<fff", i * 0.1, 0.0, 0.0))  # 位置
            data.extend(struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0))  # 回転
            data.extend(b"\x00" * 64)  # 補間データ

        # モーフフレーム
        data.extend(struct.pack("<L", morph_frame_count))
        morph_name_bytes = morph_name.encode("shift-jis", errors="ignore")[:15]
        for i in range(morph_frame_count):
            data.extend(morph_name_bytes + b"\x00" * (15 - len(morph_name_bytes)))  # モーフ名
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<f", i * 0.2))  # モーフ値

        # カメラフレーム
        data.extend(struct.pack("<L", camera_frame_count))
        for i in range(camera_frame_count):
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<f", 30.0 + i))  # 距離
            data.extend(struct.pack("<fff", 0.0, 10.0, i * 0.5))  # 位置
            data.extend(struct.pack("<fff", 0.0, i * 0.1, 0.0))  # 回転
            data.extend(b"\x00" * 24)  # 補間データ
            data.extend(struct.pack("<L", 30))  # 視野角
            data.extend(struct.pack("<B", 0))  # パースペクティブ

        # ライトフレーム
        data.extend(struct.pack("<L", light_frame_count))
        for i in range(light_frame_count):
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<fff", 1.0, 1.0, 1.0))  # 色（RGB）
            data.extend(struct.pack("<fff", 0.0, -1.0, 0.0))  # 方向ベクトル

        # セルフシャドウフレーム
        data.extend(struct.pack("<L", shadow_frame_count))
        for i in range(shadow_frame_count):
            data.extend(struct.pack("<L", i))  # フレーム番号
            data.extend(struct.pack("<B", 1))  # モード(0-2)
            data.extend(struct.pack("<f", 0.8))  # 距離

        # IK表示フレーム
        data.extend(struct.pack("<L", ik_frame_count))
        if ik_frame_count > 0:
            for i in range(ik_frame_count):
                data.extend(struct.pack("<L", i))  # フレーム番号
                data.extend(struct.pack("<B", 1))  # 表示状態
                # IK数
                ik_count = 2
                data.extend(struct.pack("<L", ik_count))
                # 各IKの状態
                for j in range(ik_count):
                    ik_name = f"IK{j}".encode("shift-jis")[:20]
                    data.extend(ik_name + b"\x00" * (20 - len(ik_name)))
                    data.extend(struct.pack("<B", 1))  # ON/OFF

        return bytes(data)


def create_test_vmd_data():
    """テスト用のVmdParserオブジェクトを作成する

    Returns:
        VmdParser: テスト用のVMDデータ
    """
    vmd_data = VmdData()
    vmd_data.header.model_name = "TestModel"

    # センターボーンのテストデータを追加
    center_frame = VmdBoneFrame()
    center_frame.bone_name = "センター"
    center_frame.frame_number = 30
    center_frame.position = [0.5, 0.5, 0.5]
    center_frame.rotation = [0.0, 0.0, 0.0, 1.0]
    center_frame.interpolation = b"\x00" * 64

    vmd_data.bone_frames = [center_frame]
    vmd_data.morph_frames = []
    vmd_data.camera_frames = []
    vmd_data.light_frames = []
    vmd_data.shadow_frames = []
    vmd_data.ik_show_hide_frames = []

    return vmd_data
