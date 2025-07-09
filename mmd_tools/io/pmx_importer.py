"""
PMXファイルをMayaシーンにインポートするためのモジュール。
"""

import os

from maya import cmds
from mmd_tools.core.pmd_data import morph

from .. import settings
from ..converters import BoneConverter, MeshConverter, MorphConverter, PhysicsConverter
from ..core.logger import get_logger

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
        joints = bone_converter.convert_pmx_bones(parser, mesh_name)
        logger.debug("ボーン変換完了: %d個のジョイント", len(joints) if joints else 0)

        # TODO: 物理などの変換処理をここに追加
        # PhysicsConverter.convert_pmx_physics(parser, mesh_group)

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
