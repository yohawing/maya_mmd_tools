"""VPDデータをMayaのポーズに変換するモジュール"""

import math
from typing import Any, Dict, List, Optional, Sequence

import maya.cmds as cmds
import maya.api.OpenMaya as om

from mmd_tools.core.logger import get_logger
from mmd_tools.core.maya_attribute_utils import get_attribute
from mmd_tools.core.constants import ATTR_MMD_BONE_NAME
from mmd_tools.core.coordinate_transform import mmd_euler_xyz_to_maya, mmd_point_to_maya
from mmd_tools.core.vpd_data.bone_pose import BonePose
from mmd_tools.converters.vmd_anim_layer import add_transform_attrs_to_anim_layer

logger = get_logger(__name__)


class VpdConverter:
    """VPDデータをMayaポーズに変換するクラス

    VPDファイルのボーンポーズデータをMayaのジョイントに適用します。
    座標系変換とボーン名のマッピングを行います。
    """

    def __init__(self) -> None:
        """VpdConverterの初期化"""
        self.bone_name_mapping: Dict[str, str] = {}  # MMDボーン名 -> Mayaジョイント名のマッピング
        self.use_animation_layers: bool = True  # アニメーションレイヤーの使用フラグ
        self.anim_layer: Optional[str] = None  # 現在のアニメーションレイヤー名

    def convert(self, vpd_data: Any, target_namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> bool:
        """VPDデータをMayaのポーズに変換して適用

        Args:
            vpd_data (VpdData): VPDデータ
            target_namespace (str): ターゲットのネームスペース
            options (dict): 変換オプション

        Returns:
            bool: 変換が成功したか
        """
        if options is None:
            options = {}

        logger.info("Starting VPD pose conversion")

        target_model = options.get("target_model")

        # ボーン名マッピングを構築
        self._build_name_mappings(target_namespace, target_model)

        # ジョイントのリストを取得
        joints = self._get_target_joints(target_namespace, target_model)
        if not joints:
            logger.warning("Target joint not found")
            return False

        # オプションからレイヤー設定を取得
        layer_name = options.get("layer_name", "VPD_Pose")
        create_keyframe = options.get("create_keyframe", True)
        current_frame = cmds.currentTime(query=True)

        # アニメーションレイヤーの作成または選択
        if self.use_animation_layers and create_keyframe:
            self._setup_animation_layer(layer_name)

        # ボーンポーズを適用
        applied_count = 0
        applied_joints: List[str] = []  # アニメーションを適用したジョイントのリスト

        for bone_pose in vpd_data.bone_poses:
            joint = self._apply_bone_pose(bone_pose, joints, target_namespace, create_keyframe, current_frame)
            if joint:
                applied_count += 1
                if joint not in applied_joints:
                    applied_joints.append(joint)

        # アニメーションレイヤーにジョイントを追加
        if self.use_animation_layers and self.anim_layer and applied_joints and create_keyframe:
            self._add_objects_to_layer(applied_joints)

        logger.info(f"VPD pose conversion completed: applied {applied_count}/{len(vpd_data.bone_poses)} bones")

        return applied_count > 0

    def _get_target_joints(self, namespace: Optional[str] = None, target_model: Optional[str] = None) -> List[str]:
        """ターゲットのジョイントを取得

        Args:
            namespace (str): ネームスペース
            target_model (str): 対象モデルの root transform

        Returns:
            list: ジョイントのリスト
        """
        if target_model and cmds.objExists(target_model):
            joints = cmds.listRelatives(target_model, allDescendents=True, type="joint", fullPath=True) or []
            if cmds.nodeType(target_model) == "joint":
                joints.append(target_model)
            return joints

        if namespace:
            pattern = f"{namespace}:*"
        else:
            pattern = "*"

        joints = cmds.ls(pattern, type="joint")
        return joints

    def _build_name_mappings(
        self, target_namespace: Optional[str] = None, target_model: Optional[str] = None
    ) -> None:
        """ボーン名マッピングを構築

        Args:
            target_namespace (str): ターゲットのネームスペース
            target_model (str): 対象モデルの root transform
        """
        logger.debug("Building bone name mapping")
        self.bone_name_mapping = {}

        # ジョイントのリストを取得
        joints = self._get_target_joints(target_namespace, target_model)

        # CustomAttributesから元のボーン名を取得
        for joint in joints:
            # PMX/PMDボーン名属性をチェック
            if cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
                original_name = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}")
                if original_name:
                    self.bone_name_mapping[original_name] = joint
                    logger.debug(f"Bone mapping: {original_name} -> {joint}")

        logger.info(f"Built {len(self.bone_name_mapping)} bone mappings")

    def _find_maya_joint(self, mmd_bone_name: str, joints: Sequence[str], namespace: Optional[str] = None) -> Optional[str]:
        """MMDボーン名に対応するMayaジョイントを探す

        Args:
            mmd_bone_name (str): MMDのボーン名
            joints (list): 検索対象のジョイントリスト
            namespace (str): ネームスペース

        Returns:
            str: 見つかったジョイント名、見つからない場合はNone
        """
        # CustomAttributesベースのマッピングから探す
        if mmd_bone_name in self.bone_name_mapping:
            return self.bone_name_mapping[mmd_bone_name]

        # 完全一致を試す（ジョイント名がそのままMMDボーン名と一致する場合）
        for joint in joints:
            joint_leaf = joint.rsplit("|", 1)[-1]
            joint_name = joint_leaf.split(":")[-1] if ":" in joint_leaf else joint_leaf
            if joint_name == mmd_bone_name:
                return joint

        return None

    def _apply_bone_pose(
        self,
        bone_pose: BonePose,
        joints: Sequence[str],
        namespace: Optional[str] = None,
        create_keyframe: bool = True,
        frame_time: Optional[float] = None,
    ) -> Optional[str]:
        """単一のボーンポーズを適用

        Args:
            bone_pose (BonePose): ボーンポーズデータ
            joints (list): ジョイントのリスト
            namespace (str): ネームスペース
            create_keyframe (bool): キーフレームを作成するか
            frame_time (float): キーフレームを設定する時間

        Returns:
            str: 適用したジョイント名、失敗した場合はNone
        """
        # 対応するMayaジョイントを探す
        maya_joint = self._find_maya_joint(bone_pose.bone_name, joints, namespace)

        if not maya_joint:
            logger.debug(f"No joint found for bone '{bone_pose.bone_name}'")
            return None

        try:
            # アニメーションレイヤーが有効な場合、レイヤーを選択
            if create_keyframe and self.use_animation_layers and self.anim_layer:
                cmds.animLayer(self.anim_layer, edit=True, selected=True)

            # 位置の適用（センターボーンなど移動可能なボーンのみ）
            if self._is_movable_bone(bone_pose.bone_name):
                position = self._convert_position_mmd_to_maya(bone_pose.position)
                cmds.setAttr(f"{maya_joint}.translateX", position[0])
                cmds.setAttr(f"{maya_joint}.translateY", position[1])
                cmds.setAttr(f"{maya_joint}.translateZ", position[2])

                # キーフレームを作成
                if create_keyframe:
                    for i, attr in enumerate(["translateX", "translateY", "translateZ"]):
                        cmds.setKeyframe(
                            maya_joint,
                            attribute=attr,
                            time=frame_time if frame_time is not None else cmds.currentTime(query=True),
                            animLayer=self.anim_layer if self.anim_layer else None,
                        )

            # 回転の適用
            rotation = self._convert_quaternion_to_euler(bone_pose.quaternion)
            rotation = self._convert_rotation_mmd_to_maya(rotation)

            # JointOrientを考慮した回転の適用
            joint_orient = [
                get_attribute(maya_joint, "jointOrientX") or 0.0,
                get_attribute(maya_joint, "jointOrientY") or 0.0,
                get_attribute(maya_joint, "jointOrientZ") or 0.0,
            ]

            if any(joint_orient):
                # JointOrientがある場合は補正が必要
                rotation = self._apply_joint_orient_correction(rotation, joint_orient)

            cmds.setAttr(f"{maya_joint}.rotateX", rotation[0])
            cmds.setAttr(f"{maya_joint}.rotateY", rotation[1])
            cmds.setAttr(f"{maya_joint}.rotateZ", rotation[2])

            # キーフレームを作成
            if create_keyframe:
                for i, attr in enumerate(["rotateX", "rotateY", "rotateZ"]):
                    cmds.setKeyframe(
                        maya_joint,
                        attribute=attr,
                        time=frame_time if frame_time is not None else cmds.currentTime(query=True),
                        animLayer=self.anim_layer if self.anim_layer else None,
                    )

            logger.debug(f"Applying bone '{bone_pose.bone_name}' to '{maya_joint}'")
            return maya_joint

        except Exception as e:
            logger.warning(f"Failed to apply bone '{bone_pose.bone_name}': {e}")
            return None

    def _is_movable_bone(self, bone_name: str) -> bool:
        """移動可能なボーンかどうかを判定

        Args:
            bone_name (str): ボーン名

        Returns:
            bool: 移動可能な場合True
        """
        movable_bones = ["センター", "center", "Center", "全ての親", "master", "Master"]
        return bone_name in movable_bones

    def _convert_position_mmd_to_maya(self, position: Sequence[float]) -> List[float]:
        """MMDの位置座標をMayaの座標系に変換

        Args:
            position (list): MMDの位置 [x, y, z]

        Returns:
            list: Mayaの位置 [x, y, z]
        """
        return list(mmd_point_to_maya(position))

    def _convert_quaternion_to_euler(self, quaternion: Sequence[float]) -> List[float]:
        """四元数をオイラー角に変換

        Args:
            quaternion (list): 四元数 [x, y, z, w]

        Returns:
            list: オイラー角（度） [x, y, z]
        """
        # Maya APIを使用して四元数からオイラー角への変換
        quat = om.MQuaternion(quaternion[3], quaternion[0], quaternion[1], quaternion[2])
        euler = quat.asEulerRotation()

        # ラジアンから度に変換
        return [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]

    def _convert_rotation_mmd_to_maya(self, rotation: Sequence[float]) -> List[float]:
        """MMDの回転をMayaの座標系に変換

        Args:
            rotation (list): MMDの回転（度） [x, y, z]

        Returns:
            list: Mayaの回転（度） [x, y, z]
        """
        return list(mmd_euler_xyz_to_maya(rotation))

    def _setup_animation_layer(self, layer_name: str) -> None:
        """アニメーションレイヤーを作成または選択

        Args:
            layer_name (str): レイヤー名
        """
        # 既存のレイヤーを確認
        existing_layers = cmds.ls(type="animLayer")

        if layer_name in existing_layers:
            # 既存のレイヤーを使用
            self.anim_layer = layer_name
            logger.info(f"Using existing animation layer: {layer_name}")
        else:
            # 新しいレイヤーを作成
            self.anim_layer = cmds.animLayer(layer_name, override=False, weight=1.0)
            logger.info(f"Created new animation layer: {layer_name}")

    def _add_objects_to_layer(self, objects: Sequence[str]) -> None:
        """オブジェクトをアニメーションレイヤーに追加

        Args:
            objects (list): 追加するオブジェクトのリスト
        """
        add_transform_attrs_to_anim_layer(self.anim_layer, objects)

    def _apply_joint_orient_correction(self, rotation: Sequence[float], joint_orient: Sequence[float]) -> List[float]:
        """JointOrientを考慮した回転の補正

        Args:
            rotation (list): 回転値（度） [x, y, z]
            joint_orient (list): JointOrient値（度） [x, y, z]

        Returns:
            list: 補正された回転値（度） [x, y, z]
        """
        # JointOrientの影響を除去
        # これは簡略化された実装で、より正確な変換が必要な場合がある
        return [rotation[0] - joint_orient[0], rotation[1] - joint_orient[1], rotation[2] - joint_orient[2]]
