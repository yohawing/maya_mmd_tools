"""HumanIkTab / HumanIkWindow GUI smoke tests.

The tab-shell tests below only check that ``HumanIkTab`` can be constructed
and rendered without raising -- scene-level HumanIK lifecycle behavior
(characterize/source/target/bake/restore) is already covered by
``tests/unit/test_humanik_menu_actions.py`` and the HumanIK E2E harnesses;
this file exists to lock the Qt widget contract, matching
``guitest_physics_tab_gui.py``.

HUMANIK-FRONTEND-1 Phase B3 moved the HumanIK workflow out of the MMD Editor
tab bar and into its own standalone window (``mmd_tools.ui.humanik_window``).
The ``TestHumanIkWindowGUI`` class below covers that window: construction,
floating/dockable ``show_window``, and that showing/hiding it drives the
presenter's ``on_tab_activated``/``on_tab_deactivated`` lifecycle, matching
``guitest_ui_components.py``'s ``test_show_window_floating`` /
``test_show_window_dockable`` coverage of ``MainWindow``.
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
            self.assertTrue(tab.status_label.text())
            self.assertFalse(hasattr(tab, "experimental_notice_label"))
            # Source/Target mode remain combo-driven. Setup is explicit so a
            # scene with zero characterized models has an obvious entry path.
            for attr in ("enter_source_btn", "enter_target_btn"):
                self.assertFalse(hasattr(tab, attr), attr)
            # The verbose status table remains removed. Bake is the one
            # collapsible group because it contains range/destination controls.
            for attr in (
                "humanik_status_group",
                "humanik_actions_group",
                "mode_value_label",
                "source_value_label",
                "target_value_label",
                "control_rigs_value_label",
                "control_rig_section",
                "restore_section",
            ):
                self.assertFalse(hasattr(tab, attr), attr)
            for attr in (
                "character_combo",
                "source_combo",
                "setup_characterize_btn",
                "status_label",
                "bake_btn",
                "create_control_rig_btn",
                "restore_btn",
                "refresh_btn",
                "bake_section",
                "bake_toggle_btn",
                "bake_content",
            ):
                self.assertTrue(hasattr(tab, attr), attr)
            self.assertFalse(hasattr(tab, "diagnostics_btn"))
            self.assertTrue(tab.bake_toggle_btn.isChecked())
            self.assertEqual(tab.bake_frame_range(), (0, 0))
            tab.set_bake_frame_range(10, 50)
            self.assertEqual(tab.bake_frame_range(), (10, 50))
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_compact_rows_and_bake_collapse(self):
        tab = HumanIkTab()
        try:
            tab.resize(260, 640)
            tab.show()
            QApplication.processEvents()

            self.assertLess(
                abs(tab.character_combo_label.geometry().center().y() - tab.character_combo.geometry().center().y()),
                8,
            )
            self.assertLess(
                abs(tab.source_combo_label.geometry().center().y() - tab.source_combo.geometry().center().y()),
                8,
            )
            self.assertGreater(tab.character_combo.width(), 100)
            self.assertGreater(tab.source_combo.width(), 100)
            margins = tab.layout().contentsMargins()
            self.assertGreaterEqual(margins.left(), 8)
            self.assertGreaterEqual(margins.top(), 8)
            self.assertGreaterEqual(tab.layout().spacing(), 6)

            tab.bake_toggle_btn.setChecked(False)
            QApplication.processEvents()
            self.assertFalse(tab.bake_content.isVisible())
            self.assertIn("▶", tab.bake_toggle_btn.text())

            tab.bake_toggle_btn.setChecked(True)
            QApplication.processEvents()
            self.assertTrue(tab.bake_content.isVisible())
            self.assertIn("▼", tab.bake_toggle_btn.text())
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_character_and_source_combos_populate_and_select(self):
        tab = HumanIkTab()
        try:
            tab.set_character_options([("ModelA", "|A"), ("ModelB", "|B")], "|B")
            self.assertEqual(tab.character_combo.count(), 2)
            self.assertEqual(tab.character_combo.currentData(), "|B")

            tab.set_source_options([("(none)", None), ("ModelA", "|A")], "|A")
            self.assertEqual(tab.source_combo.count(), 2)
            self.assertEqual(tab.source_combo.currentData(), "|A")

            # Repopulating must not fire the combo's own change signal --
            # HumanIkPresenter relies on this to avoid re-triggering
            # connect/disconnect while rendering backend-truth state.
            fired = []
            tab.source_combo.currentIndexChanged.connect(lambda *_args: fired.append(True))
            tab.set_source_options([("(none)", None)], None)
            QApplication.processEvents()
            self.assertEqual(fired, [])

            tab.set_character_options([("(none)", None)], None)
            self.assertEqual(tab.character_combo.count(), 1)
            self.assertIsNone(tab.character_combo.currentData())
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_set_state_renders_mode_status_and_blocked_reason(self):
        tab = HumanIkTab()
        try:
            state = {
                "mode": "source",
                "source": {"modelRoot": "|Model", "character": "ModelChar"},
                "target": None,
                "controlRigs": [{"modelRoot": "|Rig", "character": "ModelChar"}],
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

            # Source/Target are no longer duplicated in the status label
            # (they are already visible via the Character/Source combos) --
            # only the mode text and a Control Rig count suffix remain
            # (HUMANIK-FRONTEND-1 Phase B5).
            self.assertIn("1", tab.status_label.text())
            self.assertTrue(tab.bake_btn.isEnabled())
            self.assertTrue(tab.create_control_rig_btn.isEnabled())
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
            en_character_label = tab.character_combo_label.text()

            translator.set_language("ja")
            tab.retranslateUi()
            self.assertNotEqual(tab.refresh_btn.text(), en_refresh)
            self.assertNotEqual(tab.character_combo_label.text(), en_character_label)
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

    def test_setup_button_promotes_selected_model_into_character_list(self):
        """Lock the zero-character entry path without running a heavy HIK setup."""

        class FakeSession:
            def describe_frontend_state(self, model_root=None):
                return {"mode": "neutral", "source": None, "controlRigs": []}

            def list_source_candidates(self):
                return []

        class FakeActions:
            def __init__(self):
                self.characterized = []
                self.dispatch_calls = []
                self.session = FakeSession()

            def get_humanik_session(self):
                return self.session

            def resolve_selected_model_root_for_display(self, *, cmds_module=None):
                return "|ImportedOnly"

            def list_characterized_mmd_models(self, *, cmds_module=None):
                return list(self.characterized)

            def dispatch_action(self, action):
                self.dispatch_calls.append(action)
                if action == "setup_and_characterize":
                    self.characterized.append("|ImportedOnly")

        tab = HumanIkTab()
        actions = FakeActions()
        presenter = HumanIkPresenter(
            tab,
            SimpleNamespace(current_model_root=None),
            actions_module=actions,
            cmds_module=cmds,
        )
        try:
            presenter.on_tab_activated()
            QApplication.processEvents()
            self.assertEqual(tab.character_combo.count(), 1)
            self.assertIsNone(tab.character_combo.currentData())

            tab.setup_characterize_btn.click()
            QApplication.processEvents()

            self.assertEqual(actions.dispatch_calls, ["setup_and_characterize"])
            self.assertEqual(tab.character_combo.currentData(), "|ImportedOnly")
        finally:
            tab.deleteLater()
            QApplication.processEvents()


@requires_gui
class TestHumanIkWindowGUI(GuiTestBase):
    """Lock the standalone HumanIK Editor window's construction and lifecycle."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        from mmd_tools.ui import humanik_window

        self._humanik_window_module = humanik_window
        # Every test starts from "no window open" -- the singleton in
        # ``humanik_window`` must not leak a previous test's instance.
        humanik_window.close_humanik_window()

    def tearDown(self):
        self._humanik_window_module.close_humanik_window()
        QApplication.processEvents()
        super().tearDown()

    def test_construction_hosts_the_humanik_tab(self):
        from mmd_tools.ui.humanik_window import HumanIkWindow

        window = HumanIkWindow()
        try:
            self.assertIsInstance(window.humanik_tab, HumanIkTab)
            self.assertIsInstance(window.humanik_presenter, HumanIkPresenter)
            self.assertTrue(window.windowTitle())
        finally:
            window.close()
            window.deleteLater()
            QApplication.processEvents()

    def test_show_window_floating(self):
        from mmd_tools.ui.humanik_window import HumanIkWindow

        window = HumanIkWindow()
        try:
            window.show_window(dockable=False)
            QApplication.processEvents()
            self.assertTrue(window.isVisible())
            self.assertGreaterEqual(window.width(), window.PREFERRED_WIDTH)
        finally:
            window.close()
            window.deleteLater()
            QApplication.processEvents()

    def test_show_window_dockable_creates_workspace_control(self):
        from mmd_tools.ui.humanik_window import HumanIkWindow

        workspace_name = HumanIkWindow.WORKSPACE_CONTROL_NAME
        if cmds.workspaceControl(workspace_name, exists=True):
            cmds.deleteUI(workspace_name, control=True)

        window = HumanIkWindow()
        try:
            window.show_window(dockable=True)
            QApplication.processEvents()
            self.assertTrue(cmds.workspaceControl(workspace_name, exists=True))
            self.assertTrue(window.isVisible())
        finally:
            window.close_window()
            QApplication.processEvents()

    def test_show_hide_drives_presenter_lifecycle(self):
        from mmd_tools.ui.humanik_window import HumanIkWindow

        window = HumanIkWindow()
        try:
            self.assertFalse(window._lifecycle_active)

            window.show_window(dockable=False)
            QApplication.processEvents()
            self.assertTrue(window._lifecycle_active)

            window.hide()
            QApplication.processEvents()
            self.assertFalse(window._lifecycle_active)
        finally:
            window.close()
            window.deleteLater()
            QApplication.processEvents()

    def test_show_humanik_window_singleton_raises_existing_instance(self):
        from mmd_tools.ui import humanik_window

        first = humanik_window.show_humanik_window(dockable=False)
        QApplication.processEvents()
        second = humanik_window.show_humanik_window(dockable=False)
        QApplication.processEvents()

        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
