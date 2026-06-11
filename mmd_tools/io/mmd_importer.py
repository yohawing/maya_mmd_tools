"""
MMDファイル（PMX、PMD、VMD）を解析し、Mayaシーンにインポートするためのメインモジュール。
"""

from pathlib import Path

from mmd_tools.core import settings
from mmd_tools.core.mmd_parser import parse_mmd_file
from mmd_tools.io import pmd_importer, pmx_importer, vmd_importer
from mmd_tools.io.cpp_fast_importer import fast_import
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
    suffix = Path(filepath).suffix.lower()

    # --- C++ fast import path (opt-in, PMX only) -------------------------
    if suffix == ".pmx":
        use_fast = options.get(
            "use_cpp_fast_load",
            settings.get("import.native.use_cpp_fast_load", False),
        )
        if use_fast:
            mesh_only = options.get(
                "cpp_fast_load_mesh_only",
                settings.get("import.native.cpp_fast_load_mesh_only", True),
            )
            base_name = options.get("custom_namespace") or Path(filepath).stem
            import_scale = (
                scale
                if scale is not None
                else options.get("scale", settings.get("import.general.scale_factor", 1.0))
            )
            fast_root = fast_import(filepath, base_name=base_name, scale=import_scale, mesh_only=mesh_only)
            if fast_root is not None:
                logger.info("C++ fast import succeeded: %s", fast_root)
                return fast_root
            logger.info("C++ fast import failed/excluded – falling back to Python parser")

    try:
        # 汎用パーサーでファイルを解析
        parsed_data = parse_mmd_file(filepath)

        # 手動reload後はクラスIDがずれて isinstance が失敗することがあるため、
        # ファイル拡張子でインポーターを選ぶ。
        if suffix == ".pmx":
            return pmx_importer.import_pmx_file(
                parsed_data,
                filepath,
                settings.get("import.general.scale_factor", 1.0),
                options,
            )

        elif suffix == ".pmd":
            return pmd_importer.import_pmd_file(
                parsed_data,
                filepath,
                settings.get("import.general.scale_factor", 1.0),
                options,
            )

        elif suffix == ".vmd":
            return vmd_importer.import_vmd_file(parsed_data, filepath, options)

        else:
            logger.warning(f"Unsupported data type returned from parser: {type(parsed_data)}")
            return None

    except Exception as e:
        logger.error(f"Failed to import {filepath}: {e}")
        import traceback

        traceback.print_exc()
        return None
