"""
PhysicsTab の GUI シェル／フォーム／言語切替テスト。

実際の Maya GUI 環境でのみ実行可能。scene 書込は presenter unit / mayapy
integration が担当し、ここではタブ UI 契約と代表的フォーム状態を固定する。
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
            self.assertTrue(tab.joint_body_a_spin.isReadOnly())
            self.assertTrue(tab.joint_body_b_spin.isReadOnly())

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
                self.assertTrue(hasattr(tab, attr), "missing attribute: {0}".format(attr))

            self.assertFalse(tab.collider_visible_check.isChecked())
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertFalse(tab.reset_btn.isEnabled())
            self.assertFalse(tab.physics_details_content.isEnabled())
            self.assertFalse(tab.rigid_shape_combo.isEnabled())
            self.assertFalse(tab.rigid_physics_mode_combo.isEnabled())
            self.assertTrue(tab.rigid_related_bone_spin.isReadOnly())
            self.assertTrue(tab.rigid_collision_group_spin.isReadOnly())
            self.assertTrue(tab.rigid_collision_mask_spin.isReadOnly())
            self.assertTrue(tab.joint_type_spin.isReadOnly())
            self.assertTrue(tab.joint_body_a_spin.isReadOnly())
            self.assertTrue(tab.joint_body_b_spin.isReadOnly())

            tab.set_physics_details_enabled(True)
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertFalse(tab.reset_btn.isEnabled())
            tab.set_physics_dirty(True)
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertTrue(tab.reset_btn.isEnabled())
            tab.set_physics_dirty(True, valid=True)
            self.assertTrue(tab.apply_btn.isEnabled())
            tab.set_physics_details_enabled(False)
            self.assertFalse(tab.apply_btn.isEnabled())
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_list_tab_switch_and_search_placeholders(self):
        """Rigid Body / Joint サブタブ切替と検索プレースホルダを確認する。"""
        tab = PhysicsTab()
        try:
            self.assertEqual(tab.list_tabs.currentIndex(), 0)
            self.assertIsNotNone(tab.rigid_body_list)
            self.assertIsNotNone(tab.joint_list)
            self.assertEqual(tab.list_tabs.count(), 2)

            tab.list_tabs.setCurrentIndex(1)
            QApplication.processEvents()
            self.assertEqual(tab.list_tabs.currentIndex(), 1)

            tab.list_tabs.setCurrentIndex(0)
            QApplication.processEvents()
            self.assertEqual(tab.list_tabs.currentIndex(), 0)

            tab.rigid_body_search_edit.setText("hair")
            tab.joint_search_edit.setText("joint")
            QApplication.processEvents()
            self.assertEqual(tab.rigid_body_search_edit.text(), "hair")
            self.assertEqual(tab.joint_search_edit.text(), "joint")
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
            tab.set_physics_dirty(True, valid=True)
            self.assertTrue(tab.apply_btn.isEnabled())
            self.assertTrue(tab.reset_btn.isEnabled())
            self.assertEqual(tab.get_physics_form_values("rigid")["mass"], "3.0")

            tab.set_physics_validation_error(
                "mass",
                "physics_validation_finite",
            )
            tab.set_physics_dirty(True, valid=False)
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertTrue(tab.reset_btn.isEnabled())
            self.assertFalse(tab.validation_error_label.isHidden())
            self.assertIn(tab._form_labels["rigid_mass"][1].text(), tab.validation_error_label.text())

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
            self.assertTrue(tab.validation_error_label.isHidden())
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_representative_form_state_and_readonly_graph_fields(self):
        """代表フォーム値と graph 依存フィールドの read-only 状態を固定する。"""
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
                    "related_bone": 4,
                    "collision_group": 1,
                    "collision_mask": 0xFFFD,
                    "mass": 0.5,
                    "linear_damping": 0.5,
                    "angular_damping": 0.5,
                    "restitution": 0.0,
                    "friction": 0.5,
                },
            )
            QApplication.processEvents()

            form = tab.get_physics_form_values("rigid")
            self.assertEqual(form["name"], "右髪２")
            self.assertEqual(form["name_english"], "HairR2")
            self.assertEqual(form["shape"], 2)
            self.assertEqual(form["physics_mode"], 2)
            self.assertEqual(form["related_bone"], 4)
            self.assertEqual(form["collision_group"], 1)
            self.assertEqual(form["collision_mask"], 0xFFFD)
            self.assertEqual(form["mass"], "0.5")

            # Graph-dependent fields remain non-editable widgets.
            self.assertFalse(tab.rigid_shape_combo.isEnabled())
            self.assertFalse(tab.rigid_physics_mode_combo.isEnabled())
            self.assertTrue(tab.rigid_related_bone_spin.isReadOnly())
            self.assertTrue(tab.rigid_collision_group_spin.isReadOnly())
            self.assertTrue(tab.rigid_collision_mask_spin.isReadOnly())
            # Editable scalars accept focus when details are enabled.
            self.assertTrue(tab.rigid_mass_edit.isEnabled())
            self.assertTrue(tab.rigid_name_edit.isEnabled())
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertFalse(tab.reset_btn.isEnabled())

            tab.set_physics_form(
                "joint",
                {
                    "name": "右髪２",
                    "name_english": "HairJointR2",
                    "joint_type": 0,
                    "rigid_body_a": 1,
                    "rigid_body_b": 2,
                    "linear_constraint_states": "0, 0, 0",
                    "angular_constraint_states": "0, 0, 0",
                    "translation_limit_min": "0, 0, 0",
                    "translation_limit_max": "0, 0, 0",
                    "rotation_limit_min_degrees": "-10, -10, -10",
                    "rotation_limit_max_degrees": "10, 10, 10",
                    "spring_translation": "0, 0, 0",
                    "spring_rotation": "0.1, 0.1, 0.1",
                    "spring_translation_enabled": "0, 0, 0",
                    "spring_rotation_enabled": "1, 1, 1",
                },
            )
            QApplication.processEvents()
            joint_form = tab.get_physics_form_values("joint")
            self.assertEqual(joint_form["name"], "右髪２")
            self.assertEqual(joint_form["joint_type"], 0)
            self.assertEqual(joint_form["rigid_body_a"], 1)
            self.assertEqual(joint_form["rigid_body_b"], 2)
            self.assertTrue(tab.joint_type_spin.isReadOnly())
            self.assertTrue(tab.joint_body_a_spin.isReadOnly())
            self.assertTrue(tab.joint_body_b_spin.isReadOnly())
            self.assertTrue(tab.joint_rotation_max_edit.isEnabled())
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
            en_rigid_tab = tab.list_tabs.tabText(0)
            en_joint_tab = tab.list_tabs.tabText(1)
            tab.set_physics_validation_error("mass", "physics_validation_finite")
            en_validation = tab.validation_error_label.text()

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
            self.assertNotEqual(tab.list_tabs.tabText(0), en_rigid_tab)
            self.assertNotEqual(tab.list_tabs.tabText(1), en_joint_tab)
            self.assertEqual(
                tab.rigid_shape_combo.itemText(0),
                translator.translate("physics_shape_sphere", "options"),
            )
            self.assertNotEqual(tab.validation_error_label.text(), en_validation)
            self.assertIn("質量", tab.validation_error_label.text())

            translator.set_language("en")
            tab.retranslateUi()
            QApplication.processEvents()
            self.assertEqual(tab.refresh_btn.text(), translator.translate("refresh", "buttons"))
            self.assertEqual(tab.details_group.title(), translator.translate("details", "groups"))
            self.assertEqual(tab.list_tabs.tabText(0), translator.translate("rigid_bodies", "tabs"))
        finally:
            translator.set_language(previous_language)
            tab.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
