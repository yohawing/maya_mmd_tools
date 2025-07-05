"""
PMXファイルをMayaシーンにインポートするためのモジュール。
"""

import os

from maya import cmds

from .. import settings
from ..converters import BoneConverter, MeshConverter, MorphConverter, PhysicsConverter


def import_pmx_file(parser, filepath):
    """
    PMXファイルをMayaシーンにインポートします。

    Args:
        parser (PmxParser): PMXファイルを解析したパーサーオブジェクト
        filepath (str): インポートするPMXファイルのパス

    Returns:
        bool: インポートが成功したかどうか
    """
    print("Importing PMX file...")
    scale = settings.get("import.general.scale_factor", 1.0)

    try:
        # メッシュを変換
        mesh_converter = MeshConverter(filepath)
        mesh_group, mesh_name = mesh_converter.convert_pmx_mesh(parser)

        # ボーンを変換
        bone_converter = BoneConverter()
        joints = bone_converter.convert_pmx_bones(parser, mesh_name)

        # TODO: モーフ、物理などの変換処理をここに追加
        # MorphConverter.convert_pmx_morphs(parser, mesh_group)
        # PhysicsConverter.convert_pmx_physics(parser, mesh_group)

        # スケールを適用
        if mesh_group and scale != 1.0:
            cmds.setAttr(mesh_group + ".scaleX", scale)
            cmds.setAttr(mesh_group + ".scaleY", scale)
            cmds.setAttr(mesh_group + ".scaleZ", scale)
            cmds.makeIdentity(mesh_group, apply=True, scale=True)

        cmds.select(mesh_group)
        print(f"Successfully imported {os.path.basename(filepath)}")
        return True

    except Exception as e:
        cmds.error(f"Failed to import PMX file {filepath}: {e}")
        import traceback

        traceback.print_exc()
        return False
