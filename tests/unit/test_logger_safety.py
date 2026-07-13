#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MayaLogger / MayaScriptEditorHandler の堅牢性テスト（pure-python / Maya 非依存）。

ハンドラやフィルタが例外を投げても、ロガーの公開メソッドが
その例外を呼び出し元へ伝播させないことを検証する。

背景: PMX 物理インポート時、剛体作成失敗を ``logger.error`` で記録する
際に、ハンドラ/フィルタ段（OpenMaya 由来）で
``TypeError: Expected a String or Unicode object`` が発生し、
import 全体が巻き込まれて失敗していた。``_safe_log`` でこれを握りつぶす。
"""

import logging
import sys
import unittest
from types import ModuleType
from unittest.mock import MagicMock

from mmd_tools.core.logger import (
    MayaScriptEditorHandler,
    get_logger,
    install_maya_script_editor_handler,
    set_all_logger_levels,
)


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


class TestMayaScriptEditorHandler(unittest.TestCase):
    """MayaScriptEditorHandler のルーティングと安全性を確認する。"""

    def setUp(self):
        # ``import maya.api.OpenMaya as _om`` は親パッケージ ``maya`` と
        # ``maya.api`` が sys.modules に存在しないと ImportError になるため登録する。
        self._prev_maya = sys.modules.get("maya")
        self._prev_api = sys.modules.get("maya.api")
        self._prev_om = sys.modules.get("maya.api.OpenMaya")

        self.mock_mglobal = MagicMock(name="MGlobal")
        om_stub = ModuleType("maya.api.OpenMaya")
        om_stub.MGlobal = self.mock_mglobal
        api_stub = ModuleType("maya.api")
        api_stub.OpenMaya = om_stub
        maya_stub = ModuleType("maya")
        maya_stub.api = api_stub

        sys.modules["maya"] = maya_stub
        sys.modules["maya.api"] = api_stub
        sys.modules["maya.api.OpenMaya"] = om_stub

        self.handler = MayaScriptEditorHandler()
        self.handler.setFormatter(logging.Formatter("%(message)s"))

    def tearDown(self):
        if self._prev_maya is None:
            sys.modules.pop("maya", None)
        else:
            sys.modules["maya"] = self._prev_maya
        if self._prev_api is None:
            sys.modules.pop("maya.api", None)
        else:
            sys.modules["maya.api"] = self._prev_api
        if self._prev_om is None:
            sys.modules.pop("maya.api.OpenMaya", None)
        else:
            sys.modules["maya.api.OpenMaya"] = self._prev_om

    def _rec(self, level, msg="test"):
        return logging.LogRecord("mmd_tools.test", level, "", 0, msg, (), None)

    def test_debug_routes_to_displayInfo(self):
        self.handler.emit(self._rec(logging.DEBUG, "dbg"))
        self.mock_mglobal.displayInfo.assert_called_once_with("dbg")
        self.mock_mglobal.displayWarning.assert_not_called()
        self.mock_mglobal.displayError.assert_not_called()

    def test_info_routes_to_displayInfo(self):
        self.handler.emit(self._rec(logging.INFO, "inf"))
        self.mock_mglobal.displayInfo.assert_called_once_with("inf")

    def test_warning_routes_to_displayWarning(self):
        self.handler.emit(self._rec(logging.WARNING, "wrn"))
        self.mock_mglobal.displayWarning.assert_called_once_with("wrn")
        self.mock_mglobal.displayInfo.assert_not_called()

    def test_error_routes_to_displayError(self):
        self.handler.emit(self._rec(logging.ERROR, "err"))
        self.mock_mglobal.displayError.assert_called_once_with("err")

    def test_critical_routes_to_displayError(self):
        self.handler.emit(self._rec(logging.CRITICAL, "crit"))
        self.mock_mglobal.displayError.assert_called_once_with("crit")

    def test_safe_when_maya_absent(self):
        """maya.api.OpenMaya が import できない場合は displayXxx を呼ばず silent に返る。"""
        sys.modules.pop("maya.api.OpenMaya", None)
        sys.modules.pop("maya.api", None)
        sys.modules.pop("maya", None)
        # Should not raise
        self.handler.emit(self._rec(logging.ERROR, "no maya"))
        self.mock_mglobal.displayError.assert_not_called()


class TestInstallMayaScriptEditorHandler(unittest.TestCase):
    """install_maya_script_editor_handler の冪等性を確認する。"""

    def _cleanup(self):
        mmd_root = logging.getLogger("mmd_tools")
        mmd_root.handlers = [
            h for h in mmd_root.handlers if not isinstance(h, MayaScriptEditorHandler)
        ]

    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def test_installs_exactly_once_on_repeated_calls(self):
        install_maya_script_editor_handler()
        install_maya_script_editor_handler()
        install_maya_script_editor_handler()

        mmd_root = logging.getLogger("mmd_tools")
        count = sum(1 for h in mmd_root.handlers if isinstance(h, MayaScriptEditorHandler))
        self.assertEqual(count, 1)

    def test_handler_attached_to_mmd_tools_logger(self):
        install_maya_script_editor_handler()
        mmd_root = logging.getLogger("mmd_tools")
        self.assertTrue(
            any(isinstance(h, MayaScriptEditorHandler) for h in mmd_root.handlers)
        )


class TestSetAllLoggerLevels(unittest.TestCase):
    """set_all_logger_levels がキャッシュ済みロガーすべてに適用されることを確認する。"""

    def test_updates_every_existing_cached_logger(self):
        """既にキャッシュされた複数ロガーのレベルが両方とも変わる。"""
        from mmd_tools.core import logger as logger_mod

        a = get_logger("test_set_all_levels_a")
        b = get_logger("test_set_all_levels_b")
        prev_levels = {
            name: ml._logger.level for name, ml in list(logger_mod._loggers.items())
        }
        try:
            a.set_level(logging.WARNING)
            b.set_level(logging.ERROR)
            self.assertEqual(a._logger.level, logging.WARNING)
            self.assertEqual(b._logger.level, logging.ERROR)

            set_all_logger_levels(logging.DEBUG)

            self.assertEqual(a._logger.level, logging.DEBUG)
            self.assertEqual(b._logger.level, logging.DEBUG)
        finally:
            for name, level in prev_levels.items():
                cached = logger_mod._loggers.get(name)
                if cached is not None:
                    cached.set_level(level)



if __name__ == "__main__":
    unittest.main()
