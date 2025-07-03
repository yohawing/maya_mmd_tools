import os
import maya.cmds as cmds
from .. import settings
from ..core.mmd_parser import parse_mmd_file
from ..core import pmx_parser, pmd_parser, vmd_parser
from ..converters import mesh_converter

def import_mmd_file(filepath):
    """
    MMDファイルを解析し、Mayaシーンにインポートします。

    Args:
        filepath (str): インポートするMMDファイルのパス。
        scale (float, optional): インポート時のスケール。デフォルトは 1.0。

    Returns:
        bool: インポートが成功したかどうか。
    """

    scale = settings.get("import.general.scale_factor", 1.0)

    try:
        # 汎用パーサーでファイルを解析
        parsed_data = parse_mmd_file(filepath)

        # 解析されたデータのタイプに応じて処理を分岐
        if isinstance(parsed_data, pmx_parser.PmxParser):
            print("Importing PMX file...")
            # メッシュを変換
            converter = mesh_converter.MeshConverter(filepath)
            mesh_group = converter.convert_pmx_mesh(parsed_data)

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

        elif isinstance(parsed_data, pmd_parser.PmdParser):
            cmds.warning("PMD import is not yet fully implemented.")
            # TODO: PMDコンバーターを呼び出す

            converter = mesh_converter.MeshConverter(filepath)
            mesh_group = converter.convert_pmd_mesh(parsed_data)

            print(f"Successfully imported {os.path.basename(filepath)}")

            # スケールを適用
            if mesh_group and scale != 1.0:
                cmds.setAttr(mesh_group + ".scaleX", scale)
                cmds.setAttr(mesh_group + ".scaleY", scale)
                cmds.setAttr(mesh_group + ".scaleZ", scale)
                cmds.makeIdentity(mesh_group, apply=True, scale=True)

            return True

        elif isinstance(parsed_data, vmd_parser.VmdParser):
            cmds.warning("VMD import is not yet implemented.")
            # TODO: VMDコンバーターを呼び出す
            return False
            
        else:
            cmds.warning(f"Unsupported data type returned from parser: {type(parsed_data)}")
            return False

    except Exception as e:
        cmds.error(f"Failed to import {filepath}: {e}")
        import traceback
        traceback.print_exc()
        return False
