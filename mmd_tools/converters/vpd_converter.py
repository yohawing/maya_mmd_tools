"""VPDデータをMayaのポーズに変換するモジュール"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
import math

from mmd_tools.core.logger import get_logger
from mmd_tools.core.maya_utils import get_attribute
from mmd_tools.core.constants import ATTR_MMD_BONE_NAME

logger = get_logger(__name__)


class VpdConverter:
    """VPDデータをMayaポーズに変換するクラス

    VPDファイルのボーンポーズデータをMayaのジョイントに適用します。
    座標系変換とボーン名のマッピングを行います。
    """

    def __init__(self):
        """VpdConverterの初期化"""
        self.bone_name_mapping = {}  # MMDボーン名 -> Mayaジョイント名のマッピング
        self.use_animation_layers = True  # アニメーションレイヤーの使用フラグ
        self.anim_layer = None  # 現在のアニメーションレイヤー名

    def convert(self, vpd_data, target_namespace=None, options=None):
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

        # ボーン名マッピングを構築
        self._build_name_mappings(target_namespace)

        # ジョイントのリストを取得
        joints = self._get_target_joints(target_namespace)
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
        applied_joints = []  # アニメーションを適用したジョイントのリスト

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

    def _get_target_joints(self, namespace=None):
        """ターゲットのジョイントを取得

        Args:
            namespace (str): ネームスペース

        Returns:
            list: ジョイントのリスト
        """
        if namespace:
            pattern = f"{namespace}:*"
        else:
            pattern = "*"

        joints = cmds.ls(pattern, type="joint")
        return joints

    def _build_name_mappings(self, target_namespace=None):
        """ボーン名マッピングを構築

        Args:
            target_namespace (str): ターゲットのネームスペース
        """
        logger.debug("Building bone name mapping")
        self.bone_name_mapping = {}

        # ジョイントのリストを取得
        joints = self._get_target_joints(target_namespace)

        # CustomAttributesから元のボーン名を取得
        for joint in joints:
            # PMX/PMDボーン名属性をチェック
            if cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
                original_name = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}")
                if original_name:
                    self.bone_name_mapping[original_name] = joint
                    logger.debug(f"Bone mapping: {original_name} -> {joint}")

        logger.info(f"Built {len(self.bone_name_mapping)} bone mappings")

    def _find_maya_joint(self, mmd_bone_name, joints, namespace=None):
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
            joint_name = joint.split(":")[-1] if ":" in joint else joint
            if joint_name == mmd_bone_name:
                return joint

        return None

    def _apply_bone_pose(self, bone_pose, joints, namespace=None, create_keyframe=True, frame_time=None):
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

    def _is_movable_bone(self, bone_name):
        """移動可能なボーンかどうかを判定

        Args:
            bone_name (str): ボーン名

        Returns:
            bool: 移動可能な場合True
        """
        movable_bones = ["センター", "center", "Center", "全ての親", "master", "Master"]
        return bone_name in movable_bones

    def _convert_position_mmd_to_maya(self, position):
        """MMDの位置座標をMayaの座標系に変換

        Args:
            position (list): MMDの位置 [x, y, z]

        Returns:
            list: Mayaの位置 [x, y, z]
        """
        # MMD: 右手座標系 (X:右, Y:上, Z:手前)
        # Maya: 右手座標系 (X:右, Y:上, Z:手前)
        # ただし、単位の違いがある可能性があるため、スケール調整が必要な場合がある
        return [position[0], position[1], -position[2]]  # Z軸を反転

    def _convert_quaternion_to_euler(self, quaternion):
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

    def _convert_rotation_mmd_to_maya(self, rotation):
        """MMDの回転をMayaの座標系に変換

        Args:
            rotation (list): MMDの回転（度） [x, y, z]

        Returns:
            list: Mayaの回転（度） [x, y, z]
        """
        # 座標系の違いを補正
        return [rotation[0], rotation[1], -rotation[2]]  # Z軸の回転を反転

    def _setup_animation_layer(self, layer_name):
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

    def _add_objects_to_layer(self, objects):
        """オブジェクトをアニメーションレイヤーに追加

        Args:
            objects (list): 追加するオブジェクトのリスト
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

    def _apply_joint_orient_correction(self, rotation, joint_orient):
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
