import unittest
from unittest.mock import Mock, patch

from tests.common.maya_stub import install_headless_ui_stubs
from tests.common.mock_ui import attach_mocks

install_headless_ui_stubs()

from mmd_tools.ui.presenters.material_presenter import MaterialPresenter  # noqa: E402
from mmd_tools.ui.qt_compat import Qt  # noqa: E402
from mmd_tools.core.constants import (  # noqa: E402
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_ORIGINAL_TEXTURE_PATH,
    ATTR_MMD_SPHERE_PATH,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_SHADER_OUTLINE_ENABLED,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)
from mmd_tools.core.model_authoring_spec import (  # noqa: E402
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
)
from mmd_tools.converters.material_shader_parameters import ATTR_MMD_DIFFUSE_ALPHA  # noqa: E402


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
                "toon_sharing_check",
                "toon_texture_path_edit",
                "toon_texture_index_spin",
                "diffuse_color_widget",
                "specular_color_widget",
                "ambient_color_widget",
                "edge_color_widget",
                "specular_coefficient_spin",
                "transparency_spin",
                "edge_size_spin",
                "search_edit",
                "refresh_btn",
                "create_btn",
                "duplicate_btn",
                "delete_btn",
                "reindex_up_btn",
                "reindex_down_btn",
                "apply_btn",
                "reset_btn",
                "both_face_check",
                "ground_shadow_check",
                "self_shadow_map_check",
                "self_shadow_check",
                "edge_draw_check",
                "shader_outline_check",
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

    def _make_authoring_presenter(self):
        coordinator = Mock()
        self.mock_app_state.current_model_root = "|model_root"
        self.mock_maya_adapter.object_exists.return_value = True
        presenter = MaterialPresenter(
            self.mock_view,
            self.mock_app_state,
            maya_adapter=self.mock_maya_adapter,
            authoring_coordinator=coordinator,
        )
        return presenter, coordinator

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
        self.mock_view.toon_sharing_check.isChecked.return_value = True
        self.mock_view.toon_texture_path_edit.text.return_value = ""
        self.mock_view.toon_texture_index_spin.value.return_value = -1
        self.mock_view.edge_size_spin.value.return_value = 1.5
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

    def test_authoring_buttons_fail_closed_without_injected_coordinator(self):
        self.mock_view.create_btn.setEnabled.assert_called_with(False)
        self.mock_view.duplicate_btn.setEnabled.assert_called_with(False)
        self.mock_view.delete_btn.setEnabled.assert_called_with(False)
        self.mock_view.reindex_up_btn.setEnabled.assert_called_with(False)
        self.mock_view.reindex_down_btn.setEnabled.assert_called_with(False)

    def test_apply_changes_fails_closed_without_coordinator(self):
        self.presenter.current_material = "legacy_material"

        with patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils") as attrs:
            result = self.presenter.apply_changes()

        self.assertIsNone(result)
        attrs.set_attribute.assert_not_called()
        attrs.set_custom_attributes.assert_not_called()
        self.mock_app_state.emit_status.assert_called_once()

    def test_authoring_selection_selects_shader_and_enables_indexed_actions(self):
        presenter, _coordinator = self._make_authoring_presenter()
        item = Mock()
        item.text.return_value = "1:Material"
        item.data.side_effect = lambda role: "shader1" if role == Qt.UserRole else 3

        with patch.object(presenter, "load_material_properties") as load_properties:
            presenter.on_material_selected(item, None)

        self.assertEqual(presenter.current_material_index, 3)
        self.mock_maya_adapter.select_fast.assert_called_once_with("shader1", replace=True)
        load_properties.assert_called_once_with("shader1")
        self.mock_view.duplicate_btn.setEnabled.assert_called_with(True)
        self.mock_view.delete_btn.setEnabled.assert_called_with(True)
        self.mock_maya_adapter.hyper_shade.assert_not_called()

    def test_authoring_apply_replaces_complete_spec_without_direct_maya_writes(self):
        """Authoring Apply routes one complete immutable replacement through the coordinator."""
        from dataclasses import replace

        prior = MmdMaterialSpec(
            name="旧い材質",
            name_english="Old",
            index=2,
            diffuse=(0.1, 0.2, 0.3, 0.9),
            specular=(0.2, 0.3, 0.4),
            specular_coefficient=0.25,
            ambient=(0.05, 0.06, 0.07),
            draw_flags=0x03,
            edge_color=(0.1, 0.1, 0.1, 0.7),
            edge_size=1.25,
            texture_path="textures/顔.png",
            resolved_texture_path=r"C:\old\顔.png",
            sphere_texture_path="sphere.spa",
            resolved_sphere_texture_path=r"C:\old\sphere.spa",
            sphere_mode=1,
            shared_toon=False,
            toon_texture_index=2,
            toon_texture_path="toon.png",
            resolved_toon_texture_path=r"C:\old\toon.png",
            memo="保持するメモ",
            binding_identity="shader|材質",
        )
        current = MmdModelAuthoringSpec(
            model=MmdModelSpec("モデル", "Model", "", ""),
            materials=(prior,),
        )

        class Coordinator:
            def __init__(self):
                self.current = current
                self.read_calls = []
                self.replace_calls = []

            def read_spec(self, root):
                self.read_calls.append(root)
                return self.current

            def replace_material(self, root, material):
                self.replace_calls.append((root, material))
                self.current = replace(self.current, materials=(material,))
                return self.current

        coordinator = Coordinator()
        self.presenter.authoring_coordinator = coordinator
        self.presenter.current_material = "shader|材質"
        self.presenter.current_material_index = 2
        self.presenter.app_state.current_model_root = "|model_root"
        self.presenter.material_data = {
            "diffuse": (0.8, 0.7, 0.6),
            "specular": (0.5, 0.4, 0.3),
            "ambient": (0.2, 0.1, 0.0),
            "edge_color": (0.9, 0.8, 0.7),
            "edge_alpha": 0.35,
            "original_pmx_texture_path": "textures/顔.png",
        }
        self.mock_view.material_jp_name_edit.text.return_value = "新しい材質_日本語"
        self.mock_view.material_en_name_edit.text.return_value = "New Material"
        self.mock_view.transparency_spin.value.return_value = 0.25
        self.mock_view.specular_coefficient_spin.value.return_value = 0.75
        self.mock_view.edge_size_spin.value.return_value = 1.5
        self.mock_view.texture_path_edit.text.return_value = r"C:\new\顔.png"
        self.mock_view.sphere_map_path_edit.text.return_value = "sphere-new.spa"
        self.mock_view.sphere_mode_combo.currentIndex.return_value = 2
        self.mock_view.toon_sharing_check.isChecked.return_value = False
        self.mock_view.toon_texture_index_spin.value.return_value = 5
        self.mock_view.toon_texture_path_edit.text.return_value = "toon-new.png"
        for bit_name, value in (
            ("both_face_check", True),
            ("ground_shadow_check", False),
            ("self_shadow_map_check", True),
            ("self_shadow_check", False),
            ("edge_draw_check", True),
            ("vertex_color_check", True),
            ("point_draw_check", False),
            ("line_draw_check", True),
        ):
            getattr(self.mock_view, bit_name).isChecked.return_value = value

        self.mock_maya_adapter.node_type.return_value = "dx11Shader"
        self.mock_view.shader_outline_check.isChecked.return_value = True
        with patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils") as attrs, patch(
            "mmd_tools.converters.mesh_converter.apply_shader_outline"
        ) as apply_outline:
            result = self.presenter.apply_changes()

        self.assertIs(result, coordinator.current)
        self.assertEqual(coordinator.read_calls, ["|model_root", "|model_root"])
        self.assertEqual(len(coordinator.replace_calls), 1)
        root, replacement = coordinator.replace_calls[0]
        self.assertEqual(root, "|model_root")
        self.assertEqual(replacement.index, prior.index)
        self.assertEqual(replacement.binding_identity, prior.binding_identity)
        self.assertEqual(replacement.name, "新しい材質_日本語")
        self.assertEqual(replacement.diffuse, (0.8, 0.7, 0.6, 0.75))
        self.assertEqual(replacement.specular, (0.5, 0.4, 0.3))
        self.assertEqual(replacement.ambient, (0.2, 0.1, 0.0))
        self.assertEqual(replacement.draw_flags, 0xB5)
        self.assertEqual(replacement.edge_color, (0.9, 0.8, 0.7, 0.35))
        self.assertEqual(replacement.texture_path, prior.texture_path)
        self.assertEqual(replacement.resolved_texture_path, r"C:\new\顔.png")
        self.assertEqual(replacement.sphere_texture_path, "sphere-new.spa")
        self.assertIsNone(replacement.resolved_sphere_texture_path)
        self.assertEqual(replacement.toon_texture_path, "toon-new.png")
        self.assertIsNone(replacement.resolved_toon_texture_path)
        self.assertEqual(replacement.toon_texture_index, 5)
        self.assertEqual(replacement.memo, prior.memo)
        attrs.set_attribute.assert_not_called()
        attrs.set_custom_attributes.assert_not_called()
        apply_outline.assert_called_once_with("shader|材質", True, 1.5)
        self.assertFalse(self.presenter.has_unsaved_changes)
        self.assertEqual(self.presenter.material_data["_authoring_fingerprint"], result.fingerprint())

    def test_authoring_apply_invalid_controls_do_not_write_or_call_replace(self):
        """Malformed UI input fails before the coordinator transaction boundary."""
        prior = MmdMaterialSpec(name="Material", index=0, binding_identity="shader")
        current = MmdModelAuthoringSpec(
            model=MmdModelSpec("Model", "Model", "", ""), materials=(prior,)
        )
        coordinator = Mock()
        coordinator.read_spec.return_value = current
        self.presenter.authoring_coordinator = coordinator
        self.presenter.current_material = "shader"
        self.presenter.current_material_index = 0
        self.presenter.app_state.current_model_root = "|model_root"
        self.presenter.material_data = {"diffuse": (1.0, 0.5)}

        with patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils") as attrs:
            self.presenter.apply_changes()

        coordinator.replace_material.assert_not_called()
        attrs.set_attribute.assert_not_called()
        attrs.set_custom_attributes.assert_not_called()
        self.assertTrue(self.mock_app_state.emit_status.called)

    def test_create_and_duplicate_do_not_read_or_forward_maya_selection(self):
        presenter, coordinator = self._make_authoring_presenter()
        presenter.current_material_index = 4
        coordinator.create_material.return_value = MmdMaterialSpec(
            "Created", name_english="Created", index=5, binding_identity="shader5"
        )
        coordinator.duplicate_material.return_value = MmdMaterialSpec(
            "Copy", name_english="Copy", index=6, binding_identity="shader6"
        )
        self.mock_maya_adapter.ls.return_value = ["|model_root|mesh.f[2]"]
        with patch.object(presenter, "_append_material_row") as append_row:
            self.assertTrue(presenter.create_material())
            self.assertTrue(presenter.duplicate_material())

        coordinator.create_material.assert_called_once_with("|model_root")
        coordinator.duplicate_material.assert_called_once_with("|model_root", 4)
        self.assertEqual(append_row.call_count, 2)
        self.mock_maya_adapter.ls.assert_not_called()

    def test_move_material_routes_exact_index_permutation(self):
        presenter, coordinator = self._make_authoring_presenter()
        presenter.current_material = "shader1"
        presenter.current_material_index = 1
        coordinator.read_spec.return_value = MmdModelAuthoringSpec(
            model=MmdModelSpec("Model"),
            materials=(
                MmdMaterialSpec("A", index=0, binding_identity="shader0"),
                MmdMaterialSpec("B", index=1, binding_identity="shader1"),
                MmdMaterialSpec("C", index=2, binding_identity="shader2"),
            ),
        )
        class Item:
            def __init__(self, label, binding):
                self._label = label
                self._data = {Qt.UserRole: binding}

            def data(self, role):
                return self._data.get(role)

            def setData(self, role, value):
                self._data[role] = value

            def text(self):
                return self._label

            def setText(self, value):
                self._label = value

        items = [Item("1:A", "shader0"), Item("2:B", "shader1"), Item("3:C", "shader2")]
        presenter.view.material_list.count.return_value = 3
        presenter.view.material_list.item.side_effect = lambda row: items[row]
        presenter.view.material_list.takeItem.side_effect = lambda row: items.pop(row)
        presenter.view.material_list.insertItem.side_effect = lambda row, item: items.insert(row, item)
        presenter.view.material_list.setCurrentItem.side_effect = lambda item: setattr(
            presenter.view.material_list, "selected", item
        )

        with patch.object(presenter, "load_materials") as reload_materials:
            self.assertTrue(presenter.move_material(-1))

        coordinator.move_material_fast.assert_called_once_with("|model_root", 1, 0)
        coordinator.read_spec.assert_not_called()
        coordinator.reindex_materials.assert_not_called()
        reload_materials.assert_not_called()
        self.assertEqual([item.data(Qt.UserRole) for item in items], ["shader1", "shader0", "shader2"])
        self.assertEqual(presenter.view.material_list.selected.data(Qt.UserRole), "shader1")
        self.assertEqual(presenter.current_material_index, 0)

    @patch("mmd_tools.ui.qt_compat.QMessageBox.question")
    def test_delete_requires_confirmation_and_routes_index(self, question):
        from mmd_tools.ui.qt_compat import QMessageBox

        presenter, coordinator = self._make_authoring_presenter()
        presenter.current_material_index = 5
        question.return_value = QMessageBox.No
        self.assertFalse(presenter.delete_material())
        coordinator.delete_material.assert_not_called()

        question.return_value = QMessageBox.Yes
        with patch.object(presenter, "load_materials"):
            self.assertTrue(presenter.delete_material())
        coordinator.delete_material.assert_called_once_with("|model_root", 5)

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

    @patch("mmd_tools.ui.presenters.material_presenter.list_model_registry_members_from_adapter")
    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_load_materials_uses_registry_for_unassigned_authoring_material(
        self, mock_maya_attribute_utils, registry_members
    ):
        self.mock_app_state.current_model_root = "|model_root"
        self.mock_maya_adapter.object_exists.return_value = True
        registry_members.return_value = ["unassigned_shader"]
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: {
            ATTR_MMD_MATERIAL_NAME: "未割り当て材質",
            ATTR_MMD_MATERIAL_NAME_EN: "Unassigned",
        }.get(attr)

        self.presenter.load_materials()

        self.mock_maya_adapter.list_relatives.assert_called_once_with(
            "|model_root",
            allDescendents=True,
            fullPath=True,
            type="mesh",
        )
        self.mock_view.material_list.addItem.assert_called_once()
        added_item = self.mock_view.material_list.addItem.call_args.args[0]
        self.assertEqual(added_item.data(Qt.UserRole), "unassigned_shader")

    @patch("mmd_tools.ui.presenters.material_presenter.list_model_registry_members_from_adapter")
    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_refresh_projects_standard_set_face_membership_into_material_row(
        self, mock_maya_attribute_utils, registry_members
    ):
        self.mock_app_state.current_model_root = "|model_root"
        self.mock_maya_adapter.object_exists.return_value = True
        registry_members.return_value = ["shader1"]
        self.mock_maya_adapter.list_relatives.return_value = ["|model_root|mesh|meshShape"]

        def list_connections(node, **kwargs):
            if node == "shader1" and kwargs.get("type") == "shadingEngine":
                return ["|model_root|meshSG"]
            return []

        self.mock_maya_adapter.list_connections.side_effect = list_connections
        self.mock_maya_adapter.sets.return_value = ["|model_root|mesh.f[0:2]"]
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: {
            ATTR_MMD_MATERIAL_NAME: "Material 1",
            ATTR_MMD_MATERIAL_NAME_EN: "Material 1 EN",
        }.get(attr, "")

        self.presenter.load_materials()

        item = self.mock_view.material_list.addItem.call_args.args[0]
        self.assertNotIn("meshes=", item.text())
        self.assertNotIn("faces=", item.text())
        self.assertEqual(item.data(Qt.UserRole + 2), "meshes=1, faces=3")
        self.assertEqual(item.toolTip(), "shader1")
        self.mock_maya_adapter.sets.assert_called_once_with("|model_root|meshSG", query=True)

        # Maya standard-set membership is changed externally; Refresh must
        # observe the new graph rather than retaining the previous summary.
        self.mock_maya_adapter.sets.return_value = []
        self.presenter.load_materials()
        refreshed_item = self.mock_view.material_list.addItem.call_args.args[0]
        self.assertNotIn("meshes=", refreshed_item.text())
        self.assertEqual(refreshed_item.data(Qt.UserRole + 2), "meshes=0, faces=0")

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
        self.mock_maya_adapter.attribute_exists.side_effect = (
            lambda attr, _node: attr == ATTR_MMD_MATERIAL_NAME
        )
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
            fullPath=True,
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

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_load_materials_hides_namespace_and_path_but_preserves_full_node(
        self,
        mock_maya_attribute_utils,
    ):
        material = "|root|outer:model:face_material"
        self.mock_app_state.current_model_root = "test_model"
        self.mock_maya_adapter.object_exists.return_value = True
        self.mock_maya_adapter.list_relatives.return_value = ["meshShape"]
        self.mock_maya_adapter.list_connections.side_effect = lambda nodes, **kwargs: (
            ["SG"] if kwargs.get("type") == "shadingEngine" else [material]
        )
        self.mock_maya_adapter.ls.return_value = [material]
        self.mock_maya_adapter.attribute_exists.side_effect = (
            lambda attr, _node: attr == ATTR_MMD_MATERIAL_NAME
        )
        mock_maya_attribute_utils.get_attribute.side_effect = lambda node, attr: {
            ATTR_MMD_MATERIAL_NAME: "顔材質",
            ATTR_MMD_MATERIAL_NAME_EN: "Face",
        }.get(attr, "")
        self.mock_view.material_list.count.return_value = 1

        self.presenter.load_materials()

        item = self.mock_view.material_list.addItem.call_args[0][0]
        self.assertEqual(item.text(), "1:顔材質（face_material） [Face]")
        self.assertEqual(item.data(Qt.UserRole), material)
        self.assertEqual(item.data(Qt.UserRole + 2), "meshes=0, faces=?")
        self.assertEqual(item.toolTip(), material)

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
        self.mock_maya_adapter.select_fast.assert_called_with("material1", replace=True)
        # 詳細が有効化されることを確認
        self.mock_view._set_details_enabled.assert_called_with(True)

        # 選択ログは DEBUG のみ（INFO には出さない）
        expected = "Selected material: material1"
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_on_selection_changed_maya_uses_fast_selection_path(self):
        item = Mock()
        item.data.return_value = "material1"
        self.mock_view.material_list.selectedItems.return_value = [item]
        self.mock_maya_adapter.object_exists.return_value = True

        self.presenter.on_selection_changed_maya()

        self.mock_maya_adapter.select_fast.assert_called_once_with(["material1"], replace=True)
        self.mock_maya_adapter.select.assert_not_called()

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
                ATTR_MMD_SHADER_OUTLINE_ENABLED,
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
            ATTR_MMD_SHADER_OUTLINE_ENABLED: True,
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
        self.mock_view.shader_outline_check.setChecked.assert_called_with(True)

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

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_standard_surface_load_prefers_canonical_mmd_colors(self, mock_maya_attribute_utils):
        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        canonical = {
            ATTR_MMD_DIFFUSE_COLOR: (0.1, 0.2, 0.3),
            ATTR_MMD_SPECULAR_COLOR: (0.4, 0.5, 0.6),
            ATTR_MMD_AMBIENT_COLOR: (0.7, 0.8, 0.9),
        }
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr in canonical
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: canonical.get(attr)

        self.presenter.load_material_properties("standard_material")

        self.assertEqual(self.presenter.material_data["diffuse"], canonical[ATTR_MMD_DIFFUSE_COLOR])
        self.assertEqual(self.presenter.material_data["specular"], canonical[ATTR_MMD_SPECULAR_COLOR])
        self.assertEqual(self.presenter.material_data["ambient"], canonical[ATTR_MMD_AMBIENT_COLOR])
        queried = [call.args[1] for call in mock_maya_attribute_utils.get_attribute.call_args_list]
        self.assertNotIn("baseColor", queried)
        self.assertNotIn("specularColor", queried)
        self.assertNotIn("ambientColor", queried)

    @patch("mmd_tools.ui.presenters.material_presenter.maya_attribute_utils")
    def test_standard_surface_load_prefers_canonical_mmd_diffuse_alpha(
        self, mock_maya_attribute_utils
    ):
        """StandardSurface の Reset でも canonical diffuse alpha を復元する。"""
        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        canonical = {ATTR_MMD_DIFFUSE_ALPHA: 0.75}
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr in canonical
        mock_maya_attribute_utils.get_attribute.side_effect = lambda _node, attr: canonical.get(attr)

        self.presenter.load_material_properties("standard_material")

        self.assertEqual(self.presenter.material_data["transparency"], 0.25)
        self.mock_view.transparency_spin.setValue.assert_called_with(0.25)

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
    def test_legacy_undriven_hardware_color_remains_loadable(self, mock_maya_attribute_utils):
        self.mock_maya_adapter.attribute_exists.side_effect = lambda attr, _node: attr == "DiffuseColorRGB"
        self.mock_maya_adapter.list_connections.return_value = []
        mock_maya_attribute_utils.get_attribute.return_value = (0.3, 0.4, 0.5)

        value, owned = self.presenter._load_base_value(
            "legacy", "diffuse_color", ("DiffuseColorRGB",), (0.5, 0.5, 0.5)
        )

        self.assertEqual(value, (0.3, 0.4, 0.5))
        self.assertTrue(owned)

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

    @patch("mmd_tools.ui.presenters.material_presenter.QColorDialog")
    def test_pick_edge_color_stores_apply_key(self, mock_color_dialog):
        """Edge Color は Apply が読む edge_color キーへ一時保存する。"""
        self.presenter.current_material = "test_material"
        self.presenter.material_data = {"edge_color": (0.1, 0.2, 0.3)}

        mock_color = Mock()
        mock_color.isValid.return_value = True
        mock_color.red.return_value = 80
        mock_color.green.return_value = 90
        mock_color.blue.return_value = 100
        mock_color_dialog.getColor.return_value = mock_color

        self.presenter.pick_color("edge")

        self.assertEqual(
            self.presenter.material_data["edge_color"],
            (80 / 255.0, 90 / 255.0, 100 / 255.0),
        )
        self.assertNotIn("edge", self.presenter.material_data)
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
