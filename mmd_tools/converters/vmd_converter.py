"""VMDファイルをMayaアニメーションに変換するモジュール

このモジュールは、MikuMikuDance (MMD)のモーションデータファイル（VMD）を
Mayaのアニメーションデータに変換する機能を提供します。

フェーズ1では以下の基本機能を実装：
- ボーンの位置・回転アニメーション変換
- 線形補間のみサポート
- 基本的なエラーハンドリング
"""

import struct
from typing import Dict, List, Tuple, Optional, Set
import maya.cmds as cmds
import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2

from mmd_tools.core.logger import get_logger
from mmd_tools.core.vmd_parser import VmdParser


class VmdConverter:
    """VMDデータをMayaアニメーションに変換するクラス

    VMDファイルに含まれるボーンアニメーションとモーフアニメーションを
    Mayaのジョイントアニメーションとブレンドシェイプアニメーションに変換します。
    """

    # 相対位置として扱うボーン（MMDの仕様）
    RELATIVE_POSITION_BONES: Set[str] = {
        "センター", "グルーブ", "腰", "上半身", "上半身2", "上半身3",
        "首", "頭", "左肩", "右肩", "左腕", "右腕", "左ひじ", "右ひじ",
        "左手首", "右手首", "左足", "右足", "左ひざ", "右ひざ",
        "左足首", "右足首", "左つま先", "右つま先"
    }

    def __init__(self):
        """VmdConverterの初期化"""
        self.bone_name_mapping: Dict[str, str] = {}  # VMDボーン名 -> Mayaジョイント名
        self.morph_name_mapping: Dict[
            str, str
        ] = {}  # VMDモーフ名 -> Mayaブレンドシェイプターゲット名
        self.fps = 30.0  # デフォルトのFPS
        self.logger = get_logger(self.__class__.__name__)
        self._failed_bones = set()  # 変換に失敗したボーン名を記録
        self._bone_bind_poses: Dict[str, Tuple[float, float, float]] = {}  # ボーンの初期位置

    def convert(self, vmd_data: VmdParser, target_namespace: str = None) -> bool:
        """VMDデータをMayaアニメーションに変換

        Args:
            vmd_data: パース済みのVMDデータ
            target_namespace: 対象となるネームスペース（省略可）

        Returns:
            変換が成功した場合True、失敗した場合False
        """
        try:
            self.logger.info("VMDアニメーション変換を開始します")

            # 名前マッピングの構築
            self._build_name_mappings(target_namespace)

            # ボーンの初期位置を記録
            self._record_bind_poses()

            # タイムライン設定
            self._setup_timeline(vmd_data)

            # ボーンアニメーション変換
            if hasattr(vmd_data, "bone_frames") and vmd_data.bone_frames:
                self.logger.info(
                    f"ボーンアニメーション変換を開始: {len(vmd_data.bone_frames)}フレーム"
                )
                bone_success = self._convert_bone_animation(vmd_data.bone_frames)
                if not bone_success:
                    self.logger.warning(
                        "ボーンアニメーション変換で一部エラーが発生しました"
                    )

            # フェーズ1では線形補間のみのため、補間データは無視

            self.logger.info("VMDアニメーション変換が完了しました")
            return True

        except Exception as e:
            self.logger.error(
                f"VMDアニメーション変換中にエラーが発生しました: {str(e)}"
            )
            return False

    def _build_name_mappings(self, target_namespace: str = None):
        """ボーン名とモーフ名のマッピングを構築

        Args:
            target_namespace: 対象となるネームスペース
        """
        self.logger.info("名前マッピングを構築しています")

        # シーン内のジョイントを検索
        if target_namespace:
            joints = cmds.ls(f"{target_namespace}:*", type="joint")
        else:
            joints = cmds.ls(type="joint")

        # カスタム属性から元のボーン名を取得
        for joint in joints:
            # PMXボーン名属性をチェック
            if cmds.attributeQuery("pmx_bone_name", node=joint, exists=True):
                original_name = cmds.getAttr(f"{joint}.pmx_bone_name")
                if original_name:
                    self.bone_name_mapping[original_name] = joint
                    self.logger.debug(f"ボーンマッピング: {original_name} -> {joint}")

            # PMDボーン名属性もチェック（後方互換性）
            elif cmds.attributeQuery("pmd_bone_name", node=joint, exists=True):
                original_name = cmds.getAttr(f"{joint}.pmd_bone_name")
                if original_name:
                    self.bone_name_mapping[original_name] = joint
                    self.logger.debug(f"ボーンマッピング: {original_name} -> {joint}")

        self.logger.info(
            f"{len(self.bone_name_mapping)}個のボーンマッピングを構築しました"
        )

    def _record_bind_poses(self):
        """各ボーンの初期位置（バインドポーズ）を記録"""
        self.logger.info("ボーンの初期位置を記録しています")
        
        for vmd_bone_name, maya_joint in self.bone_name_mapping.items():
            try:
                # 現在のtranslate値を取得（これがバインドポーズ）
                translate = cmds.getAttr(f"{maya_joint}.translate")[0]
                self._bone_bind_poses[vmd_bone_name] = translate
                self.logger.debug(f"{vmd_bone_name}: バインドポーズ {translate}")
            except Exception as e:
                self.logger.warning(f"{vmd_bone_name}のバインドポーズ取得エラー: {str(e)}")

    def _setup_timeline(self, vmd_data: VmdParser):
        """タイムラインの設定

        Args:
            vmd_data: パース済みのVMDデータ
        """
        # 最大フレーム番号を取得
        max_frame = 0
        if hasattr(vmd_data, "bone_frames"):
            for frame_data in vmd_data.bone_frames:
                # VmdBoneFrameオブジェクトの場合は属性アクセス、辞書の場合はget
                if hasattr(frame_data, 'frame_number'):
                    max_frame = max(max_frame, frame_data.frame_number)
                else:
                    max_frame = max(max_frame, frame_data.get("frame_number", 0))

        if max_frame > 0:
            # タイムラインの範囲を設定
            cmds.playbackOptions(
                min=0, max=max_frame, animationStartTime=0, animationEndTime=max_frame
            )
            self.logger.info(f"タイムライン範囲を設定: 0 - {max_frame}")

    def _convert_bone_animation(self, bone_frames: List) -> bool:
        """ボーンアニメーションを変換

        Args:
            bone_frames: ボーンフレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        # ボーンごとにフレームデータをグループ化
        bone_frame_map: Dict[str, List] = {}

        for frame in bone_frames:
            # VmdBoneFrameオブジェクトの場合は属性アクセス、辞書の場合はget
            if hasattr(frame, 'bone_name'):
                bone_name = frame.bone_name
            else:
                bone_name = frame.get("bone_name", "")
            if bone_name not in bone_frame_map:
                bone_frame_map[bone_name] = []
            bone_frame_map[bone_name].append(frame)

        success_count = 0
        total_count = len(bone_frame_map)

        # 各ボーンのアニメーションを設定
        for vmd_bone_name, frames in bone_frame_map.items():
            if vmd_bone_name in self.bone_name_mapping:
                maya_joint = self.bone_name_mapping[vmd_bone_name]

                try:
                    # フレームをフレーム番号でソート
                    frames.sort(key=lambda x: x.frame_number if hasattr(x, 'frame_number') else x.get("frame_number", 0))

                    # 位置と回転のキーフレームを設定
                    self._set_bone_keyframes(maya_joint, frames, vmd_bone_name)
                    success_count += 1

                except Exception as e:
                    self.logger.error(
                        f"ボーン '{vmd_bone_name}' のアニメーション設定中にエラー: {str(e)}"
                    )
                    self._failed_bones.add(vmd_bone_name)
            else:
                if vmd_bone_name not in self._failed_bones:
                    self.logger.warning(f"ボーン '{vmd_bone_name}' が見つかりません")
                    self._failed_bones.add(vmd_bone_name)

        self.logger.info(
            f"{success_count}/{total_count}個のボーンアニメーションを変換しました"
        )
        return success_count > 0

    def _set_bone_keyframes(self, joint: str, frames: List, vmd_bone_name: str):
        """ボーンのキーフレームを設定

        Args:
            joint: Mayaジョイント名
            frames: フレームデータのリスト
            vmd_bone_name: VMDボーン名
        """
        # 既存のアニメーションカーブをクリア
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
                f"{joint}.{attr}", source=True, destination=False
            )
            if connections:
                cmds.delete(connections)

        # バインドポーズを取得
        bind_pose = self._bone_bind_poses.get(vmd_bone_name, (0, 0, 0))

        # 各フレームでキーを設定
        for frame_data in frames:
            # VmdBoneFrameオブジェクトか辞書かで処理を分岐
            if hasattr(frame_data, 'frame_number'):
                frame_num = frame_data.frame_number
                position = frame_data.position
                rotation_quat = frame_data.rotation
            else:
                frame_num = frame_data.get("frame_number", 0)
                position = frame_data.get("position", [0, 0, 0])
                rotation_quat = frame_data.get("rotation", [0, 0, 0, 1])

            # ボーンタイプに応じて位置を処理
            if vmd_bone_name in self.RELATIVE_POSITION_BONES:
                # 相対位置として処理（バインドポーズに加算）
                maya_position = [
                    bind_pose[0] + position[0],
                    bind_pose[1] + position[1],
                    bind_pose[2] - position[2]  # Z軸反転
                ]
            else:
                # 絶対位置として処理（従来の処理）
                maya_position = [position[0], position[1], -position[2]]

            # クォータニオンをオイラー角に変換
            euler_rotation = self._quaternion_to_euler(rotation_quat)

            # キーフレーム設定
            cmds.setKeyframe(
                joint, attribute="translateX", time=frame_num, value=maya_position[0]
            )
            cmds.setKeyframe(
                joint, attribute="translateY", time=frame_num, value=maya_position[1]
            )
            cmds.setKeyframe(
                joint, attribute="translateZ", time=frame_num, value=maya_position[2]
            )

            cmds.setKeyframe(
                joint, attribute="rotateX", time=frame_num, value=euler_rotation[0]
            )
            cmds.setKeyframe(
                joint, attribute="rotateY", time=frame_num, value=euler_rotation[1]
            )
            cmds.setKeyframe(
                joint, attribute="rotateZ", time=frame_num, value=euler_rotation[2]
            )

        # フェーズ1では線形補間のみ
        # タンジェントタイプを線形に設定
        for attr in attrs:
            anim_curve = cmds.listConnections(
                f"{joint}.{attr}", source=True, destination=False
            )
            if anim_curve:
                cmds.keyTangent(
                    anim_curve[0],
                    edit=True,
                    inTangentType="linear",
                    outTangentType="linear",
                )

    def _quaternion_to_euler(self, quat: List[float]) -> Tuple[float, float, float]:
        """クォータニオンをオイラー角（度）に変換

        Args:
            quat: クォータニオン [x, y, z, w]

        Returns:
            オイラー角（度）のタプル (rx, ry, rz)
        """
        # Maya API 2.0のMQuaternionを使用
        # VMDのクォータニオンは左手系、Z軸の向きが逆
        # Z成分を反転してMayaの座標系に合わせる
        maya_quat = om2.MQuaternion(quat[0], quat[1], -quat[2], quat[3])

        # 正規化（念のため）
        maya_quat = maya_quat.normal()

        # オイラー角に変換
        euler = maya_quat.asEulerRotation()

        # ラジアンから度に変換
        import math

        rx = math.degrees(euler.x)
        ry = math.degrees(euler.y)
        rz = math.degrees(euler.z)

        return (rx, ry, rz)

    def get_failed_bones(self) -> set:
        """変換に失敗したボーン名のセットを取得

        Returns:
            失敗したボーン名のセット
        """
        return self._failed_bones.copy()

    def set_bone_name_mapping(self, mapping: Dict[str, str]):
        """ボーン名マッピングを設定

        Args:
            mapping: VMDボーン名 -> Mayaジョイント名のマッピング
        """
        self.bone_name_mapping = mapping.copy()
