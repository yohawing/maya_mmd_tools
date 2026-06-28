import os
from typing import Optional

from .exceptions import MMDParseException
from .logger import get_logger

# Import specific parsers
from .pmd_data import PmdData
from .pmx_data import PmxData
from .vmd_data import VmdData
from .vpd_data import VpdData

# ロガー取得
logger = get_logger("mmd_tools.core.mmd_parser")


def _native_pmx_parse_enabled(use_native_pmx_parse: Optional[bool] = None) -> bool:
    """Return whether the optional native PMX parser should be attempted."""
    if use_native_pmx_parse is False:
        return False
    env_value = os.environ.get("MMD_TOOLS_ENABLE_NATIVE_PMX_PARSE")
    if env_value is not None:
        return env_value.lower() in {"1", "true", "yes", "on"}
    return True


def _try_native_pmx_parse(
    file_path: str,
    use_native_pmx_parse: Optional[bool] = None,
    require_native_pmx_parse: bool = False,
):
    """ネイティブ DLL での PMX パースを試みる。

    Args:
        file_path: 解析する PMX ファイルのパス。
        use_native_pmx_parse: False の場合は native parser を試行しない。
        require_native_pmx_parse: True の場合は native parser が使えない／失敗した時点で例外にする。
    """
    if not _native_pmx_parse_enabled(use_native_pmx_parse):
        if require_native_pmx_parse:
            raise MMDParseException("Native PMX parser is required but disabled")
        return None
    try:
        from .native.native_pmx_parser import parse_pmx_native

        result = parse_pmx_native(file_path)
    except Exception as exc:
        logger.debug("Native PMX parser unavailable: %s", exc)
        if require_native_pmx_parse:
            raise MMDParseException(f"Native PMX parser failed: {exc}") from exc
        return None
    if result is None and require_native_pmx_parse:
        raise MMDParseException("Native PMX parser is required but unavailable or failed")
    return result


def parse_mmd_file(
    file_path,
    use_native_pmx_parse: Optional[bool] = None,
    require_native_pmx_parse: bool = True,
):
    """
    MMDファイル（PMD, PMX, VMD, VPD）を解析し、解析されたデータオブジェクトを返す。

    Args:
        file_path (str): 解析するMMDファイルのパス。
        use_native_pmx_parse: PMX で native parser を試行するかどうか。
        require_native_pmx_parse: PMX で Python parser fallback を禁止するかどうか。既定は True。

    Returns:
        PmdData or PmxData or VmdData or VpdData: 解析されたMMDデータを含むパーサーオブジェクト。

    Raises:
        FileNotFoundError: ファイルが見つからない場合。
        MMDParseException: ファイルの解析に失敗した場合、またはサポートされていないファイル形式の場合。
    """
    logger.info(f"Starting MMD file parsing: {file_path}")

    if not os.path.exists(file_path):
        logger.error(f"MMD file not found: {file_path}")
        raise FileNotFoundError(f"MMD file not found: {file_path}")

    # VPDファイルの拡張子チェック
    if file_path.lower().endswith(".vpd"):
        logger.info("Starting parse as VPD file")
        parser = VpdData()
        parser.parse_file(file_path)
        logger.info("VPD file parsing completed")
        return parser

    # ファイルの最初の数バイトを読み込み、マジックナンバーでファイルタイプを判別する。
    # PMD: b'Pmd'
    # PMX: b'PMX '
    # VMD: b'Vocaloid Motion Data file'
    with open(file_path, "rb") as f:
        magic_bytes = f.read(4)  # Read enough bytes to cover PMD/PMX magic
        f.seek(0)  # Reset file pointer

        logger.debug(f"Magic bytes: {magic_bytes}")

        if magic_bytes.startswith(b"Pmd"):
            logger.info("Starting parse as PMD file")
            parser = PmdData()
            parser.parse_file(file_path)
            logger.info("PMD file parsing completed")
            return parser
        elif magic_bytes.startswith(b"PMX"):
            logger.info("Starting parse as PMX file")
            native_result = _try_native_pmx_parse(
                file_path,
                use_native_pmx_parse,
                require_native_pmx_parse=require_native_pmx_parse,
            )
            if native_result is not None:
                logger.info("PMX file parsing completed (native)")
                return native_result
            parser = PmxData()
            parser.parse_file(file_path)
            logger.info("PMX file parsing completed")
            return parser
        elif f.read(30).startswith(b"Vocaloid Motion Data"):  # VMD magic is longer
            f.seek(0)  # Reset file pointer again
            logger.info("Starting parse as VMD file")
            parser = VmdData()
            parser.parse_file(file_path)
            logger.info("VMD file parsing completed")
            return parser
        else:
            logger.error(f"Unsupported MMD file format: {file_path}")
            raise MMDParseException(f"Unsupported MMD file format: {file_path}")
