"""
PMDファイルをMayaシーンにインポートするためのモジュール。
"""
import os
import maya.cmds as cmds
from .. import settings
from ..converters import mesh_converter

def import_pmd_file(parser, filepath):
    """
    PMDファイルをMayaシーンにインポートします。

    Args:
        parser (PmdParser): PMDファイルを解析したパーサーオブジェクト
        filepath (str): インポートするPMDファイルのパス

    Returns:
        bool: インポートが成功したかどうか
    """
    print("Importing PMD file...")
    scale = settings.get("import.general.scale_factor", 1.0)
    
    try:
        # メッシュを変換
        mesh_converter_instance = mesh_converter.MeshConverter(filepath)
        mesh_group = mesh_converter_instance.convert_pmd_mesh(parser)
        
        # TODO: ボーン、モーフ、物理などの変換処理をここに追加
        
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
        cmds.error(f"Failed to import PMD file {filepath}: {e}")
        import traceback
        traceback.print_exc()
        return False
