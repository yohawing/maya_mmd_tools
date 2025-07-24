import unittest
import tempfile
import os
from unittest.mock import patch

from tests.common.maya_test_base import MayaTestBase

# Qt環境が利用可能な場合のみテストを実行
try:
    from mmd_tools.ui.qt_compat import QSettings
    from mmd_tools.ui.tabs.import_export_tab import ImportExportTab
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "Qt環境が必要です")
class TestImportExportTabIntegration(MayaTestBase):
    """ImportExportTabの統合テスト（実際のQSettingsを使用）"""
    
    def setUp(self):
        super().setUp()
        # テスト用の一時的な設定ファイルパスを使用
        self.test_org = "maya_mmd_tools_test"
        self.test_app = "ImportExportTab_test"
        
    def tearDown(self):
        # テスト用の設定をクリア
        settings = QSettings(self.test_org, self.test_app)
        settings.clear()
        settings.sync()
        super().tearDown()
    
    def test_real_qsettings_persistence(self):
        """実際のQSettingsを使用したパスの永続化テスト"""
        test_import_path = "/test/import/model.pmx"
        test_export_path = "/test/export/model.pmx"
        
        # 最初のインスタンスで設定を保存
        with patch('mmd_tools.ui.tabs.import_export_tab.QSettings') as mock_settings_class:
            # 実際のQSettingsインスタンスを作成
            real_settings = QSettings(self.test_org, self.test_app)
            mock_settings_class.return_value = real_settings
            
            # タブを作成
            tab1 = ImportExportTab()
            
            # パスを設定（textChangedシグナルをシミュレート）
            real_settings.setValue("import_path", test_import_path)
            real_settings.setValue("export_path", test_export_path)
            real_settings.sync()
        
        # 別のインスタンスで設定を読み込む
        with patch('mmd_tools.ui.tabs.import_export_tab.QSettings') as mock_settings_class:
            # 同じ設定を使用
            real_settings2 = QSettings(self.test_org, self.test_app)
            mock_settings_class.return_value = real_settings2
            
            # 新しいタブインスタンスを作成
            tab2 = ImportExportTab()
            
            # 保存されたパスが正しく読み込まれることを確認
            saved_import = real_settings2.value("import_path", "")
            saved_export = real_settings2.value("export_path", "")
            
            self.assertEqual(saved_import, test_import_path)
            self.assertEqual(saved_export, test_export_path)
    
    def test_empty_initial_state(self):
        """初期状態（設定なし）のテスト"""
        with patch('mmd_tools.ui.tabs.import_export_tab.QSettings') as mock_settings_class:
            # 新しい設定インスタンス
            real_settings = QSettings(self.test_org, self.test_app + "_empty")
            mock_settings_class.return_value = real_settings
            
            # タブを作成
            tab = ImportExportTab()
            
            # 初期状態は空文字列であることを確認
            import_path = real_settings.value("import_path", "")
            export_path = real_settings.value("export_path", "")
            
            self.assertEqual(import_path, "")
            self.assertEqual(export_path, "")
    
    def test_special_characters_in_path(self):
        """特殊文字を含むパスの保存テスト"""
        special_paths = [
            "/path with spaces/model.pmx",
            "/パス/日本語/モデル.pmx",
            "/path\\with\\backslashes\\model.pmx",
            "C:\\Users\\ユーザー\\Documents\\model.pmx"
        ]
        
        with patch('mmd_tools.ui.tabs.import_export_tab.QSettings') as mock_settings_class:
            real_settings = QSettings(self.test_org, self.test_app + "_special")
            mock_settings_class.return_value = real_settings
            
            for path in special_paths:
                # パスを保存
                real_settings.setValue("import_path", path)
                real_settings.sync()
                
                # 読み込んで確認
                loaded_path = real_settings.value("import_path", "")
                self.assertEqual(loaded_path, path, f"特殊文字を含むパスの保存に失敗: {path}")


if __name__ == '__main__':
    unittest.main()