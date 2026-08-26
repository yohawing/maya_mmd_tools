"""VPDデータをMayaのポーズに変換するモジュール"""

import math
from typing import Any, Dict, List, Optional, Sequence

import maya.cmds as cmds
import maya.api.OpenMaya as om

from mmd_tools.core.logger import get_logger
from mmd_tools.core.constants import ATTR_MMD_BONE_NAME
from mmd_tools.core.coordinate_transform import mmd_point_to_maya
from mmd_tools.core.vpd_data.bone_pose import BonePose
from mmd_tools.converters.vmd_anim_layer import add_transform_attrs_to_anim_layer
from mmd_tools.converters.vmd_import_state import get_stored_bind_translate
from mmd_tools.converters.vmd_scene_collector import _build_rotation_export_context

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
        try:
            self._rotation_export_context = _build_rotation_export_context(joints)
        except Exception as exc:
            raise ValueError(f"VPD rotation context could not be built: {exc}") from exc
        self._validate_pose_conversions(vpd_data.bone_poses, joints, target_namespace)

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

        logger.debug(f"Built {len(self.bone_name_mapping)} bone mappings")

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

            # VPD position is a bind-relative motion delta, matching VMD.
            position_delta = self._convert_position_mmd_to_maya(bone_pose.position)
            bind_position = get_stored_bind_translate(maya_joint) or (0.0, 0.0, 0.0)
            position = [
                float(bind) + float(delta)
                for bind, delta in zip(bind_position, position_delta)
            ]
            current_position = [
                float(cmds.getAttr(f"{maya_joint}.translate{axis}")) for axis in "XYZ"
            ]
            if any(abs(current - desired) > 1.0e-5 for current, desired in zip(current_position, position)):
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
            rotation = self._convert_quaternion_to_joint_rotate(
                maya_joint, bone_pose.quaternion
            )
            current_rotation = [
                float(cmds.getAttr(f"{maya_joint}.rotate{axis}")) for axis in "XYZ"
            ]
            rotation_changed = any(
                abs(current - desired) > 1.0e-5
                for current, desired in zip(current_rotation, rotation)
            )
            if rotation_changed:
                cmds.setAttr(f"{maya_joint}.rotateX", rotation[0])
                cmds.setAttr(f"{maya_joint}.rotateY", rotation[1])
                cmds.setAttr(f"{maya_joint}.rotateZ", rotation[2])

            # キーフレームを作成
            if create_keyframe and rotation_changed:
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

    def _convert_position_mmd_to_maya(self, position: Sequence[float]) -> List[float]:
        """MMDの位置座標をMayaの座標系に変換

        Args:
            position (list): MMDの位置 [x, y, z]

        Returns:
            list: Mayaの位置 [x, y, z]
        """
        return list(mmd_point_to_maya(position))

    def _convert_quaternion_to_joint_rotate(
        self, joint: str, quaternion: Sequence[float]
    ) -> List[float]:
        """Invert the bind/JO/rotateOrder-aware VMD/VPD export transform."""

        if len(quaternion) != 4:
            raise ValueError("VPD quaternion must contain XYZW")
        values = [float(value) for value in quaternion]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("VPD quaternion must contain finite values")
        context = getattr(self, "_rotation_export_context", {}).get(joint)
        if context is None:
            raise ValueError(f"VPD rotation context is unavailable: {joint}")
        order = int(context["rotateOrder"])
        if not 0 <= order <= 5:
            raise ValueError(f"VPD target rotateOrder is invalid: {joint}: {order}")
        qx, qy, qz, qw = values
        motion = om.MQuaternion(-qx, -qy, qz, qw)
        total = (
            context["bindCorrection"]
            * motion
            * context["parentCorrection"]
        )
        rotate = total * context["jointOrient"].inverse()
        euler = rotate.asEulerRotation()
        order_map = (
            om.MEulerRotation.kXYZ,
            om.MEulerRotation.kYZX,
            om.MEulerRotation.kZXY,
            om.MEulerRotation.kXZY,
            om.MEulerRotation.kYXZ,
            om.MEulerRotation.kZYX,
        )
        euler.reorderIt(order_map[order])
        reference = om.MEulerRotation(
            *(
                math.radians(float(cmds.getAttr(f"{joint}.rotate{axis}")))
                for axis in "XYZ"
            ),
            order_map[order],
        )
        euler = euler.closestSolution(reference)
        return [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]

    def _validate_pose_conversions(
        self,
        bone_poses: Sequence[BonePose],
        joints: Sequence[str],
        namespace: Optional[str],
    ) -> None:
        """Validate every mapped pose conversion before the first scene write."""

        for bone_pose in bone_poses:
            joint = self._find_maya_joint(bone_pose.bone_name, joints, namespace)
            if joint is None:
                continue
            position = [float(value) for value in bone_pose.position]
            if len(position) != 3 or not all(math.isfinite(value) for value in position):
                raise ValueError(
                    f"VPD position must contain three finite values: {bone_pose.bone_name}"
                )
            self._convert_quaternion_to_joint_rotate(joint, bone_pose.quaternion)

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
            logger.debug(f"Using existing animation layer: {layer_name}")
        else:
            # 新しいレイヤーを作成
            self.anim_layer = cmds.animLayer(layer_name, override=False, weight=1.0)
            logger.debug(f"Created new animation layer: {layer_name}")

    def _add_objects_to_layer(self, objects: Sequence[str]) -> None:
        """オブジェクトをアニメーションレイヤーに追加

        Args:
            objects (list): 追加するオブジェクトのリスト
        """
        add_transform_attrs_to_anim_layer(self.anim_layer, objects)
