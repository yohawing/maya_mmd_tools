"""
PMXファイルをMayaシーンにインポートするためのモジュール。
"""

import os

from maya import cmds
from mmd_tools.core.pmd_data import morph

from .. import settings
from ..converters import BoneConverter, MeshConverter, MorphConverter, PhysicsConverter
from ..core.logger import get_logger
from ..core.utils import create_bone_joint_mapping

# ロガーを取得
logger = get_logger("mmd_tools.io.pmx_importer")


def import_pmx_file(parser, filepath):
    """
    PMXファイルをMayaシーンにインポートします。

    Args:
        parser (PmxParser): PMXファイルを解析したパーサーオブジェクト
        filepath (str): インポートするPMXファイルのパス

    Returns:
        bool: インポートが成功したかどうか
    """
    logger.info("PMXファイルのインポートを開始: %s", filepath)
    scale = settings.get("import.general.scale_factor", 1.0)
    logger.debug("スケールファクター: %f", scale)

    try:
        # メッシュを変換
        logger.info("メッシュを変換中...")
        mesh_converter = MeshConverter(filepath)
        mesh_group, mesh_name = mesh_converter.convert_pmx_mesh(parser)
        logger.debug("メッシュ変換完了: グループ=%s, 名前=%s", mesh_group, mesh_name)

        logger.info("モーフを変換中...")
        morph_converter = MorphConverter()
        morph_converter.convert_pmx_morphs(parser, mesh_name)

        # ボーンを変換
        logger.info("ボーンを変換中...")
        bone_converter = BoneConverter()
        maya_joints, skin_cluster = bone_converter.convert_pmx_bones(parser, mesh_name)
        logger.debug(
            "ボーン変換完了: %d個のジョイント", len(maya_joints) if maya_joints else 0
        )

        # 物理を変換（設定で有効な場合）
        if settings.get("import.physics.import_physics", True):
            logger.info("物理を変換中...")
            physics_converter = PhysicsConverter()

            # ボーン名とMayaジョイント名のマッピングを作成
            bone_joint_mapping = create_bone_joint_mapping(
                parser.bones, maya_joints, "pmx"
            )

            # 物理データが存在する場合のみ変換
            if hasattr(parser, "rigid_bodies") and parser.rigid_bodies:
                ncloth_nodes, constraint_nodes = physics_converter.convert_pmx_physics(
                    parser, bone_joint_mapping
                )
                logger.debug(
                    "物理変換完了: nCloth=%d, Constraints=%d",
                    len(ncloth_nodes),
                    len(constraint_nodes),
                )
            else:
                logger.debug("物理データが存在しません")

        # スケールを適用
        if mesh_group and scale != 1.0:
            logger.info("スケールを適用中: %f", scale)
            cmds.setAttr(mesh_group + ".scaleX", scale)
            cmds.setAttr(mesh_group + ".scaleY", scale)
            cmds.setAttr(mesh_group + ".scaleZ", scale)
            cmds.makeIdentity(mesh_group, apply=True, scale=True)

        cmds.select(mesh_group)
        logger.info(
            "PMXファイルのインポートが完了しました: %s", os.path.basename(filepath)
        )
        return True

    except Exception as e:
        logger.error("PMXファイルのインポートに失敗しました: %s - %s", filepath, str(e))
        import traceback

        logger.debug("エラーの詳細:\n%s", traceback.format_exc())
        return False
