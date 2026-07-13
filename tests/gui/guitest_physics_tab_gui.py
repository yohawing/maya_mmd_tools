"""PhysicsTab GUI contract tests.

These tests run only with a real Qt application. Scene collection and Maya
selection behavior remain covered by the presenter unit tests.
"""

import unittest

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.tabs.physics_tab import PhysicsTab
from mmd_tools.ui.translations import UITranslator


@requires_gui
class TestPhysicsTabGUI(GuiTestBase):
    """Lock the Physics tab widget contract."""

    def test_shell_structure_and_defaults(self):
        tab = PhysicsTab()
        try:
            self.assertEqual(tab.list_tabs.count(), 2)
            self.assertTrue(tab.details_scroll_area.widgetResizable())
            self.assertFalse(tab.collider_visible_check.isChecked())
            self.assertFalse(tab.physics_details_content.isEnabled())
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertFalse(tab.reset_btn.isEnabled())
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_forms_populate_without_editing_paths(self):
        tab = PhysicsTab()
        try:
            tab.set_physics_details_enabled(True)
            tab.set_physics_form(
                "rigid",
                {
                    "name": "右髪２",
                    "name_english": "HairR2",
                    "shape": 2,
                    "physics_mode": 2,
                    "related_bone": "右髪２ (4)",
                    "collision_group": 1,
                    "collision_mask": "2",
                    "mass": 0.5,
                    "linear_damping": 0.5,
                    "angular_damping": 0.5,
                    "restitution": 0.0,
                    "friction": 0.5,
                },
            )
            QApplication.processEvents()
            self.assertFalse(tab.rigid_body_form_group.isHidden())
            self.assertTrue(tab.joint_form_group.isHidden())
            self.assertEqual(tab.rigid_name_edit.text(), "右髪２")
            self.assertEqual(tab.rigid_shape_combo.currentIndex(), 2)
            self.assertEqual(tab.rigid_mass_edit.text(), "0.5")
            self.assertTrue(tab.rigid_mass_edit.isEnabled())

            tab.set_physics_form(
                "joint",
                {
                    "name": "右髪２",
                    "name_english": "HairJointR2",
                    "joint_type": "Spring 6DOF",
                    "rigid_body_a": "Body A (1)",
                    "rigid_body_b": "Body B (2)",
                    "linear_constraint_states": "X: 0, Y: 0, Z: 0",
                    "angular_constraint_states": "X: 0, Y: 0, Z: 0",
                    "translation_limit_min": "X: 0, Y: 0, Z: 0",
                    "translation_limit_max": "X: 0, Y: 0, Z: 0",
                    "rotation_limit_min_degrees": "X: -10, Y: -10, Z: -10",
                    "rotation_limit_max_degrees": "X: 10, Y: 10, Z: 10",
                    "spring_translation": "X: 0, Y: 0, Z: 0",
                    "spring_rotation": "X: 0.1, Y: 0.1, Z: 0.1",
                    "spring_translation_enabled": "X: 0, Y: 0, Z: 0",
                    "spring_rotation_enabled": "X: 1, Y: 1, Z: 1",
                },
            )
            QApplication.processEvents()
            self.assertTrue(tab.rigid_body_form_group.isHidden())
            self.assertFalse(tab.joint_form_group.isHidden())
            self.assertEqual(tab.joint_type_spin.text(), "Spring 6DOF")
            self.assertTrue(tab.joint_rotation_max_edit.isEnabled())
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_retranslate_ui_en_ja(self):
        translator = UITranslator.instance()
        previous_language = translator.get_language()
        tab = PhysicsTab()
        try:
            translator.set_language("en")
            tab.retranslateUi()
            en_refresh = tab.refresh_btn.text()
            en_mass = tab._form_labels["rigid_mass"][1].text()

            translator.set_language("ja")
            tab.retranslateUi()
            self.assertNotEqual(tab.refresh_btn.text(), en_refresh)
            self.assertNotEqual(tab._form_labels["rigid_mass"][1].text(), en_mass)
            self.assertEqual(tab.list_tabs.tabText(0), translator.translate("rigid_bodies", "tabs"))
            self.assertEqual(tab.list_tabs.tabText(1), translator.translate("joints", "tabs"))
        finally:
            translator.set_language(previous_language)
            tab.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
