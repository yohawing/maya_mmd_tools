"""
PMXファイルをMayaシーンにインポートするためのモジュール。
"""

import os

from maya import cmds
from mmd_tools.core import maya_utils

from .. import settings
from ..converters import BoneConverter, MeshConverter, MorphConverter, PhysicsConverter
from ..core.logger import get_logger
from ..core.utils import create_bone_joint_mapping
from ..core.constants import (
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    SCENE_ROOT_SUFFIX,
)
from ..core.namespace_utils import NamespaceUtils

# ロガーを取得
logger = get_logger("mmd_tools.io.pmx_importer")


def import_pmx_file(parser, filepath, scale=1.0, options=None):
    """
    PMXファイルをMayaシーンにインポートします。

    Args:
        parser (PmxParser): PMXファイルを解析したパーサーオブジェクト
        filepath (str): インポートするPMXファイルのパス
        scale (float): スケール値（互換性のため）
        options (dict): インポートオプション

    Returns:
        bool: インポートが成功したかどうか
    """
    if options is None:
        options = {}
    logger.info("PMXファイルのインポートを開始: %s", filepath)

    logger.debug("スケールファクター: %f", scale)

    # Namespace処理
    use_namespace = options.get("use_namespace", False)
    custom_namespace = options.get("custom_namespace")
    namespace = None

    if use_namespace:
        if custom_namespace:
            # カスタムnamespaceを使用
            namespace = NamespaceUtils.ensure_unique_namespace(custom_namespace)
            logger.info(f"Using custom namespace: {namespace}")
        else:
            # モデル名からnamespace生成
            model_name = maya_utils.sanitize_text(parser.header.get_name())
            base_ns = NamespaceUtils.generate_namespace(model_name)
            namespace = NamespaceUtils.ensure_unique_namespace(base_ns)
            logger.info(f"Using auto-generated namespace: {namespace}")
        model_name = maya_utils.sanitize_text(parser.header.get_name())
    else:
        model_name = maya_utils.sanitize_text(parser.header.get_name())

    try:
        # namespace context内でモデルを構築
        with NamespaceUtils.namespace_context(namespace):
            # ルートグループを作成
            root_group = cmds.group(empty=True, name=f"{model_name}{SCENE_ROOT_SUFFIX}")
            logger.debug("ルートグループ作成: %s", root_group)

            # Add attributes to root node
            maya_utils.set_custom_attributes(
                root_group,
                {
                    ATTR_MMD_MODEL_NAME: parser.header.model_name,
                    ATTR_MMD_MODEL_NAME_EN: parser.header.model_name_english,
                    ATTR_MMD_COMMENT: parser.header.comment,
                    ATTR_MMD_COMMENT_EN: parser.header.comment_english,
                },
            )

            # メッシュを変換
            logger.info("メッシュを変換中...")
            mesh_converter = MeshConverter(filepath)
            mesh_group, mesh_name = mesh_converter.convert_pmx_mesh(parser, root_group)
            logger.debug("メッシュ変換完了: グループ=%s, 名前=%s", mesh_group, mesh_name)

            logger.info("モーフを変換中...")
            morph_converter = MorphConverter()
            morph_converter.convert_pmx_morphs(parser, mesh_name)
            logger.debug("モーフ変換完了")

            # ボーンを変換
            logger.info("ボーンを変換中...")
            bone_converter = BoneConverter()
            maya_joints, skin_cluster = bone_converter.convert_pmx_bones(parser, mesh_name, root_group)
            logger.debug(
                "ボーン変換完了: %d個のジョイント",
                len(maya_joints) if maya_joints else 0,
            )

            # 物理を変換（設定で有効な場合）
            if settings.get("import.physics.import_physics", True):
                logger.info("物理を変換中...")
                physics_converter = PhysicsConverter()

                # ボーン名とMayaジョイント名のマッピングを作成
                bone_joint_mapping = create_bone_joint_mapping(parser.bones, maya_joints, "pmx")

                # 物理データが存在する場合のみ変換
                if hasattr(parser, "rigid_bodies") and parser.rigid_bodies:
                    ncloth_nodes, constraint_nodes = physics_converter.convert_pmx_physics(
                        parser, bone_joint_mapping, root_group
                    )
                    logger.debug(
                        "物理変換完了: nCloth=%d, Constraints=%d",
                        len(ncloth_nodes),
                        len(constraint_nodes),
                    )
                else:
                    logger.debug("物理データが存在しません")

            # スケールを適用
            if root_group and scale != 1.0:
                logger.info("スケールを適用中: %f", scale)
                cmds.setAttr(root_group + ".scaleX", scale)
                cmds.setAttr(root_group + ".scaleY", scale)
                cmds.setAttr(root_group + ".scaleZ", scale)
                cmds.makeIdentity(root_group, apply=True, scale=True)

            cmds.select(root_group)
        logger.info("PMXファイルのインポートが完了しました: %s", os.path.basename(filepath))
        return root_group  # ルートノードの名前を返す

    except Exception as e:
        logger.error("PMXファイルのインポートに失敗しました: %s - %s", filepath, str(e))
        import traceback

        logger.debug("エラーの詳細:\n%s", traceback.format_exc())

        # エラー時のnamespaceクリーンアップ
        if namespace:
            logger.info(f"Cleaning up namespace: {namespace}")
            NamespaceUtils.cleanup_namespace(namespace, force=True)

        return None
