#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MayaLogger の堅牢性テスト（pure-python / Maya 非依存）。

ハンドラやフィルタが例外を投げても、ロガーの公開メソッドが
その例外を呼び出し元へ伝播させないことを検証する。

背景: PMX 物理インポート時、剛体作成失敗を ``logger.error`` で記録する
際に、ハンドラ/フィルタ段（OpenMaya 由来）で
``TypeError: Expected a String or Unicode object`` が発生し、
import 全体が巻き込まれて失敗していた。``_safe_log`` でこれを握りつぶす。
"""

import logging
import unittest

from mmd_tools.core.logger import MayaLogger, get_logger


class _RaisingHandler(logging.Handler):
    """emit で必ず例外を投げるハンドラ。"""

    def emit(self, record):
        raise RuntimeError("handler boom")


class _RaisingFilter(logging.Filter):
    """filter で必ず例外を投げるフィルタ（filter 段の失敗を模す）。"""

    def filter(self, record):
        raise TypeError("Expected a String or Unicode object for argument 1")


class TestLoggerSafety(unittest.TestCase):
    """ロガー公開メソッドが例外を伝播させないことを確認する。"""

    def _make_logger(self, name):
        mlogger = get_logger(name)
        # 各テストで素の状態から始めるためハンドラ/フィルタをクリア
        mlogger._logger.handlers.clear()
        mlogger._logger.filters.clear()
        mlogger._logger.propagate = False
        return mlogger

    def test_handler_exception_is_swallowed(self):
        """emit が例外を投げてもロガー呼び出しは成功する。"""
        mlogger = self._make_logger("test_safety_handler")
        mlogger._logger.addHandler(_RaisingHandler())

        # いずれのレベルでも例外が伝播しないこと
        mlogger.debug("デバッグ")
        mlogger.info("情報")
        mlogger.warning("警告")
        mlogger.error("エラー")
        mlogger.critical("重大")

    def test_filter_exception_is_swallowed(self):
        """filter 段で例外が出ても（OpenMaya 由来想定）伝播しない。"""
        mlogger = self._make_logger("test_safety_filter")
        mlogger._logger.addFilter(_RaisingFilter())

        # 実際の不具合と同じく filter 段で TypeError が起きるケース
        mlogger.error("剛体作成エラー '日本語の剛体名': 例外メッセージ")
        mlogger.info("情報メッセージ %s", "引数")

    def test_returns_normally_with_japanese_message(self):
        """日本語・特殊文字メッセージでも安全に処理される。"""
        mlogger = self._make_logger("test_safety_japanese")
        mlogger._logger.addHandler(_RaisingHandler())

        mlogger.error("ボタンを押してください：失敗 ✕ €")


if __name__ == "__main__":
    unittest.main()
