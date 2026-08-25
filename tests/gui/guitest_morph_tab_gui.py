"""
MorphTab の GUI テスト
実際の Maya GUI 環境でのみ実行可能
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from maya import cmds

from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.maya_plugin_setup import load_mmd_tools_plugin
from tests.common.ui_action_coverage import (
    ActionInvocationSpy,
    QtSignalInvocationSpy,
    build_surface_witness,
)
from mmd_tools.ui.application_state import ApplicationState
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.adapters import MayaCmdsAdapter
from mmd_tools.adapters.maya_morph_authoring_snapshot_provider import (
    MayaMorphAuthoringSnapshotProvider,
)
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter
from mmd_tools.ui.qt_compat import QApplication, QT_BINDING, Qt
from mmd_tools.ui.tabs.morph_tab import MorphTab
from mmd_tools.core.morph_topology import MorphTopologyInspection
from mmd_tools.core.model_authoring_spec import MmdMorphSpec

if QT_BINDING == "PySide6":
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider
else:
    from PySide2.QtTest import QTest
    from PySide2.QtWidgets import QStyle, QStyleOptionSlider


def _emit_witness(surface_id, locator_key, locator, interaction, oracle, action_spy, control):
    """Emit one deterministic runtime witness for the coverage gate."""

    locator_args = {locator_key: locator}
    evidence = build_surface_witness(
        surface_id=surface_id,
        case_id="gui.morph_tab",
        interaction=interaction,
        oracle=oracle,
        action_spy=action_spy,
        control=control,
        **locator_args,
    )
    print(
        "[UI COVERAGE WITNESS] "
        + json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        flush=True,
    )


@requires_gui
class TestMorphTabGUI(GuiTestBase):
    """MorphTab の GUI テスト（実際の Qt 環境で実行）"""

    def test_offset_and_manual_maya_connection_panels_not_exposed(self):
        """Removed offset and manual Maya-connection features stay absent."""
        tab = MorphTab()
        try:
            for name in (
                "offset_table",
                "offset_count_label",
                "blend_group",
                "connection_status_label",
                "blend_shape_edit",
                "target_name_edit",
                "select_blend_shape_btn",
                "connect_btn",
                "disconnect_btn",
                "auto_connect_btn",
            ):
                self.assertFalse(hasattr(tab, name), name)
            self.assertEqual(tab.detail_tabs.count(), 1)
            self.assertIs(tab.advanced_group.parentWidget(), tab.preview_group)
            self.assertTrue(hasattr(tab, "invert_check"))
            self.assertTrue(hasattr(tab, "multiplier_spin"))
        finally:
            tab.deleteLater()

    def test_keying_and_preset_controls_not_exposed(self):
        """Removed keying and preset features must not leave actionable UI behind."""
        tab = MorphTab()
        try:
            for name in (
                "set_morph_key_btn",
                "delete_morph_key_btn",
                "morph_key_status_label",
                "preset_combo",
                "save_preset_btn",
                "load_preset_btn",
                "delete_preset_btn",
            ):
                self.assertFalse(hasattr(tab, name), name)
        finally:
            tab.deleteLater()

    def test_topology_repair_action_is_diagnostic_only(self):
        """Repair is hidden when healthy and explicit when stale."""
        tab = MorphTab()
        try:
            self.assertFalse(tab.repair_topology_btn.isVisible())
            action_spy = ActionInvocationSpy.wrap(
                "MorphTab.set_topology_repair_state",
                tab.set_topology_repair_state,
                tab.repair_topology_btn,
            )
            action_spy("stale: controller cache differs", True)
            tab.show()
            QApplication.processEvents()
            self.assertTrue(tab.repair_topology_btn.isVisible())
            self.assertTrue(tab.repair_topology_btn.isEnabled())
            self.assertEqual(
                tab.repair_topology_btn.toolTip(),
                tab.tr("repair_topology", "tooltips"),
            )
            self.assertEqual(
                tab.topology_diagnostic_label.text(),
                tab.tr("topology_issue", "labels"),
            )
            _emit_witness(
                "morph.topology_repair",
                "selector",
                "objectName=morphRepairTopologyButton",
                "show stale topology diagnostic",
                "repair action visible and enabled only for repairable diagnostic",
                action_spy,
                tab.repair_topology_btn,
            )
        finally:
            tab.deleteLater()

    def test_topology_repair_button_routes_to_coordinator_and_refresh(self):
        """The real button invokes explicit repair and refreshes after readback."""
        cmds.file(new=True, force=True)
        root = cmds.group(empty=True, name="morphTopologyRepairGuiRoot")
        tab = MorphTab()
        state = ApplicationState()
        state.current_model_root = None
        coordinator = MagicMock()
        repair_spy = ActionInvocationSpy.wrap(
            "MorphPresenter.repair_morph_topology",
            coordinator.repair_morph_topology,
            tab.repair_topology_btn,
        )
        repair_spy._handler.return_value = MorphTopologyInspection({}, {}, ())
        coordinator.repair_morph_topology = repair_spy
        presenter = MorphPresenter(tab, state, authoring_coordinator=coordinator)
        presenter.load_morphs = MagicMock()
        state.current_model_root = root
        QApplication.processEvents()
        presenter.load_morphs.reset_mock()
        try:
            tab.show()
            QApplication.processEvents()
            tab.set_topology_repair_state("stale: controller cache differs", True)
            tab.repair_topology_btn.click()
            QApplication.processEvents()
            self.assertEqual(repair_spy.action_count, 1)
            self.assertEqual(repair_spy.calls[0][0], (state.current_model_root,))
            presenter.load_morphs.assert_called_once_with()
            _emit_witness(
                "morph.topology_repair.action",
                "selector",
                "objectName=morphRepairTopologyButton",
                "click repair topology",
                "coordinator repair completed before one presenter refresh",
                repair_spy,
                tab.repair_topology_btn,
            )
        finally:
            tab.deleteLater()
            cmds.delete(root)

    def test_mouth_alias_slider_updates_canonical_blendshape_weight(self):
        """MorphTab の実 slider signal が Mouth_A01 の weight[0] を更新する。"""
        cmds.file(new=True, force=True)
        root = cmds.group(empty=True, name="morphGuiModel")
        mesh = cmds.polyCube(name="morphGuiMesh")[0]
        target = cmds.polyCube(name="Mouth_A01_target")[0]
        cmds.parent(mesh, root)
        blend_shape = cmds.blendShape(target, mesh, name="morphGuiBlendShape")[0]
        cmds.aliasAttr("Mouth_A01", "{0}.weight[0]".format(blend_shape))
        cmds.delete(target)

        tab = MorphTab()
        presenter = None
        try:
            tab.show()
            QApplication.processEvents()
            app_state = ApplicationState()
            presenter = MorphPresenter(tab, app_state)
            # Set the fixture after construction so no delayed initial-load
            # callback can outlive this test's widget.
            app_state._current_model_root = root
            presenter.load_morphs()
            QApplication.processEvents()

            self.assertEqual(tab.morph_list.count(), 1)
            self.assertEqual(tab.morph_list.item(0).text(), "0:V|Mouth_A01")
            self.assertEqual(tab.morph_list.item(0).data(Qt.UserRole), "Mouth_A01")
            list_spy = QtSignalInvocationSpy(
                "MorphPresenter.on_morph_selected",
                tab.morph_list.currentItemChanged,
                tab.morph_list,
            )
            tab.morph_list.setCurrentRow(0)
            QApplication.processEvents()
            value_spy = QtSignalInvocationSpy(
                "MorphPresenter.set_morph_weight",
                tab.morph_slider.valueChanged,
                tab.morph_slider,
            )
            tab.morph_slider.setValue(65)
            QApplication.processEvents()

            self.assertAlmostEqual(
                cmds.getAttr("{0}.weight[0]".format(blend_shape)),
                0.65,
                places=5,
            )
            _emit_witness(
                "morph.list",
                "selector",
                "objectName=morphList",
                "QTest.setCurrentRow(objectName=morphList, 0)",
                "canonical Mouth_A01 morph row displayed and selected",
                list_spy,
                tab.morph_list,
            )
            _emit_witness(
                "morph.value",
                "attribute",
                "morph_slider",
                "QTest.setValue(attribute=morph_slider, 65)",
                "Mouth_A01 blendShape weight equals 0.65",
                value_spy,
                tab.morph_slider,
            )
        finally:
            presenter = None
            tab.deleteLater()
            QApplication.processEvents()
            cmds.file(new=True, force=True)

    def test_visible_morph_row_mouse_and_keyboard_selection_preview_canonical_target(self):
        """Real input keeps the hidden alias identity and previews only its fixed plug."""
        cmds.file(new=True, force=True)
        root = cmds.group(empty=True, name="morphSelectionLifecycleModel")
        mesh = cmds.polyCube(name="morphSelectionLifecycleMesh")[0]
        first_target = cmds.polyCube(name="Mouth_A01_selectionTarget")[0]
        second_target = cmds.polyCube(name="Mouth_A02_selectionTarget")[0]
        cmds.parent(mesh, root)
        blend_shape = cmds.blendShape(
            first_target,
            second_target,
            mesh,
            name="morphSelectionLifecycleBlendShape",
        )[0]
        cmds.aliasAttr("Mouth_A01", f"{blend_shape}.weight[0]")
        cmds.aliasAttr("Mouth_A02", f"{blend_shape}.weight[1]")
        cmds.delete(first_target, second_target)

        tab = MorphTab()
        presenter = None
        try:
            tab.resize(900, 520)
            tab.show()
            QApplication.processEvents()
            app_state = ApplicationState()
            adapter = MayaCmdsAdapter()
            snapshot_provider = MayaMorphAuthoringSnapshotProvider(adapter)
            preview_spy = ActionInvocationSpy.wrap(
                "MorphAuthoringSnapshotProvider.set_morph_preview",
                snapshot_provider.set_morph_preview,
                tab.morph_slider,
            )
            snapshot_provider.set_morph_preview = preview_spy
            presenter = MorphPresenter(
                tab,
                app_state,
                maya_adapter=adapter,
                morph_snapshot_provider=snapshot_provider,
            )
            app_state._current_model_root = root
            presenter.load_morphs()
            QApplication.processEvents()

            self.assertEqual(tab.morph_list.count(), 2)
            first_item = tab.morph_list.item(0)
            second_item = tab.morph_list.item(1)
            self.assertEqual(first_item.data(Qt.UserRole), "Mouth_A01")
            self.assertEqual(second_item.data(Qt.UserRole), "Mouth_A02")

            tab.morph_list.scrollToItem(first_item)
            QApplication.processEvents()
            first_rect = tab.morph_list.visualItemRect(first_item)
            self.assertFalse(first_rect.isEmpty())
            self.assertTrue(tab.morph_list.viewport().rect().contains(first_rect.center()))
            mouse_selection_spy = QtSignalInvocationSpy(
                "MorphPresenter.on_morph_selected.mouse",
                tab.morph_list.currentItemChanged,
                tab.morph_list,
            )
            QTest.mouseClick(
                tab.morph_list.viewport(),
                Qt.LeftButton,
                pos=first_rect.center(),
            )
            QApplication.processEvents()
            mouse_selection_spy.stop()
            self.assertEqual(mouse_selection_spy.action_count, 1)
            self.assertIs(tab.morph_list.currentItem(), first_item)
            self.assertEqual(presenter.current_morph, "Mouth_A01")

            keyboard_selection_spy = QtSignalInvocationSpy(
                "MorphPresenter.on_morph_selected.keyboard",
                tab.morph_list.currentItemChanged,
                tab.morph_list,
            )
            tab.morph_list.setFocus()
            QTest.keyClick(tab.morph_list, Qt.Key_Down)
            QApplication.processEvents()
            keyboard_selection_spy.stop()
            self.assertEqual(keyboard_selection_spy.action_count, 1)
            self.assertIs(tab.morph_list.currentItem(), second_item)
            self.assertEqual(presenter.current_morph, "Mouth_A02")

            tab.morph_slider.setFocus()
            QTest.keyClick(tab.morph_slider, Qt.Key_Right)
            QApplication.processEvents()
            self.assertEqual(preview_spy.action_count, 1)
            self.assertEqual(
                preview_spy.calls[0][0][1],
                (f"{blend_shape}.weight[1]",),
            )
            self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.0)
            self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[1]"), 0.01)
        finally:
            presenter = None
            tab.deleteLater()
            QApplication.processEvents()
            cmds.file(new=True, force=True)

    def test_pending_preview_model_switch_isolates_runtime_projection_and_undo(self):
        """A pending preview cannot route B's reset or survive a model swap."""
        cmds.file(new=True, force=True)
        load_mmd_tools_plugin(Path(__file__).resolve().parents[2], cmds_module=cmds)
        window = MainWindow()
        try:
            composition = window.authoring_composition
            self.assertIsNotNone(composition)
            coordinator = composition.coordinator
            root_a = composition.model_initializer.create(
                "pmx20-basic-v1", "Morph Isolation A", "Morph Isolation A"
            ).root
            root_b = composition.model_initializer.create(
                "pmx20-basic-v1", "Morph Isolation B", "Morph Isolation B"
            ).root
            for index, shape in enumerate(
                cmds.listRelatives(
                    root_b,
                    allDescendents=True,
                    type="mesh",
                    fullPath=True,
                )
                or ()
            ):
                cmds.rename(shape, "morphIsolationBShape{}".format(index))
            coordinator.create_morph(
                root_a,
                MmdMorphSpec("Isolation A Morph", name_english="A Original", morph_type="bone"),
            )
            coordinator.create_morph(
                root_b,
                MmdMorphSpec("Isolation B Morph", name_english="B Original", morph_type="bone"),
            )

            presenter = window.morph_presenter
            view = presenter.view
            window.show()
            window.tab_widget.setCurrentWidget(view)
            window.app_state.refresh_model_list()
            available = window.app_state.available_models
            self.assertIn(root_a, available)
            self.assertIn(root_b, available)

            def select_model(root):
                cmds.select(root, replace=True)
                self.assertTrue(window.app_state.select_model_from_maya_selection())
                QApplication.processEvents()
                self.assertEqual(window.app_state.current_model_root, root)
                self.assertEqual(presenter._loaded_model_root, root)

            def select_only_morph(root):
                spec = coordinator.read_spec(root)
                self.assertEqual(len(spec.morphs), 1)
                morph = spec.morphs[0]
                self.assertEqual(view.morph_list.count(), 1)
                item = view.morph_list.item(0)
                view.morph_list.setCurrentItem(item)
                QApplication.processEvents()
                key = item.data(Qt.UserRole)
                data = presenter.morph_data[key]
                self.assertEqual(data["binding_identity"], morph.binding_identity)
                targets = tuple(data.get("runtime_targets") or ())
                self.assertEqual(len(targets), 1)
                self.assertTrue(cmds.objExists(targets[0]))
                return morph, key, targets[0], presenter._authoring_spec, data

            select_model(root_a)
            morph_a, key_a, plug_a, spec_a, data_a = select_only_morph(root_a)
            self.assertEqual(presenter.current_morph, key_a)
            view.morph_name_en_edit.setText("A Pending Edit")
            option = QStyleOptionSlider()
            view.morph_slider.initStyleOption(option)
            handle = view.morph_slider.style().subControlRect(
                QStyle.CC_Slider,
                option,
                QStyle.SC_SliderHandle,
                view.morph_slider,
            )
            QTest.mousePress(view.morph_slider, Qt.LeftButton, pos=handle.center())
            view.morph_slider.setValue(40)
            QApplication.processEvents()
            self.assertIsNotNone(presenter._morph_preview_session)
            self.assertAlmostEqual(cmds.getAttr(plug_a), 0.4, places=7)

            select_model(root_b)
            QTest.mouseRelease(view.morph_slider, Qt.LeftButton, pos=handle.center())
            QApplication.processEvents()
            morph_b, key_b, plug_b, spec_b, data_b = select_only_morph(root_b)
            self.assertNotEqual(morph_b.binding_identity, morph_a.binding_identity)
            self.assertNotEqual(plug_b, plug_a)
            self.assertAlmostEqual(cmds.getAttr(plug_b), 0.0, places=7)
            self.assertIsNot(spec_b, spec_a)
            self.assertIsNot(data_b, data_a)
            self.assertEqual(presenter.current_morph, key_b)
            self.assertEqual(view.morph_name_en_edit.text(), "B Original")
            self.assertAlmostEqual(cmds.getAttr(plug_a), 0.0, places=7)
            self.assertIsNone(presenter._morph_preview_session)
            self.assertEqual(
                coordinator.read_spec(root_a).morphs[0].name_english,
                "A Original",
            )

            cmds.setAttr(plug_b, 0.65)
            view.morph_slider.blockSignals(True)
            view.morph_slider.setValue(65)
            view.morph_slider.blockSignals(False)
            cmds.flushUndo()
            with patch.object(
                coordinator,
                "reset_morph_preview",
                wraps=coordinator.reset_morph_preview,
            ) as reset_action:
                QTest.mouseClick(view.reset_slider_btn, Qt.LeftButton)
                QApplication.processEvents()
                self.assertEqual(reset_action.call_count, 1)
                self.assertEqual(reset_action.call_args.args, (root_b, (plug_b,)))
            self.assertAlmostEqual(cmds.getAttr(plug_b), 0.0, places=7)
            self.assertAlmostEqual(cmds.getAttr(plug_a), 0.0, places=7)
            cmds.undo()
            self.assertAlmostEqual(cmds.getAttr(plug_b), 0.65, places=7)
            self.assertAlmostEqual(cmds.getAttr(plug_a), 0.0, places=7)
            cmds.redo()
            self.assertAlmostEqual(cmds.getAttr(plug_b), 0.0, places=7)
            self.assertAlmostEqual(cmds.getAttr(plug_a), 0.0, places=7)

            select_model(root_a)
            morph_a_after, key_a_after, plug_a_after, spec_a_after, data_a_after = (
                select_only_morph(root_a)
            )
            self.assertIsNot(spec_a_after, spec_b)
            self.assertIsNot(data_a_after, data_b)
            self.assertEqual(morph_a_after.binding_identity, morph_a.binding_identity)
            self.assertEqual(key_a_after, key_a)
            self.assertEqual(plug_a_after, plug_a)
            self.assertEqual(view.morph_name_en_edit.text(), "A Original")
            self.assertNotEqual(view.morph_name_en_edit.text(), "A Pending Edit")
            self.assertAlmostEqual(cmds.getAttr(plug_a_after), 0.0, places=7)
        finally:
            window.close()
            window.deleteLater()
            QApplication.processEvents()
            cmds.file(new=True, force=True)

    def test_material_slider_uses_controller_when_network_lookup_is_unavailable(self):
        cmds.file(new=True, force=True)
        root = cmds.group(empty=True, name="materialMorphGuiModel")
        cmds.addAttr(root, longName="mmdMorphData", dataType="string")
        cmds.setAttr(
            root + ".mmdMorphData",
            json.dumps([{"name_jp": "材質", "panel": 4, "type": 8, "index": 7}]),
            type="string",
        )
        cmds.addAttr(root, longName="mmd_morph_controller", attributeType="message")
        controller = cmds.createNode("network", name="materialMorphController")
        cmds.addAttr(controller, longName="inputWeight", attributeType="double", multi=True)
        cmds.addAttr(controller, longName="outputWeight", attributeType="double", multi=True)
        cmds.connectAttr(controller + ".message", root + ".mmd_morph_controller")
        driven = cmds.createNode("network", name="materialMorphDriven")
        cmds.addAttr(driven, longName="weight", attributeType="double")
        cmds.connectAttr(controller + ".outputWeight[7]", driven + ".weight")

        tab = MorphTab()
        try:
            app_state = ApplicationState()
            presenter = MorphPresenter(tab, app_state)
            app_state._current_model_root = root
            presenter.load_morphs()
            tab.morph_list.setCurrentRow(0)
            QApplication.processEvents()

            self.assertEqual(cmds.ls(selection=True, long=True), cmds.ls(controller, long=True))
            self.assertTrue(tab.morph_slider.isEnabled())
            tab.morph_slider.setValue(65)
            self.assertAlmostEqual(cmds.getAttr(controller + ".inputWeight[7]"), 0.65)
        finally:
            tab.deleteLater()
            QApplication.processEvents()
            cmds.file(new=True, force=True)


if __name__ == "__main__":
    unittest.main()
