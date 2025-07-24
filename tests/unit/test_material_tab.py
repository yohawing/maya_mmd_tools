import unittest
from unittest.mock import Mock, patch, MagicMock, call

from maya import cmds

from tests.common.maya_test_base import MayaTestBase
from mmd_tools.ui.presenters.material_presenter import MaterialPresenter
from mmd_tools.ui.application_state import ApplicationState


class TestMaterialPresenter(MayaTestBase):
    """MaterialPresenterの単体テスト"""

    def setUp(self):
        super().setUp()

        # Viewのモック作成
        self.mock_view = Mock()
        self._setup_view_mocks()

        # ApplicationStateは実インスタンス
        self.app_state = ApplicationState()

        # Presenter作成
        self.presenter = MaterialPresenter(self.mock_view, self.app_state)

    def _setup_view_mocks(self):
        """ビューのモックを設定"""
        # Material list
        self.mock_view.material_list = Mock()
        self.mock_view.material_list.clear = Mock()
        self.mock_view.material_list.addItem = Mock()
        self.mock_view.material_list.count = Mock(return_value=0)
        self.mock_view.material_list.currentItemChanged = MagicMock()
        self.mock_view.material_list.currentItemChanged.connect = MagicMock()

        # Buttons
        self.mock_view.refresh_btn = Mock()
        self.mock_view.refresh_btn.clicked = MagicMock()
        self.mock_view.refresh_btn.clicked.connect = MagicMock()
        
        self.mock_view.diffuse_color_btn = Mock()
        self.mock_view.diffuse_color_btn.clicked = MagicMock()
        self.mock_view.diffuse_color_btn.clicked.connect = MagicMock()
        
        self.mock_view.specular_color_btn = Mock()
        self.mock_view.specular_color_btn.clicked = MagicMock()
        self.mock_view.specular_color_btn.clicked.connect = MagicMock()
        
        self.mock_view.ambient_color_btn = Mock()
        self.mock_view.ambient_color_btn.clicked = MagicMock()
        self.mock_view.ambient_color_btn.clicked.connect = MagicMock()
        
        self.mock_view.edge_color_btn = Mock()
        self.mock_view.edge_color_btn.clicked = MagicMock()
        self.mock_view.edge_color_btn.clicked.connect = MagicMock()
        
        self.mock_view.texture_browse_btn = Mock()
        self.mock_view.texture_browse_btn.clicked = MagicMock()
        self.mock_view.texture_browse_btn.clicked.connect = MagicMock()
        
        self.mock_view.sphere_map_browse_btn = Mock()
        self.mock_view.sphere_map_browse_btn.clicked = MagicMock()
        self.mock_view.sphere_map_browse_btn.clicked.connect = MagicMock()
        
        self.mock_view.apply_btn = Mock()
        self.mock_view.apply_btn.clicked = MagicMock()
        self.mock_view.apply_btn.clicked.connect = MagicMock()
        
        self.mock_view.reset_btn = Mock()
        self.mock_view.reset_btn.clicked = MagicMock()
        self.mock_view.reset_btn.clicked.connect = MagicMock()

        # Input fields
        self.mock_view.material_name_edit = Mock()
        self.mock_view.material_name_edit.setText = Mock()
        
        self.mock_view.texture_path_edit = Mock()
        self.mock_view.texture_path_edit.setText = Mock()
        self.mock_view.texture_path_edit.text = Mock(return_value="")
        self.mock_view.texture_path_edit.clear = Mock()
        
        self.mock_view.sphere_map_path_edit = Mock()
        self.mock_view.sphere_map_path_edit.setText = Mock()
        self.mock_view.sphere_map_path_edit.text = Mock(return_value="")
        
        # Spin boxes
        self.mock_view.specular_power_spin = Mock()
        self.mock_view.specular_power_spin.setValue = Mock()
        self.mock_view.specular_power_spin.value = Mock(return_value=5.0)
        
        self.mock_view.alpha_spin = Mock()
        self.mock_view.alpha_spin.setValue = Mock()
        self.mock_view.alpha_spin.value = Mock(return_value=1.0)
        
        self.mock_view.edge_size_spin = Mock()
        self.mock_view.edge_size_spin.setValue = Mock()
        self.mock_view.edge_size_spin.value = Mock(return_value=1.0)

        # Combo boxes
        self.mock_view.sphere_mode_combo = Mock()
        self.mock_view.sphere_mode_combo.setCurrentIndex = Mock()
        self.mock_view.sphere_mode_combo.currentIndex = Mock(return_value=0)
        
        self.mock_view.toon_texture_combo = Mock()
        self.mock_view.toon_texture_combo.setCurrentIndex = Mock()
        self.mock_view.toon_texture_combo.currentIndex = Mock(return_value=0)

        # Check boxes
        self.mock_view.both_face_check = Mock()
        self.mock_view.both_face_check.setChecked = Mock()
        self.mock_view.both_face_check.isChecked = Mock(return_value=False)
        
        self.mock_view.ground_shadow_check = Mock()
        self.mock_view.ground_shadow_check.setChecked = Mock()
        self.mock_view.ground_shadow_check.isChecked = Mock(return_value=False)
        
        self.mock_view.self_shadow_map_check = Mock()
        self.mock_view.self_shadow_map_check.setChecked = Mock()
        self.mock_view.self_shadow_map_check.isChecked = Mock(return_value=False)
        
        self.mock_view.self_shadow_check = Mock()
        self.mock_view.self_shadow_check.setChecked = Mock()
        self.mock_view.self_shadow_check.isChecked = Mock(return_value=False)
        
        self.mock_view.edge_draw_check = Mock()
        self.mock_view.edge_draw_check.setChecked = Mock()
        self.mock_view.edge_draw_check.isChecked = Mock(return_value=False)
        
        self.mock_view.vertex_color_check = Mock()
        self.mock_view.vertex_color_check.setChecked = Mock()
        self.mock_view.vertex_color_check.isChecked = Mock(return_value=False)
        
        self.mock_view.point_draw_check = Mock()
        self.mock_view.point_draw_check.setChecked = Mock()
        self.mock_view.point_draw_check.isChecked = Mock(return_value=False)
        
        self.mock_view.line_draw_check = Mock()
        self.mock_view.line_draw_check.setChecked = Mock()
        self.mock_view.line_draw_check.isChecked = Mock(return_value=False)

        # Color widgets
        self.mock_view.diffuse_color_widget = Mock()
        self.mock_view.diffuse_color_widget.setStyleSheet = Mock()
        
        self.mock_view.specular_color_widget = Mock()
        self.mock_view.specular_color_widget.setStyleSheet = Mock()
        
        self.mock_view.ambient_color_widget = Mock()
        self.mock_view.ambient_color_widget.setStyleSheet = Mock()
        
        self.mock_view.edge_color_widget = Mock()
        self.mock_view.edge_color_widget.setStyleSheet = Mock()

        # Helper method
        self.mock_view._set_details_enabled = Mock()

    def test_initialization(self):
        """初期化とシグナル接続のテスト"""
        # UIのシグナル接続を確認
        self.mock_view.material_list.currentItemChanged.connect.assert_called_once()
        self.mock_view.refresh_btn.clicked.connect.assert_called_once()
        self.mock_view.diffuse_color_btn.clicked.connect.assert_called_once()
        self.mock_view.apply_btn.clicked.connect.assert_called_once()
        self.mock_view.reset_btn.clicked.connect.assert_called_once()

    def test_load_materials_no_model(self):
        """モデルが選択されていない場合のマテリアルロードテスト"""
        # 現在のモデルなし
        self.app_state.current_model_root = None
        
        # マテリアルロード実行
        self.presenter.load_materials()
        
        # リストがクリアされ、詳細が無効化されたことを確認
        self.mock_view.material_list.clear.assert_called_once()
        self.mock_view._set_details_enabled.assert_called_with(False)

    @patch('mmd_tools.ui.presenters.material_presenter.cmds')
    def test_load_materials_with_model(self, mock_cmds):
        """モデルが選択されている場合のマテリアルロードテスト"""
        # テストデータ設定
        test_model = "test_model_root"
        test_shapes = ["pCubeShape1", "pCubeShape2"]
        test_shading_groups = ["lambert1SG", "lambert2SG"]
        test_materials = ["lambert1", "lambert2"]
        
        # cmdsのモック設定
        mock_cmds.objExists.return_value = True
        mock_cmds.listRelatives.return_value = test_shapes
        mock_cmds.listConnections.side_effect = [
            test_shading_groups,  # shapes -> shading groups
            [test_materials[0]],  # sg -> materials
            [test_materials[1]]   # sg -> materials
        ]
        mock_cmds.ls.side_effect = lambda x, materials=True: x
        
        # 現在のモデルを設定
        self.app_state._current_model_root = test_model
        
        # マテリアルロード実行
        self.presenter.load_materials()
        
        # 検証
        self.mock_view.material_list.clear.assert_called_once()
        self.assertEqual(self.mock_view.material_list.addItem.call_count, 2)
        self.mock_view.material_list.addItem.assert_any_call("lambert1")
        self.mock_view.material_list.addItem.assert_any_call("lambert2")

    def test_on_material_selected_none(self):
        """マテリアル未選択時の処理テスト"""
        # 選択なしで実行
        self.presenter.on_material_selected(None, None)
        
        # 詳細が無効化されたことを確認
        self.mock_view._set_details_enabled.assert_called_with(False)

    @patch('mmd_tools.ui.presenters.material_presenter.cmds')
    def test_on_material_selected_with_material(self, mock_cmds):
        """マテリアル選択時の処理テスト"""
        # モックアイテム作成
        mock_item = Mock()
        mock_item.text.return_value = "test_material"
        
        # cmdsのモック設定
        mock_cmds.getAttr.side_effect = [
            [(0.5, 0.5, 0.5)],  # diffuse color
            [(1.0, 1.0, 1.0)],  # specular color
            [(0.0, 0.0, 0.0)],  # transparency
        ]
        mock_cmds.attributeQuery.return_value = False
        mock_cmds.listConnections.return_value = None
        
        # マテリアル選択実行
        self.presenter.on_material_selected(mock_item, None)
        
        # 検証
        self.mock_view._set_details_enabled.assert_called_with(True)
        self.mock_view.material_name_edit.setText.assert_called_with("test_material")
        self.assertEqual(self.presenter.current_material, "test_material")

    @patch('mmd_tools.ui.presenters.material_presenter.cmds')
    def test_load_material_properties(self, mock_cmds):
        """マテリアルプロパティ読み込みのテスト"""
        test_material = "test_material"
        
        # cmdsのモック設定
        mock_cmds.getAttr.side_effect = [
            [(0.8, 0.2, 0.2)],  # diffuse color
            [(1.0, 1.0, 1.0)],  # specular color
            [(0.1, 0.1, 0.1)],  # transparency
        ]
        mock_cmds.attributeQuery.side_effect = lambda attr, node, exists: attr in ["cosinePower"]
        mock_cmds.listConnections.return_value = None
        
        # プロパティ読み込み実行
        self.presenter.load_material_properties(test_material)
        
        # 検証
        self.mock_view.material_name_edit.setText.assert_called_with(test_material)
        self.mock_view.alpha_spin.setValue.assert_called_with(0.9)  # 1.0 - 0.1
        
        # 色の更新を確認
        diffuse_style = self.mock_view.diffuse_color_widget.setStyleSheet.call_args[0][0]
        self.assertIn("rgb(204, 51, 51)", diffuse_style)  # 0.8 * 255 = 204

    @patch('mmd_tools.ui.presenters.material_presenter.QColorDialog')
    def test_pick_color(self, mock_color_dialog):
        """カラーピッカーのテスト"""
        # 現在のマテリアルを設定
        self.presenter.current_material = "test_material"
        self.presenter.material_data = {"diffuse": (0.5, 0.5, 0.5)}
        
        # カラーダイアログのモック設定
        mock_color = Mock()
        mock_color.isValid.return_value = True
        mock_color.red.return_value = 255
        mock_color.green.return_value = 128
        mock_color.blue.return_value = 0
        mock_color_dialog.getColor.return_value = mock_color
        
        # カラー選択実行
        self.presenter.pick_color("diffuse")
        
        # 検証
        self.assertEqual(self.presenter.material_data["diffuse"], (1.0, 0.5019607843137255, 0.0))
        diffuse_style = self.mock_view.diffuse_color_widget.setStyleSheet.call_args[0][0]
        self.assertIn("rgb(255, 128, 0)", diffuse_style)

    @patch('mmd_tools.ui.presenters.material_presenter.QFileDialog')
    @patch('mmd_tools.ui.presenters.material_presenter.cmds')
    def test_browse_file_texture(self, mock_cmds, mock_file_dialog):
        """テクスチャファイル選択のテスト"""
        # 現在のマテリアルを設定
        self.presenter.current_material = "test_material"
        
        # ダイアログのモック設定
        test_path = "/path/to/texture.png"
        mock_file_dialog.getOpenFileName.return_value = (test_path, "Image Files (*.png)")
        mock_cmds.workspace.return_value = "/maya/project"
        
        # ファイル選択実行
        self.presenter.browse_file("texture")
        
        # 検証
        self.mock_view.texture_path_edit.setText.assert_called_with(test_path)
        self.assertEqual(self.presenter.material_data["texture_path"], test_path)

    @patch('mmd_tools.ui.presenters.material_presenter.cmds')
    def test_apply_changes(self, mock_cmds):
        """変更適用のテスト"""
        # テストデータ設定
        self.presenter.current_material = "test_material"
        self.presenter.material_data = {
            "diffuse": (1.0, 0.5, 0.0),
            "specular": (1.0, 1.0, 1.0),
            "texture": ""
        }
        
        # スピンボックスの値設定
        self.mock_view.alpha_spin.value.return_value = 0.8
        self.mock_view.specular_power_spin.value.return_value = 10.0
        self.mock_view.texture_path_edit.text.return_value = "/new/texture.png"
        
        # cmdsのモック設定
        mock_cmds.attributeQuery.return_value = True
        mock_cmds.listConnections.return_value = None
        mock_cmds.shadingNode.return_value = "test_material_texture"
        
        # ステータスメッセージの記録
        status_calls = []
        self.app_state.status_message.connect(lambda msg: status_calls.append(msg))
        
        # 変更適用実行
        self.presenter.apply_changes()
        
        # 検証
        mock_cmds.setAttr.assert_any_call("test_material.color", 1.0, 0.5, 0.0, type="double3")
        mock_cmds.setAttr.assert_any_call("test_material.specularColor", 1.0, 1.0, 1.0, type="double3")
        mock_cmds.setAttr.assert_any_call("test_material.transparency", 0.2, 0.2, 0.2, type="double3")
        mock_cmds.setAttr.assert_any_call("test_material.cosinePower", 10.0)
        
        # テクスチャの適用も確認
        mock_cmds.shadingNode.assert_called_once_with("file", asTexture=True, name="test_material_texture")
        mock_cmds.connectAttr.assert_called_once()
        
        # ステータスメッセージの確認
        self.assertTrue(any("材質の変更を適用しました" in msg for msg in status_calls))

    def test_reset_changes(self):
        """変更リセットのテスト"""
        # 現在のマテリアルを設定
        self.presenter.current_material = "test_material"
        
        # load_material_propertiesをモック
        with patch.object(self.presenter, 'load_material_properties') as mock_load:
            # リセット実行
            self.presenter.reset_changes()
            
            # 検証
            mock_load.assert_called_once_with("test_material")

    @patch('mmd_tools.ui.presenters.material_presenter.cmds')
    def test_ensure_mmd_attributes(self, mock_cmds):
        """MMD属性の確認と作成のテスト"""
        test_material = "test_material"
        
        # 属性が存在しない設定
        mock_cmds.attributeQuery.return_value = False
        
        # 属性作成実行
        self.presenter._ensure_mmd_attributes(test_material)
        
        # 検証 - 各MMD属性が作成されたか
        expected_attrs = [
            "mmdSpherePath", "mmdSphereMode", "mmdToonIndex",
            "mmdDrawFlags", "mmdEdgeColor", "mmdEdgeSize", "ambientColor"
        ]
        
        # addAttrが呼ばれた回数を確認
        self.assertTrue(mock_cmds.addAttr.called)
        
        # 各属性のaddAttrが呼ばれたか確認
        add_attr_calls = mock_cmds.addAttr.call_args_list
        longname_calls = [call for call in add_attr_calls if 'longName' in call[1]]
        
        created_attrs = [call[1]['longName'] for call in longname_calls]
        for attr in expected_attrs:
            self.assertIn(attr, created_attrs)


class TestMaterialTabUI(MayaTestBase):
    """MaterialTabのUI関連テスト"""

    def test_color_widget_creation(self):
        """カラーウィジェット作成のテスト"""
        from mmd_tools.ui.tabs.material_tab import MaterialTab
        
        tab = MaterialTab()
        color_widget = tab._create_color_widget()
        
        # ウィジェットのプロパティを確認
        self.assertIsNotNone(color_widget)
        self.assertEqual(color_widget.width(), 50)
        self.assertEqual(color_widget.height(), 20)

    def test_set_details_enabled(self):
        """詳細ウィジェットの有効/無効切り替えテスト"""
        from mmd_tools.ui.tabs.material_tab import MaterialTab
        
        tab = MaterialTab()
        
        # 無効化テスト
        tab._set_details_enabled(False)
        self.assertFalse(tab.material_name_edit.isEnabled())
        self.assertFalse(tab.apply_btn.isEnabled())
        
        # 有効化テスト
        tab._set_details_enabled(True)
        self.assertTrue(tab.material_name_edit.isEnabled())
        self.assertTrue(tab.apply_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()