"""
PMDファイルをMayaシーンにインポートするためのモジュール。
"""

import os


from maya import cmds
from mmd_tools.converters import bone_converter

from .. import settings
from ..core.logger import get_logger
from ..converters import MeshConverter, BoneConverter, MorphConverter, PhysicsConverter
from ..core.utils import create_bone_joint_mapping
from ..core.constants import SCENE_ROOT_SUFFIX

# ロガーを取得
logger = get_logger("mmd_tools.io.pmd_importer")


def import_pmd_file(parser, filepath, scale=1.0, options=None):
    """
    PMDファイルをMayaシーンにインポートします。

    Args:
        parser (PmdParser): PMDファイルを解析したパーサーオブジェクト
        filepath (str): インポートするPMDファイルのパス
        scale (float): スケール値（互換性のため）
        options (dict): インポートオプション

    Returns:
        bool: インポートが成功したかどうか
    """
    if options is None:
        options = {}
    logger.info("PMXファイルのインポートを開始: %s", filepath)
    
    logger.debug("スケールファクター: %f", scale)

    try:
        # ルートグループを作成
        model_name = parser.header.get_name()
        root_group = cmds.group(empty=True, name=f"{model_name}{SCENE_ROOT_SUFFIX}")
        logger.debug("ルートグループ作成: %s", root_group)

        # Add attributes to root node
        cmds.addAttr(root_group, longName='mmd_model_name_jp', dataType='string')
        cmds.addAttr(root_group, longName='mmd_model_name_en', dataType='string')
        cmds.addAttr(root_group, longName='mmd_comment_jp', dataType='string')
        cmds.addAttr(root_group, longName='mmd_comment_en', dataType='string')

        cmds.setAttr(f"{root_group}.mmd_model_name_jp", parser.header.get_name(), type='string')
        cmds.setAttr(f"{root_group}.mmd_model_name_en", "", type='string')
        cmds.setAttr(f"{root_group}.mmd_comment_jp", parser.header.get_comment(), type='string')
        cmds.setAttr(f"{root_group}.mmd_comment_en", "", type='string')
        
        # メッシュを変換
        logger.info("メッシュを変換中...")
        mesh_converter = MeshConverter(filepath)
        mesh_group, mesh_name = mesh_converter.convert_pmd_mesh(parser, root_group)
        logger.debug("メッシュ変換完了: グループ=%s, 名前=%s", mesh_group, mesh_name)

        # モーフを変換
        logger.info("モーフを変換中...")
        morph_converter = MorphConverter()
        morph_converter.convert_pmd_morphs(parser, mesh_name)
        logger.debug("モーフ変換完了: %s", mesh_name)

        # ボーンを変換
        logger.info("ボーンを変換中...")
        bone_converter = BoneConverter()
        maya_joints, skin_cluster = bone_converter.convert_pmd_bones(parser, mesh_name, root_group)
        logger.debug(
            "ボーン変換完了: %d個のジョイント", len(maya_joints) if maya_joints else 0
        )

        # 物理を変換（設定で有効な場合）
        if settings.get("import.physics.import_physics", True):
            logger.info("物理を変換中...")
            physics_converter = PhysicsConverter()

            # ボーン名とMayaジョイント名のマッピングを作成
            bone_joint_mapping = create_bone_joint_mapping(
                parser.bones, maya_joints, "pmd"
            )

            # 物理データが存在する場合のみ変換
            if hasattr(parser, "rigid_bodies") and parser.rigid_bodies:
                ncloth_nodes, constraint_nodes = physics_converter.convert_pmd_physics(
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
            cmds.setAttr(root_group + ".scaleX", scale)
            cmds.setAttr(root_group + ".scaleY", scale)
            cmds.setAttr(root_group + ".scaleZ", scale)
            cmds.makeIdentity(root_group, apply=True, scale=True)

        cmds.select(root_group)
        logger.info("PMDファイルのインポートが成功しました: %s", filepath)
        return root_group  # ルートノードの名前を返す

    except Exception as e:
        logger.error("PMDファイルのインポートに失敗しました: %s", filepath)
        import traceback

        logger.error("エラー詳細: %s", traceback.format_exc())
        return None
