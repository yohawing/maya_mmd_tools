import unittest
from unittest.mock import Mock, call, patch

from tests.common.maya_stub import install_headless_ui_stubs
from tests.common.mock_ui import attach_mocks

install_headless_ui_stubs()

from mmd_tools.ui.presenters.material_presenter import (  # noqa: E402
    MATERIAL_ASSIGNMENT_ROLE,
    MaterialPresenter,
)
from mmd_tools.ui.qt_compat import Qt  # noqa: E402
from mmd_tools.core.model_authoring_spec import (  # noqa: E402
    MmdMaterialSpec,
    MmdModelAuthoringSpec,
    MmdModelSpec,
)
from mmd_tools.core.material_read_projection import (  # noqa: E402
    MaterialAssignmentKind,
    MaterialAssignmentSummary,
    MaterialDetailProjection,
    MaterialListItemProjection,
    MaterialListProjection,
    MaterialListSemantic,
    MaterialPreviewState,
    MaterialTextureBinding,
    MaterialTextureProvenance,
    MaterialTextureSlot,
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
        coordinator.read_material_list_projection.return_value = MaterialListProjection(
            "|model_root", ()
        )
        self.mock_app_state.current_model_root = "|model_root"
        self.mock_maya_adapter.object_exists.return_value = True
        presenter = MaterialPresenter(
            self.mock_view,
            self.mock_app_state,
            maya_adapter=self.mock_maya_adapter,
            authoring_coordinator=coordinator,
        )
        return presenter, coordinator

    @staticmethod
    def _list_projection(root, *rows):
        return MaterialListProjection(
            root,
            tuple(
                MaterialListItemProjection(
                    MaterialListSemantic(index, binding, name, name_english),
                    assignment,
                )
                for index, binding, name, name_english, assignment in rows
            ),
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

        result = self.presenter.apply_changes()

        self.assertIsNone(result)
        self.mock_app_state.emit_status.assert_called_once()

    def test_authoring_selection_selects_shader_and_enables_indexed_actions(self):
        presenter, _coordinator = self._make_authoring_presenter()
        presenter._material_list_projection = self._list_projection(
            "|model_root",
            (
                3,
                "shader1",
                "Material",
                "",
                MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            ),
        )
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

        self.mock_maya_adapter.node_type.return_value = "standardSurface"
        self.mock_view.shader_outline_check.isChecked.return_value = True
        with patch(
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
        apply_outline.assert_not_called()
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

        self.presenter.apply_changes()

        coordinator.replace_material.assert_not_called()
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
        with patch.object(presenter, "load_materials") as reload_materials, patch.object(
            presenter, "_select_projected_binding"
        ) as select_projected:
            self.assertTrue(presenter.create_material())
            self.assertTrue(presenter.duplicate_material())

        coordinator.create_material.assert_called_once_with("|model_root")
        coordinator.duplicate_material.assert_called_once_with("|model_root", 4)
        self.assertEqual(reload_materials.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in select_projected.call_args_list],
            ["shader5", "shader6"],
        )
        self.mock_maya_adapter.ls.assert_not_called()

    def test_move_material_routes_exact_index_permutation(self):
        presenter, coordinator = self._make_authoring_presenter()
        presenter.current_material = "shader1"
        presenter.current_material_index = 1
        presenter._material_list_projection = self._list_projection(
            "|model_root",
            *(
                (
                    index,
                    "shader{}".format(index),
                    name,
                    "",
                    MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
                )
                for index, name in enumerate(("A", "B", "C"))
            ),
        )
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

        with patch.object(presenter, "load_materials") as reload_materials, patch.object(
            presenter, "_select_projected_binding"
        ) as select_projected:
            self.assertTrue(presenter.move_material(-1))

        coordinator.move_material_fast.assert_called_once_with("|model_root", 1, 0)
        coordinator.read_spec.assert_not_called()
        coordinator.reindex_materials.assert_not_called()
        reload_materials.assert_called_once_with()
        self.assertEqual([item.data(Qt.UserRole) for item in items], ["shader0", "shader1", "shader2"])
        select_projected.assert_called_once_with("shader1")
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

    def test_load_materials_with_no_model(self):
        """モデルが選択されていない場合のマテリアル読み込みテスト"""
        self.presenter.load_materials()

        # リストがクリアされることを確認
        self.mock_view.material_list.clear.assert_called_once()
        # 詳細が無効化されることを確認
        self.mock_view._set_details_enabled.assert_called_with(False)
        # プレースホルダーが表示されることを確認
        self.mock_view._show_placeholder.assert_called_once()

    def test_load_materials_uses_projection_for_unassigned_authoring_material(self):
        self.mock_app_state.current_model_root = "|model_root"
        self.mock_maya_adapter.object_exists.return_value = True
        coordinator = Mock()
        coordinator.read_material_list_projection.return_value = self._list_projection(
            "|model_root",
            (
                0,
                "unassigned_shader",
                "未割り当て材質",
                "Unassigned",
                MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            ),
        )
        self.presenter.authoring_coordinator = coordinator

        self.presenter.load_materials()

        coordinator.read_material_list_projection.assert_called_once_with("|model_root")
        self.mock_view.material_list.addItem.assert_called_once()
        added_item = self.mock_view.material_list.addItem.call_args.args[0]
        self.assertEqual(added_item.data(Qt.UserRole), "unassigned_shader")
        self.assertEqual(added_item.data(MATERIAL_ASSIGNMENT_ROLE), "meshes=0, faces=0")
        self.mock_maya_adapter.list_relatives.assert_not_called()
        self.mock_maya_adapter.list_connections.assert_not_called()
        self.mock_maya_adapter.sets.assert_not_called()

    def test_load_materials_survives_stale_presenter_projection_class_reference(self):
        self.mock_app_state.current_model_root = "|model_root"
        self.mock_maya_adapter.object_exists.return_value = True
        coordinator = Mock()
        projection = self._list_projection(
            "|model_root",
            (
                0,
                "shader0",
                "Material",
                "",
                MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            ),
        )
        coordinator.read_material_list_projection.return_value = projection
        stale_projection_type = type("StaleMaterialListProjection", (), {})

        with patch(
            "mmd_tools.ui.presenters.material_presenter.MaterialListProjection",
            stale_projection_type,
        ):
            presenter = MaterialPresenter(
                self.mock_view,
                self.mock_app_state,
                maya_adapter=self.mock_maya_adapter,
                authoring_coordinator=coordinator,
            )

        assert presenter._material_list_projection is projection
        self.mock_view.material_list.addItem.assert_called_once()

    def test_refresh_consumes_new_assignment_projection_atomically(self):
        self.mock_app_state.current_model_root = "|model_root"
        self.mock_maya_adapter.object_exists.return_value = True
        coordinator = Mock()
        first = self._list_projection(
            "|model_root",
            (
                0,
                "shader1",
                "Material 1",
                "Material 1 EN",
                MaterialAssignmentSummary(MaterialAssignmentKind.EXPLICIT_FACES, 1, 3),
            ),
        )
        second = self._list_projection(
            "|model_root",
            (
                0,
                "shader1",
                "Material 1",
                "Material 1 EN",
                MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            ),
        )
        coordinator.read_material_list_projection.side_effect = (first, second)
        self.presenter.authoring_coordinator = coordinator

        self.presenter.load_materials()

        item = self.mock_view.material_list.addItem.call_args.args[0]
        self.assertNotIn("meshes=", item.text())
        self.assertNotIn("faces=", item.text())
        self.assertEqual(item.data(Qt.UserRole + 2), "meshes=1, faces=3")
        self.assertNotEqual(item.toolTip(), "shader1")
        self.assertIs(self.presenter._material_list_projection, first)

        # Maya standard-set membership is changed externally; Refresh must
        # observe the new graph rather than retaining the previous summary.
        self.presenter.load_materials()
        refreshed_item = self.mock_view.material_list.addItem.call_args.args[0]
        self.assertNotIn("meshes=", refreshed_item.text())
        self.assertEqual(refreshed_item.data(Qt.UserRole + 2), "meshes=0, faces=0")
        self.assertIs(self.presenter._material_list_projection, second)

    def test_pending_dirty_refresh_defers_projection_swap_until_clean(self):
        self.mock_app_state.current_model_root = "|model_root"
        self.mock_maya_adapter.object_exists.return_value = True
        coordinator = Mock()
        first = self._list_projection("|model_root")
        second = self._list_projection(
            "|model_root",
            (
                0,
                "shader1",
                "Material 1",
                "",
                MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            ),
        )
        coordinator.read_material_list_projection.side_effect = (first, second)
        self.presenter.authoring_coordinator = coordinator
        self.presenter.load_materials()
        self.presenter._pending_refresh_generation = 7
        self.presenter.has_unsaved_changes = True

        self.assertTrue(self.presenter.refresh_for_generation(7))
        self.assertIs(self.presenter._material_list_projection, first)
        self.assertEqual(coordinator.read_material_list_projection.call_count, 1)

        self.presenter.has_unsaved_changes = False
        self.assertTrue(self.presenter.refresh_for_generation(7))
        self.assertIs(self.presenter._material_list_projection, second)
        self.assertEqual(coordinator.read_material_list_projection.call_count, 2)

    def test_projection_root_mismatch_fails_before_adding_any_row(self):
        self.mock_app_state.current_model_root = "|model_root"
        self.mock_maya_adapter.object_exists.return_value = True
        coordinator = Mock()
        coordinator.read_material_list_projection.return_value = self._list_projection(
            "|other_root",
            (
                0,
                "shader1",
                "Material 1",
                "",
                MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            ),
        )
        self.presenter.authoring_coordinator = coordinator

        self.presenter.load_materials()

        self.assertIsNone(self.presenter._material_list_projection)
        self.mock_view.material_list.addItem.assert_not_called()

    def test_projection_read_failure_clears_prior_selection_and_actions(self):
        self.mock_app_state.current_model_root = "|model_root"
        self.mock_maya_adapter.object_exists.return_value = True
        coordinator = Mock()
        coordinator.read_material_list_projection.side_effect = RuntimeError("read failed")
        self.presenter.authoring_coordinator = coordinator
        self.presenter.current_material = "staleShader"
        self.presenter.current_material_index = 3

        self.presenter.load_materials()

        self.assertIsNone(self.presenter.current_material)
        self.assertIsNone(self.presenter.current_material_index)
        self.mock_view.duplicate_btn.setEnabled.assert_called_with(False)
        self.mock_view.delete_btn.setEnabled.assert_called_with(False)
        self.mock_view.reindex_up_btn.setEnabled.assert_called_with(False)
        self.mock_view.reindex_down_btn.setEnabled.assert_called_with(False)

    @patch("mmd_tools.ui.presenters.material_presenter.logger")
    def test_load_materials_with_model(self, mock_logger):
        """モデルが選択されている場合のマテリアル読み込みテスト"""
        # モデルが存在する設定
        self.mock_app_state.current_model_root = "|test_model"
        self.mock_maya_adapter.object_exists.return_value = True
        coordinator = Mock()
        coordinator.read_material_list_projection.return_value = self._list_projection(
            "|test_model",
            (
                0,
                "mat1",
                "Material 1",
                "Material 1 EN",
                MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            ),
        )
        self.presenter.authoring_coordinator = coordinator
        # count() は addItem 後の件数を返す想定
        self.mock_view.material_list.count.return_value = 1
        self.mock_view.material_list.blockSignals.return_value = False

        self.presenter.load_materials()

        # リストがクリアされることを確認
        self.mock_view.material_list.clear.assert_called_once()
        # マテリアルがリストに追加されることを確認
        self.mock_view.material_list.addItem.assert_called()
        self.assertEqual(
            self.mock_view.material_list.blockSignals.call_args_list,
            [call(True), call(False)],
        )
        coordinator.read_material_list_projection.assert_called_once_with("|test_model")
        self.mock_maya_adapter.list_relatives.assert_not_called()
        self.mock_maya_adapter.list_connections.assert_not_called()

        # 一覧ロード詳細は DEBUG のみ（INFO には出さない）
        expected = "Loaded 1 MMD materials for model: |test_model"
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_load_materials_hides_namespace_and_path_but_preserves_full_node(self):
        material = "|root|outer:model:face_material"
        self.mock_app_state.current_model_root = "|test_model"
        self.mock_maya_adapter.object_exists.return_value = True
        coordinator = Mock()
        coordinator.read_material_list_projection.return_value = self._list_projection(
            "|test_model",
            (
                0,
                material,
                "顔材質",
                "Face",
                MaterialAssignmentSummary(MaterialAssignmentKind.UNKNOWN, 0, None),
            ),
        )
        self.presenter.authoring_coordinator = coordinator
        self.mock_view.material_list.count.return_value = 1

        self.presenter.load_materials()

        item = self.mock_view.material_list.addItem.call_args[0][0]
        self.assertEqual(item.text(), "1:顔材質（face_material） [Face]")
        self.assertEqual(item.data(Qt.UserRole), material)
        self.assertEqual(item.data(Qt.UserRole + 2), "meshes=0, faces=?")
        self.assertNotEqual(item.toolTip(), material)

    @patch("mmd_tools.ui.presenters.material_presenter.logger")
    def test_on_material_selected(self, mock_logger):
        """マテリアル選択時の処理テスト"""
        # モックアイテムを作成
        mock_item = Mock()
        mock_item.text.return_value = "1:Material 1（material1）"
        mock_item.data.side_effect = lambda role: {
            Qt.UserRole: "material1",
            Qt.UserRole + 1: 0,
        }.get(role)

        self.mock_maya_adapter.object_exists.return_value = True
        self.mock_maya_adapter.select.return_value = None
        self.presenter._material_list_projection = self._list_projection(
            "|model_root",
            (
                0,
                "material1",
                "Material 1",
                "",
                MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            ),
        )

        with patch.object(self.presenter, "load_material_properties") as load_detail:
            self.presenter.on_material_selected(mock_item, None)

        # マテリアルが選択されることを確認
        self.mock_maya_adapter.select_fast.assert_called_with("material1", replace=True)
        # 詳細が有効化されることを確認
        self.mock_view._set_details_enabled.assert_called_with(True)
        load_detail.assert_called_once_with("material1")

        # 選択ログは DEBUG のみ（INFO には出さない）
        expected = "Selected material: material1"
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_on_material_selected_rejects_forged_hidden_binding_role(self):
        item = Mock()
        item.text.return_value = "1:Forged"
        item.data.side_effect = lambda role: {
            Qt.UserRole: "foreignShader",
            Qt.UserRole + 1: 0,
        }.get(role)
        self.presenter._material_list_projection = self._list_projection(
            "|model_root",
            (
                0,
                "ownedShader",
                "Owned",
                "",
                MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0),
            ),
        )
        self.presenter.current_material = "priorShader"
        self.presenter.current_material_index = 4

        self.presenter.on_material_selected(item, None)

        self.mock_maya_adapter.select_fast.assert_not_called()
        self.mock_view._set_details_enabled.assert_called_with(False)
        self.assertIsNone(self.presenter.current_material)
        self.assertIsNone(self.presenter.current_material_index)
        self.mock_view.duplicate_btn.setEnabled.assert_called_with(False)
        self.mock_view.delete_btn.setEnabled.assert_called_with(False)
        self.mock_view.reindex_up_btn.setEnabled.assert_called_with(False)
        self.mock_view.reindex_down_btn.setEnabled.assert_called_with(False)

    def test_on_selection_changed_maya_uses_fast_selection_path(self):
        item = Mock()
        item.data.return_value = "material1"
        self.mock_view.material_list.selectedItems.return_value = [item]
        self.mock_maya_adapter.object_exists.return_value = True

        self.presenter.on_selection_changed_maya()

        self.mock_maya_adapter.select_fast.assert_called_once_with(["material1"], replace=True)
        self.mock_maya_adapter.select.assert_not_called()

    def test_selected_detail_projection_renders_semantics_provenance_and_preview(self):
        presenter, coordinator = self._make_authoring_presenter()
        assignment = MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0)
        projection = self._list_projection(
            "|model_root",
            (0, "shader", "Material", "Material", assignment),
        )
        presenter._material_list_projection = projection
        presenter.current_material = "shader"
        presenter.current_material_index = 0
        material = MmdMaterialSpec(
            "Material",
            name_english="Material EN",
            index=0,
            binding_identity="shader",
            texture_path="textures/body.png",
            resolved_texture_path="C:/model/body.png",
            sphere_texture_path="textures/sphere.spa",
            resolved_sphere_texture_path="C:/model/sphere.spa",
            draw_flags=0x10,
        )
        coordinator.read_material_detail_projection.return_value = MaterialDetailProjection(
            "|model_root",
            material,
            assignment,
            (
                MaterialTextureProvenance(
                    MaterialTextureSlot.MAIN,
                    material.texture_path,
                    material.resolved_texture_path,
                    MaterialTextureBinding(MaterialTextureSlot.MAIN, "shader.baseColor"),
                ),
                MaterialTextureProvenance(
                    MaterialTextureSlot.SPHERE,
                    material.sphere_texture_path,
                    material.resolved_sphere_texture_path,
                ),
            ),
            MaterialPreviewState("dx11Shader", False),
        )

        presenter.load_material_properties("shader")

        coordinator.read_material_detail_projection.assert_called_once_with(
            "|model_root", 0, "shader", assignment
        )
        self.mock_view.texture_path_edit.setText.assert_called_with("C:/model/body.png")
        self.mock_view.sphere_map_path_edit.setText.assert_called_with(
            "C:/model/sphere.spa"
        )
        self.mock_view.shader_outline_check.setChecked.assert_called_with(False)
        self.assertEqual(
            presenter.material_data["original_pmx_texture_path"],
            "textures/body.png",
        )
        self.mock_maya_adapter.list_connections.assert_not_called()
        self.mock_maya_adapter.attribute_exists.assert_not_called()

    def test_selected_detail_failure_clears_routing_and_disables_stale_controls(self):
        presenter, coordinator = self._make_authoring_presenter()
        assignment = MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0)
        presenter._material_list_projection = self._list_projection(
            "|model_root",
            (0, "shaderB", "B", "", assignment),
        )
        presenter.current_material = "shaderB"
        presenter.current_material_index = 0
        presenter.material_data = {"jp_name": "stale A"}
        coordinator.read_material_detail_projection.side_effect = RuntimeError("broken")

        presenter.load_material_properties("shaderB")

        self.assertIsNone(presenter.current_material)
        self.assertIsNone(presenter.current_material_index)
        self.assertEqual(presenter.material_data, {})
        self.mock_view._set_details_enabled.assert_called_with(False)
        self.mock_view.duplicate_btn.setEnabled.assert_called_with(False)

    def test_stale_model_projection_fails_before_detail_read(self):
        presenter, coordinator = self._make_authoring_presenter()
        assignment = MaterialAssignmentSummary(MaterialAssignmentKind.EMPTY, 0, 0)
        presenter._material_list_projection = self._list_projection(
            "|old_root",
            (0, "shader", "Old", "", assignment),
        )
        presenter.current_material = "shader"
        presenter.current_material_index = 0

        presenter.load_material_properties("shader")

        coordinator.read_material_detail_projection.assert_not_called()
        self.assertIsNone(presenter.current_material)
        self.mock_view._set_details_enabled.assert_called_with(False)

    def test_effective_main_texture_display_preserves_authored_source_on_apply(self):
        prior = MmdMaterialSpec(
            "Material",
            index=0,
            binding_identity="shader",
            texture_path="textures/body.png",
            resolved_texture_path="C:/model/body.png",
        )
        self.presenter.material_data = {
            "original_pmx_texture_path": "textures/body.png"
        }
        self.mock_view.texture_path_edit.text.return_value = "C:/model/body.png"

        source, resolved = self.presenter._authoring_main_texture_paths(prior)

        self.assertEqual(source, "textures/body.png")
        self.assertEqual(resolved, "C:/model/body.png")

    def test_update_color_widget_with_valid_color(self):
        """有効な色データでのカラーウィジェット更新テスト"""
        widget = Mock()
        color = (1.0, 0.5, 0.0)

        self.presenter._update_color_widget(widget, color)

        # 正しいスタイルシートが設定されることを確認
        widget.setStyleSheet.assert_called_with("background-color: rgb(255, 127, 0); border: 1px solid black;")

    def test_unchanged_dx11_outline_has_no_transaction_intent(self):
        material = MmdMaterialSpec("Material", index=0, edge_size=1.0, binding_identity="shader")
        self.presenter.current_material = "shader"
        self.presenter.material_data = {
            "shader_outline_enabled": False,
            "shader_type": "dx11Shader",
            "edge_size": 1.0,
        }
        self.mock_maya_adapter.node_type.return_value = "dx11Shader"
        self.mock_view.shader_outline_check.isChecked.return_value = False

        self.assertIsNone(self.presenter._viewport_outline_intent(material, material))
        self.mock_maya_adapter.node_type.assert_not_called()

    def test_changed_outline_uses_projected_shader_type_without_maya_read(self):
        prior = MmdMaterialSpec(
            "Material", index=0, edge_size=1.0, binding_identity="shader"
        )
        self.presenter.current_material = "shader"
        self.presenter.material_data = {
            "shader_outline_enabled": False,
            "shader_type": "dx11Shader",
        }
        self.mock_view.shader_outline_check.isChecked.return_value = True

        self.assertTrue(self.presenter._viewport_outline_intent(prior, prior))
        self.mock_maya_adapter.node_type.assert_not_called()

    def test_update_color_widget_with_invalid_color(self):
        """無効な色データでのカラーウィジェット更新テスト"""
        widget = Mock()

        # 空の色データ
        self.presenter._update_color_widget(widget, None)
        widget.setStyleSheet.assert_called_with("background-color: rgb(128, 128, 128); border: 1px solid black;")

        # 要素不足の色データ
        self.presenter._update_color_widget(widget, (1.0, 0.5))
        widget.setStyleSheet.assert_called_with("background-color: rgb(128, 128, 128); border: 1px solid black;")

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
        for item in items:
            item.data.assert_not_called()
        self.mock_maya_adapter.attribute_exists.assert_not_called()
        self.mock_maya_adapter.get_attr.assert_not_called()

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
