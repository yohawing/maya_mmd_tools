"""Real Qt/Maya proof for the Info metadata undo contract."""

import json
import unittest

import maya.cmds as cmds

from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.ui_action_coverage import QtSignalInvocationSpy, build_surface_witness


def _emit_witness(surface_id, locator, interaction, oracle, action_spy, control):
    """Emit one deterministic runtime witness for the coverage gate."""

    evidence = build_surface_witness(
        surface_id=surface_id,
        case_id="gui.info_undo",
        attribute=locator,
        interaction=interaction,
        oracle=oracle,
        action_spy=action_spy,
        control=control,
    )
    print(
        "[UI COVERAGE WITNESS] "
        + json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        flush=True,
    )


@requires_gui
class TestInfoUndoContractGUI(GuiTestBase):
    """Exercise the production MainWindow and generated basic PMX template."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
        from mmd_tools.adapters.maya_model_template_initializer import MayaModelTemplateInitializer

        adapter = MayaCmdsAdapter(cmds_module=cmds)
        self.template = MayaModelTemplateInitializer(adapter).create(
            "pmx20-basic-v1",
            "Undo Contract JP",
            "Undo Contract EN",
        )
        for index, shape in enumerate(
            cmds.listRelatives(self.template.root, allDescendents=True, type="mesh", fullPath=True)
            or []
        ):
            cmds.rename(shape, f"infoUndoFirstShape{index}")
        self.window = MainWindow()
        self.window.show()
        self.window.app_state.current_model_root = self.template.root
        self.window.tab_widget.setCurrentWidget(self.window.info_presenter.view)
        QApplication.processEvents()

    def tearDown(self):
        try:
            if getattr(self, "window", None) is not None:
                self.window.close()
                self.window.deleteLater()
                QApplication.processEvents()
        finally:
            super().tearDown()

    def test_focus_session_immediate_write_and_undo_redo(self):
        view = self.window.info_presenter.view
        editor = view.model_name_jp_edit
        old_value = cmds.getAttr(f"{self.template.root}.mmd_model_name")
        old_generation = self.window.app_state.refresh_generation

        editor.setFocus()
        editor.setText("Undo Contract JP 1")
        action_spy = QtSignalInvocationSpy(
            "InfoPresenter.update_model_info", editor.textChanged, editor
        )
        editor.setText("Undo Contract JP 2")
        QApplication.processEvents()
        action_spy.stop()
        self.assertEqual(cmds.getAttr(f"{self.template.root}.mmd_model_name"), "Undo Contract JP 2")

        editor.clearFocus()
        QApplication.processEvents()
        self.assertIsNone(self.window.info_presenter._edit_session)
        self.assertEqual(self.window.app_state.refresh_generation, old_generation + 1)
        self.assertIn("Undo Contract JP 2", self.window.header_widget.model_combo.currentText())

        cmds.undo()
        self.assertEqual(cmds.getAttr(f"{self.template.root}.mmd_model_name"), old_value)
        cmds.redo()
        self.assertEqual(cmds.getAttr(f"{self.template.root}.mmd_model_name"), "Undo Contract JP 2")
        _emit_witness(
            "info.model_name_jp",
            "model_name_jp_edit",
            "QTest.edit(attribute=model_name_jp_edit, Undo Contract JP 2)",
            "Maya attr write and Undo/Redo restored model_name_jp",
            action_spy,
            editor,
        )



    def test_model_switch_does_not_write_loading_text_to_new_root(self):
        from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
        from mmd_tools.adapters.maya_model_template_initializer import MayaModelTemplateInitializer

        adapter = MayaCmdsAdapter(cmds_module=cmds)
        second = MayaModelTemplateInitializer(adapter).create(
            "pmx20-basic-v1",
            "Second JP",
            "Second EN",
        )
        first_before = cmds.getAttr(f"{self.template.root}.mmd_model_name")
        view = self.window.info_presenter.view
        view.model_name_jp_edit.setFocus()
        view.model_name_jp_edit.setText("First Edited")
        QApplication.processEvents()
        self.window.app_state.current_model_root = second.root
        QApplication.processEvents()
        view.model_name_jp_edit.clearFocus()
        self.assertIsNone(self.window.info_presenter._edit_session)
        self.assertEqual(cmds.getAttr(f"{self.template.root}.mmd_model_name"), first_before)
        self.assertEqual(cmds.getAttr(f"{second.root}.mmd_model_name"), "Second JP")


if __name__ == "__main__":
    unittest.main()
