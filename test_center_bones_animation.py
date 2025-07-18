"""センター系ボーンのみのアニメーション検証スクリプト

このスクリプトは、VMDアニメーションをセンター系ボーンのみに適用して
モーション崩れの原因を特定するための検証を行います。
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om2
import math
from typing import Dict, List, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.vmd_parser import VmdParser
from mmd_tools.converters.vmd_converter import VmdConverter


class CenterBonesAnimationTest:
    """センター系ボーンのアニメーション検証クラス"""

    def __init__(self):
        self.logger = get_logger(__name__)
        self.center_bones = ["センター", "グルーブ", "上半身", "上半身2"]
        self.test_results = {}

    def run_test(self, vmd_file_path: str):
        """検証を実行

        Args:
            vmd_file_path: VMDファイルのパス
        """
        self.logger.info("=" * 60)
        self.logger.info("センター系ボーンアニメーション検証開始")
        self.logger.info("=" * 60)

        # VMDファイルを読み込み
        parser = VmdParser()
        if not parser.parse_file(vmd_file_path):
            self.logger.error("VMDファイルの読み込みに失敗しました")
            return

        # センター系ボーンのフレームデータを抽出
        center_frames = self._extract_center_bone_frames(parser.bone_frames)

        # 各ボーンの初期状態を記録
        self._record_initial_state()

        # 段階的にアニメーションを適用して検証
        self.logger.info("\n--- 検証1: 移動のみを適用 ---")
        self._test_translation_only(center_frames)

        self.logger.info("\n--- 検証2: 回転のみを適用 ---")
        self._test_rotation_only(center_frames)

        self.logger.info("\n--- 検証3: 移動と回転を両方適用 ---")
        self._test_full_animation(center_frames)

        # 検証結果をまとめて出力
        self._print_test_summary()

    def _extract_center_bone_frames(
        self, bone_frames: List
    ) -> Dict[str, List]:
        """センター系ボーンのフレームデータを抽出"""
        center_frames = {}

        for frame in bone_frames:
            bone_name = frame.bone_name
            if bone_name in self.center_bones:
                if bone_name not in center_frames:
                    center_frames[bone_name] = []
                center_frames[bone_name].append(frame)

        # フレーム番号でソート
        for bone_name in center_frames:
            center_frames[bone_name].sort(key=lambda x: x.frame_number)

        self.logger.info(f"抽出されたセンター系ボーン: {list(center_frames.keys())}")
        for bone_name, frames in center_frames.items():
            self.logger.info(f"  {bone_name}: {len(frames)}フレーム")

        return center_frames

    def _record_initial_state(self):
        """各ボーンの初期状態を記録"""
        self.logger.info("\n初期状態の記録:")

        joints = cmds.ls(type="joint")
        for joint in joints:
            # PMXボーン名を取得
            if cmds.attributeQuery("pmx_bone_name", node=joint, exists=True):
                pmx_name = cmds.getAttr(f"{joint}.pmx_bone_name")
                if pmx_name in self.center_bones:
                    translate = cmds.getAttr(f"{joint}.translate")[0]
                    rotate = cmds.getAttr(f"{joint}.rotate")[0]
                    joint_orient = cmds.getAttr(f"{joint}.jointOrient")[0]

                    self.logger.info(f"\n{pmx_name} ({joint}):")
                    self.logger.info(f"  translate: {translate}")
                    self.logger.info(f"  rotate: {rotate}")
                    self.logger.info(f"  jointOrient: {joint_orient}")

    def _test_translation_only(self, center_frames: Dict[str, List]):
        """移動のみを適用してテスト"""
        # アニメーションをクリア
        self._clear_animation()

        # センターボーンのみ移動を適用
        if "センター" in center_frames:
            maya_joint = self._find_maya_joint("センター")
            if maya_joint:
                frames = center_frames["センター"]
                self.logger.info(
                    f"\nセンターボーンに移動のみ適用 (フレーム数: {len(frames)})"
                )

                # 最初の5フレームのデータを出力
                for i, frame in enumerate(frames[:5]):
                    frame_num = frame.frame_number
                    position = frame.position

                    # VMDの生データ
                    self.logger.info(f"\nフレーム {frame_num}:")
                    self.logger.info(f"  VMD position: {position}")

                    # Maya用に変換（Z軸反転）
                    maya_pos = [position[0], position[1], -position[2]]
                    self.logger.info(f"  Maya position: {maya_pos}")

                    # キーフレーム設定
                    cmds.setKeyframe(
                        maya_joint,
                        attribute="translateX",
                        time=frame_num,
                        value=maya_pos[0],
                    )
                    cmds.setKeyframe(
                        maya_joint,
                        attribute="translateY",
                        time=frame_num,
                        value=maya_pos[1],
                    )
                    cmds.setKeyframe(
                        maya_joint,
                        attribute="translateZ",
                        time=frame_num,
                        value=maya_pos[2],
                    )

                # 適用後の状態を確認
                cmds.currentTime(0)
                translate = cmds.getAttr(f"{maya_joint}.translate")[0]
                self.logger.info(f"\n適用後のセンター位置 (フレーム0): {translate}")

    def _test_rotation_only(self, center_frames: Dict[str, List]):
        """回転のみを適用してテスト"""
        # アニメーションをクリア
        self._clear_animation()

        # センターボーンのみ回転を適用
        if "センター" in center_frames:
            maya_joint = self._find_maya_joint("センター")
            if maya_joint:
                frames = center_frames["センター"]
                self.logger.info(
                    f"\nセンターボーンに回転のみ適用 (フレーム数: {len(frames)})"
                )

                # 最初の5フレームのデータを出力
                for i, frame in enumerate(frames[:5]):
                    frame_num = frame.frame_number
                    rotation = frame.rotation

                    # VMDの生データ（クォータニオン）
                    self.logger.info(f"\nフレーム {frame_num}:")
                    self.logger.info(f"  VMD quaternion: {rotation}")

                    # クォータニオンからオイラー角への変換
                    euler = self._quaternion_to_euler_debug(rotation)
                    self.logger.info(f"  Euler (degrees): {euler}")

                    # キーフレーム設定
                    cmds.setKeyframe(
                        maya_joint, attribute="rotateX", time=frame_num, value=euler[0]
                    )
                    cmds.setKeyframe(
                        maya_joint, attribute="rotateY", time=frame_num, value=euler[1]
                    )
                    cmds.setKeyframe(
                        maya_joint, attribute="rotateZ", time=frame_num, value=euler[2]
                    )

                # 適用後の状態を確認
                cmds.currentTime(0)
                rotate = cmds.getAttr(f"{maya_joint}.rotate")[0]
                self.logger.info(f"\n適用後のセンター回転 (フレーム0): {rotate}")

    def _test_full_animation(self, center_frames: Dict[str, List]):
        """移動と回転を両方適用してテスト"""
        # アニメーションをクリア
        self._clear_animation()

        # VmdConverterを使用して通常の変換処理を実行（センター系のみ）
        converter = VmdConverter()

        # センター系ボーンのみのマッピングを構築
        converter.bone_name_mapping = {}
        for bone_name in self.center_bones:
            maya_joint = self._find_maya_joint(bone_name)
            if maya_joint:
                converter.bone_name_mapping[bone_name] = maya_joint

        self.logger.info(f"\nセンター系ボーンマッピング: {converter.bone_name_mapping}")

        # センター系のフレームデータのみを含むリストを作成
        # VmdConverterは辞書形式のデータを期待するので、変換する
        filtered_frames = []
        for bone_name, frames in center_frames.items():
            for frame in frames:
                frame_dict = {
                    "bone_name": frame.bone_name,
                    "frame_number": frame.frame_number,
                    "position": frame.position,
                    "rotation": frame.rotation
                }
                filtered_frames.append(frame_dict)

        # アニメーション変換を実行
        converter._convert_bone_animation(filtered_frames)

        # 適用後の状態を確認
        self.logger.info("\n適用後の各ボーンの状態:")
        for bone_name in self.center_bones:
            maya_joint = self._find_maya_joint(bone_name)
            if maya_joint:
                cmds.currentTime(0)
                translate = cmds.getAttr(f"{maya_joint}.translate")[0]
                rotate = cmds.getAttr(f"{maya_joint}.rotate")[0]
                self.logger.info(f"\n{bone_name}:")
                self.logger.info(f"  translate: {translate}")
                self.logger.info(f"  rotate: {rotate}")

    def _quaternion_to_euler_debug(
        self, quat: List[float]
    ) -> Tuple[float, float, float]:
        """クォータニオンをオイラー角に変換（デバッグ情報付き）"""
        # VMDのクォータニオンは左手系、Z軸の向きが逆
        self.logger.info(f"  Original quaternion: {quat}")

        # Z成分を反転
        maya_quat = om2.MQuaternion(quat[0], quat[1], -quat[2], quat[3])
        self.logger.info(
            f"  Maya quaternion (Z反転): [{maya_quat.x}, {maya_quat.y}, {maya_quat.z}, {maya_quat.w}]"
        )

        # 正規化
        maya_quat = maya_quat.normal()
        self.logger.info(
            f"  Normalized: [{maya_quat.x}, {maya_quat.y}, {maya_quat.z}, {maya_quat.w}]"
        )

        # オイラー角に変換
        euler = maya_quat.asEulerRotation()

        # ラジアンから度に変換
        rx = math.degrees(euler.x)
        ry = math.degrees(euler.y)
        rz = math.degrees(euler.z)

        return (rx, ry, rz)

    def _find_maya_joint(self, pmx_bone_name: str) -> str:
        """PMXボーン名からMayaジョイントを検索"""
        joints = cmds.ls(type="joint")
        for joint in joints:
            if cmds.attributeQuery("pmx_bone_name", node=joint, exists=True):
                if cmds.getAttr(f"{joint}.pmx_bone_name") == pmx_bone_name:
                    return joint
        return None

    def _clear_animation(self):
        """全てのアニメーションをクリア"""
        # センター系ボーンのアニメーションカーブを削除
        for bone_name in self.center_bones:
            maya_joint = self._find_maya_joint(bone_name)
            if maya_joint:
                attrs = [
                    "translateX",
                    "translateY",
                    "translateZ",
                    "rotateX",
                    "rotateY",
                    "rotateZ",
                ]
                for attr in attrs:
                    connections = cmds.listConnections(
                        f"{maya_joint}.{attr}", source=True, destination=False
                    )
                    if connections:
                        cmds.delete(connections)

    def _print_test_summary(self):
        """検証結果のまとめを出力"""
        self.logger.info("\n" + "=" * 60)
        self.logger.info("検証結果まとめ")
        self.logger.info("=" * 60)
        self.logger.info("1. 移動のみ: Z軸反転で座標系変換を実施")
        self.logger.info("2. 回転のみ: クォータニオンのZ成分を反転して変換")
        self.logger.info("3. 両方適用: VmdConverterの標準処理を使用")
        self.logger.info("\n次のステップ:")
        self.logger.info("- 親子関係を含めた検証")
        self.logger.info("- jointOrientの影響を確認")
        self.logger.info("- 他のボーンとの相互作用を検証")


# 使用例
if __name__ == "__main__":
    # VMDファイルパスを指定して実行
    # tester = CenterBonesAnimationTest()
    # tester.run_test("path/to/your/vmd/file.vmd")
    pass
