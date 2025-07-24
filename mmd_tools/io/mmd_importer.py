"""
MMDファイル（PMX、PMD、VMD）を解析し、Mayaシーンにインポートするためのメインモジュール。
"""

from maya import cmds

from mmd_tools.core import PmdParser, PmxParser, VmdParser
from mmd_tools.core.mmd_parser import parse_mmd_file
from mmd_tools.io import pmd_importer, pmx_importer, vmd_importer
from mmd_tools.core.logger import get_logger

logger = get_logger("mmd_tools.io.mmd_importer")


def import_mmd_file(filepath, scale=1.0):
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
        # print(isinstance(parsed_data, pmx_parser.PmxParser))
        if isinstance(parsed_data, PmxParser):
            return pmx_importer.import_pmx_file(parsed_data, filepath, scale)

        elif isinstance(parsed_data, PmdParser):
            return pmd_importer.import_pmd_file(parsed_data, filepath, scale)

        elif isinstance(parsed_data, VmdParser):
            return vmd_importer.import_vmd_file(parsed_data, filepath)

        else:
            logger.warning(
                f"Unsupported data type returned from parser: {type(parsed_data)}"
            )
            return None

    except Exception as e:
        logger.error(f"Failed to import {filepath}: {e}")
        import traceback

        traceback.print_exc()
        return None
