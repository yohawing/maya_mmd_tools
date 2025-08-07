"""VPDデータをMayaのポーズに変換するモジュール"""

import maya.cmds as cmds
import maya.api.OpenMaya as om
import math

from mmd_tools.core.logger import get_logger
from mmd_tools.core.namespace_utils import NamespaceUtils
from mmd_tools.core.maya_utils import get_attribute, set_attribute
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
        self.fallback_mapping = {}  # CustomAttributesがない場合のフォールバック用マッピング
        self._initialize_fallback_mapping()

    def _initialize_fallback_mapping(self):
        """CustomAttributesがない場合のフォールバック用マッピングを初期化"""
        # 基本的なMMDボーン名とMayaでの一般的な名前のマッピング
        self.fallback_mapping = {
            # 体幹
            "センター": ["center", "Center", "hip", "Hip", "root", "Root"],
            "上半身": ["spine", "Spine", "spine1", "Spine1", "upper_body", "UpperBody"],
            "上半身2": ["spine2", "Spine2", "chest", "Chest", "upper_body2", "UpperBody2"],
            "下半身": ["pelvis", "Pelvis", "lower_body", "LowerBody"],
            "首": ["neck", "Neck"],
            "頭": ["head", "Head"],
            # 腕（左）
            "左肩": ["shoulder_L", "L_shoulder", "left_shoulder", "LeftShoulder"],
            "左腕": ["arm_L", "L_arm", "left_arm", "LeftArm", "upperarm_L", "L_upperarm"],
            "左ひじ": ["elbow_L", "L_elbow", "forearm_L", "L_forearm", "left_elbow", "LeftElbow"],
            "左手首": ["wrist_L", "L_wrist", "hand_L", "L_hand", "left_wrist", "LeftWrist"],
            # 腕（右）
            "右肩": ["shoulder_R", "R_shoulder", "right_shoulder", "RightShoulder"],
            "右腕": ["arm_R", "R_arm", "right_arm", "RightArm", "upperarm_R", "R_upperarm"],
            "右ひじ": ["elbow_R", "R_elbow", "forearm_R", "R_forearm", "right_elbow", "RightElbow"],
            "右手首": ["wrist_R", "R_wrist", "hand_R", "R_hand", "right_wrist", "RightWrist"],
            # 足（左）
            "左足": ["leg_L", "L_leg", "thigh_L", "L_thigh", "left_leg", "LeftLeg"],
            "左ひざ": ["knee_L", "L_knee", "shin_L", "L_shin", "left_knee", "LeftKnee"],
            "左足首": ["ankle_L", "L_ankle", "foot_L", "L_foot", "left_ankle", "LeftAnkle"],
            "左つま先": ["toe_L", "L_toe", "toes_L", "L_toes", "left_toe", "LeftToe"],
            # 足（右）
            "右足": ["leg_R", "R_leg", "thigh_R", "R_thigh", "right_leg", "RightLeg"],
            "右ひざ": ["knee_R", "R_knee", "shin_R", "R_shin", "right_knee", "RightKnee"],
            "右足首": ["ankle_R", "R_ankle", "foot_R", "R_foot", "right_ankle", "RightAnkle"],
            "右つま先": ["toe_R", "R_toe", "toes_R", "R_toes", "right_toe", "RightToe"],
            # 指（左手）
            "左親指０": ["thumb_01_L", "L_thumb_01", "left_thumb_01"],
            "左親指１": ["thumb_02_L", "L_thumb_02", "left_thumb_02"],
            "左親指２": ["thumb_03_L", "L_thumb_03", "left_thumb_03"],
            "左人指１": ["index_01_L", "L_index_01", "left_index_01"],
            "左人指２": ["index_02_L", "L_index_02", "left_index_02"],
            "左人指３": ["index_03_L", "L_index_03", "left_index_03"],
            "左中指１": ["middle_01_L", "L_middle_01", "left_middle_01"],
            "左中指２": ["middle_02_L", "L_middle_02", "left_middle_02"],
            "左中指３": ["middle_03_L", "L_middle_03", "left_middle_03"],
            "左薬指１": ["ring_01_L", "L_ring_01", "left_ring_01"],
            "左薬指２": ["ring_02_L", "L_ring_02", "left_ring_02"],
            "左薬指３": ["ring_03_L", "L_ring_03", "left_ring_03"],
            "左小指１": ["pinky_01_L", "L_pinky_01", "left_pinky_01"],
            "左小指２": ["pinky_02_L", "L_pinky_02", "left_pinky_02"],
            "左小指３": ["pinky_03_L", "L_pinky_03", "left_pinky_03"],
            # 指（右手）
            "右親指０": ["thumb_01_R", "R_thumb_01", "right_thumb_01"],
            "右親指１": ["thumb_02_R", "R_thumb_02", "right_thumb_02"],
            "右親指２": ["thumb_03_R", "R_thumb_03", "right_thumb_03"],
            "右人指１": ["index_01_R", "R_index_01", "right_index_01"],
            "右人指２": ["index_02_R", "R_index_02", "right_index_02"],
            "右人指３": ["index_03_R", "R_index_03", "right_index_03"],
            "右中指１": ["middle_01_R", "R_middle_01", "right_middle_01"],
            "右中指２": ["middle_02_R", "R_middle_02", "right_middle_02"],
            "右中指３": ["middle_03_R", "R_middle_03", "right_middle_03"],
            "右薬指１": ["ring_01_R", "R_ring_01", "right_ring_01"],
            "右薬指２": ["ring_02_R", "R_ring_02", "right_ring_02"],
            "右薬指３": ["ring_03_R", "R_ring_03", "right_ring_03"],
            "右小指１": ["pinky_01_R", "R_pinky_01", "right_pinky_01"],
            "右小指２": ["pinky_02_R", "R_pinky_02", "right_pinky_02"],
            "右小指３": ["pinky_03_R", "R_pinky_03", "right_pinky_03"],
        }

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

        logger.info("VPDポーズの変換を開始")

        # ボーン名マッピングを構築
        self._build_name_mappings(target_namespace)

        # ジョイントのリストを取得
        joints = self._get_target_joints(target_namespace)
        if not joints:
            logger.warning("ターゲットジョイントが見つかりません")
            return False

        # ボーンポーズを適用
        applied_count = 0
        for bone_pose in vpd_data.bone_poses:
            if self._apply_bone_pose(bone_pose, joints, target_namespace):
                applied_count += 1

        logger.info(f"VPDポーズの変換が完了: {applied_count}/{len(vpd_data.bone_poses)}個のボーンを適用")

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
        logger.debug("ボーン名マッピングを構築中")
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
                    logger.debug(f"ボーンマッピング: {original_name} -> {joint}")

        logger.info(f"{len(self.bone_name_mapping)}個のボーンマッピングを構築しました")

    def _find_maya_joint(self, mmd_bone_name, joints, namespace=None):
        """MMDボーン名に対応するMayaジョイントを探す

        Args:
            mmd_bone_name (str): MMDのボーン名
            joints (list): 検索対象のジョイントリスト
            namespace (str): ネームスペース

        Returns:
            str: 見つかったジョイント名、見つからない場合はNone
        """
        # まずCustomAttributesベースのマッピングから探す
        if mmd_bone_name in self.bone_name_mapping:
            return self.bone_name_mapping[mmd_bone_name]

        # 完全一致を試す（ジョイント名がそのままMMDボーン名と一致する場合）
        for joint in joints:
            joint_name = joint.split(":")[-1] if ":" in joint else joint
            if joint_name == mmd_bone_name:
                return joint

        # フォールバックマッピングテーブルから探す
        if mmd_bone_name in self.fallback_mapping:
            possible_names = self.fallback_mapping[mmd_bone_name]
            for joint in joints:
                joint_name = joint.split(":")[-1] if ":" in joint else joint
                if joint_name in possible_names:
                    return joint

        # 部分一致を試す（最後の手段）
        for joint in joints:
            joint_name = joint.split(":")[-1] if ":" in joint else joint
            # センター -> center のような簡単な変換
            if mmd_bone_name in joint_name or joint_name in mmd_bone_name:
                logger.debug(f"部分一致でボーンを発見: {mmd_bone_name} -> {joint}")
                return joint

        return None

    def _apply_bone_pose(self, bone_pose, joints, namespace=None):
        """単一のボーンポーズを適用

        Args:
            bone_pose (BonePose): ボーンポーズデータ
            joints (list): ジョイントのリスト
            namespace (str): ネームスペース

        Returns:
            bool: 適用が成功したか
        """
        # 対応するMayaジョイントを探す
        maya_joint = self._find_maya_joint(bone_pose.bone_name, joints, namespace)

        if not maya_joint:
            logger.debug(f"ボーン '{bone_pose.bone_name}' に対応するジョイントが見つかりません")
            return False

        try:
            # 位置の適用（センターボーンなど移動可能なボーンのみ）
            if self._is_movable_bone(bone_pose.bone_name):
                position = self._convert_position_mmd_to_maya(bone_pose.position)
                cmds.setAttr(f"{maya_joint}.translateX", position[0])
                cmds.setAttr(f"{maya_joint}.translateY", position[1])
                cmds.setAttr(f"{maya_joint}.translateZ", position[2])

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

            logger.debug(f"ボーン '{bone_pose.bone_name}' を '{maya_joint}' に適用")
            return True

        except Exception as e:
            logger.warning(f"ボーン '{bone_pose.bone_name}' の適用に失敗: {e}")
            return False

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
