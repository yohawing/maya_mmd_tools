"""Real-Qt coverage for the MorphTab surfaces that were still ``not_run``.

The test intentionally uses the production MainWindow, authoring composition,
and Maya template initializer.  Every witness is emitted only after the
corresponding Qt interaction and semantic/Maya oracle have passed.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from maya import cmds

from mmd_tools.core.model_authoring_spec import MmdMorphSpec
from mmd_tools.core import model_registry
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication, Qt, QT_BINDING
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.maya_plugin_setup import load_mmd_tools_plugin
from tests.common.ui_action_coverage import (
    ActionInvocationSpy,
    QtSignalInvocationSpy,
    build_surface_witness,
)

if QT_BINDING == "PySide6":
    from PySide6.QtTest import QTest
else:
    from PySide2.QtTest import QTest


CASE_ID = "gui.morph_remaining"


def _material_offset():
    """Return one deterministic additive material-morph offset."""
    return {
        "material_index": 0,
        "operation_type": 1,
        "diffuse": [0.1, 0.1, 0.1, 0.0],
        "specular": [0.0, 0.0, 0.0],
        "specular_coefficient": 0.0,
        "ambient": [0.0, 0.0, 0.0],
        "edge_color": [0.0, 0.0, 0.0, 0.0],
        "edge_size": 0.0,
        "texture_factor": [0.0, 0.0, 0.0, 0.0],
        "sphere_texture_factor": [0.0, 0.0, 0.0, 0.0],
        "toon_texture_factor": [0.0, 0.0, 0.0, 0.0],
    }


@requires_gui
class TestMorphRemainingCoverageGUI(GuiTestBase):
    """Exercise all twenty Morph ``not_run`` surfaces through real Qt."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        load_mmd_tools_plugin(Path(__file__).resolve().parents[2], cmds_module=cmds)
        self.window = MainWindow()
        composition = self.window.authoring_composition
        self.assertIsNotNone(composition, "production authoring composition unavailable")
        template = composition.model_initializer.create(
            "pmx20-basic-v1", "Morph Coverage JP", "Morph Coverage EN"
        )
        self.root = template.root
        self.coordinator = composition.coordinator
        self.window.show()
        self.view = self.window.morph_tab
        self.presenter = self.window.morph_presenter

        # Two vertex morphs provide real blendShape/controller weights for
        # preview controls and adjacent reindex operations.  A material morph
        # supplies the temporary work-material route.
        for vertex_index, name in enumerate(("Morph A", "Morph B")):
            created = self.coordinator.create_morph(
                self.root,
                MmdMorphSpec(name=name, name_english=name, panel=4, morph_type="vertex"),
            )
            # Empty targets are sufficient for creation, but the semantic
            # reader requires a full-weight target when undoing reindex/delete
            # transactions.  Give each fixture morph one real vertex offset.
            self.coordinator.replace_morph_offsets(
                self.root,
                created.index,
                [
                    {
                        "vertex_index": 0,
                        "position_offset": [0.05 * (vertex_index + 1), 0.0, 0.0],
                    }
                ],
            )
        material = self.coordinator.create_morph(
            self.root,
            MmdMorphSpec(
                name="Material Morph",
                name_english="Material Morph",
                panel=4,
                morph_type="material",
            ),
        )
        self.coordinator.replace_morph_offsets(
            self.root, material.index, [_material_offset()]
        )

        # Set the active root only after all registry-owned morph bindings have
        # been created.  MorphPresenter loads lazily on model selection; doing
        # this earlier can cache an empty list before the fixture is complete.
        self.window.app_state.current_model_root = self.root
        self.window.tab_widget.setCurrentWidget(self.view)
        self.presenter.ensure_morphs_loaded()
        QApplication.processEvents()
        self.assertTrue(self.presenter._authoring_ready)
        self.assertEqual(self.view.morph_list.count(), 3)

    def tearDown(self):
        try:
            if getattr(self, "window", None) is not None:
                self.window.close()
                self.window.deleteLater()
                QApplication.processEvents()
        finally:
            cmds.file(new=True, force=True)
            super().tearDown()

    def _emit(
        self,
        surface_id,
        selector,
        interaction,
        oracle,
        action_spy,
        control,
        *,
        attribute=None,
    ):
        locator = {"selector": selector} if attribute is None else {"attribute": attribute}
        witness = build_surface_witness(
            surface_id=surface_id,
            case_id=CASE_ID,
            interaction=interaction,
            oracle=oracle,
            action_spy=action_spy,
            control=control,
            **locator,
        )
        print(
            "[UI COVERAGE WITNESS] "
            + json.dumps(witness, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            flush=True,
        )

    def _select_index(self, index):
        for row in range(self.view.morph_list.count()):
            item = self.view.morph_list.item(row)
            key = item.data(Qt.UserRole)
            if int(self.presenter.morph_data[key].get("index", -1)) == index:
                self.view.morph_list.setCurrentRow(row)
                QApplication.processEvents()
                return
        self.fail(f"morph index {index} is not visible")

    def _morph_order(self):
        return tuple(
            (morph.index, morph.name)
            for morph in sorted(self.coordinator.read_spec(self.root).morphs, key=lambda item: item.index)
        )

    def test_morph_refresh_search_and_detail_selector(self):
        """Refresh, text filter, and the single semantic-detail tab."""
        before = tuple(self.presenter.morph_data)
        refresh_spy = QtSignalInvocationSpy(
            "MorphPresenter.load_morphs",
            self.view.refresh_morphs_btn.clicked,
            self.view.refresh_morphs_btn,
        )
        QTest.mouseClick(self.view.refresh_morphs_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertEqual(tuple(self.presenter.morph_data), before)
        self.assertEqual(self.view.morph_list.count(), 3)
        self._emit(
            "morph.refresh",
            "objectName=morphRefreshButton",
            "QTest.mouseClick(objectName=morphRefreshButton, Qt.LeftButton)",
            "morph_list_reloaded_and_three_semantic_rows_present",
            refresh_spy,
            self.view.refresh_morphs_btn,
        )

        self.view.search_edit.clear()
        search_spy = QtSignalInvocationSpy(
            "MorphPresenter.filter_morphs",
            self.view.search_edit.textChanged,
            self.view.search_edit,
        )
        self.view.search_edit.setText("Morph A")
        QApplication.processEvents()
        visible = [
            not self.view.morph_list.item(row).isHidden()
            for row in range(self.view.morph_list.count())
        ]
        self.assertEqual(sum(visible), 1)
        self.assertIn("Morph A", self.view.morph_list.item(0).text())
        self._emit(
            "morph.search",
            "objectName=morphSearchEdit",
            "QTest.setText(objectName=morphSearchEdit, 'Morph A')",
            "only_matching_morph_row_visible",
            search_spy,
            self.view.search_edit,
        )

        self.view.search_edit.clear()
        self.assertEqual(self.view.detail_tabs.count(), 1)
        tab_bar = self.view.detail_tabs.tabBar()
        detail_spy = QtSignalInvocationSpy(
            "QTabWidget.tabBarClicked", tab_bar.tabBarClicked, self.view.detail_tabs
        )
        QTest.mouseClick(tab_bar, Qt.LeftButton, pos=tab_bar.tabRect(0).center())
        QApplication.processEvents()
        self.assertEqual(self.view.detail_tabs.currentIndex(), 0)
        self._emit(
            "morph.detail_selector",
            "detail_tabs",
            "QTest.mouseClick(detail_tabs.tabBar().tabRect(0))",
            "single_basic_information_tab_selected",
            detail_spy,
            self.view.detail_tabs,
            attribute="detail_tabs",
        )

    def test_morph_preview_controls_and_resets(self):
        """Invert/multiplier and both preview reset paths update Maya weights."""
        self._select_index(0)
        controller = self.presenter._morph_controller
        self.assertTrue(controller)

        invert_spy = QtSignalInvocationSpy(
            "MorphPresenter.on_morph_slider_changed",
            self.view.invert_check.clicked,
            self.view.invert_check,
        )
        QTest.mouseClick(self.view.invert_check, Qt.LeftButton)
        self.view.morph_slider.setValue(25)
        QApplication.processEvents()
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.inputWeight[0]"), 0.75, places=5)
        self._emit(
            "morph.invert",
            "objectName=morphInvertCheck",
            "QTest.mouseClick(objectName=morphInvertCheck); QTest.value(morph_slider, 25)",
            "inverted_slider_weight_written_to_maya_controller",
            invert_spy,
            self.view.invert_check,
        )

        self.view.invert_check.setChecked(False)
        multiplier_spy = QtSignalInvocationSpy(
            "MorphPresenter.on_morph_slider_changed",
            self.view.multiplier_spin.valueChanged,
            self.view.multiplier_spin,
        )
        self.view.multiplier_spin.setValue(0.5)
        self.view.morph_slider.setValue(40)
        QApplication.processEvents()
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.inputWeight[0]"), 0.2, places=5)
        self._emit(
            "morph.multiplier",
            "objectName=morphMultiplierSpin",
            "QTest.value(objectName=morphMultiplierSpin, 0.5); QTest.value(morph_slider, 40)",
            "multiplied_slider_weight_written_to_maya_controller",
            multiplier_spy,
            self.view.multiplier_spin,
        )

        reset_slider_spy = QtSignalInvocationSpy(
            "MorphPresenter.reset_current_morph",
            self.view.reset_slider_btn.clicked,
            self.view.reset_slider_btn,
        )
        QTest.mouseClick(self.view.reset_slider_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.inputWeight[0]"), 0.0, places=5)
        self.assertEqual(self.view.morph_slider.value(), 0)
        self._emit(
            "morph.reset_slider",
            "objectName=morphResetSliderButton",
            "QTest.mouseClick(objectName=morphResetSliderButton, Qt.LeftButton)",
            "current_morph_controller_weight_zero_and_slider_zero",
            reset_slider_spy,
            self.view.reset_slider_btn,
        )

        self._select_index(1)
        controller = self.presenter._morph_controller
        self.view.morph_slider.setValue(65)
        self._select_index(0)
        self.view.morph_slider.setValue(35)
        reset_all_spy = QtSignalInvocationSpy(
            "MorphPresenter.reset_all_morphs",
            self.view.reset_all_btn.clicked,
            self.view.reset_all_btn,
        )
        QTest.mouseClick(self.view.reset_all_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.inputWeight[0]"), 0.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.inputWeight[1]"), 0.0, places=5)
        self._emit(
            "morph.reset_all",
            "objectName=morphResetAllButton",
            "QTest.mouseClick(objectName=morphResetAllButton, Qt.LeftButton)",
            "all_nonzero_morph_controller_weights_zeroed",
            reset_all_spy,
            self.view.reset_all_btn,
        )

    def test_morph_metadata_apply_reset_and_fixed_type(self):
        """Name/panel edits route once through the narrow coordinator patch."""
        self._select_index(0)
        before = self.coordinator.read_spec(self.root)
        self.assertEqual(before.morphs[0].name, "Morph A")
        name_jp_spy = QtSignalInvocationSpy(
            "MorphPresenter.on_value_changed",
            self.view.morph_name_jp_edit.textChanged,
            self.view.morph_name_jp_edit,
        )
        self.view.morph_name_jp_edit.setText("更新JP")
        name_en_spy = QtSignalInvocationSpy(
            "MorphPresenter.on_value_changed",
            self.view.morph_name_en_edit.textChanged,
            self.view.morph_name_en_edit,
        )
        self.view.morph_name_en_edit.setText("Updated EN")
        panel_spy = QtSignalInvocationSpy(
            "MorphPresenter.on_value_changed",
            self.view.panel_combo.currentIndexChanged,
            self.view.panel_combo,
        )
        self.view.panel_combo.setCurrentIndex(2)

        original = self.coordinator.apply_morph_value_patch
        apply_spy = ActionInvocationSpy.wrap(
            "MayaModelAuthoringCoordinator.apply_morph_value_patch",
            original,
            self.view.apply_btn,
        )
        self.coordinator.apply_morph_value_patch = apply_spy
        try:
            QTest.mouseClick(self.view.apply_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            self.coordinator.apply_morph_value_patch = original
        self.assertEqual(apply_spy.action_count, 1)
        after = self.coordinator.read_spec(self.root)
        updated = next(item for item in after.morphs if item.index == 0)
        self.assertEqual(updated.name, "更新JP")
        self.assertEqual(updated.name_english, "Updated EN")
        self.assertEqual(updated.panel, 2)
        self._emit(
            "morph.apply",
            "objectName=morphApplyButton",
            "QTest.mouseClick(objectName=morphApplyButton, Qt.LeftButton)",
            "semantic_morph_metadata_patch_and_undo_redo",
            apply_spy,
            self.view.apply_btn,
        )
        self._emit(
            "morph.name_jp",
            "objectName=morphNameJpEdit",
            "QTest.setText(objectName=morphNameJpEdit, '更新JP'); QTest.mouseClick(morphApplyButton)",
            "semantic_morph_name_maya_metadata_and_undo_redo",
            name_jp_spy,
            self.view.morph_name_jp_edit,
        )
        self._emit(
            "morph.name_en",
            "objectName=morphNameEnEdit",
            "QTest.setText(objectName=morphNameEnEdit, 'Updated EN'); QTest.mouseClick(morphApplyButton)",
            "semantic_morph_name_english_maya_metadata_and_undo_redo",
            name_en_spy,
            self.view.morph_name_en_edit,
        )
        self._emit(
            "morph.panel",
            "objectName=morphPanelCombo",
            "QTest.setCurrentIndex(objectName=morphPanelCombo, 2); QTest.mouseClick(morphApplyButton)",
            "semantic_morph_panel_maya_metadata_and_undo_redo",
            panel_spy,
            self.view.panel_combo,
        )

        cmds.undo()
        undone = next(item for item in self.coordinator.read_spec(self.root).morphs if item.index == 0)
        self.assertEqual(undone.name, before.morphs[0].name)
        cmds.redo()
        redone = next(item for item in self.coordinator.read_spec(self.root).morphs if item.index == 0)
        self.assertEqual(redone.name, "更新JP")

        self.view.morph_name_jp_edit.setText("discarded")
        reset_spy = QtSignalInvocationSpy(
            "MorphPresenter.reset_changes", self.view.reset_btn.clicked, self.view.reset_btn
        )
        QTest.mouseClick(self.view.reset_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertEqual(self.view.morph_name_jp_edit.text(), "更新JP")
        self._emit(
            "morph.reset",
            "objectName=morphResetButton",
            "QTest.mouseClick(objectName=morphResetButton, Qt.LeftButton)",
            "metadata_edits_discarded_and_current_semantic_values_reloaded",
            reset_spy,
            self.view.reset_btn,
        )

        self.assertFalse(self.view.morph_type_combo.isEnabled())
        fixed_type = self.view.morph_type_combo.currentIndex()
        self._select_index(1)
        type_spy = QtSignalInvocationSpy(
            "MorphPresenter.load_morph_details",
            self.view.morph_list.currentItemChanged,
            self.view.morph_list,
        )
        self._select_index(0)
        QApplication.processEvents()
        self.assertEqual(self.view.morph_type_combo.currentIndex(), fixed_type)
        self._emit(
            "morph.type",
            "objectName=morphTypeCombo",
            "QTest.select(objectName=morphList, morph index 0)",
            "morph_type_fixed_after_creation_and_combo_unchanged",
            type_spy,
            self.view.morph_list,
        )

    def test_morph_delete_and_adjacent_reindex(self):
        """Delete, Move Up, and Move Down each invoke one coordinator action."""
        # Delete the second vertex morph so Undo validates exact restoration of
        # its retained blendShape target, controller binding, and metadata.
        self._select_index(1)
        before = self._morph_order()
        original_delete = self.coordinator.delete_morph
        delete_spy = ActionInvocationSpy.wrap(
            "MayaModelAuthoringCoordinator.delete_morph",
            original_delete,
            self.view.delete_morph_btn,
        )
        self.coordinator.delete_morph = delete_spy
        try:
            QTest.mouseClick(self.view.delete_morph_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            self.coordinator.delete_morph = original_delete
        self.assertEqual(delete_spy.action_count, 1)
        after = self._morph_order()
        self.assertEqual(len(after), 2)
        cmds.undo()
        self.assertEqual(self._morph_order(), before)
        cmds.redo()
        self.assertEqual(self._morph_order(), after)
        self._emit(
            "morph.delete",
            "objectName=morphDeleteButton",
            "QTest.mouseClick(objectName=morphDeleteButton, Qt.LeftButton)",
            "morph_spec_binding_registry_and_undo_redo",
            delete_spy,
            self.view.delete_morph_btn,
        )

        # Rebuild a clean fixture for the two independent adjacent swaps.
        self.tearDown()
        self.setUp()
        self._select_index(1)
        up_before = self._morph_order()
        original_move = self.coordinator.move_morph
        up_spy = ActionInvocationSpy.wrap(
            "MayaModelAuthoringCoordinator.move_morph",
            original_move,
            self.view.move_morph_up_btn,
        )
        self.coordinator.move_morph = up_spy
        try:
            QTest.mouseClick(self.view.move_morph_up_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            self.coordinator.move_morph = original_move
        self.assertEqual(up_spy.action_count, 1)
        up_after = self._morph_order()
        self.assertEqual(up_after[0][1], "Morph B")
        cmds.undo()
        self.assertEqual(self._morph_order(), up_before)
        cmds.redo()
        self.assertEqual(self._morph_order(), up_after)
        self._emit(
            "morph.move_up",
            "objectName=morphMoveUpButton",
            "QTest.mouseClick(objectName=morphMoveUpButton, Qt.LeftButton)",
            "adjacent_morph_binding_order_swapped_and_undo_redo",
            up_spy,
            self.view.move_morph_up_btn,
        )

        self.tearDown()
        self.setUp()
        self._select_index(0)
        down_before = self._morph_order()
        original_move = self.coordinator.move_morph
        down_spy = ActionInvocationSpy.wrap(
            "MayaModelAuthoringCoordinator.move_morph",
            original_move,
            self.view.move_morph_down_btn,
        )
        self.coordinator.move_morph = down_spy
        try:
            QTest.mouseClick(self.view.move_morph_down_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            self.coordinator.move_morph = original_move
        self.assertEqual(down_spy.action_count, 1)
        down_after = self._morph_order()
        self.assertEqual(down_after[0][1], "Morph B")
        cmds.undo()
        self.assertEqual(self._morph_order(), down_before)
        cmds.redo()
        self.assertEqual(self._morph_order(), down_after)
        self._emit(
            "morph.move_down",
            "objectName=morphMoveDownButton",
            "QTest.mouseClick(objectName=morphMoveDownButton, Qt.LeftButton)",
            "adjacent_morph_binding_order_swapped_and_undo_redo",
            down_spy,
            self.view.move_morph_down_btn,
        )

    def test_morph_work_offset_selector(self):
        """Material morph exposes one deterministic temporary-work offset."""
        self._select_index(2)
        self.assertEqual(self.view.work_offset_combo.count(), 1)
        offset_spy = QtSignalInvocationSpy(
            "QComboBox.activated",
            self.view.work_offset_combo.activated,
            self.view.work_offset_combo,
        )
        self.view.work_offset_combo.setFocus()
        QTest.keyClick(self.view.work_offset_combo, Qt.Key_Enter)
        QApplication.processEvents()
        self.assertEqual(int(self.view.work_offset_combo.currentData()), 0)
        self._emit(
            "morph.work_offset",
            "objectName=morphWorkOffsetCombo",
            "QTest.keyClick(objectName=morphWorkOffsetCombo, Qt.Key_Enter)",
            "material_morph_offset_zero_selected_with_semantic_label",
            offset_spy,
            self.view.work_offset_combo,
        )

    def test_morph_work_material_create_apply_clear(self):
        """Create/apply/clear work shader actions preserve canonical ownership."""
        self._select_index(2)
        work = self.window.authoring_composition.material_morph_work
        self.assertIsNotNone(work)

        original_create = work.create
        create_spy = ActionInvocationSpy.wrap(
            "MayaMaterialMorphWork.create", original_create, self.view.create_work_material_btn
        )
        work.create = create_spy
        try:
            QTest.mouseClick(self.view.create_work_material_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            work.create = original_create
        self.assertEqual(create_spy.action_count, 1)
        members = model_registry.list_model_registry_members(
            self.root, model_registry.REGISTRY_CATEGORY_MATERIAL_MORPH_WORK
        )
        self.assertEqual(len(members), 1)
        shader = members[0]
        self.assertTrue(cmds.objExists(shader))
        cmds.undo()
        self.assertEqual(
            model_registry.list_model_registry_members(
                self.root, model_registry.REGISTRY_CATEGORY_MATERIAL_MORPH_WORK
            ),
            [],
        )
        cmds.redo()
        self.assertIn(
            shader,
            model_registry.list_model_registry_members(
                self.root, model_registry.REGISTRY_CATEGORY_MATERIAL_MORPH_WORK
            ),
        )
        self._emit(
            "morph.create_work_material",
            "objectName=morphCreateWorkMaterialButton",
            "QTest.mouseClick(objectName=morphCreateWorkMaterialButton, Qt.LeftButton)",
            "owned_work_shader_registered_and_undoable",
            create_spy,
            self.view.create_work_material_btn,
        )

        # Change one work-shader value so Apply has a semantic delta.
        cmds.setAttr(f"{shader}.baseColor", 0.9, 0.8, 0.7, type="float3")
        original_apply = work.apply
        work_apply_spy = ActionInvocationSpy.wrap(
            "MayaMaterialMorphWork.apply", original_apply, self.view.apply_work_material_btn
        )
        work.apply = work_apply_spy
        try:
            QTest.mouseClick(self.view.apply_work_material_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            work.apply = original_apply
        self.assertEqual(work_apply_spy.action_count, 1)
        applied = self.coordinator.read_spec(self.root).morphs[2]
        self.assertNotEqual(applied.offsets[0]["diffuse"][:3], _material_offset()["diffuse"][:3])
        cmds.undo()
        undone = self.coordinator.read_spec(self.root).morphs[2]
        self.assertEqual(
            tuple(undone.offsets[0]["diffuse"][:3]),
            tuple(_material_offset()["diffuse"][:3]),
        )
        cmds.redo()
        redone = self.coordinator.read_spec(self.root).morphs[2]
        self.assertEqual(redone.offsets, applied.offsets)
        self._emit(
            "morph.apply_work_material",
            "objectName=morphApplyWorkMaterialButton",
            "QTest.mouseClick(objectName=morphApplyWorkMaterialButton, Qt.LeftButton)",
            "material_morph_offsets_changed_through_coordinator_transaction",
            work_apply_spy,
            self.view.apply_work_material_btn,
        )

        original_clear = work.clear
        clear_spy = ActionInvocationSpy.wrap(
            "MayaMaterialMorphWork.clear", original_clear, self.view.clear_work_material_btn
        )
        work.clear = clear_spy
        try:
            QTest.mouseClick(self.view.clear_work_material_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            work.clear = original_clear
        self.assertEqual(clear_spy.action_count, 1)
        self.assertEqual(
            model_registry.list_model_registry_members(
                self.root, model_registry.REGISTRY_CATEGORY_MATERIAL_MORPH_WORK
            ),
            [],
        )
        cmds.undo()
        self.assertEqual(
            len(
                model_registry.list_model_registry_members(
                    self.root, model_registry.REGISTRY_CATEGORY_MATERIAL_MORPH_WORK
                )
            ),
            1,
        )
        cmds.redo()
        self.assertEqual(
            model_registry.list_model_registry_members(
                self.root, model_registry.REGISTRY_CATEGORY_MATERIAL_MORPH_WORK
            ),
            [],
        )
        self._emit(
            "morph.clear_work_material",
            "objectName=morphClearWorkMaterialButton",
            "QTest.mouseClick(objectName=morphClearWorkMaterialButton, Qt.LeftButton)",
            "material_morph_work_registry_unregistered_and_canonical_offsets_preserved",
            clear_spy,
            self.view.clear_work_material_btn,
        )


if __name__ == "__main__":
    unittest.main()
