"""
mmd_tools.core.log_formatters モジュールのユニットテスト

MayaFormatter / CompactFormatter / ColoredFormatter / TimestampFormatter の
format() 出力・レベル短縮・ANSI カラー挿入・タイムスタンプ付与を検証する。
"""

import logging
import sys
import os
import unittest

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mmd_tools.core.log_formatters import (
    MayaFormatter,
    CompactFormatter,
    ColoredFormatter,
    TimestampFormatter,
)


def _make_record(level: int, message: str) -> logging.LogRecord:
    """テスト用のログレコードを生成するヘルパー関数。

    Args:
        level: ログレベル（例: logging.INFO）
        message: ログメッセージ

    Returns:
        設定済みの LogRecord インスタンス
    """
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname="test_file.py",
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    return record


class TestMayaFormatter(unittest.TestCase):
    """MayaFormatter のユニットテスト"""

    def setUp(self):
        """各テスト前にフォーマッターを初期化する。"""
        self.formatter = MayaFormatter()

    def test_format_returns_string(self):
        """format() が文字列を返すことを確認する。"""
        record = _make_record(logging.INFO, "test message")
        result = self.formatter.format(record)
        self.assertIsInstance(result, str)

    def test_format_contains_message(self):
        """フォーマット結果にメッセージが含まれることを確認する。"""
        record = _make_record(logging.INFO, "hello world")
        result = self.formatter.format(record)
        self.assertIn("hello world", result)

    def test_format_japanese_message(self):
        """日本語メッセージが正常にフォーマットされることを確認する。"""
        record = _make_record(logging.WARNING, "日本語メッセージです")
        result = self.formatter.format(record)
        self.assertIn("日本語メッセージです", result)

    def test_format_contains_level_name(self):
        """フォーマット結果にレベル名が含まれることを確認する。"""
        record = _make_record(logging.ERROR, "error msg")
        result = self.formatter.format(record)
        self.assertIn("ERROR", result)

    def test_format_does_not_corrupt_record(self):
        """format() 呼び出し後に record.levelname が変化しないことを確認する。"""
        record = _make_record(logging.DEBUG, "debug")
        original_levelname = record.levelname
        self.formatter.format(record)
        self.assertEqual(record.levelname, original_levelname)

    def test_custom_format_string(self):
        """カスタムフォーマット文字列が反映されることを確認する。"""
        formatter = MayaFormatter(fmt="%(levelname)s|%(message)s")
        record = _make_record(logging.INFO, "custom")
        result = formatter.format(record)
        self.assertIn("INFO|custom", result)

    def test_format_time_returns_string(self):
        """formatTime() が文字列を返すことを確認する。"""
        record = _make_record(logging.INFO, "time test")
        result = self.formatter.formatTime(record)
        self.assertIsInstance(result, str)


class TestCompactFormatter(unittest.TestCase):
    """CompactFormatter のユニットテスト"""

    def setUp(self):
        """各テスト前にフォーマッターを初期化する。"""
        self.formatter = CompactFormatter()

    def test_warning_abbreviated(self):
        """WARNING が WARN に短縮されることを確認する。"""
        record = _make_record(logging.WARNING, "warn msg")
        result = self.formatter.format(record)
        self.assertIn("WARN", result)
        self.assertNotIn("WARNING", result)

    def test_critical_abbreviated(self):
        """CRITICAL が CRIT に短縮されることを確認する。"""
        record = _make_record(logging.CRITICAL, "crit msg")
        result = self.formatter.format(record)
        self.assertIn("CRIT", result)
        self.assertNotIn("CRITICAL", result)

    def test_info_not_changed(self):
        """INFO レベルは変更されないことを確認する。"""
        record = _make_record(logging.INFO, "info msg")
        result = self.formatter.format(record)
        self.assertIn("INFO", result)

    def test_error_not_changed(self):
        """ERROR レベルは変更されないことを確認する。"""
        record = _make_record(logging.ERROR, "error msg")
        result = self.formatter.format(record)
        self.assertIn("ERROR", result)

    def test_levelname_restored_after_format(self):
        """format() 後に record.levelname が元に戻ることを確認する。"""
        record = _make_record(logging.WARNING, "warn")
        self.formatter.format(record)
        # 元のレベル名が保持されているか
        self.assertEqual(record.levelname, "WARNING")

    def test_format_contains_message(self):
        """フォーマット結果にメッセージが含まれることを確認する。"""
        record = _make_record(logging.INFO, "compact message")
        result = self.formatter.format(record)
        self.assertIn("compact message", result)


class TestColoredFormatter(unittest.TestCase):
    """ColoredFormatter のユニットテスト"""

    def setUp(self):
        """各テスト前にフォーマッターを初期化する。"""
        self.formatter = ColoredFormatter()

    def test_format_returns_string(self):
        """format() が文字列を返すことを確認する。"""
        record = _make_record(logging.INFO, "colored")
        result = self.formatter.format(record)
        self.assertIsInstance(result, str)

    def test_format_contains_ansi_reset(self):
        """フォーマット結果に ANSI リセットコードが含まれることを確認する。"""
        record = _make_record(logging.INFO, "ansi test")
        result = self.formatter.format(record)
        # ANSI リセット: \033[0m
        self.assertIn("\033[0m", result)

    def test_info_uses_green_color(self):
        """INFO レベルにグリーンの ANSI コードが使われることを確認する。"""
        record = _make_record(logging.INFO, "green")
        result = self.formatter.format(record)
        self.assertIn("\033[32m", result)

    def test_warning_uses_yellow_color(self):
        """WARNING レベルにイエローの ANSI コードが使われることを確認する。"""
        record = _make_record(logging.WARNING, "yellow")
        result = self.formatter.format(record)
        self.assertIn("\033[33m", result)

    def test_error_uses_red_color(self):
        """ERROR レベルにレッドの ANSI コードが使われることを確認する。"""
        record = _make_record(logging.ERROR, "red")
        result = self.formatter.format(record)
        self.assertIn("\033[31m", result)

    def test_levelname_restored_after_format(self):
        """format() 後に record.levelname が元の文字列に戻ることを確認する。"""
        record = _make_record(logging.INFO, "restore")
        self.formatter.format(record)
        self.assertEqual(record.levelname, "INFO")

    def test_format_contains_message(self):
        """フォーマット結果にメッセージが含まれることを確認する。"""
        record = _make_record(logging.DEBUG, "colored msg content")
        result = self.formatter.format(record)
        self.assertIn("colored msg content", result)

    def test_colors_dict_has_required_keys(self):
        """COLORS 辞書に主要レベル名とリセットキーが存在することを確認する。"""
        for key in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "RESET"):
            self.assertIn(key, ColoredFormatter.COLORS)


class TestTimestampFormatter(unittest.TestCase):
    """TimestampFormatter のユニットテスト"""

    def setUp(self):
        """各テスト前にフォーマッターを初期化する。"""
        self.formatter = TimestampFormatter()

    def test_format_returns_string(self):
        """format() が文字列を返すことを確認する。"""
        record = _make_record(logging.INFO, "timestamp test")
        result = self.formatter.format(record)
        self.assertIsInstance(result, str)

    def test_format_contains_message(self):
        """フォーマット結果にメッセージが含まれることを確認する。"""
        record = _make_record(logging.INFO, "ts msg")
        result = self.formatter.format(record)
        self.assertIn("ts msg", result)

    def test_format_contains_brackets(self):
        """デフォルトフォーマットに [] ブラケットが含まれることを確認する。"""
        record = _make_record(logging.INFO, "bracket test")
        result = self.formatter.format(record)
        self.assertIn("[", result)
        self.assertIn("]", result)

    def test_format_contains_level_name(self):
        """フォーマット結果にレベル名が含まれることを確認する。"""
        record = _make_record(logging.WARNING, "level check")
        result = self.formatter.format(record)
        self.assertIn("WARNING", result)


if __name__ == "__main__":
    unittest.main()
