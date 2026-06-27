"""VPDファイルのインポート機能を提供するモジュール"""

import os
import maya.cmds as cmds

from mmd_tools.core.logger import get_logger
from mmd_tools.core.namespace_utils import NamespaceUtils
from mmd_tools.converters.vpd_converter import VpdConverter

logger = get_logger(__name__)


def import_vpd_file(parser, filepath, options=None):
    """VPDファイルをMayaシーンにインポートしてポーズを適用

    Args:
        parser (VpdData): VPDファイルを解析したパーサーオブジェクト
        filepath (str): インポートするVPDファイルのパス
        options (dict): インポートオプション
            - target_model: ターゲットモデル（指定しない場合は選択オブジェクトから取得）
            - create_keyframe: 現在のフレームにキーフレームを作成するか（デフォルト: True）
            - apply_to_all: 全てのモデルに適用するか（デフォルト: False）

    Returns:
        bool: インポートが成功したか
    """
    if options is None:
        options = {}

    logger.info(f"Starting VPD file import: {filepath}")

    try:
        # オプションからターゲットモデルを取得
        target_namespace = None
        target_model = options.get("target_model")
        apply_to_all = options.get("apply_to_all", False)
        create_keyframe = options.get("create_keyframe", True)

        if target_model:
            # ターゲットモデルからネームスペースを取得
            target_namespace = NamespaceUtils.get_namespace_from_node(target_model)
            if target_namespace:
                logger.info(f"Target namespace: {target_namespace}")
        else:
            # 選択されているオブジェクトからターゲットを取得
            selected = cmds.ls(selection=True)
            if selected:
                for sel in selected:
                    # ジョイントまたはメッシュから関連するネームスペースを取得
                    if cmds.nodeType(sel) == "joint" or cmds.nodeType(sel) == "transform":
                        target_namespace = NamespaceUtils.get_namespace_from_node(sel)
                        if target_namespace:
                            logger.info(f"Target namespace from selected object: {target_namespace}")
                            break

            if not target_namespace and not apply_to_all:
                # ネームスペースが見つからない場合は、選択されたジョイントに直接適用を試みる
                selected_joints = cmds.ls(selection=True, type="joint")
                if not selected_joints:
                    logger.warning("Target model is not specified. Select model joints.")
                    cmds.warning("Please select target model joints to apply the pose.")
                    return False

        # 現在のフレームを取得
        current_frame = cmds.currentTime(query=True)

        # VpdConverterを使用してポーズを変換・適用
        converter = VpdConverter()

        if apply_to_all:
            # 全てのモデルに適用
            logger.info("Applying pose to all models")
            namespaces = NamespaceUtils.get_all_namespaces()
            success_count = 0

            for ns in namespaces:
                if ns and ns != ":":  # ルートネームスペースは除外
                    if converter.convert(parser, ns, options):
                        success_count += 1
                        if create_keyframe:
                            _create_keyframes_for_namespace(ns, current_frame)

            if success_count == 0:
                # ネームスペースなしのジョイントに適用を試みる
                if converter.convert(parser, None, options):
                    success_count = 1
                    if create_keyframe:
                        _create_keyframes_for_namespace(None, current_frame)

            success = success_count > 0
            if success:
                logger.info(f"Applied pose to {success_count} model(s)")
        else:
            # 特定のモデルに適用
            success = converter.convert(parser, target_namespace, options)

            if success and create_keyframe:
                _create_keyframes_for_namespace(target_namespace, current_frame)

        if success:
            logger.info("VPD file import completed")

            # ビューポートにメッセージを表示
            cmds.inViewMessage(
                amg=f"VPD pose applied successfully from: {os.path.basename(filepath)}",
                pos="midCenter",
                fade=True,
                fadeStayTime=2000,
                fadeOutTime=500,
            )
        else:
            logger.warning("VPD file import failed")
            cmds.warning(f"Failed to apply VPD pose from: {filepath}")

        return success

    except Exception as e:
        logger.error(f"Failed to import VPD file: {e}")
        cmds.error(f"Failed to import VPD file {filepath}: {e}")
        import traceback

        traceback.print_exc()
        return False


def _create_keyframes_for_namespace(namespace, frame):
    """指定されたネームスペースのジョイントにキーフレームを作成

    Args:
        namespace (str): ネームスペース
        frame (float): キーフレームを作成するフレーム
    """
    if namespace:
        pattern = f"{namespace}:*"
    else:
        pattern = "*"

    joints = cmds.ls(pattern, type="joint")

    for joint in joints:
        # 回転のキーフレームを作成
        cmds.setKeyframe(joint, attribute="rotateX", time=frame)
        cmds.setKeyframe(joint, attribute="rotateY", time=frame)
        cmds.setKeyframe(joint, attribute="rotateZ", time=frame)

        # 移動可能なジョイント（センターなど）の場合は位置もキーフレーム化
        if _is_movable_joint(joint):
            cmds.setKeyframe(joint, attribute="translateX", time=frame)
            cmds.setKeyframe(joint, attribute="translateY", time=frame)
            cmds.setKeyframe(joint, attribute="translateZ", time=frame)

    logger.debug(f"Created keyframes for {len(joints)} joints at frame {frame}")


def _is_movable_joint(joint_name):
    """移動可能なジョイントかどうかを判定

    Args:
        joint_name (str): ジョイント名

    Returns:
        bool: 移動可能な場合True
    """
    movable_keywords = ["center", "Center", "hip", "Hip", "root", "Root", "master", "Master"]
    joint_base_name = joint_name.split(":")[-1] if ":" in joint_name else joint_name

    for keyword in movable_keywords:
        if keyword in joint_base_name:
            return True

    return False
