"""VMDファイルをMayaアニメーションに変換するモジュール

このモジュールは、MikuMikuDance (MMD)のモーションデータファイル（VMD）を
Mayaのアニメーションデータに変換する機能を提供します。

フェーズ1では以下の基本機能を実装：
- ボーンの位置・回転アニメーション変換
- 線形補間のみサポート
- 基本的なエラーハンドリング
"""

import math
import struct
from typing import Dict, List, Optional, Set, Tuple

import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import maya.cmds as cmds

from ..core import maya_utils, utils
from ..core.constants import (
    ATTR_MMD_CAMERA,
    ATTR_MMD_LIGHT,
    DEFAULT_CAMERA_NAME,
    DEFAULT_LIGHT_NAME,
)
from ..core.logger import get_logger
from ..core.vmd_parser import VmdParser


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
        self.logger = get_logger(__name__)
        self.bone_name_mapping: Dict[str, str] = {}  # VMDボーン名 -> Mayaジョイント名
        self.morph_name_mapping: Dict[
            str, str
        ] = {}  # VMDモーフ名 -> Mayaブレンドシェイプターゲット名
        self.fps = 30.0  # デフォルトのFPS
        self._failed_bones = set()  # 変換に失敗したボーン名を記録
        self._bone_bind_poses: Dict[
            str, Tuple[float, float, float]
        ] = {}  # ボーンの初期位置
        self.use_quaternion_interpolation = True  # Quaternion補間の使用フラグ

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

            # カメラアニメーション変換
            if hasattr(vmd_data, "camera_frames") and vmd_data.camera_frames:
                self.logger.info(
                    f"カメラアニメーション変換を開始: {len(vmd_data.camera_frames)}フレーム"
                )
                camera_success = self._convert_camera_animation(vmd_data.camera_frames)
                if not camera_success:
                    self.logger.warning(
                        "カメラアニメーション変換でエラーが発生しました"
                    )

            # 照明アニメーション変換
            if hasattr(vmd_data, "light_frames") and vmd_data.light_frames:
                self.logger.info(
                    f"照明アニメーション変換を開始: {len(vmd_data.light_frames)}フレーム"
                )
                light_success = self._convert_light_animation(vmd_data.light_frames)
                if not light_success:
                    self.logger.warning(
                        "照明アニメーション変換でエラーが発生しました"
                    )

            # モーフアニメーション変換
            if hasattr(vmd_data, "morph_frames") and vmd_data.morph_frames:
                self.logger.info(
                    f"モーフアニメーション変換を開始: {len(vmd_data.morph_frames)}フレーム"
                )
                morph_success = self._convert_morph_animation(vmd_data.morph_frames)
                if not morph_success:
                    self.logger.warning(
                        "モーフアニメーション変換でエラーが発生しました"
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

        # モーフ名マッピングの構築
        self._build_morph_mappings(target_namespace)

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
        # FPSを設定
        self._set_scene_fps(self.fps)

        # 最大フレーム番号を取得
        max_frame = 0
        
        # ボーンフレームから最大フレーム取得
        if hasattr(vmd_data, "bone_frames"):
            for frame_data in vmd_data.bone_frames:
                # VmdBoneFrameオブジェクトの場合は属性アクセス、辞書の場合はget
                if hasattr(frame_data, "frame_number"):
                    max_frame = max(max_frame, frame_data.frame_number)
                else:
                    max_frame = max(max_frame, frame_data.get("frame_number", 0))
        
        # カメラフレームから最大フレーム取得
        if hasattr(vmd_data, "camera_frames"):
            for frame_data in vmd_data.camera_frames:
                if hasattr(frame_data, "frame_number"):
                    max_frame = max(max_frame, frame_data.frame_number)
        
        # 照明フレームから最大フレーム取得
        if hasattr(vmd_data, "light_frames"):
            for frame_data in vmd_data.light_frames:
                if hasattr(frame_data, "frame_number"):
                    max_frame = max(max_frame, frame_data.frame_number)
        
        # モーフフレームから最大フレーム取得
        if hasattr(vmd_data, "morph_frames"):
            for frame_data in vmd_data.morph_frames:
                if hasattr(frame_data, "frame_number"):
                    max_frame = max(max_frame, frame_data.frame_number)

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
        # MMDもMayaも右手座標系だが、Z軸の向きが逆（MMD: +Z手前, Maya: +Z奥）
        # この違いにより、回転の向きも影響を受ける
        maya_quat = om2.MQuaternion(quat[0], quat[1], -quat[2], quat[3])

        # 正規化（念のため）
        maya_quat = maya_quat.normal()

        # オイラー角に変換
        euler = maya_quat.asEulerRotation()

        # ラジアンから度に変換
        # Z軸の向きが逆のため、X軸とY軸の回転方向が反転する
        rx = math.degrees(euler.x) * -1  # X軸回転を反転
        ry = math.degrees(euler.y) * -1  # Y軸回転を反転
        rz = math.degrees(euler.z) * -1  # Z軸回転も反転

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

    def _set_scene_fps(self, fps: float):
        """シーンのFPSを設定

        Args:
            fps: 設定するFPS値
        """
        # FPSとタイムユニットのマッピング
        fps_mapping = {
            15.0: "game",
            24.0: "film",
            25.0: "pal",
            30.0: "ntsc",
            48.0: "show",
            50.0: "palf",
            60.0: "ntscf",
        }

        if fps in fps_mapping:
            # 定義済みのタイムユニットを使用
            cmds.currentUnit(time=fps_mapping[fps])
            self.logger.info(f"シーンFPSを{fps} ({fps_mapping[fps]})に設定しました")
        else:
            self.logger.warning(
                f"指定されたFPS {fps} はサポートされていません。デフォルトの30.0 FPSを使用します"
            )
            cmds.currentUnit(time="ntsc")  # デフォルトは30fpsのNTSC

    def _convert_camera_animation(self, camera_frames: List) -> bool:
        """カメラアニメーションを変換

        Args:
            camera_frames: カメラフレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        try:
            if not camera_frames:
                return True

            # カメラを作成または取得
            camera_name = self._get_or_create_camera()
            if not camera_name:
                self.logger.error("カメラの作成または取得に失敗しました")
                return False

            # フレームをフレーム番号でソート
            camera_frames.sort(key=lambda x: x.frame_number)

            # カメラのアニメーションを設定
            self._set_camera_keyframes(camera_name, camera_frames)

            self.logger.info(f"{len(camera_frames)}個のカメラフレームを変換しました")
            return True

        except Exception as e:
            self.logger.error(f"カメラアニメーション変換中にエラー: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def _get_or_create_camera(self) -> Optional[str]:
        """MMDカメラを取得または作成

        Returns:
            カメラのトランスフォーム名
        """
        # 既存のMMDカメラを検索
        cameras = cmds.ls(type="camera")
        for cam in cameras:
            transform = cmds.listRelatives(cam, parent=True)[0]
            if cmds.attributeQuery(ATTR_MMD_CAMERA, node=transform, exists=True):
                self.logger.info(f"既存のMMDカメラを使用: {transform}")
                return transform

        # 新しいカメラを作成
        camera_transform, camera_shape = cmds.camera(name=DEFAULT_CAMERA_NAME)
        
        # MMDカメラマーカーを追加
        if not cmds.attributeQuery(ATTR_MMD_CAMERA, node=camera_transform, exists=True):
            cmds.addAttr(camera_transform, longName=ATTR_MMD_CAMERA, attributeType="bool", defaultValue=True)
        
        # カメラの初期設定
        cmds.setAttr(f"{camera_shape}.nearClipPlane", 0.1)
        cmds.setAttr(f"{camera_shape}.farClipPlane", 10000.0)
        
        self.logger.info(f"新しいMMDカメラを作成: {camera_transform}")
        return camera_transform

    def _set_camera_keyframes(self, camera_transform: str, frames: List):
        """カメラのキーフレームを設定

        Args:
            camera_transform: カメラのトランスフォーム名
            frames: フレームデータのリスト
        """
        # カメラシェイプを取得
        camera_shape = cmds.listRelatives(camera_transform, shapes=True, type="camera")[0]
        
        # アニメーションカーブを作成
        trans_attrs = ["translateX", "translateY", "translateZ"]
        rot_attrs = ["rotateX", "rotateY", "rotateZ"]
        trans_curves = maya_utils.create_animation_curves(camera_transform, trans_attrs)
        rot_curves = maya_utils.create_animation_curves(camera_transform, rot_attrs)
        fov_curves = maya_utils.create_animation_curves(camera_shape, ["focalLength"])
        fov_curve = fov_curves["focalLength"]

        # 値生成関数を定義
        def generate_camera_values(frame_data):
            # MMDカメラの位置と注視点からMayaカメラの位置と回転を計算
            position = frame_data.position
            rotation = frame_data.rotation  # Euler angles in radians
            distance = frame_data.distance
            
            # 回転をラジアンから度に変換し、座標系を調整
            rx_deg = -math.degrees(rotation[0])  # X軸回転を反転
            ry_deg = -math.degrees(rotation[1])  # Y軸回転を反転
            rz_deg = math.degrees(rotation[2])   # Z軸回転はそのまま

            # カメラの実際の位置を計算
            # MMDではカメラが注視点から指定距離だけ離れた位置にある
            rx_rad = rotation[0]
            ry_rad = rotation[1]
            rz_rad = rotation[2]
            
            # 回転行列を構築（ZXY順）
            cos_x, sin_x = math.cos(rx_rad), math.sin(rx_rad)
            cos_y, sin_y = math.cos(ry_rad), math.sin(ry_rad)
            cos_z, sin_z = math.cos(rz_rad), math.sin(rz_rad)
            
            # カメラの向きベクトル（初期状態では-Z方向を向いている）
            camera_dir_x = sin_y * cos_x
            camera_dir_y = sin_x
            camera_dir_z = cos_y * cos_x
            
            # カメラの実際の位置 = 注視点 + (向きベクトル * 距離)
            camera_x = position[0] + camera_dir_x * distance
            camera_y = position[1] + camera_dir_y * distance
            camera_z = -position[2] + camera_dir_z * distance  # Z軸反転
            
            return {
                "translateX": camera_x,
                "translateY": camera_y,
                "translateZ": camera_z,
                "rotateX": rx_deg,
                "rotateY": ry_deg,
                "rotateZ": rz_deg
            }
        
        def generate_fov_values(frame_data):
            fov_angle = frame_data.viewing_angle
            # FOVから焦点距離を計算
            # Maya: focalLength = (cameraAperture * 25.4) / (2 * tan(fov/2))
            # デフォルトのカメラアパーチャ（フィルムゲート）を取得
            h_aperture = cmds.getAttr(f"{camera_shape}.horizontalFilmAperture")
            h_aperture_mm = h_aperture * 25.4  # インチからmmに変換
            focal_length = h_aperture_mm / (2 * math.tan(math.radians(fov_angle) / 2))
            
            return {
                "focalLength": focal_length
            }

        # キーフレームを一括設定
        # トランスフォームと回転の値を同時に設定
        all_attrs = trans_attrs + rot_attrs
        all_curves = {**trans_curves, **rot_curves}
        maya_utils.set_keyframes_batch(all_curves, frames, generate_camera_values)
        
        # FOVのキーフレームを設定
        maya_utils.set_keyframes_batch({"focalLength": fov_curve}, frames, generate_fov_values)

    def _convert_light_animation(self, light_frames: List) -> bool:
        """照明アニメーションを変換

        Args:
            light_frames: 照明フレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        try:
            if not light_frames:
                return True

            # 照明を作成または取得
            light_name = self._get_or_create_light()
            if not light_name:
                self.logger.error("照明の作成または取得に失敗しました")
                return False

            # フレームをフレーム番号でソート
            light_frames.sort(key=lambda x: x.frame_number)

            # 照明のアニメーションを設定
            self._set_light_keyframes(light_name, light_frames)

            self.logger.info(f"{len(light_frames)}個の照明フレームを変換しました")
            return True

        except Exception as e:
            self.logger.error(f"照明アニメーション変換中にエラー: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def _get_or_create_light(self) -> Optional[str]:
        """MMD照明を取得または作成

        Returns:
            照明のトランスフォーム名
        """
        # 既存のMMD照明を検索
        lights = cmds.ls(type="directionalLight")
        for light in lights:
            transform = cmds.listRelatives(light, parent=True)[0]
            if cmds.attributeQuery(ATTR_MMD_LIGHT, node=transform, exists=True):
                self.logger.info(f"既存のMMD照明を使用: {transform}")
                return transform

        # 新しい方向性ライトを作成
        light_transform = cmds.directionalLight(name=DEFAULT_LIGHT_NAME, intensity=1.0)
        light_transform = cmds.listRelatives(light_transform, parent=True)[0]
        
        # MMD照明マーカーを追加
        if not cmds.attributeQuery(ATTR_MMD_LIGHT, node=light_transform, exists=True):
            cmds.addAttr(light_transform, longName=ATTR_MMD_LIGHT, attributeType="bool", defaultValue=True)
        
        self.logger.info(f"新しいMMD照明を作成: {light_transform}")
        return light_transform

    def _set_light_keyframes(self, light_transform: str, frames: List):
        """照明のキーフレームを設定

        Args:
            light_transform: 照明のトランスフォーム名
            frames: フレームデータのリスト
        """
        # 照明シェイプを取得
        light_shape = cmds.listRelatives(light_transform, shapes=True, type="directionalLight")[0]
        
        # アニメーションカーブを作成
        rot_attrs = ["rotateX", "rotateY", "rotateZ"]
        rot_curves = maya_utils.create_animation_curves(light_transform, rot_attrs)
        color_attrs = ["colorR", "colorG", "colorB"]
        color_curves = maya_utils.create_animation_curves(light_shape, color_attrs)

        # 値生成関数を定義
        def generate_light_rotation_values(frame_data):
            # MMDの照明方向をMayaの回転に変換
            # MMDでは照明の方向ベクトルとして与えられる
            direction = frame_data.position  # これは実際には方向ベクトル
            
            # 方向ベクトルから回転角度を計算
            # ベクトルを正規化
            length = math.sqrt(direction[0]**2 + direction[1]**2 + direction[2]**2)
            if length > 0:
                dir_x = direction[0] / length
                dir_y = direction[1] / length
                dir_z = -direction[2] / length  # Z軸反転
            else:
                dir_x, dir_y, dir_z = 0, -1, 0  # デフォルト方向（下向き）
            
            # 方向ベクトルから回転角度を計算
            # Mayaのdirectionalライトは初期状態で-Y方向を向いている
            # アークタンジェントを使用して角度を計算
            ry = math.degrees(math.atan2(dir_x, -dir_z))
            rx = math.degrees(math.asin(dir_y))
            rz = 0  # Z軸回転は通常0
            
            return {
                "rotateX": rx,
                "rotateY": ry,
                "rotateZ": rz
            }
        
        def generate_light_color_values(frame_data):
            color = frame_data.color
            return {
                "colorR": color[0],
                "colorG": color[1],
                "colorB": color[2]
            }
        
        # キーフレームを一括設定
        maya_utils.set_keyframes_batch(rot_curves, frames, generate_light_rotation_values)
        maya_utils.set_keyframes_batch(color_curves, frames, generate_light_color_values)

    def _build_morph_mappings(self, target_namespace: str = None):
        """モーフ名のマッピングを構築

        Args:
            target_namespace: 対象となるネームスペース
        """
        self.logger.info("モーフ名マッピングを構築しています")

        # シーン内のブレンドシェイプノードを検索
        if target_namespace:
            blend_shapes = cmds.ls(f"{target_namespace}:*", type="blendShape")
        else:
            blend_shapes = cmds.ls(type="blendShape")

        self.logger.debug(f"見つかったブレンドシェイプ: {blend_shapes}")

        # 各ブレンドシェイプのターゲットを確認
        for blend_shape in blend_shapes:
            # ターゲット数を取得
            target_count = cmds.blendShape(blend_shape, query=True, target=True)
            if not target_count:
                self.logger.debug(f"{blend_shape} にターゲットがありません")
                continue

            # 各ターゲットのエイリアスを取得
            weight_count = cmds.blendShape(blend_shape, query=True, weightCount=True)
            self.logger.debug(f"{blend_shape} のウェイト数: {weight_count}")
            
            for i in range(weight_count):
                # エイリアス（モーフ名）を取得
                alias_attr = f"{blend_shape}.weight[{i}]"
                alias_name = cmds.aliasAttr(alias_attr, query=True)
                self.logger.debug(f"weight[{i}] のエイリアス: {alias_name}")
                
                if alias_name:
                    # aliasAttrは文字列を返す
                    morph_name = alias_name
                    # 属性がない場合はエイリアス名をそのまま使用
                    self.morph_name_mapping[morph_name] = (blend_shape, i, morph_name)
                    self.logger.debug(f"マッピング追加: {morph_name} -> ({blend_shape}, {i}, {morph_name})")

        self.logger.info(
            f"{len(self.morph_name_mapping)}個のモーフマッピングを構築しました"
        )

    def _convert_morph_animation(self, morph_frames: List) -> bool:
        """モーフアニメーションを変換

        Args:
            morph_frames: モーフフレームデータのリスト

        Returns:
            変換が成功した場合True
        """
        try:
            if not morph_frames:
                return True

            # モーフごとにフレームデータをグループ化
            morph_frame_map: Dict[str, List] = {}

            for frame in morph_frames:
                morph_name = frame.morph_name
                if morph_name not in morph_frame_map:
                    morph_frame_map[morph_name] = []
                morph_frame_map[morph_name].append(frame)

            success_count = 0
            total_count = len(morph_frame_map)

            # 各モーフのアニメーションを設定
            for vmd_morph_name, frames in morph_frame_map.items():
                if vmd_morph_name in self.morph_name_mapping:
                    blend_shape, target_index, maya_morph_name = self.morph_name_mapping[vmd_morph_name]

                    try:
                        # フレームをフレーム番号でソート
                        frames.sort(key=lambda x: x.frame_number)

                        # モーフのキーフレームを設定
                        self._set_morph_keyframes(blend_shape, target_index, frames)
                        success_count += 1

                    except Exception as e:
                        self.logger.error(
                            f"モーフ '{vmd_morph_name}' のアニメーション設定中にエラー: {str(e)}"
                        )
                else:
                    self.logger.info(f"モーフ '{vmd_morph_name}' が見つかりません")

            self.logger.info(
                f"{success_count}/{total_count}個のモーフアニメーションを変換しました"
            )
            return success_count > 0

        except Exception as e:
            self.logger.error(f"モーフアニメーション変換中にエラー: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def _set_morph_keyframes(self, blend_shape: str, target_index: int, frames: List):
        """モーフのキーフレームを設定

        Args:
            blend_shape: ブレンドシェイプノード名
            target_index: ターゲットインデックス
            frames: フレームデータのリスト
        """
        # ブレンドシェイプのウェイト属性名
        weight_attr = f"{blend_shape}.weight[{target_index}]"
        
        # 各フレームでキーフレームを設定
        for frame in frames:
            # 現在のフレームに移動
            cmds.currentTime(frame.frame_number)
            
            # ウェイト値を設定
            cmds.setAttr(weight_attr, frame.value)
            
            # キーフレームを設定
            cmds.setKeyframe(weight_attr, time=frame.frame_number, value=frame.value)
