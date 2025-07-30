#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_given_bone.pmxの構造を解析するスクリプト
"""

import os
import sys

# プロジェクトのルートディレクトリをPythonパスに追加
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from mmd_tools.core import mmd_parser
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


def analyze_given_bone_pmx():
    """test_given_bone.pmxの構造を解析して表示"""
    pmx_path = os.path.join(project_root, "tests", "data", "for_unit_test", "test_given_bone.pmx")
    
    if not os.path.exists(pmx_path):
        print(f"ファイルが見つかりません: {pmx_path}")
        return
    
    # PMXファイルを解析
    pmx_data = mmd_parser.parse_mmd_file(pmx_path)
    
    if not pmx_data:
        print("PMXファイルの解析に失敗しました")
        return
    
    print(f"モデル名: {pmx_data.header.model_name}")
    print(f"モデル名(英語): {pmx_data.header.model_name_english}")
    print(f"コメント: {pmx_data.header.comment}")
    print(f"コメント(英語): {pmx_data.header.comment_english}")
    print(f"頂点数: {len(pmx_data.vertices)}")
    print(f"面数: {len(pmx_data.faces)}")
    print(f"テクスチャ数: {len(pmx_data.textures)}")
    print(f"材質数: {len(pmx_data.materials)}")
    print(f"ボーン数: {len(pmx_data.bones)}")
    print(f"モーフ数: {len(pmx_data.morphs)}")
    print(f"表示枠数: {len(pmx_data.display_frames)}")
    print(f"剛体数: {len(pmx_data.rigid_bodies)}")
    print(f"ジョイント数: {len(pmx_data.joints)}")
    
    print("\n=== ボーン情報 ===")
    for i, bone in enumerate(pmx_data.bones):
        print(f"\n[ボーン {i}]")
        print(f"  名前: {bone.name}")
        print(f"  名前(英語): {bone.name_english}")
        print(f"  位置: {bone.position}")
        print(f"  親ボーン: {bone.parent_bone_index}")
        print(f"  変形階層: {bone.transform_layer}")
        print(f"  ボーンフラグ: 0x{bone.bone_flag:04X}")
        
        # フラグの詳細を表示
        flags = []
        if bone.get_flag(PmxBoneFlag.CONNECT_BONE):
            flags.append("接続先ボーン指定")
            print(f"    接続先ボーン: {bone.connect_bone_index}")
        else:
            print(f"    接続先オフセット: {bone.connect_position_offset}")
        
        if bone.get_flag(PmxBoneFlag.ROTATABLE):
            flags.append("回転可能")
        if bone.get_flag(PmxBoneFlag.MOVABLE):
            flags.append("移動可能")
        if bone.get_flag(PmxBoneFlag.DISPLAY):
            flags.append("表示")
        if bone.get_flag(PmxBoneFlag.OPERATABLE):
            flags.append("操作可能")
        if bone.get_flag(PmxBoneFlag.IK):
            flags.append("IK")
        if bone.get_flag(PmxBoneFlag.LOCAL):
            flags.append("ローカル付与")
        if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE):
            flags.append("回転付与")
        if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
            flags.append("移動付与")
        if bone.get_flag(PmxBoneFlag.AXIS_FIXED):
            flags.append("軸固定")
        if bone.get_flag(PmxBoneFlag.LOCAL_AXIS):
            flags.append("ローカル軸")
        if bone.get_flag(PmxBoneFlag.DEFORM_AFTER_PHYSICS):
            flags.append("物理演算後変形")
        if bone.get_flag(PmxBoneFlag.EXTERNAL_PARENT_DEFORM):
            flags.append("外部親変形")
        
        print(f"  フラグ詳細: {', '.join(flags)}")
        
        # 付与関連の情報
        if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE) or bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
            print(f"  付与親ボーン: {bone.given_parent_bone_index}")
            print(f"  付与率: {bone.given_rate}")
        
        # IK関連の情報
        if bone.get_flag(PmxBoneFlag.IK):
            print(f"  IKターゲット: {bone.ik_target_bone_index}")
            print(f"  IKループ回数: {bone.ik_loop_count}")
            print(f"  IK制限角度: {bone.ik_limit_angle}")
            print(f"  IKリンク数: {len(bone.ik_links)}")


if __name__ == "__main__":
    analyze_given_bone_pmx()