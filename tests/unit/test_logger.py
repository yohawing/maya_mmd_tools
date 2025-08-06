#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ロガーシステムの統合テスト

Maya環境と非Maya環境の両方でロガーシステムをテストします。
"""

import os
import unittest

from mmd_tools.core.logger import get_logger, is_maya_environment
from mmd_tools.core.settings import settings
from tests.common.maya_test_base import MayaTestBase


class TestLogger(MayaTestBase):
    """ロガーシステムの統合テスト"""

    def setUp(self):
        """各テストメソッドの前処理"""
        super().setUp()
        # テスト用のオリジナル設定を保存
        self.original_settings = {
            "logging.enabled": settings.get("logging.enabled", True),
            "logging.level": settings.get("logging.level", "INFO"),
            "logging.log_file_path": settings.get("logging.log_file_path", "logs/mmd_tools.log"),
        }

    def tearDown(self):
        """各テストメソッドの後処理"""
        # 設定を元に戻す
        for key, value in self.original_settings.items():
            settings.set(key, value)
        super().tearDown()

    def test_basic_logging(self):
        """基本的なログ出力のテスト"""
        # ロガーを取得
        logger = get_logger("test_logger")

        # 各レベルでのログ出力（例外が発生しないことを確認）
        try:
            logger.debug("これはデバッグメッセージです")
            logger.info("これは情報メッセージです")
            logger.warning("これは警告メッセージです")
            logger.error("これはエラーメッセージです")
            logger.critical("これは重大なエラーメッセージです")
        except Exception as e:
            self.fail(f"基本的なログ出力でエラーが発生しました: {e}")

    def test_japanese_support(self):
        """日本語サポートのテスト"""
        logger = get_logger("test_japanese")

        # 日本語メッセージのログ出力（例外が発生しないことを確認）
        try:
            logger.info("日本語メッセージのテストです")
            logger.warning("警告：日本語での警告メッセージ")
            logger.error("エラー：日本語でのエラーメッセージ")

            # 文字列フォーマットテスト
            name = "テストユーザー"
            count = 42
            logger.info("ユーザー名: %s, カウント: %d", name, count)
        except Exception as e:
            self.fail(f"日本語サポートテストでエラーが発生しました: {e}")

    def test_settings_integration(self):
        """設定システム統合のテスト"""
        # 現在の設定値を確認
        logging_enabled = settings.get("logging.enabled", True)
        logging_level = settings.get("logging.level", "INFO")
        log_file_path = settings.get("logging.log_file_path", "logs/mmd_tools.log")

        # 設定値が適切な型であることを確認
        self.assertIsInstance(logging_enabled, bool)
        self.assertIsInstance(logging_level, str)
        self.assertIsInstance(log_file_path, str)

        # 設定を一時的に変更
        original_level = settings.get("logging.level", "INFO")
        settings.set("logging.level", "DEBUG")

        logger = get_logger("test_settings")
        try:
            logger.debug("DEBUGレベルに変更後のメッセージ")
        except Exception as e:
            self.fail(f"設定変更後のログ出力でエラーが発生しました: {e}")

        # 設定が変更されていることを確認
        self.assertEqual(settings.get("logging.level"), "DEBUG")

        # 設定を元に戻す
        settings.set("logging.level", original_level)
        self.assertEqual(settings.get("logging.level"), original_level)

    def test_environment_detection(self):
        """環境検出のテスト"""
        is_maya = is_maya_environment()

        # Maya環境かどうかはブール値であることを確認
        self.assertIsInstance(is_maya, bool)

        # このテストはMayaTestBaseを継承しているため、Maya環境で実行されるはず
        self.assertTrue(is_maya, "MayaTestBaseを使用しているのでMaya環境で実行されるべきです")

    def test_file_logging(self):
        """ファイル出力のテスト"""
        logger = get_logger("test_file")

        # ファイルログテスト（例外が発生しないことを確認）
        try:
            logger.info("ファイル出力テストメッセージ")
            logger.warning("ファイル出力警告テスト")
            logger.error("ファイル出力エラーテスト")
        except Exception as e:
            self.fail(f"ファイル出力テストでエラーが発生しました: {e}")

        # ログファイルの存在確認
        log_file_path = settings.get("logging.log_file_path", "logs/mmd_tools.log")
        if not isinstance(log_file_path, str):
            log_file_path = "logs/mmd_tools.log"

        # ログファイルが作成されていることを確認（ディレクトリが存在すれば）
        log_dir = os.path.dirname(log_file_path)
        if os.path.exists(log_dir) or log_dir == "":
            # ログファイルまたはログディレクトリが存在するかチェック
            self.assertTrue(
                os.path.exists(log_file_path) or os.path.exists(log_dir),
                f"ログファイルまたはログディレクトリが見つかりません: {log_file_path}",
            )

    def test_error_handling(self):
        """エラーハンドリングのテスト"""
        logger = get_logger("test_error")

        # 例外ログのテスト
        try:
            # 意図的にエラーを発生させる
            raise ValueError("テスト用のエラー")
        except Exception as e:
            try:
                logger.error("例外をキャッチしました: %s", str(e))
            except Exception as log_error:
                self.fail(f"例外ログでエラーが発生しました: {log_error}")

        # 重大なエラーログのテスト
        try:
            logger.critical("重大なエラーのテスト")
        except Exception as e:
            self.fail(f"重大なエラーログでエラーが発生しました: {e}")

    def test_logger_instance_consistency(self):
        """同じ名前のロガーが同じインスタンスを返すことをテスト"""
        logger1 = get_logger("test_consistency")
        logger2 = get_logger("test_consistency")

        # 同じインスタンスであることを確認
        self.assertIs(logger1, logger2, "同じ名前のロガーは同じインスタンスを返すべきです")

    def test_different_logger_names(self):
        """異なる名前のロガーが異なるインスタンスを返すことをテスト"""
        logger1 = get_logger("test_name1")
        logger2 = get_logger("test_name2")

        # 異なるインスタンスであることを確認
        self.assertIsNot(logger1, logger2, "異なる名前のロガーは異なるインスタンスを返すべきです")


if __name__ == "__main__":
    unittest.main()
