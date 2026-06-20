"""
MMDファイル（PMX、PMD、VMD）を解析し、Mayaシーンにインポートするためのメインモジュール。
"""

from contextlib import contextmanager
from pathlib import Path

from mmd_tools.core import settings
from mmd_tools.core.mmd_parser import parse_mmd_file
from mmd_tools.io import pmd_importer, pmx_importer, vmd_importer
from mmd_tools.io.cpp_fast_importer import fast_import
from mmd_tools.core.logger import get_logger

logger = get_logger("mmd_tools.io.mmd_importer")

# Maps option-dict keys to global settings paths for model imports.
# Used by _scoped_settings_override to ensure downstream converters that read
# settings directly receive the same forced values as the options dict.
_OPTION_TO_SETTINGS_KEY = {
    "separate_meshes_by_material": "import.model.separate_meshes_by_material",
    "split_meshes_by_morph_groups": "import.model.split_meshes_by_morph_groups",
    "auto_classify_transparency": "import.model.auto_classify_transparency",
    "auto_resolve_textures": "import.model.auto_resolve_textures",
    "disable_backface_culling": "import.model.disable_backface_culling",
    "uv_set_name": "import.model.uv_set_name",
    "texture_search_path": "import.model.texture_search_path",
    "add_semi_standard_bones": "import.rig.add_semi_standard_bones",
    "translate_names": "import.naming.translate_names",
    "hide_hidden_geometry": "import.model.hide_hidden_geometry",
}


@contextmanager
def _scoped_settings_override(options):
    """Temporarily apply option values to global settings for the duration of a model import.

    Downstream converters that read settings directly will see the values from
    the options dict. Original values are restored unconditionally in a finally block.
    Only option keys present in both *options* and *_OPTION_TO_SETTINGS_KEY* are applied.
    """
    saved = {}
    for opt_key, settings_key in _OPTION_TO_SETTINGS_KEY.items():
        if opt_key in options:
            saved[settings_key] = settings.get(settings_key)
            settings.set(settings_key, options[opt_key])
    try:
        yield
    finally:
        for settings_key, original_value in saved.items():
            settings.set(settings_key, original_value)


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
            include_morphs = options.get(
                "import_morphs",
                settings.get("import.morph.import_morphs", True),
            )
            fast_root = fast_import(
                filepath,
                base_name=base_name,
                scale=import_scale,
                mesh_only=mesh_only,
                include_morphs=include_morphs,
            )
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
            with _scoped_settings_override(options):
                return pmx_importer.import_pmx_file(
                    parsed_data,
                    filepath,
                    settings.get("import.general.scale_factor", 1.0),
                    options,
                )

        elif suffix == ".pmd":
            with _scoped_settings_override(options):
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
