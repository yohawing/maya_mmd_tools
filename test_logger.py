#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ロガーシステムのテストスクリプト

Maya環境と非Maya環境の両方でロガーシステムをテストします。
"""

import sys
import os

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mmd_tools.core.logger import get_logger, is_maya_environment
from mmd_tools.settings import settings


def test_basic_logging():
    """基本的なログ出力のテスト"""
    print("=== 基本的なログ出力テスト ===")

    # ロガーを取得
    logger = get_logger("test_logger")

    # 各レベルでのログ出力
    logger.debug("これはデバッグメッセージです")
    logger.info("これは情報メッセージです")
    logger.warning("これは警告メッセージです")
    logger.error("これはエラーメッセージです")
    logger.critical("これは重大なエラーメッセージです")

    print("基本的なログ出力テスト完了\n")


def test_japanese_support():
    """日本語サポートのテスト"""
    print("=== 日本語サポートテスト ===")

    logger = get_logger("test_japanese")

    # 日本語メッセージ
    logger.info("日本語メッセージのテストです")
    logger.warning("警告：日本語での警告メッセージ")
    logger.error("エラー：日本語でのエラーメッセージ")

    # 文字列フォーマットテスト
    name = "テストユーザー"
    count = 42
    logger.info("ユーザー名: %s, カウント: %d", name, count)

    print("日本語サポートテスト完了\n")


def test_settings_integration():
    """設定システム統合のテスト"""
    print("=== 設定システム統合テスト ===")

    # 現在の設定を表示
    print(f"ログ有効: {settings.get('logging.enabled', True)}")
    print(f"ログレベル: {settings.get('logging.level', 'INFO')}")
    print(
        f"ログファイルパス: {settings.get('logging.log_file_path', 'logs/mmd_tools.log')}"
    )

    # 設定を一時的に変更
    original_level = settings.get("logging.level", "INFO")
    settings.set("logging.level", "DEBUG")

    logger = get_logger("test_settings")
    logger.debug("DEBUGレベルに変更後のメッセージ")

    # 設定を元に戻す
    settings.set("logging.level", original_level)

    print("設定システム統合テスト完了\n")


def test_environment_detection():
    """環境検出のテスト"""
    print("=== 環境検出テスト ===")

    is_maya = is_maya_environment()
    print(f"Maya環境: {is_maya}")

    if is_maya:
        print("Maya環境が検出されました")
    else:
        print("非Maya環境で動作しています")

    print("環境検出テスト完了\n")


def test_file_logging():
    """ファイル出力のテスト"""
    print("=== ファイル出力テスト ===")

    logger = get_logger("test_file")

    # ファイルログテスト
    logger.info("ファイル出力テストメッセージ")
    logger.warning("ファイル出力警告テスト")
    logger.error("ファイル出力エラーテスト")

    # ログファイルの存在確認
    log_file_path = settings.get("logging.log_file_path", "logs/mmd_tools.log")
    if not isinstance(log_file_path, str):
        log_file_path = "logs/mmd_tools.log"

    if os.path.exists(log_file_path):
        print(f"ログファイルが作成されました: {log_file_path}")

        # ファイルサイズを確認
        file_size = os.path.getsize(log_file_path)
        print(f"ログファイルサイズ: {file_size} bytes")
    else:
        print("ログファイルが見つかりません")

    print("ファイル出力テスト完了\n")


def test_error_handling():
    """エラーハンドリングのテスト"""
    print("=== エラーハンドリングテスト ===")

    logger = get_logger("test_error")

    try:
        # 意図的にエラーを発生させる
        raise ValueError("テスト用のエラー")
    except Exception as e:
        logger.error("例外をキャッチしました: %s", str(e))

    # 重大なエラー
    logger.critical("重大なエラーのテスト")

    print("エラーハンドリングテスト完了\n")


def main():
    """メイン実行関数"""
    print("Maya MMD Tools ロガーシステムテスト")
    print("=" * 50)

    # 環境情報
    test_environment_detection()

    # 基本テスト
    test_basic_logging()

    # 日本語サポート
    test_japanese_support()

    # 設定システム統合
    test_settings_integration()

    # ファイル出力
    test_file_logging()

    # エラーハンドリング
    test_error_handling()

    print("=" * 50)
    print("全てのテストが完了しました")


if __name__ == "__main__":
    main()
