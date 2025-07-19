"""VMDファイルをMayaアニメーションに変換するモジュール

このモジュールは、MikuMikuDance (MMD)のモーションデータファイル（VMD）を
Mayaのアニメーションデータに変換する機能を提供します。

フェーズ1では以下の基本機能を実装：
- ボーンの位置・回転アニメーション変換
- 線形補間のみサポート
- 基本的なエラーハンドリング
"""

import struct
import math
from typing import Dict, List, Tuple, Optional, Set
import maya.cmds as cmds
import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2

from mmd_tools.core.logger import get_logger
from mmd_tools.core.vmd_parser import VmdParser
from mmd_tools.core import maya_utils
from mmd_tools.core import utils


class VmdConverter:
    """VMDデータをMayaアニメーションに変換するクラス

    VMDファイルに含まれるボーンアニメーションとモーフアニメーションを
    Mayaのジョイントアニメーションとブレンドシェイプアニメーションに変換します。
    """

    # 相対位置として扱うボーン（MMDの仕様）
    RELATIVE_POSITION_BONES: Set[str] = {
        "センター",
        "グルーブ",
        "腰",
        "上半身",
        "上半身2",
        "上半身3",
        "首",
        "頭",
        "左肩",
        "右肩",
        "左腕",
        "右腕",
        "左ひじ",
        "右ひじ",
        "左手首",
        "右手首",
        "左足",
        "右足",
        "左ひざ",
        "右ひざ",
        "左足首",
        "右足首",
        "左つま先",
        "右つま先",
        "左足ＩＫ",
        "右足ＩＫ",
        "左つま先ＩＫ",
        "右つま先ＩＫ",
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
        self._bone_bind_poses: Dict[
            str, Tuple[float, float, float]
        ] = {}  # ボーンの初期位置
        self.use_quaternion_interpolation = True  # Quaternion補間の使用フラグ
        self.generate_pole_vectors = True  # PoleVector自動生成フラグ
        self._pole_targets: Dict[str, str] = {}  # IKボーン名 -> PoleTarget名のマッピング
        self._ik_info: Dict[str, Dict] = {}  # IK情報の保存

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

            # IK情報を収集（PoleVector生成用）
            if self.generate_pole_vectors:
                self._collect_ik_info()

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

            # PMDボーン名属性もチェック（後方互換性）
            elif cmds.attributeQuery("pmd_bone_name", node=joint, exists=True):
                original_name = cmds.getAttr(f"{joint}.pmd_bone_name")
                if original_name:
                    self.bone_name_mapping[original_name] = joint

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
            except Exception as e:
                self.logger.warning(
                    f"{vmd_bone_name}のバインドポーズ取得エラー: {str(e)}"
                )

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
                if hasattr(frame_data, "frame_number"):
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
            if hasattr(frame, "bone_name"):
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
                    frames.sort(
                        key=lambda x: x.frame_number
                        if hasattr(x, "frame_number")
                        else x.get("frame_number", 0)
                    )

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
                    self.logger.info(f"ボーン '{vmd_bone_name}' が見つかりません")
                    self._failed_bones.add(vmd_bone_name)

        # PoleVectorキーフレームを生成
        if self.generate_pole_vectors:
            self._create_pole_vectors_for_ik(bone_frame_map)

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
        # アニメーションカーブを作成
        attrs = [
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
        ]
        curves = maya_utils.create_animation_curves(joint, attrs)

        # バインドポーズを取得
        bind_pose = self._bone_bind_poses.get(vmd_bone_name, (0, 0, 0))

        # 値生成関数を定義
        def generate_values(frame_data):
            # VmdBoneFrameオブジェクトか辞書かで処理を分岐
            if hasattr(frame_data, "frame_number"):
                position = frame_data.position
                rotation_quat = frame_data.rotation
            else:
                position = frame_data.get("position", [0, 0, 0])
                rotation_quat = frame_data.get("rotation", [0, 0, 0, 1])

            # ボーンタイプに応じて位置を処理
            if vmd_bone_name in self.RELATIVE_POSITION_BONES:
                # 相対位置として処理（バインドポーズに加算）
                maya_position = [
                    bind_pose[0] + position[0],
                    bind_pose[1] + position[1],
                    bind_pose[2] - position[2],  # Z軸反転
                ]
            else:
                # 絶対位置として処理（従来の処理）
                maya_position = [position[0], position[1], -position[2]]

            # クォータニオンをオイラー角に変換
            euler_rotation = self._quaternion_to_euler(rotation_quat)

            return {
                "translateX": maya_position[0],
                "translateY": maya_position[1],
                "translateZ": maya_position[2],
                "rotateX": euler_rotation[0],
                "rotateY": euler_rotation[1],
                "rotateZ": euler_rotation[2],
            }

        # キーフレームを一括設定
        maya_utils.set_keyframes_batch(curves, frames, generate_values)

        # Quaternion補間を適用
        if self.use_quaternion_interpolation:
            try:
                # rotationInterpolationコマンドでQuaternion補間に変換
                cmds.rotationInterpolation(
                    f"{joint}.rotateX",
                    f"{joint}.rotateY",
                    f"{joint}.rotateZ",
                    convert="quaternion",  # "quaternionSquad"も選択可能（より滑らか）
                )
            except Exception as e:
                self.logger.warning(f"{joint}へのQuaternion補間適用に失敗: {str(e)}")

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

        rx = math.degrees(euler.x)
        ry = math.degrees(euler.y) * -1  # Y軸は反転
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

    def _collect_ik_info(self):
        """シーン内のIK情報を収集する"""
        self.logger.info("IK情報を収集しています")
        
        # シーン内のすべてのikHandleを検索
        ik_handles = cmds.ls(type="ikHandle")
        
        for ik_handle in ik_handles:
            # カスタムアトリビュートからMMDのIK情報を取得
            if cmds.attributeQuery("mmd_ik_bone", node=ik_handle, exists=True):
                ik_bone = cmds.getAttr(f"{ik_handle}.mmd_ik_bone")
                
                # PoleTargetを検索
                pole_targets = cmds.ls("*poleTarget*", type="transform")
                for pole_target in pole_targets:
                    if cmds.attributeQuery("mmd_ik_handle", node=pole_target, exists=True):
                        if cmds.getAttr(f"{pole_target}.mmd_ik_handle") == ik_handle:
                            self._pole_targets[ik_bone] = pole_target
                            
                            # IK情報を保存
                            self._ik_info[ik_bone] = {
                                "ik_handle": ik_handle,
                                "pole_target": pole_target,
                                "start_joint": cmds.ikHandle(ik_handle, query=True, startJoint=True),
                                "end_joint": cmds.ikHandle(ik_handle, query=True, endEffector=True)
                            }
                            
                            self.logger.info(f"IK情報を収集: {ik_bone} -> {pole_target}")
                            break

    def _create_pole_vectors_for_ik(self, bone_frame_map: Dict[str, List]):
        """足IKのPoleVectorキーフレームを生成する
        
        Args:
            bone_frame_map: ボーン名ごとのフレームデータ
        """
        if not self.generate_pole_vectors:
            return
            
        self.logger.info("PoleVectorキーフレームの生成を開始します")
        
        # 足IKと対応する太ももボーンのマッピング
        leg_ik_mapping = {
            "左足ＩＫ": "左足",
            "右足ＩＫ": "右足",
            "left_leg_ik": "left_leg",
            "right_leg_ik": "right_leg",
        }
        
        for ik_bone_vmd, thigh_bone_vmd in leg_ik_mapping.items():
            # VMD名からMayaジョイント名を取得
            if ik_bone_vmd not in self.bone_name_mapping:
                continue
                
            ik_bone_maya = self.bone_name_mapping[ik_bone_vmd]
            
            # 対応するPoleTargetが存在するか確認
            if ik_bone_maya not in self._pole_targets:
                continue
                
            pole_target = self._pole_targets[ik_bone_maya]
            
            # 太ももボーンのフレームデータを取得
            if thigh_bone_vmd not in bone_frame_map:
                self.logger.warning(f"{thigh_bone_vmd}のフレームデータが見つかりません")
                continue
                
            thigh_frames = bone_frame_map[thigh_bone_vmd]
            
            # IK情報を取得
            if ik_bone_maya not in self._ik_info:
                continue
                
            ik_info = self._ik_info[ik_bone_maya]
            
            # PoleVectorキーフレームを設定
            self._set_pole_vector_keyframes(
                pole_target, thigh_frames, ik_info, thigh_bone_vmd
            )

    def _set_pole_vector_keyframes(self, pole_target: str, thigh_frames: List, 
                                   ik_info: Dict, thigh_bone_vmd: str):
        """PoleVectorのキーフレームを設定する
        
        Args:
            pole_target: PoleTargetノード名
            thigh_frames: 太ももボーンのフレームデータ
            ik_info: IK情報
            thigh_bone_vmd: VMDの太ももボーン名
        """
        # アニメーションカーブを作成
        attrs = ["translateX", "translateY", "translateZ"]
        curves = maya_utils.create_animation_curves(pole_target, attrs)
        
        # 太ももジョイントを取得
        thigh_joint = self.bone_name_mapping.get(thigh_bone_vmd)
        if not thigh_joint:
            return
            
        # 値生成関数を定義
        def generate_values(frame_data):
            # VmdBoneFrameオブジェクトか辞書かで処理を分岐
            if hasattr(frame_data, "rotation"):
                rotation_quat = frame_data.rotation
            else:
                rotation_quat = frame_data.get("rotation", [0, 0, 0, 1])
            
            # PoleVector位置を計算
            pole_pos = self._calculate_pole_vector_position(
                ik_info["start_joint"],
                ik_info["end_joint"],
                rotation_quat,
                thigh_joint
            )
            
            return {
                "translateX": pole_pos[0],
                "translateY": pole_pos[1],
                "translateZ": pole_pos[2],
            }
        
        # キーフレームを一括設定
        maya_utils.set_keyframes_batch(curves, thigh_frames, generate_values)
        
        self.logger.info(f"{pole_target}のPoleVectorキーフレームを設定しました")

    def _calculate_pole_vector_position(self, start_joint: str, end_joint: str,
                                       thigh_rotation: List[float], thigh_joint: str) -> List[float]:
        """太ももの回転からPoleVectorの位置を計算する
        
        Args:
            start_joint: IKチェーンの開始ジョイント（太もも）
            end_joint: IKチェーンの終了ジョイント（足首）
            thigh_rotation: 太ももの回転（クォータニオン）
            thigh_joint: 太ももジョイント名
            
        Returns:
            PoleVectorの位置 [x, y, z]
        """
        # 太ももと足首の位置を取得
        hip_pos = cmds.xform(start_joint, query=True, worldSpace=True, translation=True)
        ankle_pos = cmds.xform(end_joint, query=True, worldSpace=True, translation=True)
        
        # 膝の位置を取得（太ももの子ジョイント）
        children = cmds.listRelatives(thigh_joint, children=True, type="joint")
        if children:
            knee_joint = children[0]
            knee_pos = cmds.xform(knee_joint, query=True, worldSpace=True, translation=True)
        else:
            # 膝が見つからない場合は中点を使用
            knee_pos = [(hip_pos[i] + ankle_pos[i]) / 2 for i in range(3)]
        
        # クォータニオンからオイラー角に変換
        euler_rotation = self._quaternion_to_euler(thigh_rotation)
        
        # Y軸回転（膝の向き）を抽出
        knee_angle_y = math.radians(euler_rotation[1])
        
        # IKチェーンの平面に対して垂直方向を計算
        hip_to_knee = [knee_pos[i] - hip_pos[i] for i in range(3)]
        knee_to_ankle = [ankle_pos[i] - knee_pos[i] for i in range(3)]
        
        # 外積で初期の垂直方向を計算
        normal = utils.cross_product(hip_to_knee, knee_to_ankle)
        normal = utils.normalize_vector(normal)
        
        # Y軸回転を適用して方向を調整
        # 簡易的な実装：XZ平面での回転
        rotated_normal = [
            normal[0] * math.cos(knee_angle_y) - normal[2] * math.sin(knee_angle_y),
            normal[1],
            normal[0] * math.sin(knee_angle_y) + normal[2] * math.cos(knee_angle_y)
        ]
        
        # PoleVectorの位置を計算（膝から一定距離）
        offset_distance = 10.0
        pole_pos = [
            knee_pos[0] + rotated_normal[0] * offset_distance,
            knee_pos[1] + rotated_normal[1] * offset_distance,
            knee_pos[2] + rotated_normal[2] * offset_distance
        ]
        
        return pole_pos
