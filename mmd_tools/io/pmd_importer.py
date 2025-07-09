"""
PMDファイルをMayaシーンにインポートするためのモジュール。
"""

import os


from maya import cmds
from mmd_tools.converters import bone_converter

from .. import settings
from ..core.logger import get_logger
from ..converters import MeshConverter, BoneConverter, MorphConverter, PhysicsConverter

# ロガーを取得
logger = get_logger("mmd_tools.io.pmd_importer")


def import_pmd_file(parser, filepath):
    """
    PMDファイルをMayaシーンにインポートします。

    Args:
        parser (PmdParser): PMDファイルを解析したパーサーオブジェクト
        filepath (str): インポートするPMDファイルのパス

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
        mesh_group, mesh_name = mesh_converter.convert_pmd_mesh(parser)
        logger.debug("メッシュ変換完了: グループ=%s, 名前=%s", mesh_group, mesh_name)

        # モーフを変換
        logger.info("モーフを変換中...")
        morph_converter = MorphConverter()
        morph_converter.convert_pmd_morphs(parser, mesh_name)
        logger.debug("モーフ変換完了: %s", mesh_name)

        # ボーンを変換
        logger.info("ボーンを変換中...")
        bone_converter = BoneConverter()
        joints = bone_converter.convert_pmd_bones(parser, mesh_name)
        logger.debug("ボーン変換完了: %d個のジョイント", len(joints) if joints else 0)

        # TODO: モーフ、物理などの変換処理をここに追加
        # PhysicsConverter.convert_pmd_physics(parser, mesh_group)

        # スケールを適用
        if mesh_group and scale != 1.0:
            cmds.setAttr(mesh_group + ".scaleX", scale)
            cmds.setAttr(mesh_group + ".scaleY", scale)
            cmds.setAttr(mesh_group + ".scaleZ", scale)
            cmds.makeIdentity(mesh_group, apply=True, scale=True)

        cmds.select(mesh_group)
        logger.info("PMDファイルのインポートが成功しました: %s", filepath)
        return True

    except Exception as e:
        logger.error("PMDファイルのインポートに失敗しました: %s", filepath)
        import traceback

        logger.error("エラー詳細: %s", traceback.format_exc())
        return False
