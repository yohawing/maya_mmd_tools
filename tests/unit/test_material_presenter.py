import unittest
from unittest.mock import Mock, patch

from tests.common.maya_stub import install_headless_ui_stubs
from tests.common.mock_ui import attach_mocks

install_headless_ui_stubs()

from mmd_tools.ui.presenters.material_presenter import MaterialPresenter  # noqa: E402
from mmd_tools.core.constants import (  # noqa: E402
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_ORIGINAL_TEXTURE_PATH,
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
        attach_mocks(
            self.mock_view,
            [
                "material_list",
                "material_jp_name_edit",
                "material_en_name_edit",
                "texture_path_edit",
                "sphere_map_path_edit",
                "sphere_mode_combo",
                "toon_texture_combo",
                "diffuse_color_widget",
                "specular_color_widget",
                "ambient_color_widget",
                "edge_color_widget",
                "specular_coefficient_spin",
                "transparency_spin",
                "edge_size_spin",
                "shader_outline_check",
                "search_edit",
                "refresh_btn",
                "apply_btn",
                "reset_btn",
                "both_face_check",
                "ground_shadow_check",
                "self_shadow_map_check",
                "self_shadow_check",
                "edge_draw_check",
                "vertex_color_check",
                "point_draw_check",
                "line_draw_check",
                "transparency_slider",
                "specular_coefficient_slider",
                "texture_browse_btn",
                "sphere_map_browse_btn",
            ],
        )
        self.mock_view.material_list.count.return_value = 0
        self.mock_view.material_list.currentItem.return_value = None

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

    def _configure_apply_inputs(self):
        self.presenter.current_material = "test_material"
        self.presenter.material_data = {
            "diffuse": (1.0, 0.5, 0.0),
            "specular": (0.8, 0.8, 0.8),
            "edge_color": (0.1, 0.2, 0.3),
            "edge_size_view": 1.5,
        }
        self.mock_view.material_jp_name_edit.text.return_value = "新しい名前"
        self.mock_view.material_en_name_edit.text.return_value = "New Name"
        self.mock_view.transparency_spin.value.return_value = 0.25
        self.mock_view.specular_coefficient_spin.value.return_value = 0.75
        self.mock_view.texture_path_edit.text.return_value = ""
        self.mock_view.sphere_map_path_edit.text.return_value = ""
        self.mock_view.sphere_mode_combo.currentIndex.return_value = 0
        self.mock_view.toon_texture_combo.currentIndex.return_value = 0
        self.mock_view.edge_size_spin.value.return_value = 1.5
        self.mock_view.shader_outline_check.isChecked.return_value = False
        self.mock_view.transparency_mode_combo.currentIndex.return_value = 0
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

    @patch("mmd_tools.ui.presenters.material_presenter.maya_material_utils")
    def test_load_texture_provenance_uses_material_utils(self, mock_maya_material_utils):
        self.mock_maya_adapter.attribute_exists.return_value = True
        mock_maya_material_utils.get_mmd_original_texture_path.return_value = "textures/original.png"

        self.presenter._load_texture_provenance("file1")

        self.mock_maya_adapter.attribute_exists.assert_called_once_with(ATTR_MMD_ORIGINAL_TEXTURE_PATH, "file1")
        mock_maya_material_utils.get_mmd_original_texture_path.assert_called_once_with("file1")
        self.assertEqual(self.presenter.material_data["original_pmx_texture_path"], "textures/original.png")
        self.mock_view.original_pmx_path_edit.setText.assert_called_once_with("textures/original.png")

    def _set_existing_mmd_attribute_query(self, missing_attrs=()):
        missing = set(missing_attrs)
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, node: attr not in missing

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_load_materials_with_no_model(self, mock_maya_attribute_utils):
        """モデルが選択されていない場合のマテリアル読み込みテスト"""
        self.presenter.load_materials()

        # リストがクリアされることを確認
        self.mock_view.material_list.clear.assert_called_once()
        # 詳細が無効化されることを確認
        self.mock_view._set_details_enabled.assert_called_with(False)
        # プレースホルダーが表示されることを確認
        self.mock_view._show_placeholder.assert_called_once()

    @patch("mmd_tools.ui.presenters.material_presenter.logger")
    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_load_materials_with_model(self, mock_maya_attribute_utils, mock_logger):
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
        mock_maya_attribute_utils.get_attribute.side_effect = lambda node, attr: {
            "mmd_material_name": "Material 1",
            "mmd_material_name_en": "Material 1 EN",
        }.get(attr, "")
        # count() は addItem 後の件数を返す想定
        self.mock_view.material_list.count.return_value = 1

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

        # 一覧ロード詳細は DEBUG のみ（INFO には出さない）
        expected = "Loaded 1 MMD materials for model: test_model"
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    @patch("mmd_tools.ui.presenters.material_presenter.logger")
    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_on_material_selected(self, mock_maya_attribute_utils, mock_logger):
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

        # 選択ログは DEBUG のみ（INFO には出さない）
        expected = "Selected material: material1"
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    @patch("mmd_tools.ui.presenters.material_presenter.logger")
    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_load_material_properties_dx11shader(self, mock_maya_attribute_utils, mock_logger):
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
        mock_maya_attribute_utils.get_attribute.side_effect = lambda node, attr: {
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
            "fileTextureName": "textures/main.png",
        }.get(attr, None)

        # テクスチャ接続の設定
        self.mock_maya_adapter.list_connections.return_value = ["file1"]

        self.presenter.load_material_properties(material_name)

        # 各フィールドに値が設定されることを確認
        self.mock_view.material_jp_name_edit.setText.assert_called_with("テストマテリアル")
        self.mock_view.material_en_name_edit.setText.assert_called_with("Test Material")
        self.mock_view.specular_coefficient_spin.setValue.assert_called_with(0.5)
        self.mock_view.texture_path_edit.setText.assert_called_with("textures/main.png")

        # テクスチャロード詳細は DEBUG のみ（INFO には出さない）
        expected = "Loaded texture: textures/main.png"
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    @patch("mmd_tools.ui.presenters.material_presenter.logger")
    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_load_material_properties_no_texture_logs_debug(self, mock_maya_attribute_utils, mock_logger):
        """テクスチャ未検出の詳細ログは DEBUG のみ。"""
        material_name = "test_material"
        self.mock_maya_adapter.node_type.return_value = "lambert"
        self.mock_maya_adapter.attribute_exists.return_value = False
        self.mock_maya_adapter.list_connections.return_value = None
        mock_maya_attribute_utils.get_attribute.return_value = None

        self.presenter.load_material_properties(material_name)

        self.assertEqual(self.presenter.material_data.get("texture"), "")
        self.mock_view.texture_path_edit.clear.assert_called()

        expected = f"No texture found for material: {material_name}"
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

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

    @patch("mmd_tools.ui.presenters.material_presenter.apply_sphere_map", return_value=True)
    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_apply_changes(self, mock_maya_attribute_utils, mock_apply_sphere_map):
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
        mock_maya_attribute_utils.set_custom_attributes.assert_called()
        mock_apply_sphere_map.assert_called_once_with(
            "test_material",
            "sphere.spa",
            1,
            maya_adapter=self.mock_maya_adapter,
        )

        # 変更フラグがリセットされることを確認
        self.assertFalse(self.presenter.has_unsaved_changes)

    @patch("mmd_tools.converters.mesh_converter.apply_shader_outline")
    @patch("mmd_tools.converters.mesh_converter.apply_transparency_mode")
    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_apply_changes_skips_dx11_missing_specular_and_transparency(
        self,
        mock_maya_attribute_utils,
        mock_apply_mode,
        mock_apply_outline,
    ):
        """dx11Shaderに無い標準属性を避け、MMD edge alphaも保持する。"""
        self._configure_apply_inputs()
        self.mock_maya_adapter.node_type.return_value = "dx11Shader"
        self._set_existing_mmd_attribute_query(missing_attrs=("color", "specularColor", "transparency"))

        self.presenter.apply_changes()

        set_attr_names = [call[0][1] for call in mock_maya_attribute_utils.set_attribute.call_args_list]
        self.assertNotIn("specularColor", set_attr_names)
        self.assertNotIn("transparency", set_attr_names)
        mock_maya_attribute_utils.set_attribute.assert_any_call(
            "test_material",
            "mmd_edge_color",
            [0.1, 0.2, 0.3],
            "double3",
        )
        mock_maya_attribute_utils.set_attribute.assert_any_call(
            "test_material", "mmd_edge_alpha", 1.0, "float"
        )
        mock_apply_mode.assert_called_once()
        mock_apply_outline.assert_called_once_with("test_material", False, 1.5)
        self.assertFalse(self.presenter.has_unsaved_changes)

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_apply_changes_updates_shared_dx11_and_glsl_base_uniforms(self, mock_maya_attribute_utils):
        """Apply synchronizes the common hardware contract and authored attrs."""
        for shader_type in ("dx11Shader", "GLSLShader"):
            with self.subTest(shader_type=shader_type):
                mock_maya_attribute_utils.reset_mock()
                self._configure_apply_inputs()
                self.presenter.material_data["ambient"] = (0.2, 0.3, 0.4)
                self.presenter.material_data["edge_alpha"] = 0.35
                self.mock_maya_adapter.node_type.return_value = shader_type
                self.mock_maya_adapter.attribute_exists.return_value = True
                self.mock_maya_adapter.list_connections.return_value = []

                self.presenter.apply_changes()

                mock_maya_attribute_utils.set_custom_attributes.assert_any_call(
                    "test_material",
                    {
                        "diffuse_color": (1.0, 0.5, 0.0),
                        "specular_color": (0.8, 0.8, 0.8),
                        "ambient_color": (0.2, 0.3, 0.4),
                        "mmd_diffuse_alpha": 0.75,
                    },
                )
                for attr, value, attr_type in (
                    ("DiffuseColorRGB", (1.0, 0.5, 0.0), "double3"),
                    ("DiffuseColorA", 0.75, "float"),
                    ("AmbientColor", (0.2, 0.3, 0.4), "double3"),
                    ("SpecularColor", (0.8, 0.8, 0.8), "double3"),
                    ("Shininess", 0.75, "float"),
                ):
                    mock_maya_attribute_utils.set_attribute.assert_any_call(
                        "test_material", attr, value, attr_type
                    )
                if shader_type == "dx11Shader":
                    mock_maya_attribute_utils.set_attribute.assert_any_call(
                        "test_material", "EdgeColorRGB", [0.1, 0.2, 0.3], "double3"
                    )
                    mock_maya_attribute_utils.set_attribute.assert_any_call(
                        "test_material", "EdgeColorA", 0.35, "float"
                    )
                    self.assertFalse(
                        any(call.args[1] == "EdgeColor" for call in mock_maya_attribute_utils.set_attribute.call_args_list)
                    )
                else:
                    mock_maya_attribute_utils.set_attribute.assert_any_call(
                        "test_material", "EdgeColor", (0.1, 0.2, 0.3, 0.35), "double4"
                    )
                    self.assertFalse(
                        any(call.args[1] == "EdgeColorRGB" for call in mock_maya_attribute_utils.set_attribute.call_args_list)
                    )

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_hardware_base_sync_does_not_overwrite_driven_final_plug(self, mock_maya_attribute_utils):
        """A material-morph evaluator connection owns the final shader plug."""
        self.presenter.current_material = "test_material"
        self.mock_maya_adapter.attribute_exists.return_value = True
        self.mock_maya_adapter.list_connections.side_effect = lambda plug, **_kwargs: (
            ["morphEval.outputDiffuse"] if plug.endswith(".DiffuseColorRGB") else []
        )

        self.presenter._apply_hardware_base_values(
            {"diffuse_rgb": (0.1, 0.2, 0.3), "ambient": (0.4, 0.5, 0.6)},
            "dx11Shader",
        )

        self.assertNotIn(
            unittest.mock.call("test_material", "DiffuseColorRGB", (0.1, 0.2, 0.3), "double3"),
            mock_maya_attribute_utils.set_attribute.call_args_list,
        )
        mock_maya_attribute_utils.set_attribute.assert_called_once_with(
            "test_material", "AmbientColor", (0.4, 0.5, 0.6), "double3"
        )

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_dx11_edge_sync_skips_only_driven_split_plug(self, mock_maya_attribute_utils):
        self.presenter.current_material = "test_material"
        self.mock_maya_adapter.attribute_exists.return_value = True
        self.mock_maya_adapter.list_connections.side_effect = lambda plug, **_kwargs: (
            ["morphEval.outputEdgeAlpha"] if plug.endswith(".EdgeColorA") else []
        )

        self.presenter._apply_hardware_base_values(
            {"edge_color": (0.1, 0.2, 0.3, 0.4)}, "dx11Shader"
        )

        mock_maya_attribute_utils.set_attribute.assert_called_once_with(
            "test_material", "EdgeColorRGB", [0.1, 0.2, 0.3], "double3"
        )

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_load_rgb_only_edge_scene_defaults_separate_alpha(self, mock_maya_attribute_utils):
        self.mock_maya_adapter.node_type.return_value = "dx11Shader"
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr == "mmd_edge_color"
        self.mock_maya_adapter.list_connections.return_value = None
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: (
            (0.2, 0.3, 0.4) if attr == "mmd_edge_color" else None
        )

        self.presenter.load_material_properties("legacy_mat")

        self.assertEqual(self.presenter.material_data["edge_color"], (0.2, 0.3, 0.4))
        self.assertEqual(self.presenter.material_data["edge_alpha"], 1.0)

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_load_separate_edge_alpha_attribute(self, mock_maya_attribute_utils):
        self.mock_maya_adapter.node_type.return_value = "GLSLShader"
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr in {
            "mmd_edge_color",
            "mmd_edge_alpha",
        }
        self.mock_maya_adapter.list_connections.return_value = None
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: {
            "mmd_edge_color": (0.2, 0.3, 0.4),
            "mmd_edge_alpha": 0.25,
        }.get(attr)

        self.presenter.load_material_properties("current_mat")

        self.assertEqual(self.presenter.material_data["edge_alpha"], 0.25)

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_driven_hardware_load_and_unrelated_apply_preserve_authored_base(
        self, mock_maya_attribute_utils
    ):
        authored = {
            "diffuse_color": (0.1, 0.2, 0.3),
            "specular_color": (0.4, 0.5, 0.6),
            "ambient_color": (0.2, 0.25, 0.3),
            "mmd_diffuse_alpha": 0.6,
            "mmd_specular_coefficient": 8.0,
            "mmd_edge_color": (0.0, 0.0, 0.0),
            "mmd_edge_alpha": 0.7,
        }
        evaluated = {
            "DiffuseColorRGB": (0.9, 0.9, 0.9),
            "SpecularColor": (0.8, 0.8, 0.8),
            "AmbientColor": (0.7, 0.7, 0.7),
            "DiffuseColorA": 0.2,
        }
        self.mock_maya_adapter.node_type.return_value = "dx11Shader"
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr in authored or attr in evaluated
        self.mock_maya_adapter.list_connections.side_effect = lambda plug, **_kwargs: (
            ["morphEval.output"] if plug.rsplit(".", 1)[-1] in evaluated else []
        )
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: {
            **authored,
            **evaluated,
        }.get(attr)

        self.presenter.load_material_properties("test_material")

        self.assertEqual(self.presenter.material_data["diffuse"], authored["diffuse_color"])
        self.assertEqual(self.presenter.material_data["specular"], authored["specular_color"])
        self.assertEqual(self.presenter.material_data["ambient"], authored["ambient_color"])
        self.assertEqual(self.presenter.material_data["transparency"], 0.4)

        mock_maya_attribute_utils.reset_mock()
        self._configure_apply_inputs()
        self.presenter.material_data.update(
            diffuse=authored["diffuse_color"],
            specular=authored["specular_color"],
            ambient=authored["ambient_color"],
            edge_alpha=authored["mmd_edge_alpha"],
        )
        self.mock_view.transparency_spin.value.return_value = 0.4
        self.mock_view.specular_coefficient_spin.value.return_value = 8.0
        self.presenter.apply_changes()

        mock_maya_attribute_utils.set_custom_attributes.assert_any_call(
            "test_material",
            {
                "diffuse_color": authored["diffuse_color"],
                "specular_color": authored["specular_color"],
                "ambient_color": authored["ambient_color"],
                "mmd_diffuse_alpha": authored["mmd_diffuse_alpha"],
            },
        )
        written_hardware = {
            call.args[1] for call in mock_maya_attribute_utils.set_attribute.call_args_list
        }
        self.assertFalse({"DiffuseColorRGB", "SpecularColor", "AmbientColor", "DiffuseColorA"} & written_hardware)
        mock_maya_attribute_utils.set_custom_attributes.assert_any_call(
            "test_material", {"mmd_specular_coefficient": 8.0, "shininess": 8.0}
        )

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_legacy_undriven_hardware_color_remains_loadable(self, mock_maya_attribute_utils):
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr == "DiffuseColorRGB"
        self.mock_maya_adapter.list_connections.return_value = []
        mock_maya_attribute_utils.get_attribute.return_value = (0.3, 0.4, 0.5)

        value, owned = self.presenter._load_base_value(
            "legacy", "diffuse_color", ("DiffuseColorRGB",), (0.5, 0.5, 0.5)
        )

        self.assertEqual(value, (0.3, 0.4, 0.5))
        self.assertTrue(owned)

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_standard_surface_actual_values_override_stale_custom_attrs(self, mock_maya_attribute_utils):
        actual = {
            "baseColor": (0.2, 0.4, 0.6),
            "opacity": (0.7, 0.7, 0.7),
            "specularColor": (0.3, 0.5, 0.7),
            "ambientColor": (0.1, 0.15, 0.2),
            "specular": 0.22,
        }
        stale = {
            "diffuse_color": (0.9, 0.9, 0.9),
            "specular_color": (0.8, 0.8, 0.8),
            "ambient_color": (0.7, 0.7, 0.7),
            "mmd_diffuse_alpha": 0.1,
        }
        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr in actual or attr in stale
        self.mock_maya_adapter.list_connections.return_value = None
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: {
            **actual,
            **stale,
        }.get(attr)

        self.presenter.load_material_properties("test_material")
        loaded = dict(self.presenter.material_data)

        self.assertEqual(loaded["diffuse"], actual["baseColor"])
        self.assertEqual(loaded["specular"], actual["specularColor"])
        self.assertEqual(loaded["ambient"], actual["ambientColor"])
        self.assertAlmostEqual(loaded["transparency"], 0.3)

        self._configure_apply_inputs()
        self.presenter.material_data = loaded
        self.mock_view.transparency_spin.value.return_value = 0.3
        self.mock_view.specular_coefficient_spin.value.return_value = 0.22
        mock_maya_attribute_utils.reset_mock()
        self.presenter.apply_changes()

        mock_maya_attribute_utils.set_attribute.assert_any_call(
            "test_material", "baseColor", actual["baseColor"], "double3"
        )
        mock_maya_attribute_utils.set_attribute.assert_any_call(
            "test_material", "opacity", [0.7, 0.7, 0.7], "double3"
        )
        mock_maya_attribute_utils.set_custom_attributes.assert_any_call(
            "test_material",
            {
                "diffuse_color": actual["baseColor"],
                "specular_color": actual["specularColor"],
                "ambient_color": actual["ambientColor"],
                "mmd_diffuse_alpha": 0.7,
            },
        )

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_editing_unknown_driven_hardware_values_acquires_base_ownership(
        self, mock_maya_attribute_utils
    ):
        self._configure_apply_inputs()
        self.presenter.material_data = {
            "diffuse": (0.2, 0.3, 0.4),
            "specular": (0.5, 0.6, 0.7),
            "ambient": (0.1, 0.2, 0.3),
            "edge_color": (0.0, 0.0, 0.0),
            "_diffuse_base_owned": False,
            "_specular_base_owned": False,
            "_ambient_base_owned": False,
            "_diffuse_alpha_base_owned": False,
            "_specular_power_base_owned": False,
            "_loaded_base_snapshot": {
                "diffuse": (0.5, 0.5, 0.5),
                "specular": (0.5, 0.5, 0.5),
                "ambient": (0.5, 0.5, 0.5),
                "transparency": 0.0,
                "specular_coefficient": 0.5,
            },
        }
        self.mock_view.transparency_spin.value.return_value = 0.35
        self.mock_view.specular_coefficient_spin.value.return_value = 12.0
        self.mock_maya_adapter.node_type.return_value = "dx11Shader"
        hardware_attrs = {
            "DiffuseColorRGB",
            "DiffuseColorA",
            "SpecularColor",
            "AmbientColor",
            "Shininess",
            "EdgeColorRGB",
            "EdgeColorA",
            "EdgeSize",
            "SphereMode",
            "Opacity",
        }
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr in hardware_attrs
        self.mock_maya_adapter.list_connections.return_value = ["morphEval.output"]

        self.presenter.apply_changes()

        mock_maya_attribute_utils.set_custom_attributes.assert_any_call(
            "test_material",
            {
                "diffuse_color": (0.2, 0.3, 0.4),
                "specular_color": (0.5, 0.6, 0.7),
                "ambient_color": (0.1, 0.2, 0.3),
                "mmd_diffuse_alpha": 0.65,
            },
        )
        mock_maya_attribute_utils.set_custom_attributes.assert_any_call(
            "test_material", {"mmd_specular_coefficient": 12.0, "shininess": 12.0}
        )
        for key in (
            "_diffuse_base_owned",
            "_specular_base_owned",
            "_ambient_base_owned",
            "_diffuse_alpha_base_owned",
            "_specular_power_base_owned",
        ):
            self.assertTrue(self.presenter.material_data[key])

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_unrelated_apply_keeps_unknown_driven_bases_unowned(self, mock_maya_attribute_utils):
        self._configure_apply_inputs()
        unknown = (0.5, 0.5, 0.5)
        self.presenter.material_data = {
            "diffuse": unknown,
            "specular": unknown,
            "ambient": unknown,
            "edge_color": (0.0, 0.0, 0.0),
            "_diffuse_base_owned": False,
            "_specular_base_owned": False,
            "_ambient_base_owned": False,
            "_diffuse_alpha_base_owned": False,
            "_specular_power_base_owned": False,
            "_loaded_base_snapshot": {
                "diffuse": unknown,
                "specular": unknown,
                "ambient": unknown,
                "transparency": 0.0,
                "specular_coefficient": 0.5,
            },
        }
        self.mock_view.transparency_spin.value.return_value = 0.0
        self.mock_view.specular_coefficient_spin.value.return_value = 0.5
        self.mock_maya_adapter.node_type.return_value = "dx11Shader"
        hardware_attrs = {
            "DiffuseColorRGB",
            "DiffuseColorA",
            "SpecularColor",
            "AmbientColor",
            "Shininess",
            "EdgeColorRGB",
            "EdgeColorA",
            "EdgeSize",
            "SphereMode",
            "Opacity",
        }
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr in hardware_attrs
        self.mock_maya_adapter.list_connections.return_value = ["morphEval.output"]

        self.presenter.apply_changes()

        custom_payloads = [call.args[1] for call in mock_maya_attribute_utils.set_custom_attributes.call_args_list]
        self.assertFalse(any("diffuse_color" in payload for payload in custom_payloads))
        self.assertFalse(any("specular_color" in payload for payload in custom_payloads))
        self.assertFalse(any("ambient_color" in payload for payload in custom_payloads))
        self.assertFalse(any("mmd_diffuse_alpha" in payload for payload in custom_payloads))
        self.assertFalse(any("shininess" in payload for payload in custom_payloads))

        # Reload remains unknown: unrelated Apply did not manufacture authority.
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: {
            "DiffuseColorRGB": (0.9, 0.9, 0.9),
            "DiffuseColorA": 0.2,
            "SpecularColor": (0.8, 0.8, 0.8),
            "AmbientColor": (0.7, 0.7, 0.7),
            "Shininess": 20.0,
        }.get(attr)
        self.presenter.load_material_properties("test_material")
        self.assertFalse(self.presenter.material_data["_diffuse_base_owned"])
        self.assertFalse(self.presenter.material_data["_specular_base_owned"])
        self.assertFalse(self.presenter.material_data["_ambient_base_owned"])
        self.assertFalse(self.presenter.material_data["_diffuse_alpha_base_owned"])
        self.assertFalse(self.presenter.material_data["_specular_power_base_owned"])

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_ensure_mmd_attributes_excludes_ownership_sensitive_bases(self, mock_maya_attribute_utils):
        self.mock_maya_adapter.attribute_exists.return_value = False

        self.presenter._ensure_mmd_attributes("legacy_mat")

        defaults = mock_maya_attribute_utils.set_custom_attributes.call_args.args[1]
        for attr in (
            "diffuse_color",
            "mmd_diffuse_alpha",
            "specular_color",
            "ambient_color",
            "shininess",
            "mmd_specular_coefficient",
        ):
            self.assertNotIn(attr, defaults)

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_standard_surface_preserves_mmd_coefficient_and_direct_specular_without_edit(
        self, mock_maya_attribute_utils
    ):
        values = {
            "baseColor": (0.2, 0.3, 0.4),
            "specularColor": (0.5, 0.6, 0.7),
            "specular": 0.6,
            "opacity": (1.0, 1.0, 1.0),
            "mmd_specular_coefficient": 25.0,
        }
        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr in values
        self.mock_maya_adapter.list_connections.return_value = None
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: values.get(attr)

        self.presenter.load_material_properties("test_material")
        loaded = dict(self.presenter.material_data)
        self.assertEqual(loaded["specular_coefficient"], 1.0)
        self.assertEqual(loaded["_authored_specular_coefficient"], 25.0)
        self.assertEqual(loaded["_standard_specular_weight"], 0.6)

        self._configure_apply_inputs()
        self.presenter.material_data = loaded
        self.mock_view.specular_coefficient_spin.value.return_value = 1.0
        self.mock_view.transparency_spin.value.return_value = 0.0
        mock_maya_attribute_utils.reset_mock()
        self.presenter.apply_changes()

        specular_writes = [
            call for call in mock_maya_attribute_utils.set_attribute.call_args_list
            if call.args[1] == "specular"
        ]
        self.assertEqual(specular_writes, [])
        mock_maya_attribute_utils.set_custom_attributes.assert_any_call(
            "test_material", {"mmd_specular_coefficient": 25.0, "shininess": 25.0}
        )

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_standard_surface_coefficient_edit_maps_directly_to_specular_weight(
        self, mock_maya_attribute_utils
    ):
        self._configure_apply_inputs()
        self.presenter.material_data.update(
            specular_coefficient=1.0,
            _authored_specular_coefficient=25.0,
            _standard_specular_weight=0.6,
            _specular_power_base_owned=True,
            _loaded_base_snapshot={
                "diffuse": self.presenter.material_data["diffuse"],
                "specular": self.presenter.material_data["specular"],
                "ambient": self.presenter.material_data.get("ambient"),
                "transparency": 0.25,
                "specular_coefficient": 1.0,
            },
        )
        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        self.mock_maya_adapter.attribute_exists.return_value = True
        self.mock_view.specular_coefficient_spin.value.return_value = 0.7

        self.presenter.apply_changes()

        mock_maya_attribute_utils.set_attribute.assert_any_call(
            "test_material", "specular", 0.7, "float"
        )
        mock_maya_attribute_utils.set_custom_attributes.assert_any_call(
            "test_material", {"mmd_specular_coefficient": 0.7, "shininess": 0.7}
        )

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_standard_surface_without_custom_coefficient_roundtrips_direct_weight(
        self, mock_maya_attribute_utils
    ):
        values = {
            "baseColor": (0.2, 0.3, 0.4),
            "specularColor": (0.5, 0.6, 0.7),
            "specular": 0.5,
            "opacity": (1.0, 1.0, 1.0),
        }
        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr in values
        self.mock_maya_adapter.list_connections.return_value = None
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: values.get(attr)

        self.presenter.load_material_properties("test_material")
        loaded = dict(self.presenter.material_data)
        self.assertEqual(loaded["specular_coefficient"], 0.5)

        self._configure_apply_inputs()
        self.presenter.material_data = loaded
        self.mock_view.specular_coefficient_spin.value.return_value = 0.5
        self.mock_view.transparency_spin.value.return_value = 0.0
        mock_maya_attribute_utils.reset_mock()
        self.presenter.apply_changes()

        self.assertFalse(
            any(
                call.args[1] == "specular"
                for call in mock_maya_attribute_utils.set_attribute.call_args_list
            )
        )
        mock_maya_attribute_utils.set_custom_attributes.assert_any_call(
            "test_material", {"mmd_specular_coefficient": 0.5, "shininess": 0.5}
        )

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_apply_changes_routes_standard_surface_opacity_and_specular(self, mock_maya_attribute_utils):
        """standardSurfaceは透過をopacityへ変換し、存在するspecularColorだけ設定する"""
        self._configure_apply_inputs()
        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        self._set_existing_mmd_attribute_query()

        self.presenter.apply_changes()

        mock_maya_attribute_utils.set_attribute.assert_any_call(
            "test_material",
            "opacity",
            [0.75, 0.75, 0.75],
            "double3",
        )
        mock_maya_attribute_utils.set_attribute.assert_any_call(
            "test_material",
            "specularColor",
            (0.8, 0.8, 0.8),
            "double3",
        )
        self.assertFalse(self.presenter.has_unsaved_changes)

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_apply_changes_routes_transparency_when_attribute_exists(self, mock_maya_attribute_utils):
        """transparency属性を持つ非standardSurface shaderでは従来通りtransparencyを設定する"""
        self._configure_apply_inputs()
        self.mock_maya_adapter.node_type.return_value = "blinn"
        self._set_existing_mmd_attribute_query()

        self.presenter.apply_changes()

        mock_maya_attribute_utils.set_attribute.assert_any_call(
            "test_material",
            "transparency",
            [0.25, 0.25, 0.25],
            "double3",
        )
        mock_maya_attribute_utils.set_attribute.assert_any_call(
            "test_material",
            "specularColor",
            (0.8, 0.8, 0.8),
            "double3",
        )
        self.assertFalse(self.presenter.has_unsaved_changes)

    @patch("mmd_tools.ui.presenters.material_presenter.apply_sphere_map", side_effect=RuntimeError("boom"))
    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_apply_changes_reports_sphere_map_exception(self, mock_maya_attribute_utils, _mock_apply_sphere_map):
        """sphere map 適用失敗は material 全体を落とさず専用 status で通知する"""
        self._configure_apply_inputs()
        self.mock_view.sphere_map_path_edit.text.return_value = "sphere.spa"
        self.mock_view.sphere_mode_combo.currentIndex.return_value = 1
        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        self._set_existing_mmd_attribute_query()

        self.presenter.apply_changes()

        statuses = [call.args[0] for call in self.mock_app_state.emit_status.call_args_list]
        self.assertTrue(any("boom" in status for status in statuses))
        self.assertFalse(self.presenter.has_unsaved_changes)

    @patch("mmd_tools.ui.presenters.material_presenter.apply_sphere_map", return_value=False)
    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_apply_changes_reports_sphere_map_false_result(self, mock_maya_attribute_utils, _mock_apply_sphere_map):
        """sphere map が未適用なら例外なしでも専用 status で通知する"""
        self._configure_apply_inputs()
        self.mock_view.sphere_map_path_edit.text.return_value = "missing.spa"
        self.mock_view.sphere_mode_combo.currentIndex.return_value = 1
        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        self._set_existing_mmd_attribute_query()

        self.presenter.apply_changes()

        statuses = [call.args[0] for call in self.mock_app_state.emit_status.call_args_list]
        self.assertTrue(any("missing.spa" in status for status in statuses))
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

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_apply_mmd_attributes_preserves_raw_edge_size_when_spin_unchanged(self, mock_maya_attribute_utils):
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

        mock_maya_attribute_utils.set_attribute.assert_any_call("test_material", "mmd_edge_size", 2.5, "float")

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
