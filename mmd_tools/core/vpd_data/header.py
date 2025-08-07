"""VPDファイルのヘッダー情報を扱うモジュール"""

class VpdHeader:
    """VPDファイルのヘッダー情報を格納するクラス
    
    VPDファイルのヘッダー部分に含まれる情報を管理します。
    
    Attributes:
        signature (str): ファイル識別子 ("Vocaloid Pose Data file")
        parent_file (str): 親ファイル名（通常は.osmファイル）
        bone_count (int): ボーンの総数
    """
    
    def __init__(self):
        """VpdHeaderの初期化"""
        self.signature = "Vocaloid Pose Data file"
        self.parent_file = ""  # 親ファイル名
        self.bone_count = 0    # 総ボーン数
    
    def __repr__(self):
        """文字列表現を返す"""
        return (
            f"VpdHeader(signature='{self.signature}', "
            f"parent_file='{self.parent_file}', "
            f"bone_count={self.bone_count})"
        )
    
    def __str__(self):
        """読みやすい文字列表現を返す"""
        return (
            f"VPD Header:\n"
            f"  Signature: {self.signature}\n"
            f"  Parent File: {self.parent_file}\n"
            f"  Bone Count: {self.bone_count}"
        )