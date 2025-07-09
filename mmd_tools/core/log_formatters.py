"""
Maya対応ログフォーマッター

Maya環境に特化したログフォーマッターを提供します。
"""

import logging
import time
from datetime import datetime


class MayaFormatter(logging.Formatter):
    """Maya環境用カスタムフォーマッター"""

    def __init__(self, fmt=None, datefmt=None, style="%", validate=True):
        """
        Maya環境用フォーマッターを初期化

        Args:
            fmt: フォーマット文字列
            datefmt: 日付フォーマット文字列
            style: フォーマットスタイル
            validate: フォーマットの検証を行うか
        """
        if fmt is None:
            fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        if datefmt is None:
            datefmt = "%Y-%m-%d %H:%M:%S"

        super().__init__(fmt, datefmt, style, validate)

    def format(self, record):
        """ログレコードをフォーマット"""
        # Maya環境用の特別な処理
        original_msg = record.getMessage()

        # 日本語文字列の処理
        if isinstance(original_msg, str):
            # UTF-8エンコーディングを確実にする
            try:
                # 既にUTF-8文字列の場合はそのまま使用
                formatted_msg = original_msg
            except UnicodeDecodeError:
                # デコードエラーがある場合は安全に処理
                formatted_msg = original_msg.encode("utf-8", errors="replace").decode(
                    "utf-8"
                )
        else:
            formatted_msg = str(original_msg)

        # レコードのメッセージを更新
        record.msg = formatted_msg
        record.args = ()

        # 標準のフォーマット処理を実行
        return super().format(record)

    def formatTime(self, record, datefmt=None):
        """時間フォーマット"""
        if datefmt is None:
            datefmt = self.datefmt

        ct = self.converter(record.created)
        if datefmt:
            s = time.strftime(datefmt, ct)
        else:
            s = time.strftime(self.datefmt, ct)

        return s


class CompactFormatter(logging.Formatter):
    """コンパクト表示用フォーマッター"""

    def __init__(self, fmt=None, datefmt=None, style="%", validate=True):
        """
        コンパクトフォーマッターを初期化

        Args:
            fmt: フォーマット文字列
            datefmt: 日付フォーマット文字列
            style: フォーマットスタイル
            validate: フォーマットの検証を行うか
        """
        if fmt is None:
            fmt = "%(levelname)s: %(message)s"

        super().__init__(fmt, datefmt, style, validate)

    def format(self, record):
        """ログレコードをコンパクトにフォーマット"""
        # レベル名を短縮
        level_name = record.levelname
        if level_name == "WARNING":
            level_name = "WARN"
        elif level_name == "CRITICAL":
            level_name = "CRIT"

        # 短縮されたレベル名を設定
        original_levelname = record.levelname
        record.levelname = level_name

        # 標準のフォーマット処理を実行
        result = super().format(record)

        # 元のレベル名を復元
        record.levelname = original_levelname

        return result


class ColoredFormatter(logging.Formatter):
    """色付きフォーマッター（コンソール用）"""

    # ANSIカラーコード
    COLORS = {
        "DEBUG": "\033[36m",  # シアン
        "INFO": "\033[32m",  # 緑
        "WARNING": "\033[33m",  # 黄
        "ERROR": "\033[31m",  # 赤
        "CRITICAL": "\033[35m",  # マゼンタ
        "RESET": "\033[0m",  # リセット
    }

    def __init__(self, fmt=None, datefmt=None, style="%", validate=True):
        """
        色付きフォーマッターを初期化

        Args:
            fmt: フォーマット文字列
            datefmt: 日付フォーマット文字列
            style: フォーマットスタイル
            validate: フォーマットの検証を行うか
        """
        if fmt is None:
            fmt = "%(levelname)s: %(message)s"

        super().__init__(fmt, datefmt, style, validate)

    def format(self, record):
        """ログレコードを色付きでフォーマット"""
        # レベルに応じた色を適用
        level_name = record.levelname
        color = self.COLORS.get(level_name, self.COLORS["RESET"])

        # 色付きレベル名を設定
        original_levelname = record.levelname
        record.levelname = f"{color}{level_name}{self.COLORS['RESET']}"

        # 標準のフォーマット処理を実行
        result = super().format(record)

        # 元のレベル名を復元
        record.levelname = original_levelname

        return result


class TimestampFormatter(logging.Formatter):
    """タイムスタンプ付きフォーマッター"""

    def __init__(self, fmt=None, datefmt=None, style="%", validate=True):
        """
        タイムスタンプ付きフォーマッターを初期化

        Args:
            fmt: フォーマット文字列
            datefmt: 日付フォーマット文字列
            style: フォーマットスタイル
            validate: フォーマットの検証を行うか
        """
        if fmt is None:
            fmt = "[%(asctime)s] %(levelname)s: %(message)s"
        if datefmt is None:
            datefmt = "%H:%M:%S"

        super().__init__(fmt, datefmt, style, validate)

    def format(self, record):
        """ログレコードにタイムスタンプを付けてフォーマット"""
        return super().format(record)
