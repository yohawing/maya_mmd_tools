"""VMDの位置データがバインドポーズからの相対位置かを検証するスクリプト"""

import maya.cmds as cmds
from typing import Dict, List, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.vmd_parser import VmdParser


class TranslationOffsetTest:
    """VMDの位置データの基準点を検証するクラス"""
    
    def __init__(self):
        self.logger = get_logger(__name__)
        self.test_bones = ["センター", "グルーブ", "上半身", "上半身2", "頭", "左腕", "右腕"]
        
    def run_test(self, vmd_file_path: str):
        """位置データの基準点を検証"""
        self.logger.info("="*60)
        self.logger.info("VMD位置データ基準点検証")
        self.logger.info("="*60)
        
        # 各ボーンの初期位置（バインドポーズ）を記録
        bind_poses = self._record_bind_poses()
        
        # VMDファイルを読み込み
        parser = VmdParser()
        if not parser.parse_file(vmd_file_path):
            self.logger.error("VMDファイルの読み込みに失敗しました")
            return
            
        # 各ボーンの最初のフレームでの位置を分析
        self._analyze_first_frame_positions(parser.bone_frames, bind_poses)
        
        # センターボーンの移動パターンを詳細に分析
        self._analyze_center_movement(parser.bone_frames, bind_poses)
        
    def _record_bind_poses(self) -> Dict[str, Tuple[float, float, float]]:
        """各ボーンのバインドポーズ（初期位置）を記録"""
        bind_poses = {}
        
        self.logger.info("\nバインドポーズの記録:")
        self.logger.info("-" * 40)
        
        joints = cmds.ls(type="joint")
        for joint in joints:
            if cmds.attributeQuery("pmx_bone_name", node=joint, exists=True):
                pmx_name = cmds.getAttr(f"{joint}.pmx_bone_name")
                if pmx_name in self.test_bones:
                    # 現在のtranslate値を取得
                    translate = cmds.getAttr(f"{joint}.translate")[0]
                    bind_poses[pmx_name] = translate
                    
                    # 親ボーンを確認
                    parent = cmds.listRelatives(joint, parent=True)
                    parent_info = f" (親: {parent[0]})" if parent else " (ルート)"
                    
                    self.logger.info(f"{pmx_name}: {translate}{parent_info}")
                    
                    # PMXでの初期位置も確認（もしあれば）
                    if cmds.attributeQuery("pmx_bone_position", node=joint, exists=True):
                        pmx_pos = cmds.getAttr(f"{joint}.pmx_bone_position")[0]
                        self.logger.info(f"  PMX初期位置: {pmx_pos}")
                        
        return bind_poses
        
    def _analyze_first_frame_positions(self, bone_frames: List, bind_poses: Dict):
        """各ボーンの最初のフレームでの位置を分析"""
        self.logger.info("\n\n最初のフレームでの位置分析:")
        self.logger.info("-" * 40)
        
        # ボーンごとに最初のフレームを抽出
        first_frames = {}
        for frame in bone_frames:
            bone_name = frame.bone_name
            if bone_name in self.test_bones and bone_name not in first_frames:
                first_frames[bone_name] = frame
                
        # 各ボーンの位置を分析
        for bone_name, frame in first_frames.items():
            if bone_name in bind_poses:
                bind_pose = bind_poses[bone_name]
                vmd_pos = frame.position
                
                self.logger.info(f"\n{bone_name}:")
                self.logger.info(f"  バインドポーズ: {bind_pose}")
                self.logger.info(f"  VMD位置 (frame {frame.frame_number}): {vmd_pos}")
                
                # VMD位置がゼロかどうかチェック
                if all(abs(v) < 0.001 for v in vmd_pos):
                    self.logger.info(f"  → VMD位置はゼロ（相対位置の可能性大）")
                else:
                    # バインドポーズとの差分を計算
                    diff_x = vmd_pos[0] - bind_pose[0]
                    diff_y = vmd_pos[1] - bind_pose[1]
                    diff_z = vmd_pos[2] - (-bind_pose[2])  # Z軸は反転を考慮
                    
                    self.logger.info(f"  → VMD位置は非ゼロ")
                    self.logger.info(f"    差分: ({diff_x:.3f}, {diff_y:.3f}, {diff_z:.3f})")
                    
    def _analyze_center_movement(self, bone_frames: List, bind_poses: Dict):
        """センターボーンの移動パターンを詳細に分析"""
        self.logger.info("\n\nセンターボーンの移動パターン分析:")
        self.logger.info("-" * 40)
        
        # センターボーンのフレームを抽出
        center_frames = []
        for frame in bone_frames:
            if frame.bone_name == "センター":
                center_frames.append(frame)
                
        center_frames.sort(key=lambda x: x.frame_number)
        
        if "センター" in bind_poses:
            bind_pose = bind_poses["センター"]
            self.logger.info(f"センターのバインドポーズ: {bind_pose}")
            
            # 最初の10フレームを分析
            for frame in center_frames[:10]:
                vmd_pos = frame.position
                
                # 絶対位置として解釈した場合
                abs_pos = [vmd_pos[0], vmd_pos[1], -vmd_pos[2]]
                
                # 相対位置として解釈した場合
                rel_pos = [
                    bind_pose[0] + vmd_pos[0],
                    bind_pose[1] + vmd_pos[1],
                    bind_pose[2] - vmd_pos[2]  # Z軸反転
                ]
                
                self.logger.info(f"\nフレーム {frame.frame_number}:")
                self.logger.info(f"  VMD生データ: {vmd_pos}")
                self.logger.info(f"  絶対位置として: {abs_pos}")
                self.logger.info(f"  相対位置として: {rel_pos}")
                
        # PMXファイルでのセンターボーンの位置も確認
        self._check_pmx_bone_positions()
                
    def _check_pmx_bone_positions(self):
        """PMXファイルでのボーン位置情報を確認"""
        self.logger.info("\n\nPMXボーン位置情報:")
        self.logger.info("-" * 40)
        
        # センター系ボーンのPMX情報を確認
        center_joint = self._find_maya_joint("センター")
        if center_joint:
            # PMXのボーン位置情報があるか確認
            attrs_to_check = [
                "pmx_bone_position",
                "pmx_bone_parent_bone_index",
                "pmx_connect_bone_offset"
            ]
            
            for attr in attrs_to_check:
                if cmds.attributeQuery(attr, node=center_joint, exists=True):
                    value = cmds.getAttr(f"{center_joint}.{attr}")
                    self.logger.info(f"  {attr}: {value}")
                    
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
    # tester = TranslationOffsetTest()
    # tester.run_test("path/to/your/vmd/file.vmd")
    pass