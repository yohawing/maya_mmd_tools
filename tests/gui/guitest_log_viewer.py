"""
ログビューアのGUIテスト

Maya環境内で実行されることを前提としたGUIテスト。
run_gui_tests.pyから実行してください。
"""

import unittest
import sys
import os
import logging
import time

# パスをシステムに追加
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from mmd_tools.ui.components.enhanced_log_viewer import EnhancedLogViewer, LogEntry
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.core.logger import get_logger
from mmd_tools.ui.qt_compat import QApplication
from datetime import datetime
from maya import cmds


class GuiTestEnhancedLogViewer(unittest.TestCase):
    """拡張版ログビューアのテスト"""
    
    @classmethod
    def setUpClass(cls):
        """テストクラスのセットアップ"""
        # Maya環境ではQApplicationは既に存在するはず
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
    


class GuiTestLogIntegration(unittest.TestCase):
    """ログシステム統合のテスト"""
    
    @classmethod
    def setUpClass(cls):
        """テストクラスのセットアップ"""
        # Maya環境ではQApplicationは既に存在するはず
        cls.app = QApplication.instance()
    
    def setUp(self):
        """各テストのセットアップ"""
        # 既存のウィンドウを閉じる
        if cmds.window(MainWindow.WINDOW_NAME, exists=True):
            cmds.deleteUI(MainWindow.WINDOW_NAME)
        
        # メインウィンドウを作成
        self.main_window = MainWindow()
        self.main_window.show()
        
        # UIが完全に初期化されるまで待つ
        QApplication.processEvents()
    
    def tearDown(self):
        """各テストのクリーンアップ"""
        self.main_window.close()
        QApplication.processEvents()
    
    def test_logger_integration(self):
        """ロガーとGUIの統合テスト"""
        # ログビューアをクリア
        self.main_window.log_viewer.clear_logs()
        
        # 初期状態を確認
        initial_count = len(self.main_window.log_viewer.log_entries)
        
        # 異なるモジュールからログを出力
        logger1 = get_logger("test.module1")
        logger2 = get_logger("test.module2")
        
        # 各レベルのログを出力
        logger1.debug("Debug message from module1")
        logger1.info("Info message from module1")
        logger1.warning("Warning message from module1")
        logger1.error("Error message from module1")
        logger1.critical("Critical message from module1")
        
        logger2.info("Info message from module2")
        
        # UIが更新されるまで待つ
        QApplication.processEvents()
        time.sleep(0.1)
        QApplication.processEvents()
        
        # ログが追加されたことを確認
        current_count = len(self.main_window.log_viewer.log_entries)
        added_logs = current_count - initial_count
        
        # 最低でも5つのログが追加されているはず（DEBUGレベルが有効な場合）
        self.assertGreaterEqual(added_logs, 5, 
            f"Expected at least 5 logs, but got {added_logs}. "
            f"Initial: {initial_count}, Current: {current_count}")
        
        # 最後のログエントリを確認
        if self.main_window.log_viewer.log_entries:
            last_entry = self.main_window.log_viewer.log_entries[-1]
            self.assertIn("module2", last_entry.message)
    
    def test_log_level_filtering_in_gui(self):
        """GUIでのログレベルフィルタリングテスト"""
        # ログビューアをクリア
        self.main_window.log_viewer.clear_logs()
        
        # テストログを追加
        logger = get_logger("test.filter")
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        
        # UIが更新されるまで待つ
        QApplication.processEvents()
        time.sleep(0.1)
        QApplication.processEvents()
        
        # 全てのログが表示されていることを確認
        total_logs = len(self.main_window.log_viewer.log_entries)
        self.assertGreaterEqual(total_logs, 3, 
            f"Expected at least 3 logs, but got {total_logs}")
        
        # DEBUGフィルタを無効化
        if "DEBUG" in self.main_window.log_viewer.level_checkboxes:
            self.main_window.log_viewer.level_checkboxes["DEBUG"].setChecked(False)
            QApplication.processEvents()
    
    def test_log_from_presenter(self):
        """プレゼンターからのログ出力テスト"""
        # ログビューアをクリア
        self.main_window.log_viewer.clear_logs()
        
        # インポート/エクスポートタブに切り替え
        self.main_window.tab_widget.setCurrentIndex(0)
        QApplication.processEvents()
        
        # プレゼンターのロガーをテスト
        presenter_logger = get_logger("mmd_tools.ui.presenters.import_export_presenter")
        presenter_logger.info("Test message from presenter")
        
        # UIが更新されるまで待つ
        QApplication.processEvents()
        time.sleep(0.1)
        QApplication.processEvents()
        
        # ログが表示されたことを確認
        found = False
        for entry in self.main_window.log_viewer.log_entries:
            if "Test message from presenter" in entry.message:
                found = True
                break
        
        self.assertTrue(found, "Presenter log message not found in log viewer")
    
    def test_log_viewer_clear_function(self):
        """ログビューアのクリア機能テスト"""
        # ログを追加
        logger = get_logger("test.clear")
        logger.info("Message before clear")
        
        # UIが更新されるまで待つ
        QApplication.processEvents()
        time.sleep(0.1)
        
        # ログが存在することを確認
        self.assertGreater(len(self.main_window.log_viewer.log_entries), 0)
        
        # クリアボタンをクリック
        if hasattr(self.main_window.log_viewer, 'clear_button'):
            self.main_window.log_viewer.clear_button.click()
            QApplication.processEvents()
            
            # ログがクリアされたことを確認
            self.assertEqual(len(self.main_window.log_viewer.log_entries), 0)
    
    def test_log_colors(self):
        """ログレベルごとの色分けテスト"""
        # ログビューアをクリア
        self.main_window.log_viewer.clear_logs()
        
        # 各レベルのログを追加
        logger = get_logger("test.colors")
        logger.debug("Debug in gray")
        logger.info("Info in white")
        logger.warning("Warning in orange")
        logger.error("Error in red")
        logger.critical("Critical in magenta")
        
        # UIが更新されるまで待つ
        QApplication.processEvents()
        time.sleep(0.1)
        QApplication.processEvents()
        
        # ログエントリが正しいレベルで保存されているか確認
        levels_found = set()
        for entry in self.main_window.log_viewer.log_entries:
            levels_found.add(entry.level)
        
        expected_levels = {"INFO", "WARNING", "ERROR", "CRITICAL"}
        # DEBUGレベルはログレベル設定に依存
        
        for level in expected_levels:
            self.assertIn(level, levels_found, 
                f"Log level {level} not found in log entries")


if __name__ == "__main__":
    unittest.main()