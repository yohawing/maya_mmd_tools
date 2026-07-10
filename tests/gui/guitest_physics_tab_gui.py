"""
PhysicsTab の GUI シェルテスト (Slice A: splitter / search / scroll / i18n)。
実際の Maya GUI 環境でのみ実行可能。
"""

import unittest

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
            self.assertTrue(tab.apply_btn.isEnabled())
            self.assertTrue(tab.reset_btn.isEnabled())
            tab.set_physics_details_enabled(False)
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
