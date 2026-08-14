"""
MorphTab の GUI テスト
実際の Maya GUI 環境でのみ実行可能
"""

import json
import unittest
from unittest.mock import MagicMock

from maya import cmds

from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.ui_action_coverage import (
    ActionInvocationSpy,
    QtSignalInvocationSpy,
    build_surface_witness,
)
from mmd_tools.ui.application_state import ApplicationState
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter
from mmd_tools.ui.qt_compat import QApplication, Qt
from mmd_tools.ui.tabs.morph_tab import MorphTab
from mmd_tools.core.morph_topology import MorphTopologyInspection


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
