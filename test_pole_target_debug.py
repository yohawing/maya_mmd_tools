"""
ポールターゲット位置計算のデバッグテスト

このスクリプトは、IKチェーンのポールターゲット位置計算の問題を特定するための
デバッグテストを実行します。
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om2
import math


def create_test_leg_chain():
    """テスト用のシンプルな脚のIKチェーンを作成"""
    # シーンをクリア
    cmds.file(new=True, force=True)
    
    # 既知の位置でジョイントを作成
    # 脚の付け根（hip）
    hip_pos = [0, 10, 0]
    knee_pos = [0, 5, -1]  # 膝は少し後ろに曲がっている
    ankle_pos = [0, 0, 0]
    
    cmds.select(clear=True)
    hip_joint = cmds.joint(position=hip_pos, name="test_hip")
    knee_joint = cmds.joint(position=knee_pos, name="test_knee")
    ankle_joint = cmds.joint(position=ankle_pos, name="test_ankle")
    
    # IKハンドルを作成
    ik_handle = cmds.ikHandle(
        startJoint=hip_joint,
        endEffector=ankle_joint,
        solver="ikRPsolver",
        name="test_leg_ik"
    )[0]
    
    return hip_joint, knee_joint, ankle_joint, ik_handle


def calculate_pole_target_position_current_method(hip_joint, knee_joint, ankle_joint):
    """現在の方法でポールターゲット位置を計算"""
    # ジョイントの位置を取得
    hip_pos = cmds.xform(hip_joint, query=True, worldSpace=True, translation=True)
    knee_pos = cmds.xform(knee_joint, query=True, worldSpace=True, translation=True)
    ankle_pos = cmds.xform(ankle_joint, query=True, worldSpace=True, translation=True)
    
    hip_vec = om2.MVector(hip_pos)
    knee_vec = om2.MVector(knee_pos)
    ankle_vec = om2.MVector(ankle_pos)
    
    # 現在の実装方法
    # 脚の方向ベクトル（股関節から足首へ）
    leg_direction = ankle_vec - hip_vec
    leg_direction.normalize()
    
    # 膝の方向ベクトル（股関節から膝へ）
    knee_direction = knee_vec - hip_vec
    knee_direction.normalize()
    
    # 前方向を計算（クロス積）
    forward_direction = leg_direction ^ knee_direction
    forward_direction.normalize()
    
    # ポールターゲットの位置を計算
    offset_distance = (hip_vec - ankle_vec).length() * 0.5
    pole_target_pos = knee_vec + forward_direction * offset_distance
    
    return {
        "hip_pos": hip_pos,
        "knee_pos": knee_pos,
        "ankle_pos": ankle_pos,
        "leg_direction": [leg_direction.x, leg_direction.y, leg_direction.z],
        "knee_direction": [knee_direction.x, knee_direction.y, knee_direction.z],
        "forward_direction": [forward_direction.x, forward_direction.y, forward_direction.z],
        "offset_distance": offset_distance,
        "pole_target_pos": [pole_target_pos.x, pole_target_pos.y, pole_target_pos.z]
    }


def calculate_pole_target_position_alternative_method(hip_joint, knee_joint, ankle_joint):
    """代替方法でポールターゲット位置を計算"""
    # ジョイントの位置を取得
    hip_pos = cmds.xform(hip_joint, query=True, worldSpace=True, translation=True)
    knee_pos = cmds.xform(knee_joint, query=True, worldSpace=True, translation=True)
    ankle_pos = cmds.xform(ankle_joint, query=True, worldSpace=True, translation=True)
    
    hip_vec = om2.MVector(hip_pos)
    knee_vec = om2.MVector(knee_pos)
    ankle_vec = om2.MVector(ankle_pos)
    
    # 代替方法1: 膝の曲がり方向を直接計算
    # 股関節から膝へのベクトル
    hip_to_knee = knee_vec - hip_vec
    # 股関節から足首へのベクトル
    hip_to_ankle = ankle_vec - hip_vec
    
    # 膝の曲がり方向（膝が突き出ている方向）
    # 股関節-足首の直線から膝への垂直ベクトル
    hip_to_ankle_normalized = hip_to_ankle.normal()
    projection = (hip_to_knee * hip_to_ankle_normalized) * hip_to_ankle_normalized
    knee_bend_direction = hip_to_knee - projection
    knee_bend_direction.normalize()
    
    # ポールターゲットの位置を計算
    offset_distance = (hip_vec - ankle_vec).length() * 0.5
    pole_target_pos = knee_vec + knee_bend_direction * offset_distance
    
    return {
        "hip_to_knee": [hip_to_knee.x, hip_to_knee.y, hip_to_knee.z],
        "hip_to_ankle": [hip_to_ankle.x, hip_to_ankle.y, hip_to_ankle.z],
        "knee_bend_direction": [knee_bend_direction.x, knee_bend_direction.y, knee_bend_direction.z],
        "offset_distance": offset_distance,
        "pole_target_pos": [pole_target_pos.x, pole_target_pos.y, pole_target_pos.z]
    }


def visualize_results(results_current, results_alternative, ik_handle):
    """結果を視覚化"""
    # 現在の方法でのポールターゲット
    pole_current = cmds.spaceLocator(name="pole_target_current")[0]
    cmds.xform(pole_current, worldSpace=True, translation=results_current["pole_target_pos"])
    cmds.setAttr(pole_current + ".overrideEnabled", 1)
    cmds.setAttr(pole_current + ".overrideColor", 13)  # 赤
    
    # 代替方法でのポールターゲット
    pole_alternative = cmds.spaceLocator(name="pole_target_alternative")[0]
    cmds.xform(pole_alternative, worldSpace=True, translation=results_alternative["pole_target_pos"])
    cmds.setAttr(pole_alternative + ".overrideEnabled", 1)
    cmds.setAttr(pole_alternative + ".overrideColor", 14)  # 緑
    
    # 方向ベクトルを視覚化（カーブで表示）
    knee_pos = results_current["knee_pos"]
    
    # 現在の方法の前方向ベクトル
    forward_end = [
        knee_pos[0] + results_current["forward_direction"][0] * 3,
        knee_pos[1] + results_current["forward_direction"][1] * 3,
        knee_pos[2] + results_current["forward_direction"][2] * 3
    ]
    curve_current = cmds.curve(degree=1, point=[knee_pos, forward_end], name="forward_direction_current")
    cmds.setAttr(curve_current + ".overrideEnabled", 1)
    cmds.setAttr(curve_current + ".overrideColor", 13)  # 赤
    
    # 代替方法の膝曲がり方向ベクトル
    bend_end = [
        knee_pos[0] + results_alternative["knee_bend_direction"][0] * 3,
        knee_pos[1] + results_alternative["knee_bend_direction"][1] * 3,
        knee_pos[2] + results_alternative["knee_bend_direction"][2] * 3
    ]
    curve_alternative = cmds.curve(degree=1, point=[knee_pos, bend_end], name="knee_bend_direction")
    cmds.setAttr(curve_alternative + ".overrideEnabled", 1)
    cmds.setAttr(curve_alternative + ".overrideColor", 14)  # 緑
    
    # IKハンドルにポールベクターコンストレイントを追加してテスト
    cmds.poleVectorConstraint(pole_alternative, ik_handle)


def main():
    """メインテスト関数"""
    print("=" * 80)
    print("ポールターゲット位置計算デバッグテスト")
    print("=" * 80)
    
    # テスト用のIKチェーンを作成
    hip_joint, knee_joint, ankle_joint, ik_handle = create_test_leg_chain()
    
    # 現在の方法で計算
    results_current = calculate_pole_target_position_current_method(
        hip_joint, knee_joint, ankle_joint
    )
    
    # 代替方法で計算
    results_alternative = calculate_pole_target_position_alternative_method(
        hip_joint, knee_joint, ankle_joint
    )
    
    # 結果を出力
    print("\n現在の方法:")
    print(f"  股関節位置: {results_current['hip_pos']}")
    print(f"  膝位置: {results_current['knee_pos']}")
    print(f"  足首位置: {results_current['ankle_pos']}")
    print(f"  脚方向ベクトル: {results_current['leg_direction']}")
    print(f"  膝方向ベクトル: {results_current['knee_direction']}")
    print(f"  前方向ベクトル（クロス積）: {results_current['forward_direction']}")
    print(f"  オフセット距離: {results_current['offset_distance']}")
    print(f"  ポールターゲット位置: {results_current['pole_target_pos']}")
    
    print("\n代替方法:")
    print(f"  股関節→膝ベクトル: {results_alternative['hip_to_knee']}")
    print(f"  股関節→足首ベクトル: {results_alternative['hip_to_ankle']}")
    print(f"  膝曲がり方向: {results_alternative['knee_bend_direction']}")
    print(f"  オフセット距離: {results_alternative['offset_distance']}")
    print(f"  ポールターゲット位置: {results_alternative['pole_target_pos']}")
    
    # 視覚化
    visualize_results(results_current, results_alternative, ik_handle)
    
    print("\n視覚化完了:")
    print("  赤いロケーター: 現在の方法によるポールターゲット")
    print("  緑のロケーター: 代替方法によるポールターゲット")
    print("  赤いライン: 現在の方法の前方向ベクトル")
    print("  緑のライン: 代替方法の膝曲がり方向ベクトル")
    
    # 期待される動作の確認
    print("\n期待される動作:")
    print("  - ポールターゲットは膝が曲がっている方向（前方）に配置されるべき")
    print("  - この例では、膝がZ=-1の位置にあるため、ポールターゲットはZ軸の負の方向に配置されるべき")
    
    # 問題の分析
    current_z = results_current['pole_target_pos'][2]
    alternative_z = results_alternative['pole_target_pos'][2]
    
    print("\n分析:")
    print(f"  現在の方法のZ座標: {current_z}")
    print(f"  代替方法のZ座標: {alternative_z}")
    
    if current_z > 0 and alternative_z < 0:
        print("  → 現在の方法は逆方向にポールターゲットを配置している可能性があります")
    elif abs(current_z) < 0.1:
        print("  → 現在の方法はクロス積が0に近い値を返している可能性があります")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()