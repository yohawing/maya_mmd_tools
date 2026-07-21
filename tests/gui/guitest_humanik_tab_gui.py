"""HumanIkTab GUI smoke tests.

These only check that the tab can be constructed, wired to a real presenter,
and refreshed without raising -- scene-level HumanIK lifecycle behavior
(characterize/source/target/bake/restore) is already covered by
``tests/unit/test_humanik_menu_actions.py`` and the HumanIK E2E harnesses;
this file exists to lock the Qt widget contract, matching
``guitest_physics_tab_gui.py``.
"""

import unittest
from types import SimpleNamespace

from maya import cmds

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.presenters.humanik_presenter import HumanIkPresenter
from mmd_tools.ui.tabs.humanik_tab import HumanIkTab
from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.translations import UITranslator


@requires_gui
class TestHumanIkTabGUI(GuiTestBase):
    """Lock the HumanIK tab widget contract."""

    def test_shell_structure_and_defaults(self):
        tab = HumanIkTab()
        try:
            self.assertTrue(tab.experimental_notice_label.text())
            self.assertTrue(tab.restore_explanation_label.text())
            self.assertFalse(tab.orphaned_warning_label.isVisible())
            for attr in (
                "setup_characterize_btn",
                "enter_source_btn",
                "enter_target_btn",
                "bake_btn",
                "create_control_rig_btn",
                "restore_btn",
                "diagnostics_btn",
                "refresh_btn",
            ):
                self.assertTrue(hasattr(tab, attr), attr)
            self.assertEqual(tab.bake_frame_range(), (0, 0))
            tab.set_bake_frame_range(10, 50)
            self.assertEqual(tab.bake_frame_range(), (10, 50))
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_set_state_renders_mode_source_target_and_blocked_reason(self):
        tab = HumanIkTab()
        try:
            state = {
                "mode": "source",
                "source": {"modelRoot": "|Model", "character": "ModelChar"},
                "target": None,
                "controlRigs": [],
                "restoreHint": {"orphanedControlRigs": []},
                "actions": {
                    "setup_and_characterize": {"allowed": True, "reasonCode": None},
                    "enter_source_mode": {"allowed": True, "reasonCode": None},
                    "enter_target_mode": {
                        "allowed": False,
                        "reasonCode": "no_source",
                    },
                    "create_control_rig": {"allowed": True, "reasonCode": None},
                    "bake_to_mmd_rig": {"allowed": False, "reasonCode": "no_active_preview"},
                    "restore_mmd_rig": {"allowed": True, "reasonCode": None},
                    "diagnostics": {"allowed": True, "reasonCode": None},
                },
            }

            tab.set_state(state)
            QApplication.processEvents()

            self.assertIn("ModelChar", tab.source_value_label.text())
            self.assertFalse(tab.enter_target_btn.isEnabled())
            self.assertFalse(tab.bake_btn.isEnabled())
            self.assertTrue(tab.enter_source_btn.isEnabled())
            self.assertFalse(tab.orphaned_warning_label.isVisible())
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_set_state_with_no_data_does_not_raise(self):
        tab = HumanIkTab()
        try:
            tab.set_state({})
            tab.set_state(None)
            QApplication.processEvents()
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_retranslate_ui_en_ja(self):
        translator = UITranslator.instance()
        previous_language = translator.get_language()
        tab = HumanIkTab()
        try:
            translator.set_language("en")
            tab.retranslateUi()
            en_refresh = tab.refresh_btn.text()
            en_setup = tab.setup_characterize_btn.text()

            translator.set_language("ja")
            tab.retranslateUi()
            self.assertNotEqual(tab.refresh_btn.text(), en_refresh)
            self.assertNotEqual(tab.setup_characterize_btn.text(), en_setup)
        finally:
            translator.set_language(previous_language)
            tab.deleteLater()
            QApplication.processEvents()

    def test_presenter_refresh_against_a_real_scene_does_not_raise(self):
        cmds.file(new=True, force=True)
        tab = HumanIkTab()
        app_state = SimpleNamespace(current_model_root=None)
        presenter = HumanIkPresenter(tab, app_state)
        try:
            presenter.on_tab_activated()
            QApplication.processEvents()
            presenter.refresh()
            QApplication.processEvents()
        finally:
            tab.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
