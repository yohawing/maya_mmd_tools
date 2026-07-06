"""
Maya対応汎用ロガーシステム

Maya環境に最適化されたロガーシステムを提供します。
既存のsettingsシステムと統合し、Maya環境と非Maya環境の両方に対応します。
"""

import logging
import os
from typing import Any, Dict, Optional

from mmd_tools.core import settings_keys as setting_keys
from mmd_tools.core.settings import settings


class MayaScriptEditorHandler(logging.Handler):
    """Maya Script Editor に emit するハンドラ。

    - DEBUG/INFO  → MGlobal.displayInfo
    - WARNING     → MGlobal.displayWarning
    - ERROR/CRITICAL → MGlobal.displayError

    ``maya.api.OpenMaya`` が import できない環境（非Maya）では silent に返るため
    クラッシュしない。emit 失敗時は標準の handleError() に委ねる。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            try:
                import maya.api.OpenMaya as _om  # noqa: PLC0415
                MGlobal = _om.MGlobal
            except ImportError:
                return
            if record.levelno >= logging.ERROR:
                MGlobal.displayError(msg)
            elif record.levelno >= logging.WARNING:
                MGlobal.displayWarning(msg)
            else:
                MGlobal.displayInfo(msg)
        except Exception:
            self.handleError(record)


def install_maya_script_editor_handler() -> None:
    """mmd_tools ロガーに MayaScriptEditorHandler を一度だけ追加する。

    重複呼び出しに対して冪等（既にハンドラが存在する場合は何もしない）。
    ルートロガーのレベルは変更しない。
    """
    mmd_root = logging.getLogger("mmd_tools")
    marker = "_mmd_tools_maya_script_editor_handler"
    mmd_root.handlers = [
        h
        for h in mmd_root.handlers
        if getattr(h, marker, False)
        or not (
            type(h).__name__ == "MayaScriptEditorHandler"
            and type(h).__module__ == __name__
        )
    ]
    if any(getattr(h, marker, False) for h in mmd_root.handlers):
        return
    handler = MayaScriptEditorHandler()
    setattr(handler, marker, True)
    handler.setFormatter(logging.Formatter("[MMD] %(levelname)s: %(message)s"))
    mmd_root.addHandler(handler)



def is_maya_environment() -> bool:
    """Maya環境かどうかを判定"""
    try:
        import maya.cmds  # noqa: F401

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
            "console": {"enabled": False},
            "file": {"enabled": True},
            "maya_script_editor": {"enabled": False},
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

        # 親ロガーへの伝播を確実に有効にする
        self._logger.propagate = True

        # 重複ハンドラーを防ぐため、既存のハンドラーをクリア
        # Note: _loggersはget_logger関数で管理されるため、ここではシンプルにクリア
        if not self._logger.handlers:
            self._logger.handlers.clear()

        # 設定に基づいてハンドラーを追加
        self._setup_handlers()

    def _setup_handlers(self):
        """設定に基づいてハンドラーを設定"""
        if not settings.get(setting_keys.LOGGING_ENABLED, True):
            return

        # Maya Dialogハンドラー（ERROR/CRITICALレベル用）
        # if is_maya_environment():
        #     dialog_handler = MayaDialogHandler(level=logging.ERROR)
        #     dialog_handler.setFormatter(standard_formatter)
        #     self._logger.addHandler(dialog_handler)

        # ログレベルを設定から更新
        level_str = settings.get(setting_keys.LOGGING_LEVEL, "INFO")
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
            print("警告: ログファイルの作成に失敗しました。ファイルログは無効です。")
            return None

    def _safe_log(self, level: int, message: str, *args, **kwargs) -> None:
        """
        ログ出力を安全に行う内部ヘルパー。

        ハンドラ/フィルタ（Qt ブリッジや Maya API 経由のもの）が
        日本語・特殊文字メッセージや非文字列引数で例外を投げても、
        その例外を呼び出し元に伝播させない。

        logging 標準の handleError は emit() の失敗しか吸収せず、
        Filterer.filter() 段で発生する例外（OpenMaya 由来の
        ``TypeError: Expected a String or Unicode object`` など）は
        素通りしてしまうため、ここで最終的に握りつぶす。

        Args:
            level: logging のレベル定数（logging.ERROR 等）
            message: ログメッセージ
        """
        if not settings.get(setting_keys.LOGGING_ENABLED, True):
            return
        try:
            self._logger.log(level, message, *args, **kwargs)
        except Exception:
            # ロギング経路の失敗でアプリ処理を止めないための最終フォールバック。
            try:
                print(f"[MMD] {message}")
            except Exception:
                pass

    def debug(self, message: str, *args, **kwargs):
        """デバッグメッセージを出力"""
        self._safe_log(logging.DEBUG, message, *args, **kwargs)

    def info(self, message: str, *args, **kwargs):
        """情報メッセージを出力"""
        self._safe_log(logging.INFO, message, *args, **kwargs)

    def warning(self, message: str, *args, **kwargs):
        """警告メッセージを出力"""
        self._safe_log(logging.WARNING, message, *args, **kwargs)

    def error(self, message: str, *args, **kwargs):
        """エラーメッセージを出力"""
        self._safe_log(logging.ERROR, message, *args, **kwargs)

    def critical(self, message: str, *args, **kwargs):
        """重大なエラーメッセージを出力"""
        self._safe_log(logging.CRITICAL, message, *args, **kwargs)

    def set_level(self, level: int):
        """ログレベルを設定"""
        self._logger.setLevel(level)

    def add_handler(self, handler: logging.Handler):
        """ハンドラーを追加"""
        self._logger.addHandler(handler)

    def remove_handler(self, handler: logging.Handler):
        """ハンドラーを削除"""
        self._logger.removeHandler(handler)


def safe_log_error(logger: MayaLogger, prefix: str, item: Any, error: Exception) -> None:
    """per-item エラーを安全にログ出力する。

    ログ出力経路（カスタムハンドラ/フィルタや Maya API 由来）が
    例外を投げても呼び出し元に伝播させず、最終的に必ず握りつぶす。

    Args:
        logger: ログ出力先
        prefix: ログメッセージの接頭辞
        item: 失敗したオブジェクト（name 属性を持つ想定、str の場合はそのまま表示）
        error: 発生した例外
    """
    try:
        name = getattr(item, "name", "?")
    except Exception:
        name = "?"
    try:
        logger.error(f"{prefix} '{name}': {error}")
    except Exception:
        try:
            print(f"[MMD] {prefix} '{name}': {error}")
        except Exception:
            pass


def safe_log_warning(logger: MayaLogger, prefix: str, item: Any, message: str) -> None:
    """per-item 警告を安全にログ出力する。

    ログ出力経路が例外を投げても呼び出し元に伝播させず、最終的に必ず握りつぶす。

    Args:
        logger: ログ出力先
        prefix: ログメッセージの接頭辞
        item: 対象オブジェクト（name 属性を持つ想定、str の場合はそのまま表示）
        message: 警告メッセージ
    """
    try:
        name = getattr(item, "name", "?")
    except Exception:
        name = "?"
    try:
        logger.warning(f"{prefix} '{name}': {message}")
    except Exception:
        try:
            print(f"[MMD] {prefix} '{name}': {message}")
        except Exception:
            pass


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
