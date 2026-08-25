"""Real-Maya GUI coverage for mmdMorphController Attribute Editor controls."""

from pathlib import Path
import unittest

from maya import cmds, mel

from mmd_tools.ui import morph_controller_ae
from mmd_tools.ui.qt_compat import QApplication
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.maya_plugin_setup import load_mmd_tools_plugin


@requires_gui
class TestMorphControllerAttributeEditorGUI(GuiTestBase):
    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        load_mmd_tools_plugin(
            Path(__file__).resolve().parents[2],
            required_node_types=("mmdMorphController",),
            cmds_module=cmds,
        )

    def tearDown(self):
        if cmds.window("mmdMorphControllerAeTest", exists=True):
            cmds.deleteUI("mmdMorphControllerAeTest", window=True)
        cmds.file(new=True, force=True)
        super().tearDown()

    def test_template_installed_and_weight_value_controls_are_bound(self):
        controller = cmds.createNode("mmdMorphController", name="aeMorphController")
        target = cmds.createNode("network", name="aeBoneMorph")
        cmds.addAttr(target, longName="weight", attributeType="double")
        cmds.addAttr(target, longName="mmd_morph_index", attributeType="long")
        cmds.addAttr(target, longName="mmd_morph_name", dataType="string")
        cmds.setAttr(f"{target}.mmd_morph_index", 0)
        cmds.setAttr(f"{target}.mmd_morph_name", "ボーン表示名", type="string")
        cmds.setAttr(f"{controller}.inputWeight[0]", 0.375)
        cmds.connectAttr(f"{controller}.outputWeight[0]", f"{target}.weight")

        template_info = mel.eval("whatIs AEmmdMorphControllerTemplate")
        self.assertNotIn("Unknown", template_info)
        morph_controller_ae._WEIGHT_COLUMNS.clear()
        cmds.select(controller, replace=True)
        mel.eval(f'showEditorExact "{controller}"')
        QApplication.processEvents()
        column = morph_controller_ae._WEIGHT_COLUMNS[-1]
        controls = cmds.layout(column, query=True, childArray=True) or []

        self.assertEqual(len(controls), 1)
        morph_frame = cmds.layout(column, query=True, parent=True)
        while morph_frame and not cmds.frameLayout(morph_frame, exists=True):
            morph_frame = cmds.layout(morph_frame, query=True, parent=True)
        self.assertTrue(morph_frame)
        self.assertEqual(cmds.frameLayout(morph_frame, query=True, label=True), "Morph Weights")
        control = controls[0]
        self.assertEqual(cmds.control(control, query=True, parent=True), column)
        self.assertEqual(
            cmds.attrFieldSliderGrp(control, query=True, attribute=True),
            f"{controller}.inputWeight[0]",
        )
        self.assertEqual(
            cmds.attrFieldSliderGrp(control, query=True, label=True),
            "ボーン表示名",
        )
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.inputWeight[0]"), 0.375, places=6)


if __name__ == "__main__":
    unittest.main()
