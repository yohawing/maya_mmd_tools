"""
Maya対応汎用ロガーシステム

Maya環境に最適化されたロガーシステムを提供します。
既存のsettingsシステムと統合し、Maya環境と非Maya環境の両方に対応します。
"""

import logging
import os
import sys
from typing import Optional, Dict, Any

from .log_handlers import (
    MayaScriptEditorHandler,
    MayaOutputWindowHandler,
    UTF8FileHandler,
    MayaDialogHandler,
)
from .log_formatters import MayaFormatter, CompactFormatter
from ..settings import settings


def is_maya_environment() -> bool:
    """Maya環境かどうかを判定"""
    try:
        import maya.cmds

        return True
    except ImportError:
        return False


class MayaLogger:
    """Maya環境対応の汎用ログクラス"""

    # ハードコード設定項目
    LOGGING_CONFIG = {
        "file": {
            "max_size": 10485760,  # 10MB
            "backup_count": 5,
        },
        "formatters": {
            "standard": "[MMD] %(asctime)s - %(name)s - %(levelname)s - %(message)s",
            "compact": "[MMD] %(levelname)s: %(message)s",
        },
        "handlers": {
            "console": {"enabled": True},
            "file": {"enabled": True},
            "maya_script_editor": {"enabled": True},
        },
    }

    def __init__(self, name: str, level: int = logging.INFO):
        """
        ロガーを初期化します

        Args:
            name: ロガー名
            level: ログレベル
        """
        self.name = name
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)

        # 重複ハンドラーを防ぐため、既存のハンドラーをクリア
        self._logger.handlers.clear()

        # 設定に基づいてハンドラーを追加
        self._setup_handlers()

    def _setup_handlers(self):
        """設定に基づいてハンドラーを設定"""
        if not settings.get("logging.enabled", True):
            return

        # フォーマッターを準備
        standard_formatter = MayaFormatter(
            self.LOGGING_CONFIG["formatters"]["standard"]
        )
        compact_formatter = CompactFormatter(
            self.LOGGING_CONFIG["formatters"]["compact"]
        )

        # コンソールハンドラー
        # if self.LOGGING_CONFIG["handlers"]["console"]["enabled"]:
        #     if is_maya_environment():
        #         console_handler = MayaOutputWindowHandler()
        #     else:
        #         console_handler = logging.StreamHandler(sys.stdout)
        #     console_handler.setFormatter(compact_formatter)
        #     self._logger.addHandler(console_handler)

        # ファイルハンドラー
        if self.LOGGING_CONFIG["handlers"]["file"]["enabled"]:
            log_file_path = settings.get("logging.log_file_path", "logs/mmd_tools.log")

            # ファイルパスが文字列であることを確認
            if not isinstance(log_file_path, str):
                log_file_path = "logs/mmd_tools.log"

            # 書き込み可能なログファイルパスを取得
            log_file_path = self._get_writable_log_path(log_file_path)

            if log_file_path:
                try:
                    file_handler = UTF8FileHandler(
                        log_file_path,
                        max_bytes=self.LOGGING_CONFIG["file"]["max_size"],
                        backup_count=self.LOGGING_CONFIG["file"]["backup_count"],
                    )
                    file_handler.setFormatter(standard_formatter)
                    self._logger.addHandler(file_handler)
                except (OSError, IOError) as e:
                    # ファイルハンドラーの作成に失敗した場合は警告を出力
                    print(f"ログファイルの作成に失敗しました: {e}")

        # Maya Script Editorハンドラー
        # if (
        #     is_maya_environment()
        #     and self.LOGGING_CONFIG["handlers"]["maya_script_editor"]["enabled"]
        # ):
        #     maya_handler = MayaScriptEditorHandler()
        #     maya_handler.setFormatter(compact_formatter)
        #     self._logger.addHandler(maya_handler)

        # Maya Dialogハンドラー（ERROR/CRITICALレベル用）
        if is_maya_environment():
            dialog_handler = MayaDialogHandler(level=logging.ERROR)
            dialog_handler.setFormatter(standard_formatter)
            self._logger.addHandler(dialog_handler)

        # ログレベルを設定から更新
        level_str = settings.get("logging.level", "INFO")
        if isinstance(level_str, str):
            level = getattr(logging, level_str.upper(), logging.INFO)
        else:
            level = logging.INFO
        self._logger.setLevel(level)

    def _get_writable_log_path(self, preferred_path: str) -> Optional[str]:
        """
        書き込み可能なログファイルパスを取得します

        Args:
            preferred_path: 希望するログファイルパス

        Returns:
            書き込み可能なログファイルパス、失敗した場合はNone
        """
        import tempfile

        # まず希望するパスでディレクトリ作成を試行
        log_dir = os.path.dirname(preferred_path)
        if log_dir:
            try:
                os.makedirs(log_dir, exist_ok=True)
                # ディレクトリが作成できた場合、ファイルの書き込みテスト
                test_file = preferred_path + ".test"
                try:
                    with open(test_file, "w") as f:
                        f.write("test")
                    os.remove(test_file)
                    return preferred_path
                except (OSError, IOError):
                    pass
            except (OSError, IOError, PermissionError):
                pass

        # 希望するパスでディレクトリ作成に失敗した場合、一時ディレクトリを使用
        try:
            temp_dir = tempfile.gettempdir()
            temp_log_dir = os.path.join(temp_dir, "maya_mmd_tools")
            os.makedirs(temp_log_dir, exist_ok=True)

            log_filename = os.path.basename(preferred_path)
            if not log_filename:
                log_filename = "mmd_tools.log"

            fallback_path = os.path.join(temp_log_dir, log_filename)

            # 一時ディレクトリでの書き込みテスト
            test_file = fallback_path + ".test"
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)

            print(f"警告: ログファイルを一時ディレクトリに作成します: {fallback_path}")
            return fallback_path

        except (OSError, IOError, PermissionError):
            # 一時ディレクトリでも失敗した場合、ファイルハンドラーを無効化
            print(f"警告: ログファイルの作成に失敗しました。ファイルログは無効です。")
            return None

    def debug(self, message: str, *args, **kwargs):
        """デバッグメッセージを出力"""
        if settings.get("logging.enabled", True):
            self._logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args, **kwargs):
        """情報メッセージを出力"""
        if settings.get("logging.enabled", True):
            self._logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args, **kwargs):
        """警告メッセージを出力"""
        if settings.get("logging.enabled", True):
            self._logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args, **kwargs):
        """エラーメッセージを出力"""
        if settings.get("logging.enabled", True):
            self._logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args, **kwargs):
        """重大なエラーメッセージを出力"""
        if settings.get("logging.enabled", True):
            self._logger.critical(message, *args, **kwargs)

    def set_level(self, level: int):
        """ログレベルを設定"""
        self._logger.setLevel(level)

    def add_handler(self, handler: logging.Handler):
        """ハンドラーを追加"""
        self._logger.addHandler(handler)

    def remove_handler(self, handler: logging.Handler):
        """ハンドラーを削除"""
        self._logger.removeHandler(handler)


# ロガーインスタンスのキャッシュ
_loggers: Dict[str, MayaLogger] = {}


def get_logger(name: str) -> MayaLogger:
    """
    指定された名前のロガーを取得します

    Args:
        name: ロガー名

    Returns:
        MayaLoggerインスタンス
    """
    if name not in _loggers:
        _loggers[name] = MayaLogger(name)
    return _loggers[name]


def setup_logger(logger_name: str) -> MayaLogger:
    """
    既存のsetup_logger関数の互換性維持のための関数

    Args:
        logger_name: ロガー名

    Returns:
        MayaLoggerインスタンス
    """
    return get_logger(logger_name)


# ロガーの例外クラス
class LoggerException(Exception):
    """ロガー固有の例外クラス"""

    pass


class HandlerException(LoggerException):
    """ハンドラー関連の例外"""

    pass
