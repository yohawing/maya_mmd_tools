"""
MMDファイル（PMX、PMD、VMD）を解析し、Mayaシーンにインポートするためのメインモジュール。
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mmd_tools.core import settings, settings_keys
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.core.import_strategy import resolve_model_import_strategy
from mmd_tools.core.mmd_parser import parse_mmd_file
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.converters import vmd_profile
from mmd_tools.io import pmx_importer, vmd_importer
from mmd_tools.io.cpp_fast_importer import fast_import
from mmd_tools.core.logger import get_logger

logger = get_logger("mmd_tools.io.mmd_importer")

_OPTION_TO_SETTINGS_KEY = settings_keys.MODEL_OPTION_TO_SETTINGS_KEY


@contextmanager
def _scoped_settings_override(options: Dict[str, Any]):
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


def import_mmd_file(
    filepath: str,
    scale: Optional[float] = None,
    options: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Optional[Any]:
    """
    MMDファイルを解析し、Mayaシーンにインポートします。
    ファイルタイプに応じて適切なインポーターを呼び出します。

    Args:
        filepath (str): インポートするMMDファイルのパス。
        scale (float): インポート時のスケール値。(互換性のために残している)
        options (dict): インポートオプション。scaleを含むことができる。
        progress_callback (Callable[[int], None]): フェーズ進捗通知コールバック。

    Returns:
        str: インポートされたモデルのルートノード名。

    Raises:
        MMDImportException: ファイルの解析またはインポートに失敗した場合。
    """

    # デフォルトオプション
    if options is None:
        options = {}

    def _emit_progress(value: int) -> None:
        if progress_callback is not None:
            try:
                progress_callback(value)
            except Exception:
                logger.debug("Progress callback failed", exc_info=True)

    _emit_progress(5)
    strategy = resolve_model_import_strategy(filepath, options)
    suffix = strategy.suffix
    # Precedence: explicit scale= kwarg > options["scale"] > mode-aware policy
    # (dev: persisted scale_factor, normal: DEFAULT_SCALE_FACTOR 1.0).
    # Explicit scale= remains an intentional public API override.
    if scale is not None:
        import_scale = scale
    elif "scale" in options:
        import_scale = options["scale"]
    else:
        from mmd_tools.services.settings_service import SettingsService

        import_scale = SettingsService().resolve_import_scale()

    # --- C++ fast import path (opt-in, PMX only) -------------------------
    logger.info("Model import strategy: cpp_fast_load=%s (%s)", strategy.use_cpp_fast_load, strategy.cpp_fast_load_reason)
    if strategy.use_cpp_fast_load:
        _emit_progress(10)
        mesh_only = options.get(
            "cpp_fast_load_mesh_only",
            settings.get(settings_keys.IMPORT_NATIVE_CPP_FAST_LOAD_MESH_ONLY, True),
        )
        base_name = options.get("custom_namespace") or Path(filepath).stem
        include_morphs = options.get(
            "import_morphs",
            settings.get(settings_keys.IMPORT_MORPH_IMPORT_MORPHS, True),
        )
        fast_root = fast_import(
            filepath,
            base_name=base_name,
            scale=import_scale,
            mesh_only=mesh_only,
            include_morphs=include_morphs,
        )
        if fast_root is not None:
            _emit_progress(90)
            logger.info("C++ fast import succeeded: %s", fast_root)
            return fast_root
        logger.info("C++ fast import failed/excluded – falling back to Python parser")

    parse_completed = False
    try:
        # 汎用パーサーでファイルを解析
        with vmd_profile.scope("vmd_parse" if suffix == ".vmd" else "model_parse"):
            parsed_data = parse_mmd_file(
                filepath,
                use_native_pmx_parse=strategy.use_native_pmx_parse,
                require_native_pmx_parse=strategy.require_native_pmx_parse,
            )
        parse_completed = True
        if suffix == ".vmd":
            vmd_profile.set_extra("vmd_path", str(Path(filepath).resolve()))
            for attr in ("bone_frames", "morph_frames", "camera_frames", "light_frames"):
                vmd_profile.set_extra(attr, len(getattr(parsed_data, attr, []) or []))
        _emit_progress(12)

        # 手動reload後はクラスIDがずれて isinstance が失敗することがあるため、
        # ファイル拡張子でインポーターを選ぶ。
        if suffix == ".pmx":
            with _scoped_settings_override(options):
                return pmx_importer.import_pmx_file(
                    parsed_data,
                    filepath,
                    import_scale,
                    options,
                    progress_callback=progress_callback,
                )

        elif suffix == ".pmd":
            with _scoped_settings_override(options):
                return pmx_importer.import_pmx_file(
                    parsed_data,
                    filepath,
                    import_scale,
                    options,
                    progress_callback=progress_callback,
                )

        elif suffix == ".vmd":
            return vmd_importer.import_vmd_file(
                parsed_data,
                filepath,
                options,
                progress_callback=progress_callback,
            )

        else:
            raise MMDImportException(f"Unsupported data type returned from parser: {type(parsed_data)}")

    except Exception as e:
        if suffix == ".vmd" and options.get("bake_mode", False) and not parse_completed:
            logger.warning(
                "VMD parser failed in bake mode; attempting runtime bake from raw bytes: %s",
                e,
            )
            vmd_data = VmdData()
            vmd_data.source_file = str(Path(filepath).resolve())
            return vmd_importer.import_vmd_file(
                vmd_data,
                filepath,
                options,
                progress_callback=progress_callback,
            )
        if isinstance(e, MMDImportException):
            raise
        logger.error(f"Failed to import {filepath}: {e}", exc_info=True)
        raise MMDImportException(f"Failed to import {filepath}: {e}") from e
