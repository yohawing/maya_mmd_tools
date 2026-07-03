"""VPDファイルのボーンポーズ情報を扱うモジュール"""

from typing import List


class BonePose:
    """VPDファイルのボーンポーズ情報を格納するクラス

    単一ボーンのポーズ情報（位置と回転）を管理します。

    Attributes:
        bone_index (int): ボーン番号 (Bone0, Bone1...)
        bone_name (str): ボーン名（日本語）
        position (list): 位置 [x, y, z]
        quaternion (list): 回転（四元数）[x, y, z, w]
    """

    def __init__(self) -> None:
        """BonePoseの初期化"""
        self.bone_index: int = 0  # ボーン番号
        self.bone_name: str = ""  # ボーン名（日本語）
        self.position: List[float] = [0.0, 0.0, 0.0]  # 位置 [x, y, z]
        self.quaternion: List[float] = [0.0, 0.0, 0.0, 1.0]  # 回転（四元数）[x, y, z, w]

    def __repr__(self) -> str:
        """文字列表現を返す"""
        return f"BonePose(index={self.bone_index}, name='{self.bone_name}', pos={self.position}, quat={self.quaternion})"

    def __str__(self) -> str:
        """読みやすい文字列表現を返す"""
        return (
            f"Bone{self.bone_index}{{{self.bone_name}\n"
            f"  {self.position[0]:.6f},{self.position[1]:.6f},{self.position[2]:.6f};\n"
            f"  {self.quaternion[0]:.6f},{self.quaternion[1]:.6f},"
            f"{self.quaternion[2]:.6f},{self.quaternion[3]:.6f};\n"
            f"}}"
        )

    def to_vpd_format(self) -> str:
        """VPDファイル形式の文字列を生成する

        Returns:
            str: VPDファイル形式の文字列
        """
        return (
            f"Bone{self.bone_index}{{{self.bone_name}\n"
            f"  {self.position[0]:.6f},{self.position[1]:.6f},{self.position[2]:.6f};\n"
            f"  {self.quaternion[0]:.6f},{self.quaternion[1]:.6f},"
            f"{self.quaternion[2]:.6f},{self.quaternion[3]:.6f};\n"
            f"}}\n"
        )
