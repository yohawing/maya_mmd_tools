"""
MMDファイル（PMX、PMD、VMD）を解析し、Mayaシーンにインポートするためのメインモジュール。
"""
from maya import cmds

from ..core import pmd_parser, pmx_parser, vmd_parser
from ..core.mmd_parser import parse_mmd_file
from . import pmd_importer, pmx_importer, vmd_importer


def import_mmd_file(filepath):
    """
    MMDファイルを解析し、Mayaシーンにインポートします。
    ファイルタイプに応じて適切なインポーターを呼び出します。

    Args:
        filepath (str): インポートするMMDファイルのパス。

    Returns:
        bool: インポートが成功したかどうか。
    """
    try:
        # 汎用パーサーでファイルを解析
        parsed_data = parse_mmd_file(filepath)

        # 解析されたデータのタイプに応じてインポーターを呼び出す
        if isinstance(parsed_data, pmx_parser.PmxParser):
            return pmx_importer.import_pmx_file(parsed_data, filepath)

        elif isinstance(parsed_data, pmd_parser.PmdParser):
            return pmd_importer.import_pmd_file(parsed_data, filepath)

        elif isinstance(parsed_data, vmd_parser.VmdParser):
            return vmd_importer.import_vmd_file(parsed_data, filepath)

        else:
            cmds.warning(f"Unsupported data type returned from parser: {type(parsed_data)}")
            return False

    except Exception as e:
        cmds.error(f"Failed to import {filepath}: {e}")
        import traceback
        traceback.print_exc()
        return False
