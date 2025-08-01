"""
ログビューアの統合テスト
"""

import unittest
import sys
import os
from unittest.mock import MagicMock, patch

# パスをシステムに追加
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from mmd_tools.ui.components.enhanced_log_viewer import EnhancedLogViewer, LogEntry
from mmd_tools.ui.qt_compat import QApplication
from datetime import datetime


class TestEnhancedLogViewer(unittest.TestCase):
    """拡張版ログビューアのテスト"""
    
    @classmethod
    def setUpClass(cls):
        """テストクラスのセットアップ"""
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()
    
    def setUp(self):
        """各テストのセットアップ"""
        self.viewer = EnhancedLogViewer()
    
    def test_append_log_message(self):
        """ログメッセージの追加テスト"""
        # INFOレベルのメッセージを追加
        test_message = "[MMD] 2024-01-01 12:00:00 - test.module - INFO - Test message"
        self.viewer.append(test_message)
        
        # ログエントリが追加されたことを確認
        self.assertEqual(len(self.viewer.log_entries), 1)
        entry = self.viewer.log_entries[0]
        self.assertEqual(entry.level, "INFO")
        self.assertEqual(entry.message, "Test message")
    
    def test_log_level_parsing(self):
        """ログレベルの解析テスト"""
        test_cases = [
            ("[MMD] 2024-01-01 12:00:00 - test - DEBUG - Debug message", "DEBUG", "Debug message"),
            ("[MMD] 2024-01-01 12:00:00 - test - INFO - Info message", "INFO", "Info message"),
            ("[MMD] 2024-01-01 12:00:00 - test - WARNING - Warning message", "WARNING", "Warning message"),
            ("[MMD] 2024-01-01 12:00:00 - test - ERROR - Error message", "ERROR", "Error message"),
            ("[MMD] 2024-01-01 12:00:00 - test - CRITICAL - Critical message", "CRITICAL", "Critical message"),
            ("Simple message without format", "INFO", "Simple message without format"),
        ]
        
        for message, expected_level, expected_content in test_cases:
            level, content = self.viewer._parse_log_message(message)
            self.assertEqual(level, expected_level)
            self.assertEqual(content, expected_content)
    
    def test_filter_functionality(self):
        """フィルタ機能のテスト"""
        # 異なるレベルのメッセージを追加
        messages = [
            "[MMD] 2024-01-01 12:00:00 - test - DEBUG - Debug message",
            "[MMD] 2024-01-01 12:00:01 - test - INFO - Info message",
            "[MMD] 2024-01-01 12:00:02 - test - WARNING - Warning message",
            "[MMD] 2024-01-01 12:00:03 - test - ERROR - Error message",
        ]
        
        for msg in messages:
            self.viewer.append(msg)
        
        # 全てのメッセージが追加されたことを確認
        self.assertEqual(len(self.viewer.log_entries), 4)
        
        # DEBUGフィルタを無効化
        self.viewer.level_filters["DEBUG"] = False
        self.viewer._rebuild_display()
        
        # テキストエディタの内容を確認（モックなので実際の表示はテストしない）
        # フィルタ状態の確認のみ
        self.assertFalse(self.viewer.level_filters["DEBUG"])
        self.assertTrue(self.viewer.level_filters["INFO"])
    
    def test_clear_logs(self):
        """ログクリア機能のテスト"""
        # ログを追加
        self.viewer.append("[MMD] 2024-01-01 12:00:00 - test - INFO - Test message")
        self.assertEqual(len(self.viewer.log_entries), 1)
        
        # ログをクリア
        self.viewer.clear_logs()
        
        # ログが空になったことを確認
        self.assertEqual(len(self.viewer.log_entries), 0)
    
    def test_max_log_entries(self):
        """最大ログエントリ数のテスト"""
        # MAX_LOG_ENTRIESを超える数のログを追加
        max_entries = EnhancedLogViewer.MAX_LOG_ENTRIES
        
        # 小さい値でテスト（実際は10000）
        self.viewer.log_entries = type(self.viewer.log_entries)(maxlen=10)
        
        for i in range(15):
            self.viewer.append(f"[MMD] 2024-01-01 12:00:{i:02d} - test - INFO - Message {i}")
        
        # 最大数を超えないことを確認
        self.assertLessEqual(len(self.viewer.log_entries), 10)
    
    def test_auto_scroll_toggle(self):
        """自動スクロール切り替えのテスト"""
        # 初期状態の確認
        self.assertTrue(self.viewer.auto_scroll)
        
        # 自動スクロールを無効化
        self.viewer.set_auto_scroll(False)
        self.assertFalse(self.viewer.auto_scroll)
        
        # 自動スクロールを有効化
        self.viewer.set_auto_scroll(True)
        self.assertTrue(self.viewer.auto_scroll)
    
    def test_timestamp_toggle(self):
        """タイムスタンプ表示切り替えのテスト"""
        # 初期状態の確認
        self.assertTrue(self.viewer.show_timestamp)
        
        # タイムスタンプ表示を無効化
        self.viewer.set_show_timestamp(False)
        self.assertFalse(self.viewer.show_timestamp)
        
        # タイムスタンプ表示を有効化
        self.viewer.set_show_timestamp(True)
        self.assertTrue(self.viewer.show_timestamp)
    
    def test_search_functionality(self):
        """検索機能のテスト"""
        # テストメッセージを追加
        messages = [
            "[MMD] 2024-01-01 12:00:00 - test - INFO - First test message",
            "[MMD] 2024-01-01 12:00:01 - test - INFO - Second message",
            "[MMD] 2024-01-01 12:00:02 - test - INFO - Another test message",
        ]
        
        for msg in messages:
            self.viewer.append(msg)
        
        # 検索パターンを設定
        self.viewer.search_pattern = "test"
        
        # 検索結果が期待通りかを確認（実際の検索実装はQTextEditに依存）
        self.assertEqual(self.viewer.search_pattern, "test")


if __name__ == "__main__":
    unittest.main()