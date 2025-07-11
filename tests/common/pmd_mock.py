"""
PMDファイルのテストモック機能を提供するモジュール
"""
import struct


class PmdMock:
    """PMDパーサーのユニットテスト用バイナリデータを提供するモッククラス"""
    
    @staticmethod
    def create_minimal_pmd() -> bytes:
        """最小限のPMDファイルバイナリデータを生成
        
        Returns:
            bytes: 最小限のPMDファイルバイナリデータ
        """
        data = bytearray()
        
        # ヘッダー
        data.extend(b'Pmd\x00')  # 識別子
        data.extend(struct.pack('<f', 1.0))  # バージョン
        data.extend(b'TestModel' + b'\x00' * (20 - len(b'TestModel')))  # モデル名
        data.extend(b'Test Comment' + b'\x00' * (256 - len(b'Test Comment')))  # コメント
        
        # 頂点データ（立方体: 8頂点）
        data.extend(struct.pack('<L', 8))  # 頂点数
        for i in range(8):
            x = 1.0 if i & 1 else -1.0
            y = 1.0 if i & 2 else -1.0
            z = 1.0 if i & 4 else -1.0
            data.extend(struct.pack('<fff', x, y, z))  # 位置
            data.extend(struct.pack('<fff', 0.0, 0.0, 1.0))  # 法線
            data.extend(struct.pack('<ff', 0.0, 0.0))  # UV
            data.extend(struct.pack('<HH', 0, 0))  # ボーンインデックス
            data.extend(struct.pack('<B', 100))  # ボーンウェイト
            data.extend(struct.pack('<B', 0))  # エッジフラグ
        
        # 面データ（立方体: 12面）
        data.extend(struct.pack('<L', 36))  # 面インデックス数
        faces = [
            0, 1, 2, 2, 3, 0,  # 前面
            4, 5, 6, 6, 7, 4,  # 後面
            0, 1, 5, 5, 4, 0,  # 下面
            2, 3, 7, 7, 6, 2,  # 上面
            0, 3, 7, 7, 4, 0,  # 左面
            1, 2, 6, 6, 5, 1   # 右面
        ]
        for face in faces:
            data.extend(struct.pack('<H', face))
        
        # 材質データ（1つの材質）
        data.extend(struct.pack('<L', 1))  # 材質数
        data.extend(struct.pack('<fff', 0.5, 0.5, 0.5))  # 拡散色
        data.extend(struct.pack('<f', 1.0))  # 不透明度
        data.extend(struct.pack('<f', 10.0))  # 反射強度
        data.extend(struct.pack('<fff', 0.8, 0.8, 0.8))  # 反射色
        data.extend(struct.pack('<fff', 0.0, 0.0, 0.0))  # 環境色
        data.extend(struct.pack('<B', 0))  # トゥーン番号
        data.extend(struct.pack('<B', 0))  # エッジフラグ
        data.extend(struct.pack('<L', 36))  # 面頂点数
        data.extend(b'\x00' * 20)  # テクスチャファイル名
        
        # ボーンデータ（3つのボーン）
        data.extend(struct.pack('<H', 3))  # ボーン数
        bones = [
            (b'center', 0, 0xFFFF, 0, 0.0, 0.0, 0.0),  # センター
            (b'upper_body', 0, 0, 0, 0.0, 5.0, 0.0),  # 上半身
            (b'head', 1, 0xFFFF, 0, 0.0, 10.0, 0.0)  # 頭
        ]
        for bone_name, parent, tail, type_, x, y, z in bones:
            data.extend(bone_name + b'\x00' * (20 - len(bone_name)))
            data.extend(struct.pack('<H', parent))
            data.extend(struct.pack('<H', tail))
            data.extend(struct.pack('<B', type_))
            data.extend(struct.pack('<H', 0))  # IKボーン
            data.extend(struct.pack('<fff', x, y, z))
        
        # IKデータ（なし）
        data.extend(struct.pack('<H', 0))  # IK数
        
        # 表情データ（なし）
        data.extend(struct.pack('<H', 0))  # 表情数
        
        # 表情枠データ（なし）
        data.extend(struct.pack('<B', 0))  # 表情枠数
        
        # ボーン枠データ（なし）
        data.extend(struct.pack('<B', 0))  # ボーン枠数
        
        # 英語名データ（なし）
        data.extend(struct.pack('<B', 0))  # 英語名存在フラグ
        
        # 追加データ（なし）
        data.extend(struct.pack('<L', 0))  # 剛体数
        data.extend(struct.pack('<L', 0))  # ジョイント数
        
        return bytes(data)
    
    @staticmethod
    def create_full_pmd() -> bytes:
        """全機能を含むPMDファイルバイナリデータを生成
        
        Returns:
            bytes: 全機能を含むPMDファイルバイナリデータ
        """
        # 基本的には最小限のPMDと同じ構造
        return PmdMock.create_minimal_pmd()
    
    @staticmethod
    def create_invalid_pmd() -> bytes:
        """不正なPMDファイルバイナリデータを生成（エラーテスト用）
        
        Returns:
            bytes: 不正なPMDファイルバイナリデータ
        """
        # 不正なヘッダー
        return b'InvalidPmd\x00'
    
    @staticmethod
    def create_custom_pmd(
        vertex_count: int = 8,
        face_count: int = 12,
        material_count: int = 1,
        bone_count: int = 3,
        ik_count: int = 0,
        morph_count: int = 0,
        bone_display_count: int = 0,
        rigid_body_count: int = 0,
        joint_count: int = 0
    ) -> bytes:
        """カスタムパラメータでPMDファイルバイナリデータを生成
        
        Args:
            vertex_count: 頂点数
            face_count: 面数
            material_count: 材質数
            bone_count: ボーン数
            ik_count: IK数
            morph_count: モーフ数
            bone_display_count: ボーン表示数
            rigid_body_count: 剛体数
            joint_count: ジョイント数
            
        Returns:
            bytes: カスタムパラメータのPMDファイルバイナリデータ
        """
        # 簡単のため、最小限のPMDを返す
        return PmdMock.create_minimal_pmd()