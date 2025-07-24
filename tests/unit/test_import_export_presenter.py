import unittest
from unittest.mock import Mock, patch, MagicMock

from maya import cmds

from tests.common.maya_test_base import MayaTestBase
from mmd_tools.ui.presenters.import_export_presenter import ImportExportPresenter
from mmd_tools.ui.application_state import ApplicationState


class TestImportExportPresenter(MayaTestBase):
    """ImportExportPresenterの単体テスト"""
    
    def setUp(self):
        super().setUp()
        
        # Viewのモック作成
        self.mock_view = Mock()
        self._setup_view_mocks()
        
        # ApplicationStateは実インスタンス
        self.app_state = ApplicationState()
        
        # Presenter作成
        self.presenter = ImportExportPresenter(self.mock_view, self.app_state)
        
    def _setup_view_mocks(self):
        """ビューのモックを設定"""
        # インポート関連
        self.mock_view.import_path_edit = Mock()
        self.mock_view.import_path_edit.text = Mock(return_value="test.pmx")
        self.mock_view.import_path_button = Mock()
        self.mock_view.import_path_button.clicked = MagicMock()
        self.mock_view.import_button = Mock()
        self.mock_view.import_button.clicked = MagicMock()
        self.mock_view.scale_edit = Mock()
        self.mock_view.scale_edit.text = Mock(return_value="1.0")
        
        # エクスポート関連
        self.mock_view.export_path_edit = Mock()
        self.mock_view.export_path_edit.text = Mock(return_value="export.pmx")
        self.mock_view.export_path_button = Mock()
        self.mock_view.export_path_button.clicked = MagicMock()
        self.mock_view.export_button = Mock()
        self.mock_view.export_button.clicked = MagicMock()
        
    def test_initialization(self):
        """初期化とシグナル接続のテスト"""
        # clickedシグナルのconnectが呼ばれていることを確認
        self.mock_view.import_path_button.clicked.connect.assert_called_once()
        self.mock_view.export_path_button.clicked.connect.assert_called_once()
        self.mock_view.import_button.clicked.connect.assert_called_once()
        self.mock_view.export_button.clicked.connect.assert_called_once()
        
    @patch('mmd_tools.ui.presenters.import_export_presenter.import_mmd_file')
    def test_import_file_success(self, mock_import):
        """インポート成功のテスト"""
        # テスト用のルートノード作成
        test_root = cmds.group(empty=True, name="test_model_root")
        cmds.addAttr(test_root, ln="mmd_root", at="bool", dv=True)
        
        # モックの戻り値設定
        mock_import.return_value = test_root
        
        # ステータスとプログレスの呼び出しを記録
        status_calls = []
        progress_calls = []
        self.app_state.status_message.connect(lambda msg: status_calls.append(msg))
        self.app_state.progress_updated.connect(lambda val: progress_calls.append(val))
        
        # インポート実行
        self.presenter.import_file()
        
        # 検証
        mock_import.assert_called_once_with("test.pmx", 1.0)
        self.assertEqual(self.app_state.current_model_root, test_root)
        
        # ステータスメッセージとプログレスが適切に送信されたか
        self.assertIn(0, progress_calls)  # 開始時
        self.assertIn(100, progress_calls)  # 完了時
        self.assertTrue(any("インポート中" in msg for msg in status_calls))
        self.assertTrue(any("インポート完了" in msg for msg in status_calls))
        
    @patch('mmd_tools.ui.presenters.import_export_presenter.import_mmd_file')
    def test_import_file_failure(self, mock_import):
        """インポート失敗のテスト"""
        # 例外を発生させる
        mock_import.side_effect = Exception("Import error")
        
        # ステータスの呼び出しを記録
        status_calls = []
        progress_calls = []
        self.app_state.status_message.connect(lambda msg: status_calls.append(msg))
        self.app_state.progress_updated.connect(lambda val: progress_calls.append(val))
        
        # インポート実行
        self.presenter.import_file()
        
        # ApplicationStateが更新されていないことを確認
        self.assertIsNone(self.app_state.current_model_root)
        
        # エラーメッセージが送信されたか
        self.assertTrue(any("インポートエラー" in msg for msg in status_calls))
        # プログレスが0にリセットされたか
        self.assertEqual(progress_calls[-1], 0)
        
    @patch('mmd_tools.ui.presenters.import_export_presenter.import_mmd_file')
    def test_import_file_with_invalid_scale(self, mock_import):
        """不正なスケール値でのインポートテスト"""
        # スケール値を不正な値に設定
        self.mock_view.scale_edit.text.return_value = "invalid"
        
        # ValueErrorが発生することを確認
        with self.assertRaises(ValueError):
            self.presenter.import_file()
        
        # import_mmd_fileが呼ばれていないことを確認
        mock_import.assert_not_called()
        
    @patch('mmd_tools.ui.presenters.import_export_presenter.PmxExporter')
    def test_export_file_basic(self, mock_exporter_class):
        """基本的なエクスポートのテスト"""
        # モックエクスポーターインスタンス
        mock_exporter = Mock()
        mock_exporter_class.return_value = mock_exporter
        
        # エクスポート実行
        self.presenter.export_file()
        
        # エクスポーターが作成され、export_pmx_modelが呼ばれたか確認
        mock_exporter_class.assert_called_once()
        mock_exporter.export_pmx_model.assert_called_once_with("export.pmx", {})
        
    @patch('mmd_tools.ui.qt_compat.QFileDialog.getOpenFileName')
    def test_select_import_file(self, mock_dialog):
        """インポートファイル選択のテスト"""
        # ダイアログの戻り値を設定
        mock_dialog.return_value = ("/path/to/file.pmx", "PMX Files (*.pmx)")
        
        # ファイル選択実行
        self.presenter.select_import_file()
        
        # ダイアログが適切に呼ばれたか
        mock_dialog.assert_called_once()
        # テキストエディットに設定されたか
        self.mock_view.import_path_edit.setText.assert_called_once_with("/path/to/file.pmx")
        
    @patch('mmd_tools.ui.qt_compat.QFileDialog.getSaveFileName')
    def test_select_export_file(self, mock_dialog):
        """エクスポートファイル選択のテスト"""
        # ダイアログの戻り値を設定
        mock_dialog.return_value = ("/path/to/export.pmx", "PMX Files (*.pmx)")
        
        # ファイル選択実行
        self.presenter.select_export_file()
        
        # ダイアログが適切に呼ばれたか
        mock_dialog.assert_called_once()
        # テキストエディットに設定されたか
        self.mock_view.export_path_edit.setText.assert_called_once_with("/path/to/export.pmx")


if __name__ == '__main__':
    unittest.main()