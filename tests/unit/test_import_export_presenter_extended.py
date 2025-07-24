import unittest
from unittest.mock import Mock, patch, MagicMock, call

from maya import cmds

from tests.common.maya_test_base import MayaTestBase
from mmd_tools.ui.presenters.import_export_presenter import ImportExportPresenter
from mmd_tools.ui.application_state import ApplicationState


class TestImportExportPresenterExtended(MayaTestBase):
    """ImportExportPresenterの拡張機能（VMDインポート）のテスト"""
    
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
        """ビューのモックを設定（VMD関連を追加）"""
        # 既存のモック
        self.mock_view.import_path_edit = Mock()
        self.mock_view.import_path_edit.text = Mock(return_value="test.pmx")
        self.mock_view.import_path_button = Mock()
        self.mock_view.import_path_button.clicked = MagicMock()
        self.mock_view.import_button = Mock()
        self.mock_view.import_button.clicked = MagicMock()
        
        # VMD関連のモック
        self.mock_view.vmd_path_edit = Mock()
        self.mock_view.vmd_path_edit.text = Mock(return_value="test.vmd")
        self.mock_view.vmd_path_button = Mock()
        self.mock_view.vmd_path_button.clicked = MagicMock()
        self.mock_view.import_vmd_button = Mock()
        self.mock_view.import_vmd_button.clicked = MagicMock()
        
        # ターゲットモデルコンボボックス
        self.mock_view.target_model_combo = Mock()
        self.mock_view.target_model_combo.currentIndex = Mock(return_value=0)
        self.mock_view.target_model_combo.itemData = Mock(return_value=None)
        
        # refresh_model_listメソッド
        self.mock_view.refresh_model_list = Mock()
        
        # エクスポート関連
        self.mock_view.export_path_edit = Mock()
        self.mock_view.export_path_edit.text = Mock(return_value="export.pmx")
        self.mock_view.export_path_button = Mock()
        self.mock_view.export_path_button.clicked = MagicMock()
        self.mock_view.export_button = Mock()
        self.mock_view.export_button.clicked = MagicMock()
    
    def test_vmd_signals_connected(self):
        """VMD関連のシグナルが接続されているかテスト"""
        # VMDボタンのシグナルが接続されていることを確認
        self.mock_view.vmd_path_button.clicked.connect.assert_called_once()
        self.mock_view.import_vmd_button.clicked.connect.assert_called_once()
    
    @patch('mmd_tools.ui.presenters.import_export_presenter.QFileDialog.getOpenFileName')
    def test_select_vmd_file(self, mock_dialog):
        """VMDファイル選択のテスト"""
        # ダイアログの戻り値を設定
        mock_dialog.return_value = ("/path/to/animation.vmd", "VMD Files (*.vmd)")
        
        # ファイル選択実行
        self.presenter.select_vmd_file()
        
        # ダイアログが適切に呼ばれたか
        mock_dialog.assert_called_once()
        args, kwargs = mock_dialog.call_args
        self.assertIn("VMD", args[1])  # タイトルにVMDが含まれる
        self.assertIn("*.vmd", args[3])  # フィルターにVMDが含まれる
        
        # テキストエディットに設定されたか
        self.mock_view.vmd_path_edit.setText.assert_called_once_with("/path/to/animation.vmd")
    
    @patch('mmd_tools.ui.presenters.import_export_presenter.import_mmd_file')
    @patch('mmd_tools.ui.presenters.import_export_presenter.settings')
    def test_import_vmd_file_with_options(self, mock_settings, mock_import):
        """VMDファイルインポートでオプションが正しく渡されるかテスト"""
        # settingsのモック設定
        mock_settings.get.side_effect = lambda key, default: {
            "import.animation.animation_start_frame": 10,
            "import.animation.import_animations": True,
            "import.animation.import_morph_animation": True,
            "import.animation.import_camera_animation": False,
            "import.animation.import_light_animation": False,
            "import.animation.resample_curves": True,
        }.get(key, default)
        
        # ターゲットモデルを設定
        self.mock_view.target_model_combo.currentIndex.return_value = 1
        self.mock_view.target_model_combo.itemData.return_value = "model:root_node"
        
        # インポート成功を設定
        mock_import.return_value = True
        
        # VMDインポート実行
        self.presenter.import_vmd_file()
        
        # import_mmd_fileが正しいオプションで呼ばれたか確認
        mock_import.assert_called_once()
        args, kwargs = mock_import.call_args
        
        self.assertEqual(args[0], "test.vmd")
        options = kwargs.get('options', args[1] if len(args) > 1 else {})
        
        # オプションの内容を確認
        self.assertEqual(options['start_frame'], 10)
        self.assertTrue(options['import_bone_animation'])
        self.assertTrue(options['import_morph_animation'])
        self.assertFalse(options['import_camera_animation'])
        self.assertFalse(options['import_light_animation'])
        self.assertTrue(options['resample_curves'])
        self.assertEqual(options['target_model'], "model:root_node")
    
    @patch('mmd_tools.ui.presenters.import_export_presenter.import_mmd_file')
    def test_import_vmd_file_without_path(self, mock_import):
        """パスが空の場合のVMDインポートテスト"""
        # 空のパスを設定
        self.mock_view.vmd_path_edit.text.return_value = ""
        
        # ステータスメッセージを記録
        status_calls = []
        self.app_state.status_message.connect(lambda msg: status_calls.append(msg))
        
        # VMDインポート実行
        self.presenter.import_vmd_file()
        
        # import_mmd_fileが呼ばれていないことを確認
        mock_import.assert_not_called()
        
        # エラーメッセージが表示されたか
        self.assertTrue(any("VMDファイルパス" in msg for msg in status_calls))
    
    @patch('mmd_tools.ui.presenters.import_export_presenter.import_mmd_file')
    @patch('mmd_tools.ui.presenters.import_export_presenter.settings')
    def test_import_model_with_settings(self, mock_settings, mock_import):
        """モデルインポートでsettingsからオプションが取得されるかテスト"""
        # settingsのモック設定
        mock_settings.get.side_effect = lambda key, default: {
            "import.general.scale_factor": 0.5,
            "import.general.use_namespace": True,
            "import.model.import_models": False,
            "import.model.create_mmd_shaders": False,
        }.get(key, default)
        
        # ルートノードを作成
        test_root = cmds.group(empty=True, name="imported_model")
        mock_import.return_value = test_root
        
        # インポート実行
        self.presenter.import_file()
        
        # import_mmd_fileが呼ばれたか確認
        mock_import.assert_called_once()
        args, kwargs = mock_import.call_args
        
        options = kwargs.get('options', {})
        
        # オプションの内容を確認
        self.assertEqual(options['scale'], 0.5)
        self.assertTrue(options['use_namespace'])
        self.assertFalse(options['import_models'])
        self.assertFalse(options['create_mmd_shaders'])
        
        # refresh_model_listが呼ばれたか確認
        self.mock_view.refresh_model_list.assert_called_once()


if __name__ == '__main__':
    unittest.main()