import unittest
from unittest.mock import Mock, patch, MagicMock

from tests.common.maya_test_base import MayaTestBase


class TestImportExportTabExtended(MayaTestBase):
    """ImportExportTabの拡張機能（settings統合、VMDサポート）のテスト"""
    
    @patch('mmd_tools.ui.tabs.import_export_tab.find_all_mmd_models')
    @patch('mmd_tools.ui.tabs.import_export_tab.settings')
    def test_settings_integration(self, mock_settings, mock_find_models):
        """settings.pyとの統合をテスト"""
        # settingsのモック設定
        mock_settings.get.side_effect = lambda key, default: {
            "import.general.scale_factor": 2.0,
            "import.general.use_namespace": True,
            "import.model.import_models": False,
            "import.model.create_mmd_shaders": True,
            "export.general.export_format": "pmd",
            "export.general.apply_scale": False,
        }.get(key, default)
        
        # set呼び出しを記録
        set_calls = []
        mock_settings.set.side_effect = lambda k, v: set_calls.append((k, v))
        
        # find_all_mmd_modelsのモック
        mock_find_models.return_value = []
        
        # ImportExportTabのインポート
        from mmd_tools.ui.tabs.import_export_tab import ImportExportTab
        
        # Qtウィジェットのモック化を試みる
        with patch('mmd_tools.ui.tabs.import_export_tab.QSettings'):
            with patch('mmd_tools.ui.tabs.import_export_tab.QLineEdit'):
                with patch('mmd_tools.ui.tabs.import_export_tab.QPushButton'):
                    with patch('mmd_tools.ui.tabs.import_export_tab.QCheckBox'):
                        with patch('mmd_tools.ui.tabs.import_export_tab.QDoubleSpinBox') as mock_spin:
                            with patch('mmd_tools.ui.tabs.import_export_tab.QComboBox') as mock_combo:
                                # スピンボックスのモック
                                mock_spin_instance = Mock()
                                mock_spin_instance.setValue = Mock()
                                mock_spin_instance.valueChanged = Mock()
                                mock_spin_instance.valueChanged.connect = Mock()
                                mock_spin.return_value = mock_spin_instance
                                
                                # コンボボックスのモック
                                mock_combo_instance = Mock()
                                mock_combo_instance.setCurrentText = Mock()
                                mock_combo_instance.currentTextChanged = Mock()
                                mock_combo_instance.currentTextChanged.connect = Mock()
                                mock_combo.return_value = mock_combo_instance
                                
                                # タブを作成
                                tab = ImportExportTab()
                                
                                # settings.getが適切に呼ばれたか確認
                                mock_settings.get.assert_any_call("import.general.scale_factor", 1.0)
                                mock_settings.get.assert_any_call("import.general.use_namespace", False)
                                mock_settings.get.assert_any_call("export.general.export_format", "pmx")
    
    def test_vmd_section_functionality(self):
        """VMDインポートセクションの機能をテスト"""
        # VMDセクションに必要な要素の存在を確認
        expected_attributes = [
            'vmd_path_edit',
            'vmd_path_button',
            'target_model_combo',
            'animation_start_frame',
            'import_bone_animation_check',
            'import_morph_animation_check',
            'import_camera_animation_check',
            'import_light_animation_check',
            'import_vmd_button',
        ]
        
        # ImportExportTabがこれらの属性を持つことを確認
        from mmd_tools.ui.tabs.import_export_tab import ImportExportTab
        
        # 属性の存在をドキュメントで確認
        # 実際のインスタンス化はQt環境が必要なため、クラス定義を検査
        import inspect
        source = inspect.getsource(ImportExportTab.__init__)
        
        for attr in expected_attributes:
            self.assertIn(f"self.{attr}", source, f"Missing attribute: {attr}")
    
    @patch('mmd_tools.ui.tabs.import_export_tab.settings')
    def test_animation_settings_binding(self, mock_settings):
        """アニメーション設定のバインディングをテスト"""
        # settingsのモック設定
        mock_settings.get.return_value = True
        
        # 設定キーの確認
        animation_keys = [
            "import.animation.animation_start_frame",
            "import.animation.import_animations",
            "import.animation.import_morph_animation",
            "import.animation.import_camera_animation",
            "import.animation.import_light_animation",
        ]
        
        # default_settings.jsonに追加された設定を確認
        from mmd_tools.settings import Settings
        settings_instance = Settings()
        defaults = settings_instance._load_defaults_from_json()
        
        # アニメーション設定が存在することを確認
        self.assertIn("animation", defaults.get("import", {}))
        animation_settings = defaults["import"]["animation"]
        
        # 新しい設定項目が追加されていることを確認
        self.assertIn("import_morph_animation", animation_settings)
        self.assertTrue(animation_settings["import_morph_animation"])
    
    def test_model_list_refresh_functionality(self):
        """モデルリスト更新機能のテスト"""
        # refresh_model_listメソッドの存在を確認
        from mmd_tools.ui.tabs.import_export_tab import ImportExportTab
        
        # メソッドが定義されていることを確認
        self.assertTrue(hasattr(ImportExportTab, 'refresh_model_list'))
        
        # メソッドのシグネチャを確認
        import inspect
        method = getattr(ImportExportTab, 'refresh_model_list')
        self.assertTrue(inspect.ismethod(method) or inspect.isfunction(method))


if __name__ == '__main__':
    unittest.main()