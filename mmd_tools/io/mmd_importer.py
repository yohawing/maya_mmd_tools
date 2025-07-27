"""
MMDファイル（PMX、PMD、VMD）を解析し、Mayaシーンにインポートするためのメインモジュール。
"""

from maya import cmds

from mmd_tools.core import PmdParser, PmxParser, VmdParser, settings
from mmd_tools.core.mmd_parser import parse_mmd_file
from mmd_tools.io import pmd_importer, pmx_importer, vmd_importer
from mmd_tools.core.logger import get_logger

logger = get_logger("mmd_tools.io.mmd_importer")


def import_mmd_file(filepath, scale=None, options=None):
    """
    MMDファイルを解析し、Mayaシーンにインポートします。
    ファイルタイプに応じて適切なインポーターを呼び出します。

    Args:
        filepath (str): インポートするMMDファイルのパス。
        scale (float): インポート時のスケール値。(互換性のために残している)
        options (dict): インポートオプション。scaleを含むことができる。

    Returns:
        str: インポートされたモデルのルートノード名。失敗時はNone。
    """

    # デフォルトオプション
    if options is None:
        options = {}
    try:
        # 汎用パーサーでファイルを解析
        parsed_data = parse_mmd_file(filepath)

        # 解析されたデータのタイプに応じてインポーターを呼び出す
        if isinstance(parsed_data, PmxParser):
            return pmx_importer.import_pmx_file(
                parsed_data,
                filepath,
                settings.get("import.general.scale_factor", 1.0),
                options,
            )

        elif isinstance(parsed_data, PmdParser):
            return pmd_importer.import_pmd_file(
                parsed_data,
                filepath,
                settings.get("import.general.scale_factor", 1.0),
                options,
            )

        elif isinstance(parsed_data, VmdParser):
            return vmd_importer.import_vmd_file(parsed_data, filepath, options)

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
