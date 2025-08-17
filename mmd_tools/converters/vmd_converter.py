"""VMDファイルをMayaアニメーションに変換するモジュール

このモジュールは、MikuMikuDance (MMD)のモーションデータファイル（VMD）を
Mayaのアニメーションデータに変換する機能を提供します。

フェーズ1では以下の基本機能を実装：
- ボーンの位置・回転アニメーション変換
- 線形補間のみサポート
- 基本的なエラーハンドリング
"""

import math
from threading import local
from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core import maya_utils
from ..core.constants import (
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_CAMERA,
    ATTR_MMD_LIGHT,
    DEFAULT_CAMERA_NAME,
    DEFAULT_LIGHT_NAME,
)
from ..core.logger import get_logger
from ..core.vmd_data import VmdData


class VmdConverter:
    """VMDデータをMayaアニメーションに変換するクラス

    VMDファイルに含まれるボーンアニメーションとモーフアニメーションを
    Mayaのジョイントアニメーションとブレンドシェイプアニメーションに変換します。
    アニメーションレイヤーを使用して、複数のモーションを加算的に適用できます。
    """

    def __init__(self):
        """VmdConverterの初期化"""
        self.logger = get_logger(__name__)
        self.bone_name_mapping: Dict[str, str] = {}  # VMDボーン名 -> Mayaジョイント名
        self.morph_name_mapping: Dict[str, str] = {}  # VMDモーフ名 -> Mayaブレンドシェイプターゲット名
        self.fps = 60.0  # デフォルトのFPS
        self._failed_bones = set()  # 変換に失敗したボーン名を記録
        self._bone_bind_poses: Dict[str, Tuple[float, float, float]] = {}  # ボーンの初期位置
        self.use_quaternion_interpolation = True  # Quaternion補間の使用フラグ
        self.anim_layer = None  # 現在のアニメーションレイヤー名
        self.use_animation_layers = True  # アニメーションレイヤーの使用フラグ

    def convert(self, vmd_data: VmdData, target_namespace: str = None, layer_name: str = "VMD_Motion") -> bool:
        """VMDデータをMayaアニメーションに変換

        Args:
            vmd_data: パース済みのVMDデータ
            target_namespace: 対象となるネームスペース（省略可）
            layer_name: アニメーションレイヤー名（省略時は自動生成）
            layer_mode: レイヤーモード（"additive" または "override"）

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

            # アニメーションレイヤーの作成(overrideで作って、最後にAdditiveに変換)
            if self.use_animation_layers:
                self.anim_layer = cmds.animLayer(layer_name, override=False, weight=1.0)

            # ボーンアニメーション変換
            if hasattr(vmd_data, "bone_frames") and vmd_data.bone_frames:
                self.logger.info(f"ボーンアニメーション変換を開始: {len(vmd_data.bone_frames)}フレーム")
                bone_success = self._convert_bone_animation(vmd_data.bone_frames)
                if not bone_success:
                    self.logger.warning("ボーンアニメーション変換で一部エラーが発生しました")

            # フェーズ1では線形補間のみのため、補間データは無視

            # 最後に作成したアニメーションレイヤーのモードをAdditiveにする
            # if self.use_animation_layers and self.anim_layer:
            #     cmds.animLayer(self.anim_layer, edit=True, override=False)

            self.logger.info("VMDアニメーション変換が完了しました")
            return True

        except Exception as e:
            self.logger.error(f"VMDアニメーション変換中にエラーが発生しました: {str(e)}")
            return False

    def _add_objects_to_layer(self, objects: List[str]):
        """オブジェクトをアニメーションレイヤーに追加

        Args:
            objects: 追加するオブジェクトのリスト
        """
        if not self.anim_layer:
            return

        # オブジェクトをレイヤーに追加
        for obj in objects:
            if cmds.objExists(obj):
                # 各属性をレイヤーに追加
                attrs = [
                    "translateX",
                    "translateY",
                    "translateZ",
                    "rotateX",
                    "rotateY",
                    "rotateZ",
                ]
                for attr in attrs:
                    attr_path = f"{obj}.{attr}"
                    if cmds.attributeQuery(attr, node=obj, exists=True):
                        cmds.animLayer(self.anim_layer, edit=True, attribute=attr_path)

    def _build_name_mappings(self, target_namespace: str = None):
        """ボーン名とモーフ名のマッピングを構築

        Args:
            target_namespace: 対象となるネームスペース
        """
        self.logger.info("名前マッピングを構築しています")

        # シーン内のジョイントを検索
        if target_namespace:
            joints = maya_utils.list_objects(object_filter=f"{target_namespace}:*", type="joint")
        else:
            joints = maya_utils.list_objects(type="joint")

        # カスタム属性から元のボーン名を取得
        for joint in joints:
            # PMX/PMDボーン名属性をチェック（新しい属性名）
            if cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
                original_name = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}")
                if original_name:
                    self.bone_name_mapping[original_name] = joint

        self.logger.info(f"{len(self.bone_name_mapping)}個のボーンマッピングを構築しました")

        # モーフ名マッピングの構築
        # self._build_morph_mappings(target_namespace)

    def _record_bind_poses(self):
        """各ボーンの初期位置（バインドポーズ）を記録"""
        self.logger.info("ボーンの初期位置を記録しています")

        for vmd_bone_name, maya_joint in self.bone_name_mapping.items():
            try:
                # 現在のtranslate値を取得（これがバインドポーズ）
                translate = cmds.getAttr(f"{maya_joint}.translate")[0]
                self._bone_bind_poses[vmd_bone_name] = translate
            except Exception as e:
                self.logger.warning(f"{vmd_bone_name}のバインドポーズ取得エラー: {str(e)}")

    def _setup_timeline(self, vmd_data: VmdData):
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

        if max_frame > 0:
            # タイムラインの範囲を設定
            cmds.playbackOptions(min=0, max=max_frame, animationStartTime=0, animationEndTime=max_frame)
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
        animated_joints = []  # アニメーションを適用したジョイントのリスト

        # 各ボーンのアニメーションを設定
        for vmd_bone_name, frames in bone_frame_map.items():
            if vmd_bone_name in self.bone_name_mapping:
                maya_joint = self.bone_name_mapping[vmd_bone_name]

                try:
                    # フレームをフレーム番号でソート
                    frames.sort(key=lambda x: x.frame_number if hasattr(x, "frame_number") else x.get("frame_number", 0))

                    # 位置と回転のキーフレームを設定
                    self._set_bone_keyframes(maya_joint, frames, vmd_bone_name)
                    animated_joints.append(maya_joint)
                    success_count += 1

                except Exception as e:
                    self.logger.error(f"ボーン '{vmd_bone_name}' のアニメーション設定中にエラー: {str(e)}")
                    self._failed_bones.add(vmd_bone_name)
            else:
                if vmd_bone_name not in self._failed_bones:
                    self.logger.info(f"ボーン '{vmd_bone_name}' が見つかりません")
                    self._failed_bones.add(vmd_bone_name)

        # アニメーションレイヤーにジョイントを追加
        if self.use_animation_layers and self.anim_layer and animated_joints:
            self._add_objects_to_layer(animated_joints)

        self.logger.info(f"{success_count}/{total_count}個のボーンアニメーションを変換しました")
        return success_count > 0

    def _set_bone_keyframes(self, joint: str, frames: List, vmd_bone_name: str):
        """ボーンのキーフレームを設定

        Args:
            joint: Mayaジョイント名
            frames: フレームデータのリスト
            vmd_bone_name: VMDボーン名
        """
        # アニメーションレイヤーが有効な場合、レイヤーを選択
        if self.use_animation_layers and self.anim_layer:
            cmds.animLayer(self.anim_layer, edit=True, selected=True)

        affected_layers = cmds.animLayer([joint], query=True, affectedLayers=True) or []
        if self.anim_layer not in affected_layers:
            # オブジェクトをレイヤーに追加
            current_selection = cmds.ls(selection=True)
            cmds.select(joint, replace=True)
            cmds.animLayer(self.anim_layer, edit=True, addSelectedObjects=True)

        for frame in frames:
            if hasattr(frame, "frame_number"):
                pos = om.MVector(frame.position)
                rotation_quat = frame.rotation
            else:
                pos = om.MVector(frame.get("position", [0, 0, 0]))
                rotation_quat = frame.get("rotation", [0, 0, 0, 1])

            # 属性リストをまとめてループ処理
            attrs = ["translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"]

            # Translateを適用
            pos.z = -pos.z  # Z軸反転
            pos += om.MVector(maya_utils.get_attribute(joint, "translate"))
            maya_utils.set_attribute(joint, "translate", pos, "double3")

            # 回転を適用
            self.apply_rotation(joint, om.MQuaternion(*rotation_quat))

            for attr in attrs:
                cmds.setKeyframe(
                    joint,
                    attribute=attr,
                    time=frame.frame_number,
                    animLayer=self.anim_layer,
                )

        # TODO: maya apiを使うなら、キーフレームを先に打って、カーブを作成した後に、一括で設定するとパフォーマンスが向上する。
        # curves = maya_utils.create_animation_curves(
        #     joint, attrs, animation_layer=self.animation_layer_name
        # )

        # # キーフレームを一括設定
        # maya_utils.set_keyframes_batch(curves, frames, generate_values)

        # Quaternion補間を適用
        if self.use_quaternion_interpolation:
            try:
                # rotationInterpolationコマンドでQuaternion補間に変換
                cmds.rotationInterpolation(
                    f"{joint}.rotateX",
                    f"{joint}.rotateY",
                    f"{joint}.rotateZ",
                    convert="quaternionSlerp",  # "quaternionSquad"も選択可能（より滑らか）
                )
            except Exception as e:
                self.logger.warning(f"{joint}へのQuaternion補間適用に失敗: {str(e)}")

    def get_parent_world_rotation(self, joint):
        """Maya API 2.0を使用して親ワールド変換行列から親の回転を取得"""
        # MSelectionListを使用してMObjectを取得
        selection_list = om.MSelectionList()
        selection_list.add(joint)
        joint_mobject = selection_list.getDagPath(0)

        # MFnTransformを作成
        fn_transform = om.MFnTransform(joint_mobject)

        # 親のワールド変換行列を取得
        parent_path = fn_transform.dagPath().pop()
        if parent_path.length() == 0:
            return om.MQuaternion(0, 0, 0, 1)

        parent_transform = om.MFnTransform(parent_path)
        parent_world_matrix = parent_path.inclusiveMatrix()
        parent_transform_matrix = om.MTransformationMatrix(parent_world_matrix)

        return parent_transform_matrix.rotation(asQuaternion=True)

    def apply_rotation(self, joint, world_quat):
        # Z軸反転の変換行列
        flip_matrix = om.MMatrix([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, -1, 0], [0, 0, 0, 1]])
        transform = om.MTransformationMatrix(flip_matrix)
        flip_quat = transform.rotation(asQuaternion=True)
        # EulerRotationとして取得
        flip_euler = transform.rotation()
        converted_quat = flip_quat.inverse() * world_quat * flip_quat

        # JointOrientをQuaternionとして取得
        orient_euler = maya_utils.get_attribute(joint, "jointOrient")
        joint_orient = om.MEulerRotation(orient_euler).asQuaternion()

        # 親のワールド回転を取得（ワールド変換行列から）
        parent_world_rotation = self.get_parent_world_rotation(joint)

        # 回転の適応
        local_rotation = (
            joint_orient.inverse() * parent_world_rotation.inverse() * converted_quat * parent_world_rotation * joint_orient
        )
        # 3. ローカル回転をEulerに変換してrotate属性に設定
        local_euler = local_rotation.asEulerRotation()

        # なぜかはわからないが、QuaternionのZ軸を反転させることはできずに、最後にオイラーにしたものを変換したら出来る。
        local_euler.z = -local_euler.z  # Z軸反転
        local_euler = local_euler.inverse()

        # MatrixのZ軸反転と単純にZ軸をInvertするのとでは挙動が違う
        # local_euler *= flip_euler

        maya_utils.set_attribute(joint, "rotate", local_euler, "double3")

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
            self.logger.warning(f"指定されたFPS {fps} はサポートされていません。デフォルトの60.0 FPSを使用します")
            cmds.currentUnit(time="ntscf")  # デフォルトは60fpsのNTSCF
