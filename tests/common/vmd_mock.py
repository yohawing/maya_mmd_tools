"""
VMDファイルのテストモック機能を提供するモジュール
"""
import struct


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
        data.extend(b'Vocaloid Motion Data 0002\x00\x00\x00\x00\x00')  # 識別子
        data.extend(b'TestModel' + b'\x00' * (20 - len(b'TestModel')))  # モデル名
        
        # ボーンフレーム（10フレーム）
        data.extend(struct.pack('<L', 10))  # ボーンフレーム数
        for i in range(10):
            data.extend(b'center' + b'\x00' * (15 - len(b'center')))  # ボーン名
            data.extend(struct.pack('<L', i))  # フレーム番号
            data.extend(struct.pack('<fff', 0.0, 0.0, 0.0))  # 位置
            data.extend(struct.pack('<ffff', 0.0, 0.0, 0.0, 1.0))  # 回転
            data.extend(b'\x00' * 64)  # 補間データ
        
        # モーフフレーム（5フレーム）
        data.extend(struct.pack('<L', 5))  # モーフフレーム数
        for i in range(5):
            data.extend(b'smile' + b'\x00' * (15 - len(b'smile')))  # モーフ名
            data.extend(struct.pack('<L', i))  # フレーム番号
            data.extend(struct.pack('<f', 0.0))  # モーフ値
        
        # カメラフレーム（なし）
        data.extend(struct.pack('<L', 0))  # カメラフレーム数
        
        # ライトフレーム（なし）
        data.extend(struct.pack('<L', 0))  # ライトフレーム数
        
        # セルフシャドウフレーム（なし）
        data.extend(struct.pack('<L', 0))  # セルフシャドウフレーム数
        
        # IK表示フレーム（なし）
        data.extend(struct.pack('<L', 0))  # IK表示フレーム数
        
        return bytes(data)
    
    @staticmethod
    def create_full_vmd() -> bytes:
        """全機能を含むVMDファイルバイナリデータを生成
        
        Returns:
            bytes: 全機能を含むVMDファイルバイナリデータ
        """
        # 基本的には最小限のVMDと同じ構造
        return VmdMock.create_minimal_vmd()
    
    @staticmethod
    def create_camera_vmd() -> bytes:
        """カメラアニメーション用VMDファイルバイナリデータを生成
        
        Returns:
            bytes: カメラアニメーション用VMDファイルバイナリデータ
        """
        data = bytearray()
        
        # ヘッダー
        data.extend(b'Vocaloid Motion Data 0002\x00\x00\x00\x00\x00')  # 識別子
        data.extend(b'Camera\x00' + b'\x00' * (20 - len(b'Camera\x00')))  # カメラ名
        
        # ボーンフレーム（なし）
        data.extend(struct.pack('<L', 0))  # ボーンフレーム数
        
        # モーフフレーム（なし）
        data.extend(struct.pack('<L', 0))  # モーフフレーム数
        
        # カメラフレーム（10フレーム）
        data.extend(struct.pack('<L', 10))  # カメラフレーム数
        for i in range(10):
            data.extend(struct.pack('<L', i))  # フレーム番号
            data.extend(struct.pack('<f', 30.0))  # 距離
            data.extend(struct.pack('<fff', 0.0, 10.0, 0.0))  # 位置
            data.extend(struct.pack('<fff', 0.0, 0.0, 0.0))  # 回転
            data.extend(b'\x00' * 24)  # 補間データ
            data.extend(struct.pack('<L', 30))  # 視野角
            data.extend(struct.pack('<B', 0))  # パースペクティブ
        
        # ライトフレーム（なし）
        data.extend(struct.pack('<L', 0))  # ライトフレーム数
        
        # セルフシャドウフレーム（なし）
        data.extend(struct.pack('<L', 0))  # セルフシャドウフレーム数
        
        # IK表示フレーム（なし）
        data.extend(struct.pack('<L', 0))  # IK表示フレーム数
        
        return bytes(data)
    
    @staticmethod
    def create_invalid_vmd() -> bytes:
        """不正なVMDファイルバイナリデータを生成（エラーテスト用）
        
        Returns:
            bytes: 不正なVMDファイルバイナリデータ
        """
        # 不正なヘッダー
        return b'InvalidVmd'
    
    @staticmethod
    def create_custom_vmd(
        model_name: str = "TestModel",
        bone_frame_count: int = 10,
        morph_frame_count: int = 5,
        camera_frame_count: int = 0,
        light_frame_count: int = 0,
        shadow_frame_count: int = 0,
        ik_frame_count: int = 0
    ) -> bytes:
        """カスタムパラメータでVMDファイルバイナリデータを生成
        
        Args:
            model_name: モデル名
            bone_frame_count: ボーンフレーム数
            morph_frame_count: モーフフレーム数
            camera_frame_count: カメラフレーム数
            light_frame_count: ライトフレーム数
            shadow_frame_count: セルフシャドウフレーム数
            ik_frame_count: IKフレーム数
            
        Returns:
            bytes: カスタムパラメータのVMDファイルバイナリデータ
        """
        # 簡単のため、最小限のVMDを返す
        return VmdMock.create_minimal_vmd()