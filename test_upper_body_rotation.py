"""上半身ボーンの回転を詳細に検証するスクリプト"""

import maya.cmds as cmds
import maya.api.OpenMaya as om2
import math
from typing import Dict, List, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.vmd_parser import VmdParser


class UpperBodyRotationTest:
    """上半身ボーンの回転変換を詳細に検証するクラス"""
    
    def __init__(self):
        self.logger = get_logger(__name__)
        
    def run_test(self, vmd_file_path: str):
        """上半身ボーンの回転を詳細に検証"""
        self.logger.info("="*60)
        self.logger.info("上半身ボーン回転検証開始")
        self.logger.info("="*60)
        
        # VMDファイルを読み込み
        parser = VmdParser()
        if not parser.parse_file(vmd_file_path):
            self.logger.error("VMDファイルの読み込みに失敗しました")
            return
            
        # 上半身と上半身2のフレームデータを抽出
        upper_body_frames = self._extract_upper_body_frames(parser.bone_frames)
        
        # 各ボーンの回転データを詳細に分析
        for bone_name, frames in upper_body_frames.items():
            self.logger.info(f"\n{'='*40}")
            self.logger.info(f"{bone_name} の回転分析")
            self.logger.info(f"{'='*40}")
            
            # 最初の10フレームまたは回転が変化するフレームを分析
            for i, frame in enumerate(frames[:10]):
                if frame.rotation != (0.0, 0.0, 0.0, 1.0):  # 回転がある場合のみ
                    self._analyze_rotation(bone_name, frame)
                    
    def _extract_upper_body_frames(self, bone_frames: List) -> Dict[str, List]:
        """上半身関連のフレームデータを抽出"""
        target_bones = ["上半身", "上半身2"]
        upper_body_frames = {}
        
        for frame in bone_frames:
            if frame.bone_name in target_bones:
                if frame.bone_name not in upper_body_frames:
                    upper_body_frames[frame.bone_name] = []
                upper_body_frames[frame.bone_name].append(frame)
                
        # フレーム番号でソート
        for bone_name in upper_body_frames:
            upper_body_frames[bone_name].sort(key=lambda x: x.frame_number)
            
        return upper_body_frames
        
    def _analyze_rotation(self, bone_name: str, frame):
        """回転データを詳細に分析"""
        self.logger.info(f"\nフレーム {frame.frame_number}:")
        self.logger.info(f"VMD Quaternion: {frame.rotation}")
        
        # クォータニオンの各成分
        qx, qy, qz, qw = frame.rotation
        self.logger.info(f"  X: {qx:.6f}, Y: {qy:.6f}, Z: {qz:.6f}, W: {qw:.6f}")
        
        # 正規化チェック
        length = math.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
        self.logger.info(f"  Length: {length:.6f} (正規化{'済み' if abs(length - 1.0) < 0.001 else '必要'})")
        
        # 異なる変換方法を試す
        self.logger.info("\n変換方法の比較:")
        
        # 1. Z軸反転のみ
        method1 = self._convert_method1(frame.rotation)
        self.logger.info(f"  方法1 (Z反転): {method1}")
        
        # 2. Y軸とZ軸を反転
        method2 = self._convert_method2(frame.rotation)
        self.logger.info(f"  方法2 (YZ反転): {method2}")
        
        # 3. 左手系から右手系への完全な変換
        method3 = self._convert_method3(frame.rotation)
        self.logger.info(f"  方法3 (完全変換): {method3}")
        
        # 4. 軸の入れ替え
        method4 = self._convert_method4(frame.rotation)
        self.logger.info(f"  方法4 (軸入れ替え): {method4}")
        
        # Mayaジョイントの現在の状態も確認
        maya_joint = self._find_maya_joint(bone_name)
        if maya_joint:
            current_rotation = cmds.getAttr(f"{maya_joint}.rotate")[0]
            joint_orient = cmds.getAttr(f"{maya_joint}.jointOrient")[0]
            self.logger.info(f"\nMayaジョイントの現在の状態:")
            self.logger.info(f"  rotate: {current_rotation}")
            self.logger.info(f"  jointOrient: {joint_orient}")
            
    def _convert_method1(self, quat: Tuple[float, float, float, float]) -> Tuple[float, float, float]:
        """方法1: Z成分のみ反転（現在の実装）"""
        maya_quat = om2.MQuaternion(quat[0], quat[1], -quat[2], quat[3])
        maya_quat = maya_quat.normal()
        euler = maya_quat.asEulerRotation()
        return (math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z))
        
    def _convert_method2(self, quat: Tuple[float, float, float, float]) -> Tuple[float, float, float]:
        """方法2: YとZ成分を反転"""
        maya_quat = om2.MQuaternion(quat[0], -quat[1], -quat[2], quat[3])
        maya_quat = maya_quat.normal()
        euler = maya_quat.asEulerRotation()
        return (math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z))
        
    def _convert_method3(self, quat: Tuple[float, float, float, float]) -> Tuple[float, float, float]:
        """方法3: 左手系から右手系への完全な変換"""
        # 左手系のクォータニオンを右手系に変換
        # X軸周りの回転は同じ、Y軸とZ軸周りの回転は反転
        maya_quat = om2.MQuaternion(-quat[0], quat[1], quat[2], quat[3])
        maya_quat = maya_quat.normal()
        euler = maya_quat.asEulerRotation()
        return (math.degrees(euler.x), -math.degrees(euler.y), -math.degrees(euler.z))
        
    def _convert_method4(self, quat: Tuple[float, float, float, float]) -> Tuple[float, float, float]:
        """方法4: 軸の入れ替え（MMDのY軸がMayaのZ軸の可能性）"""
        # MMD: X, Y, Z -> Maya: X, Z, Y
        maya_quat = om2.MQuaternion(quat[0], quat[2], quat[1], quat[3])
        maya_quat = maya_quat.normal()
        euler = maya_quat.asEulerRotation()
        return (math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z))
        
    def _find_maya_joint(self, pmx_bone_name: str) -> str:
        """PMXボーン名からMayaジョイントを検索"""
        joints = cmds.ls(type="joint")
        for joint in joints:
            if cmds.attributeQuery("pmx_bone_name", node=joint, exists=True):
                if cmds.getAttr(f"{joint}.pmx_bone_name") == pmx_bone_name:
                    return joint
        return None


# 使用例
if __name__ == "__main__":
    # tester = UpperBodyRotationTest()
    # tester.run_test("path/to/your/vmd/file.vmd")
    pass