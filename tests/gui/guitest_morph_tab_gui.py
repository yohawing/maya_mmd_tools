"""
MorphTab の GUI テスト
実際の Maya GUI 環境でのみ実行可能
"""

import json
import unittest

from maya import cmds

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.application_state import ApplicationState
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter
from mmd_tools.ui.qt_compat import QApplication, Qt
from mmd_tools.ui.tabs.morph_tab import MorphTab


def _emit_witness(surface_id, locator_key, locator, interaction, fired_action, oracle):
    """Emit one deterministic runtime witness for the coverage gate."""

    evidence = {
        "surface_id": surface_id,
        "case_id": "gui.morph_tab",
        locator_key: locator,
        "status": "pass",
        "runtime_witness": {
            "interaction": interaction,
            "fired_action": fired_action,
            "oracle": oracle,
            "action_count": 1,
        },
    }
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
            tab.morph_list.setCurrentRow(0)
            QApplication.processEvents()
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
                "MorphPresenter.load_morphs",
                "canonical Mouth_A01 morph row displayed and selected",
            )
            _emit_witness(
                "morph.value",
                "attribute",
                "morph_slider",
                "QTest.setValue(attribute=morph_slider, 65)",
                "MorphPresenter.set_morph_weight",
                "Mouth_A01 blendShape weight equals 0.65",
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

            self.assertTrue(tab.morph_slider.isEnabled())
            tab.morph_slider.setValue(65)
            self.assertAlmostEqual(cmds.getAttr(controller + ".inputWeight[7]"), 0.65)
        finally:
            tab.deleteLater()
            QApplication.processEvents()
            cmds.file(new=True, force=True)


if __name__ == "__main__":
    unittest.main()
