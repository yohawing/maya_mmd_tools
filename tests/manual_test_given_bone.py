#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
付与ボーンPMXファイルのMayaインポート動作確認スクリプト
Maya内で実行してください
"""

import os
import maya.cmds as cmds

# プロジェクトのルートディレクトリを取得
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# mmd_toolsをインポートパスに追加
import sys
sys.path.insert(0, project_root)

from mmd_tools.maya_plugin.importer.pmx_importer import PmxImporter
from mmd_tools.core import mmd_parser


def test_given_bone_import():
    """付与ボーンPMXファイルをインポートして検証"""
    
    # 新しいシーンを作成
    cmds.file(new=True, force=True)
    
    # 包括的な付与ボーン検証用PMXファイルのパス
    pmx_path = os.path.join(
        project_root, "tests", "data", "for_unit_test", 
        "test_given_bone_comprehensive.pmx"
    )
    
    if not os.path.exists(pmx_path):
        print(f"エラー: テストファイルが見つかりません: {pmx_path}")
        return
    
    print(f"PMXファイルをインポート: {pmx_path}")
    
    # PMXファイルを解析
    pmx_data = mmd_parser.parse_mmd_file(pmx_path)
    
    # インポーターを作成してインポート実行
    importer = PmxImporter()
    root_group = importer.import_pmx(pmx_data, pmx_path)
    
    print(f"インポート完了: ルートグループ = {root_group}")
    
    # インポートされたボーンを確認
    joints = cmds.listRelatives(root_group, allDescendents=True, type="joint") or []
    print(f"\nインポートされたジョイント数: {len(joints)}")
    
    # 付与ボーンの属性を確認
    print("\n=== 付与ボーンの確認 ===")
    for joint in joints:
        # MMDボーン属性を取得
        if cmds.attributeQuery("mmd_bone_flags", node=joint, exists=True):
            flags = cmds.getAttr(f"{joint}.mmd_bone_flags")
            
            # 付与フラグをチェック（0x0100: 回転付与, 0x0200: 移動付与）
            if flags & 0x0300:
                print(f"\n[{joint}]")
                print(f"  フラグ: 0x{flags:04X}")
                
                # 付与関連の属性を確認
                attrs = [
                    "mmd_given_parent_bone_index",
                    "mmd_given_rate",
                    "mmd_is_local_given"
                ]
                
                for attr in attrs:
                    if cmds.attributeQuery(attr, node=joint, exists=True):
                        value = cmds.getAttr(f"{joint}.{attr}")
                        print(f"  {attr}: {value}")
    
    # ビューポートで表示
    cmds.viewFit(all=True)
    cmds.select(root_group)
    
    print("\n付与ボーンのインポートテストが完了しました。")
    print("Mayaのアウトライナーとアトリビュートエディタで確認してください。")


def test_given_bone_animation():
    """付与ボーンのアニメーション動作を確認"""
    
    # 既にインポートされているか確認
    if not cmds.objExists("付与ボーン検証モデル"):
        print("先にtest_given_bone_import()を実行してください")
        return
    
    print("\n=== 付与ボーンアニメーションテスト ===")
    
    # アニメーション範囲を設定
    cmds.playbackOptions(min=1, max=120, animationStartTime=1, animationEndTime=120)
    
    # テスト用のキーフレームを設定
    test_bones = [
        "子ボーン1",  # 付与元として使用
    ]
    
    for bone_name in test_bones:
        joints = cmds.ls(f"*{bone_name}*", type="joint")
        if joints:
            joint = joints[0]
            print(f"\n{joint}にアニメーションを設定")
            
            # 回転アニメーション
            cmds.setKeyframe(joint, attribute="rotateZ", value=0, time=1)
            cmds.setKeyframe(joint, attribute="rotateZ", value=45, time=30)
            cmds.setKeyframe(joint, attribute="rotateZ", value=-45, time=60)
            cmds.setKeyframe(joint, attribute="rotateZ", value=0, time=90)
            
            # 移動アニメーション
            cmds.setKeyframe(joint, attribute="translateY", value=0, time=1)
            cmds.setKeyframe(joint, attribute="translateY", value=2, time=45)
            cmds.setKeyframe(joint, attribute="translateY", value=0, time=90)
    
    print("\nアニメーションを設定しました。")
    print("タイムラインを再生して付与ボーンの動作を確認してください。")
    print("特に以下のボーンの動きに注目してください：")
    print("- グローバル回転付与2")
    print("- グローバル移動付与3")
    print("- グローバル回転移動付与4")
    print("- 付与チェーン8A, 8B（多重付与）")


if __name__ == "__main__":
    # Maya内で実行する場合
    test_given_bone_import()
    # test_given_bone_animation()  # アニメーションテストも実行する場合