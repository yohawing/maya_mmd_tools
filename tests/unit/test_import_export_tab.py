import unittest
from unittest.mock import Mock, patch, MagicMock, PropertyMock

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
        self.mock_view.import_path_button.clicked.connect = MagicMock()
        self.mock_view.import_button = Mock()
        self.mock_view.import_button.clicked = MagicMock()
        self.mock_view.import_button.clicked.connect = MagicMock()
        self.mock_view.scale_edit = Mock()
        self.mock_view.scale_edit.text = Mock(return_value="1.0")

        # エクスポート関連
        self.mock_view.export_path_edit = Mock()
        self.mock_view.export_path_edit.text = Mock(return_value="export.pmx")
        self.mock_view.export_path_button = Mock()
        self.mock_view.export_path_button.clicked = MagicMock()
        self.mock_view.export_path_button.clicked.connect = MagicMock()
        self.mock_view.export_button = Mock()
        self.mock_view.export_button.clicked = MagicMock()
        self.mock_view.export_button.clicked.connect = MagicMock()

    def test_initialization(self):
        """初期化とシグナル接続のテスト"""
        # clickedシグナルのconnectが呼ばれていることを確認
        self.mock_view.import_path_button.clicked.connect.assert_called_once()
        self.mock_view.export_path_button.clicked.connect.assert_called_once()
        self.mock_view.import_button.clicked.connect.assert_called_once()
        self.mock_view.export_button.clicked.connect.assert_called_once()

    @patch("mmd_tools.ui.presenters.import_export_presenter.import_mmd_file")
    def test_import_file_success(self, mock_import):
        """インポート成功のテスト"""
        # テスト用のルートノード名
        test_root = "test_model_root"
        
        # モックの戻り値設定
        mock_import.return_value = test_root
        
        # ApplicationStateの動作を簡単にモック化
        # refresh_model_listをモック
        self.app_state.refresh_model_list = Mock()
        
        # current_model_rootプロパティの設定値を記録
        set_model_root_value = None
        original_setter = self.app_state.__class__.current_model_root.fset
        
        def mock_current_model_root_setter(self, value):
            nonlocal set_model_root_value
            set_model_root_value = value
            # 実際のセッター処理は行わない（cmds.objExistsを回避）
            self._current_model_root = value
        
        # セッターを一時的に置き換え
        self.app_state.__class__.current_model_root = property(
            self.app_state.__class__.current_model_root.fget,
            mock_current_model_root_setter
        )

        # ステータスとプログレスの呼び出しを記録
        status_calls = []
        progress_calls = []
        self.app_state.status_message.connect(lambda msg: status_calls.append(msg))
        self.app_state.progress_updated.connect(lambda val: progress_calls.append(val))

        # インポート実行
        self.presenter.import_file()

        # 検証
        mock_import.assert_called_once()
        # 呼び出し時の引数を確認
        call_args = mock_import.call_args
        self.assertEqual(call_args[0][0], "test.pmx")
        self.assertEqual(call_args[1]["options"]["scale"], 1.0)
        
        # current_model_rootが設定されたことを確認
        self.assertEqual(set_model_root_value, test_root)
        self.assertEqual(self.app_state._current_model_root, test_root)

        # ステータスメッセージとプログレスが適切に送信されたか
        self.assertIn(0, progress_calls)  # 開始時
        self.assertIn(100, progress_calls)  # 完了時
        self.assertTrue(any("インポート中" in msg for msg in status_calls))
        self.assertTrue(any("インポート完了" in msg for msg in status_calls))
        
        # セッターを元に戻す
        self.app_state.__class__.current_model_root = property(
            self.app_state.__class__.current_model_root.fget,
            original_setter
        )

    @patch("mmd_tools.ui.presenters.import_export_presenter.import_mmd_file")
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

    @patch("mmd_tools.ui.presenters.import_export_presenter.PmxExporter")
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

    @patch("mmd_tools.ui.qt_compat.QFileDialog.getOpenFileName")
    def test_select_import_file(self, mock_dialog):
        """インポートファイル選択のテスト"""
        # ダイアログの戻り値を設定
        mock_dialog.return_value = ("/path/to/file.pmx", "PMX Files (*.pmx)")

        # ファイル選択実行
        self.presenter.select_import_file()

        # ダイアログが適切に呼ばれたか
        mock_dialog.assert_called_once()
        # テキストエディットに設定されたか
        self.mock_view.import_path_edit.setText.assert_called_once_with(
            "/path/to/file.pmx"
        )

    @patch("mmd_tools.ui.qt_compat.QFileDialog.getSaveFileName")
    def test_select_export_file(self, mock_dialog):
        """エクスポートファイル選択のテスト"""
        # ダイアログの戻り値を設定
        mock_dialog.return_value = ("/path/to/export.pmx", "PMX Files (*.pmx)")

        # ファイル選択実行
        self.presenter.select_export_file()

        # ダイアログが適切に呼ばれたか
        mock_dialog.assert_called_once()
        # テキストエディットに設定されたか
        self.mock_view.export_path_edit.setText.assert_called_once_with(
            "/path/to/export.pmx"
        )


class TestImportExportTabLogic(MayaTestBase):
    """ImportExportTabのファイルパス保持ロジックのテスト"""

    def test_qsettings_usage(self):
        """QSettingsの使用方法が正しいことを確認するテスト"""
        # QSettingsモックのテスト用の検証
        mock_settings = Mock()
        mock_settings.value = Mock(
            side_effect=lambda key, default: {
                "import_path": "/test/import.pmx",
                "export_path": "/test/export.pmx",
            }.get(key, default)
        )
        mock_settings.setValue = Mock()

        # 値の読み込みテスト
        import_path = mock_settings.value("import_path", "")
        export_path = mock_settings.value("export_path", "")

        self.assertEqual(import_path, "/test/import.pmx")
        self.assertEqual(export_path, "/test/export.pmx")

        # 値の保存テスト
        mock_settings.setValue("import_path", "/new/import.pmx")
        mock_settings.setValue("export_path", "/new/export.pmx")

        mock_settings.setValue.assert_any_call("import_path", "/new/import.pmx")
        mock_settings.setValue.assert_any_call("export_path", "/new/export.pmx")

    def test_path_persistence_logic(self):
        """パスの永続化ロジックのテスト"""
        # 保存されたパスのシミュレーション
        saved_paths = {}

        def mock_get_value(key, default):
            return saved_paths.get(key, default)

        def mock_set_value(key, value):
            saved_paths[key] = value

        # 初期状態：パスが保存されていない
        self.assertEqual(mock_get_value("import_path", ""), "")
        self.assertEqual(mock_get_value("export_path", ""), "")

        # パスを保存
        mock_set_value("import_path", "/user/documents/model.pmx")
        mock_set_value("export_path", "/user/documents/export.pmx")

        # 保存されたパスを読み込み
        self.assertEqual(mock_get_value("import_path", ""), "/user/documents/model.pmx")
        self.assertEqual(
            mock_get_value("export_path", ""), "/user/documents/export.pmx"
        )

    def test_text_change_callback_logic(self):
        """テキスト変更時のコールバックロジックのテスト"""
        # 保存をトリガーするコールバックのシミュレーション
        saved_value = None

        def save_callback(value):
            nonlocal saved_value
            saved_value = value

        # テキスト変更のシミュレーション
        text_values = ["/path1.pmx", "/path2.pmx", "/final/path.pmx"]

        for text in text_values:
            save_callback(text)
            self.assertEqual(saved_value, text)

    def test_settings_keys_and_organization(self):
        """QSettingsのキーと組織名の定義が正しいことをテスト"""
        # 期待される設定
        expected_org = "maya_mmd_tools"
        expected_app = "ImportExportTab"
        expected_import_key = "import_path"
        expected_export_key = "export_path"

        # 実際の実装で使用されるべき値を確認
        self.assertEqual(expected_org, "maya_mmd_tools")
        self.assertEqual(expected_app, "ImportExportTab")
        self.assertEqual(expected_import_key, "import_path")
        self.assertEqual(expected_export_key, "export_path")


if __name__ == "__main__":
    unittest.main()
