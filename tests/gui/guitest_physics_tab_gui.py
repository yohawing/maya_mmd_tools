"""
PhysicsTab の GUI シェルテスト (Slice A: splitter / search / scroll / i18n)。
実際の Maya GUI 環境でのみ実行可能。
"""

import unittest
import sys

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.tabs.physics_tab import PhysicsTab
from mmd_tools.ui.translations import UITranslator


@requires_gui
class TestPhysicsTabGUI(GuiTestBase):
    """PhysicsTab の UI 契約を実 Qt ウィジェットで検証する。"""

    def test_shell_structure_and_defaults(self):
        """splitter / search / scroll / Apply-Reset / 既定値と legacy 属性を確認する。"""
        tab = PhysicsTab()
        try:
            self.assertIsNotNone(tab.splitter)
            self.assertIsNotNone(tab.list_tabs)
            self.assertEqual(tab.list_tabs.count(), 2)
            self.assertIsNotNone(tab.rigid_body_search_edit)
            self.assertIsNotNone(tab.joint_search_edit)
            self.assertIsNotNone(tab.details_scroll_area)
            self.assertTrue(tab.details_scroll_area.widgetResizable())
            self.assertIsNotNone(tab.apply_btn)
            self.assertIsNotNone(tab.reset_btn)

            for attr in (
                "refresh_btn",
                "collider_visible_check",
                "rigid_body_list",
                "joint_list",
                "detail_name_value",
                "detail_type_value",
                "detail_shape_value",
                "detail_bodies_value",
                "detail_node_value",
            ):
                self.assertTrue(hasattr(tab, attr), f"missing attribute: {attr}")

            self.assertFalse(tab.collider_visible_check.isChecked())
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertFalse(tab.reset_btn.isEnabled())
            self.assertFalse(tab.physics_details_content.isEnabled())

            tab.set_physics_details_enabled(True)
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertFalse(tab.reset_btn.isEnabled())
            tab.set_physics_dirty(True)
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertTrue(tab.reset_btn.isEnabled())
            tab.set_physics_details_enabled(False)
            self.assertFalse(tab.apply_btn.isEnabled())
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_editable_forms_populate_without_dirty_then_emit_on_user_edit(self):
        """cached populate は clean、ユーザー編集だけが form changed を通知する。"""
        tab = PhysicsTab()
        changes = []
        tab.physics_form_changed.connect(lambda: changes.append(True))
        try:
            tab.set_physics_details_enabled(True)
            tab.set_physics_form(
                "rigid",
                {
                    "name": "skirt",
                    "name_english": "Skirt",
                    "shape": 1,
                    "physics_mode": 2,
                    "related_bone": 9,
                    "collision_group": 7,
                    "collision_mask": 0xFF7F,
                    "mass": sys.float_info.max,
                    "linear_damping": 0.15,
                    "angular_damping": 0.25,
                    "restitution": 0.35,
                    "friction": 0.45,
                },
            )
            self.assertFalse(tab.rigid_body_form_group.isHidden())
            self.assertTrue(tab.joint_form_group.isHidden())
            self.assertEqual(tab.rigid_name_edit.text(), "skirt")
            self.assertEqual(tab.rigid_shape_combo.currentIndex(), 1)
            self.assertEqual(tab.rigid_mass_edit.text(), repr(sys.float_info.max))
            self.assertEqual(changes, [])
            self.assertFalse(tab.apply_btn.isEnabled())

            tab.rigid_mass_edit.setText("3.0")
            QApplication.processEvents()
            self.assertEqual(changes, [True])
            tab.set_physics_dirty(True)
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertTrue(tab.reset_btn.isEnabled())

            tab.set_physics_form(
                "joint",
                {
                    "name": "joint",
                    "name_english": "Joint",
                    "joint_type": 4,
                    "rigid_body_a": 2,
                    "rigid_body_b": 5,
                    "linear_constraint_states": "0, 1, 2",
                    "angular_constraint_states": "2, 1, 0",
                    "translation_limit_min": "-1, -2, -3",
                    "translation_limit_max": "1, 2, 3",
                    "rotation_limit_min_degrees": "-10, -20, -30",
                    "rotation_limit_max_degrees": "10, 20, 30",
                    "spring_translation": "0.1, 0.2, 0.3",
                    "spring_rotation": "0.4, 0.5, 0.6",
                    "spring_translation_enabled": "1, 0, 1",
                    "spring_rotation_enabled": "0, 1, 0",
                },
            )
            self.assertTrue(tab.rigid_body_form_group.isHidden())
            self.assertFalse(tab.joint_form_group.isHidden())
            self.assertEqual(tab.joint_rotation_max_edit.text(), "10, 20, 30")
            self.assertFalse(tab.apply_btn.isEnabled())
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_retranslate_ui_en_ja(self):
        """retranslateUi() が EN/JA で代表的なラベルを切り替える。"""
        translator = UITranslator.instance()
        previous_language = translator.get_language()
        tab = PhysicsTab()
        try:
            translator.set_language("en")
            tab.retranslateUi()
            QApplication.processEvents()
            en_refresh = tab.refresh_btn.text()
            en_details = tab.details_group.title()
            en_search = tab.rigid_body_search_edit.placeholderText()
            en_mass = tab._form_labels["rigid_mass"][1].text()
            en_shape_option = tab.rigid_shape_combo.itemText(0)

            translator.set_language("ja")
            tab.retranslateUi()
            QApplication.processEvents()
            self.assertEqual(tab.refresh_btn.text(), translator.translate("refresh", "buttons"))
            self.assertEqual(tab.details_group.title(), translator.translate("details", "groups"))
            self.assertEqual(
                tab.rigid_body_search_edit.placeholderText(),
                translator.translate("search_rigid_bodies", "placeholders"),
            )
            self.assertEqual(tab.list_tabs.tabText(0), translator.translate("rigid_bodies", "tabs"))
            self.assertEqual(tab.list_tabs.tabText(1), translator.translate("joints", "tabs"))
            self.assertNotEqual(tab.refresh_btn.text(), en_refresh)
            self.assertNotEqual(tab.details_group.title(), en_details)
            self.assertNotEqual(tab.rigid_body_search_edit.placeholderText(), en_search)
            self.assertNotEqual(tab._form_labels["rigid_mass"][1].text(), en_mass)
            self.assertNotEqual(tab.rigid_shape_combo.itemText(0), en_shape_option)
            self.assertEqual(
                tab.rigid_shape_combo.itemText(0),
                translator.translate("physics_shape_sphere", "options"),
            )

            translator.set_language("en")
            tab.retranslateUi()
            QApplication.processEvents()
            self.assertEqual(tab.refresh_btn.text(), translator.translate("refresh", "buttons"))
            self.assertEqual(tab.details_group.title(), translator.translate("details", "groups"))
        finally:
            translator.set_language(previous_language)
            tab.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
