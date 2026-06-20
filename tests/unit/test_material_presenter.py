import unittest
from unittest.mock import Mock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.material_presenter import MaterialPresenter  # noqa: E402
from mmd_tools.core.constants import (  # noqa: E402
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_SPHERE_PATH,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_SHADER_OUTLINE_ENABLED,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)


class TestMaterialPresenter(unittest.TestCase):
    """MaterialPresenterのテストクラス"""

    def setUp(self):
        """テスト前の準備"""
        # モックビューを作成
        self.mock_view = Mock()
        self.mock_view.material_list = Mock()
        self.mock_view.material_list.count.return_value = 0
        self.mock_view.material_list.currentItem.return_value = None

        # 各種UIウィジェットのモック
        self.mock_view.material_jp_name_edit = Mock()
        self.mock_view.material_en_name_edit = Mock()
        self.mock_view.texture_path_edit = Mock()
        self.mock_view.sphere_map_path_edit = Mock()
        self.mock_view.sphere_mode_combo = Mock()
        self.mock_view.toon_texture_combo = Mock()
        self.mock_view.diffuse_color_widget = Mock()
        self.mock_view.specular_color_widget = Mock()
        self.mock_view.ambient_color_widget = Mock()
        self.mock_view.edge_color_widget = Mock()
        self.mock_view.specular_coefficient_spin = Mock()
        self.mock_view.transparency_spin = Mock()
        self.mock_view.edge_size_spin = Mock()
        self.mock_view.shader_outline_check = Mock()
        self.mock_view.search_edit = Mock()
        self.mock_view.refresh_btn = Mock()
        self.mock_view.apply_btn = Mock()
        self.mock_view.reset_btn = Mock()

        # チェックボックスのモック
        self.mock_view.both_face_check = Mock()
        self.mock_view.ground_shadow_check = Mock()
        self.mock_view.self_shadow_map_check = Mock()
        self.mock_view.self_shadow_check = Mock()
        self.mock_view.edge_draw_check = Mock()
        self.mock_view.vertex_color_check = Mock()
        self.mock_view.point_draw_check = Mock()
        self.mock_view.line_draw_check = Mock()

        # スライダーのモック
        self.mock_view.transparency_slider = Mock()
        self.mock_view.specular_coefficient_slider = Mock()

        # ファイルブラウザボタンのモック
        self.mock_view.texture_browse_btn = Mock()
        self.mock_view.sphere_map_browse_btn = Mock()

        # モックアプリケーション状態を作成
        self.mock_app_state = Mock()
        self.mock_app_state.current_model_root = None
        self.mock_app_state.current_model_changed = Mock()
        self.mock_app_state.emit_status = Mock()

        # プレゼンターを作成
        self.mock_maya_adapter = Mock()
        self.mock_maya_adapter.object_exists.return_value = False
        self.mock_maya_adapter.attribute_exists.return_value = False
        self.mock_maya_adapter.node_type.return_value = ""
        self.mock_maya_adapter.list_connections.return_value = None
        self.mock_maya_adapter.list_attr.return_value = []
        self.mock_maya_adapter.window.return_value = False
        self.presenter = MaterialPresenter(
            self.mock_view,
            self.mock_app_state,
            maya_adapter=self.mock_maya_adapter,
        )

    def tearDown(self):
        """テスト後のクリーンアップ"""
        pass

    @patch("mmd_tools.ui.presenters.material_presenter.maya_utils")
    def test_load_materials_with_no_model(self, mock_maya_utils):
        """モデルが選択されていない場合のマテリアル読み込みテスト"""
        self.presenter.load_materials()

        # リストがクリアされることを確認
        self.mock_view.material_list.clear.assert_called_once()
        # 詳細が無効化されることを確認
        self.mock_view._set_details_enabled.assert_called_with(False)
        # プレースホルダーが表示されることを確認
        self.mock_view._show_placeholder.assert_called_once()

    @patch("mmd_tools.ui.presenters.material_presenter.maya_utils")
    def test_load_materials_with_model(self, mock_maya_utils):
        """モデルが選択されている場合のマテリアル読み込みテスト"""
        # モデルが存在する設定
        self.mock_app_state.current_model_root = "test_model"
        self.mock_maya_adapter.object_exists.return_value = True
        self.mock_maya_adapter.list_relatives.return_value = ["meshShape"]

        # より詳細なlistConnectionsの設定
        def mock_list_connections(nodes, **kwargs):
            if kwargs.get("type") == "shadingEngine":
                return ["SG"]
            elif nodes == "SG":
                return ["mat1"]
            return None

        self.mock_maya_adapter.list_connections.side_effect = mock_list_connections
        self.mock_maya_adapter.ls.return_value = ["mat1"]
        self.mock_maya_adapter.attribute_exists.return_value = True
        mock_maya_utils.get_attribute.side_effect = lambda node, attr: {
            "mmd_material_name": "Material 1",
            "mmd_material_name_en": "Material 1 EN",
        }.get(attr, "")

        self.presenter.load_materials()

        # リストがクリアされることを確認
        self.mock_view.material_list.clear.assert_called_once()
        # マテリアルがリストに追加されることを確認
        self.mock_view.material_list.addItem.assert_called()
        self.mock_maya_adapter.list_relatives.assert_called_with(
            "test_model",
            allDescendents=True,
            type="mesh",
        )
        self.mock_maya_adapter.list_connections.assert_any_call(["meshShape"], type="shadingEngine")
        self.mock_maya_adapter.ls.assert_called_with(["mat1"], materials=True)
        self.mock_maya_adapter.attribute_exists.assert_called_with(ATTR_MMD_MATERIAL_NAME, "mat1")

    @patch("mmd_tools.ui.presenters.material_presenter.maya_utils")
    def test_on_material_selected(self, mock_maya_utils):
        """マテリアル選択時の処理テスト"""
        # モックアイテムを作成
        mock_item = Mock()
        mock_item.text.return_value = "1:Material 1（material1）"
        mock_item.data.return_value = "material1"

        self.mock_maya_adapter.object_exists.return_value = True
        self.mock_maya_adapter.select.return_value = None

        self.presenter.on_material_selected(mock_item, None)

        # マテリアルが選択されることを確認
        self.mock_maya_adapter.select.assert_called_with("material1", replace=True)
        # 詳細が有効化されることを確認
        self.mock_view._set_details_enabled.assert_called_with(True)

    @patch("mmd_tools.ui.presenters.material_presenter.maya_utils")
    def test_load_material_properties_dx11shader(self, mock_maya_utils):
        """dx11Shaderのプロパティ読み込みテスト"""
        material_name = "test_material"
        self.mock_maya_adapter.node_type.return_value = "dx11Shader"
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, node: (
            attr
            in [
                "DiffuseColorRGB",
                "SpecularColor",
                "AmbientColor",
                "MainTexture",
            ]
        )

        # 色データの設定
        mock_maya_utils.get_attribute.side_effect = lambda node, attr: {
            ATTR_MMD_MATERIAL_NAME: "テストマテリアル",
            ATTR_MMD_MATERIAL_NAME_EN: "Test Material",
            "DiffuseColorRGB": (1.0, 0.5, 0.0),
            "SpecularColor": (0.8, 0.8, 0.8),
            "AmbientColor": (0.2, 0.2, 0.2),
            "mmd_specular_coefficient": 0.5,
            "transparency": (0.1,),
            ATTR_MMD_SPHERE_PATH: "sphere.spa",
            ATTR_MMD_SPHERE_MODE: 1,
            ATTR_MMD_DRAW_FLAGS: 0x1F,
            ATTR_MMD_EDGE_COLOR: (0.0, 0.0, 0.0, 1.0),
            ATTR_MMD_EDGE_SIZE: 1.0,
            ATTR_MMD_SHADER_OUTLINE_ENABLED: False,
            ATTR_MMD_TOON_TEXTURE_INDEX: 0,
        }.get(attr, None)

        # テクスチャ接続の設定
        self.mock_maya_adapter.list_connections.return_value = ["file1"]

        self.presenter.load_material_properties(material_name)

        # 各フィールドに値が設定されることを確認
        self.mock_view.material_jp_name_edit.setText.assert_called_with("テストマテリアル")
        self.mock_view.material_en_name_edit.setText.assert_called_with("Test Material")
        self.mock_view.specular_coefficient_spin.setValue.assert_called_with(0.5)

    def test_update_color_widget_with_valid_color(self):
        """有効な色データでのカラーウィジェット更新テスト"""
        widget = Mock()
        color = (1.0, 0.5, 0.0)

        self.presenter._update_color_widget(widget, color)

        # 正しいスタイルシートが設定されることを確認
        widget.setStyleSheet.assert_called_with("background-color: rgb(255, 127, 0); border: 1px solid black;")

    def test_update_color_widget_with_invalid_color(self):
        """無効な色データでのカラーウィジェット更新テスト"""
        widget = Mock()

        # 空の色データ
        self.presenter._update_color_widget(widget, None)
        widget.setStyleSheet.assert_called_with("background-color: rgb(128, 128, 128); border: 1px solid black;")

        # 要素不足の色データ
        self.presenter._update_color_widget(widget, (1.0, 0.5))
        widget.setStyleSheet.assert_called_with("background-color: rgb(128, 128, 128); border: 1px solid black;")

    @patch("mmd_tools.ui.presenters.material_presenter.maya_utils")
    def test_apply_changes(self, mock_maya_utils):
        """変更適用のテスト"""
        self.presenter.current_material = "test_material"
        self.presenter.material_data = {
            "diffuse": (1.0, 0.5, 0.0),
            "specular": (0.8, 0.8, 0.8),
            "ambient": (0.2, 0.2, 0.2),
            "edge_color": (0.0, 0.0, 0.0),
        }

        # UIの値を設定
        self.mock_view.material_jp_name_edit.text.return_value = "新しい名前"
        self.mock_view.material_en_name_edit.text.return_value = "New Name"
        self.mock_view.transparency_spin.value.return_value = 0.5
        self.mock_view.specular_coefficient_spin.value.return_value = 0.75
        self.mock_view.texture_path_edit.text.return_value = "texture.png"
        self.mock_view.sphere_map_path_edit.text.return_value = "sphere.spa"
        self.mock_view.sphere_mode_combo.currentIndex.return_value = 1
        self.mock_view.edge_size_spin.value.return_value = 1.5
        self.mock_view.shader_outline_check.isChecked.return_value = True

        # チェックボックスの状態を設定
        self.mock_view.both_face_check.isChecked.return_value = True
        self.mock_view.ground_shadow_check.isChecked.return_value = True
        self.mock_view.self_shadow_map_check.isChecked.return_value = True
        self.mock_view.self_shadow_check.isChecked.return_value = True
        self.mock_view.edge_draw_check.isChecked.return_value = True
        self.mock_view.vertex_color_check.isChecked.return_value = False
        self.mock_view.point_draw_check.isChecked.return_value = False
        self.mock_view.line_draw_check.isChecked.return_value = False

        # シェーダータイプを設定
        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        self.mock_maya_adapter.attribute_exists.return_value = True

        self.presenter.apply_changes()

        # カスタムアトリビュートが設定されることを確認
        mock_maya_utils.set_custom_attributes.assert_called()

        # 変更フラグがリセットされることを確認
        self.assertFalse(self.presenter.has_unsaved_changes)

    @patch("mmd_tools.converters.mesh_converter.apply_shader_outline")
    @patch("mmd_tools.converters.mesh_converter.apply_transparency_mode")
    def test_apply_transparency_mode_to_selected_applies_outline(self, mock_apply_mode, mock_apply_outline):
        """選択マテリアルへの透過モード適用時にアウトライン設定も反映される"""
        item = Mock()
        item.data.return_value = "dx11_mat"
        self.mock_view.material_list.selectedItems.return_value = [item]
        self.mock_view.transparency_mode_combo.currentIndex.return_value = 2
        self.mock_view.shader_outline_check.isChecked.return_value = True
        self.mock_view.edge_size_spin.value.return_value = 1.25
        self.mock_maya_adapter.object_exists.return_value = True
        self.mock_maya_adapter.node_type.return_value = "dx11Shader"

        self.presenter.apply_transparency_mode_to_selected()

        mock_apply_mode.assert_called_once()
        mock_apply_outline.assert_called_once_with("dx11_mat", True, 1.25)

    @patch("mmd_tools.ui.presenters.material_presenter.maya_utils")
    def test_apply_mmd_attributes_preserves_raw_edge_size_when_spin_unchanged(self, mock_maya_utils):
        """UI上限を超える元のエッジサイズは未変更なら保持する"""
        self.presenter.current_material = "test_material"
        self.presenter.material_data = {"edge_size": 2.5, "edge_size_view": 2.0}
        self.mock_view.edge_size_spin.value.return_value = 2.0
        self.mock_view.sphere_map_path_edit.text.return_value = ""
        self.mock_view.sphere_mode_combo.currentIndex.return_value = 0
        self.mock_view.toon_texture_combo.currentIndex.return_value = 0
        self.mock_view.shader_outline_check.isChecked.return_value = False
        for checkbox in [
            self.mock_view.both_face_check,
            self.mock_view.ground_shadow_check,
            self.mock_view.self_shadow_map_check,
            self.mock_view.self_shadow_check,
            self.mock_view.edge_draw_check,
            self.mock_view.vertex_color_check,
            self.mock_view.point_draw_check,
            self.mock_view.line_draw_check,
        ]:
            checkbox.isChecked.return_value = False
        self.mock_maya_adapter.attribute_exists.return_value = True

        self.presenter._apply_mmd_attributes()

        mock_maya_utils.set_attribute.assert_any_call("test_material", "mmd_edge_size", 2.5, "float")

    def test_on_search_text_changed(self):
        """検索機能のテスト"""
        # テスト用のマテリアルアイテムを作成
        items = []
        for i, (name, jp_name) in enumerate(
            [
                ("material1", "マテリアル1"),
                ("material2", "マテリアル2"),
                ("test_mat", "テスト"),
            ]
        ):
            item = Mock()
            item.text.return_value = f"{i + 1}:{jp_name}（{name}）"
            item.data.return_value = name
            items.append(item)

        self.mock_view.material_list.count.return_value = len(items)
        self.mock_view.material_list.item.side_effect = lambda i: items[i]

        # 検索テキスト "test" で検索
        self.presenter.on_search_text_changed("test")

        # "test_mat" のみ表示されることを確認
        items[0].setHidden.assert_called_with(True)
        items[1].setHidden.assert_called_with(True)
        items[2].setHidden.assert_called_with(False)

    @patch("mmd_tools.ui.presenters.material_presenter.QColorDialog")
    def test_pick_color(self, mock_color_dialog):
        """色選択ダイアログのテスト"""
        self.presenter.current_material = "test_material"
        self.presenter.material_data = {
            "diffuse": (1.0, 0.5, 0.0),
        }

        # 色選択ダイアログの戻り値を設定
        mock_color = Mock()
        mock_color.isValid.return_value = True
        mock_color.red.return_value = 255
        mock_color.green.return_value = 0
        mock_color.blue.return_value = 0
        mock_color_dialog.getColor.return_value = mock_color

        self.presenter.pick_color("diffuse")

        # 新しい色が設定されることを確認
        self.assertEqual(self.presenter.material_data["diffuse"], (1.0, 0.0, 0.0))
        # 変更フラグが設定されることを確認
        self.assertTrue(self.presenter.has_unsaved_changes)

    def test_reset_changes(self):
        """変更リセットのテスト"""
        self.presenter.current_material = "test_material"
        self.presenter.has_unsaved_changes = True

        with patch.object(self.presenter, "load_material_properties") as mock_load:
            self.presenter.reset_changes()

            # プロパティが再読み込みされることを確認
            mock_load.assert_called_once_with("test_material")
            # 変更フラグがリセットされることを確認
            self.assertFalse(self.presenter.has_unsaved_changes)


if __name__ == "__main__":
    unittest.main()
