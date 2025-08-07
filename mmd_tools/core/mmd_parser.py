import os

from .exceptions import MMDParseException
from .logger import get_logger

# Import specific parsers
from .pmd_data import PmdData
from .pmx_data import PmxData
from .vmd_data import VmdData
from .vpd_data import VpdData

# ロガー取得
logger = get_logger("mmd_tools.core.mmd_parser")


def parse_mmd_file(file_path):
    """
    MMDファイル（PMD, PMX, VMD, VPD）を解析し、解析されたデータオブジェクトを返す。

    Args:
        file_path (str): 解析するMMDファイルのパス。

    Returns:
        PmdData or PmxData or VmdData or VpdData: 解析されたMMDデータを含むパーサーオブジェクト。

    Raises:
        FileNotFoundError: ファイルが見つからない場合。
        MMDParseException: ファイルの解析に失敗した場合、またはサポートされていないファイル形式の場合。
    """
    logger.info(f"MMDファイルの解析を開始: {file_path}")

    if not os.path.exists(file_path):
        logger.error(f"MMDファイルが見つかりません: {file_path}")
        raise FileNotFoundError(f"MMD file not found: {file_path}")

    # VPDファイルの拡張子チェック
    if file_path.lower().endswith('.vpd'):
        logger.info("VPDファイルとして解析を開始")
        parser = VpdData()
        parser.parse_file(file_path)
        logger.info("VPDファイルの解析が完了しました")
        return parser
    
    # ファイルの最初の数バイトを読み込み、マジックナンバーでファイルタイプを判別する。
    # PMD: b'Pmd'
    # PMX: b'PMX '
    # VMD: b'Vocaloid Motion Data file'
    with open(file_path, "rb") as f:
        magic_bytes = f.read(4)  # Read enough bytes to cover PMD/PMX magic
        f.seek(0)  # Reset file pointer

        logger.debug(f"マジックバイト: {magic_bytes}")

        if magic_bytes.startswith(b"Pmd"):
            logger.info("PMDファイルとして解析を開始")
            parser = PmdData()
            parser.parse_file(file_path)
            logger.info("PMDファイルの解析が完了しました")
            return parser
        elif magic_bytes.startswith(b"PMX"):
            logger.info("PMXファイルとして解析を開始")
            parser = PmxData()
            parser.parse_file(file_path)
            logger.info("PMXファイルの解析が完了しました")
            return parser
        elif f.read(30).startswith(b"Vocaloid Motion Data"):  # VMD magic is longer
            f.seek(0)  # Reset file pointer again
            logger.info("VMDファイルとして解析を開始")
            parser = VmdData()
            parser.parse_file(file_path)
            logger.info("VMDファイルの解析が完了しました")
            return parser
        else:
            logger.error(f"サポートされていないMMDファイル形式: {file_path}")
            raise MMDParseException(f"Unsupported MMD file format: {file_path}")
