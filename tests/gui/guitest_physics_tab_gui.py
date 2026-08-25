"""PhysicsTab GUI contract tests.

These tests run only with a real Qt application. Scene collection and Maya
selection behavior remain covered by the presenter unit tests.
"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from maya import cmds

from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.ui_action_coverage import QtSignalInvocationSpy, build_surface_witness
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter
from mmd_tools.ui.tabs.physics_tab import PhysicsTab
from mmd_tools.ui.translations import UITranslator


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


PHYSICS_SURFACE_LOCATORS = {
    "physics.list_selector": ("attribute", "list_tabs"),
    "physics.rigid_list": ("selector", "objectName=rigidBodyList"),
    "physics.joint_list": "objectName=jointList",
    "physics.apply": "objectName=physicsApplyButton",
    "physics.reset": "objectName=physicsResetButton",
    "physics.rigid_name": "objectName=physicsRigidNameEdit",
    "physics.rigid_name_english": "objectName=physicsRigidNameEnglishEdit",
    "physics.rigid_shape": "objectName=physicsRigidShapeCombo",
    "physics.rigid_physics_mode": "objectName=physicsRigidPhysicsModeCombo",
    "physics.rigid_collision_group": "objectName=physicsRigidCollisionGroupSpin",
    "physics.rigid_collision_mask": "objectName=physicsRigidCollisionMaskEdit",
    "physics.rigid_mass": "objectName=physicsRigidMassEdit",
    "physics.rigid_linear_damping": "objectName=physicsRigidLinearDampingEdit",
    "physics.rigid_angular_damping": "objectName=physicsRigidAngularDampingEdit",
    "physics.rigid_restitution": "objectName=physicsRigidRestitutionEdit",
    "physics.rigid_friction": "objectName=physicsRigidFrictionEdit",
    "physics.joint_name": "objectName=physicsJointNameEdit",
    "physics.joint_name_english": "objectName=physicsJointNameEnglishEdit",
    "physics.joint_type": "objectName=physicsJointTypeCombo",
    "physics.joint_translation_min": "objectName=physicsJointTranslationMinEdit",
    "physics.joint_translation_max": "objectName=physicsJointTranslationMaxEdit",
    "physics.joint_rotation_min": "objectName=physicsJointRotationMinEdit",
    "physics.joint_rotation_max": "objectName=physicsJointRotationMaxEdit",
    "physics.joint_spring_translation": "objectName=physicsJointSpringTranslationEdit",
    "physics.joint_spring_rotation": "objectName=physicsJointSpringRotationEdit",
}


def _import_fixture(path, namespace):
    return import_mmd_file(
        str(path),
        options={
            "import_physics": True,
            "create_mmd_shaders": False,
            "setup_rig": False,
            "use_cpp_fast_load": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
            "use_namespace": True,
            "custom_namespace": namespace,
        },
    )


def _shape_from_item(item):
    from mmd_tools.ui.qt_compat import Qt

    return item.data(Qt.UserRole)


def _assert_mapping_almost_equal(test, actual, expected, fields):
    for field in fields:
        actual_value = actual[field]
        expected_value = expected[field]
        if isinstance(expected_value, (tuple, list)):
            test.assertEqual(len(actual_value), len(expected_value), field)
            for axis, (left, right) in enumerate(zip(actual_value, expected_value)):
                test.assertAlmostEqual(left, right, places=5, msg=f"{field}[{axis}]")
        elif isinstance(expected_value, float):
            test.assertAlmostEqual(actual_value, expected_value, places=5, msg=field)
        else:
            test.assertEqual(actual_value, expected_value, field)


@requires_gui
class TestPhysicsTabGUI(GuiTestBase):
    """Lock the Physics tab widget contract."""

    @staticmethod
    def _emit_surface_witness(surface_id, interaction, oracle, action_spy, control):
        """Emit one manifest-addressable witness after its semantic oracle passes."""
        locator = PHYSICS_SURFACE_LOCATORS[surface_id]
        if isinstance(locator, tuple):
            locator_key, locator_value = locator
        else:
            locator_key, locator_value = "selector", locator
        payload = build_surface_witness(
            surface_id=surface_id,
            case_id="gui.physics_tab",
            interaction=interaction,
            oracle=oracle,
            action_spy=action_spy,
            control=control,
            **{locator_key: locator_value},
        )
        print(
            "[UI COVERAGE WITNESS] "
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            flush=True,
        )

    @staticmethod
    def _surface_selector(surface_id):
        locator = PHYSICS_SURFACE_LOCATORS[surface_id]
        return locator[1] if isinstance(locator, tuple) else locator

    def test_shell_structure_and_defaults(self):
        tab = PhysicsTab()
        try:
            self.assertEqual(tab.list_tabs.count(), 2)
            self.assertTrue(tab.details_scroll_area.widgetResizable())
            self.assertFalse(tab.collider_visible_check.isChecked())
            self.assertFalse(tab.physics_enable_check.isChecked())
            self.assertFalse(tab.physics_enable_check.isEnabled())
            self.assertFalse(tab.physics_details_content.isEnabled())
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertFalse(tab.reset_btn.isEnabled())
            self.assertFalse(hasattr(tab, "scope_notice_label"))
            self.assertEqual(tab.layout().count(), 1)
            self.assertIs(tab.layout().itemAt(0).widget(), tab.splitter)
        finally:
            tab.deleteLater()
            QApplication.processEvents()

    def test_unsupported_mutation_buttons_are_hidden_from_visible_toolbar(self):
        tab = PhysicsTab()
        try:
            tab.show()
            QApplication.processEvents()
            self.assertTrue(tab.refresh_btn.isVisibleTo(tab))
            self.assertTrue(tab.collider_visible_check.isVisibleTo(tab))
            self.assertTrue(tab.physics_enable_check.isVisibleTo(tab))
            for button in (tab.create_btn, tab.duplicate_btn, tab.delete_btn):
                self.assertTrue(button.isHidden())

            group_layout = tab.physics_objects_group.layout()
            toolbar_layout = group_layout.itemAt(0).layout()
            visible_widgets = [
                toolbar_layout.itemAt(index).widget()
                for index in range(toolbar_layout.count())
                if toolbar_layout.itemAt(index).widget() is not None
                and toolbar_layout.itemAt(index).widget().isVisibleTo(tab)
            ]
            self.assertEqual(
                visible_widgets,
                [tab.refresh_btn, tab.collider_visible_check, tab.physics_enable_check],
            )
        finally:
            tab.close()
            tab.deleteLater()
            QApplication.processEvents()

    def test_forms_populate_without_editing_paths(self):
        tab = PhysicsTab()
        try:
            tab.set_physics_details_enabled(True)
            self.assertTrue(tab.physics_details_content.isEnabled())
            bone = "|Nested:Base_root|Nested:右髪２"
            body_a = "|Nested:Base_root|Nested:Physics|Nested:RigidBodies|Nested:Body"
            body_b = "|Other:Base_root|Other:Physics|Other:RigidBodies|Other:Body"
            tab.set_binding_options("rigid_related_bone", [("4: 右髪２ [HairR2]", bone, 4)])
            tab.set_binding_options(
                "joint_body_a",
                [("1: 剛体 [Body]", body_a, 1), ("2: 剛体 [Body]", body_b, 2)],
            )
            tab.set_binding_options(
                "joint_body_b",
                [("1: 剛体 [Body]", body_a, 1), ("2: 剛体 [Body]", body_b, 2)],
            )
            tab.set_physics_form(
                "rigid",
                {
                    "name": "右髪２",
                    "name_english": "HairR2",
                    "shape": 2,
                    "physics_mode": 2,
                    "shape_size": "0.5, 1.0, 1.5",
                    "pmx_position": "1, 2, 3",
                    "pmx_rotation_degrees": "10, 20, 30",
                    "related_bone": (bone, 4),
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
            self.assertTrue(tab.physics_details_content.isEnabled())
            self.assertEqual(tab.rigid_name_edit.text(), "右髪２")
            self.assertEqual(tab.rigid_shape_combo.currentIndex(), 2)
            self.assertEqual(tab.rigid_mass_edit.text(), "0.5")
            self.assertTrue(tab.rigid_mass_edit.isEnabled())
            self.assertEqual(tab.rigid_shape_size_edit.values(), (0.5, 1.0, 1.5))
            self.assertEqual(len(tab.rigid_shape_size_edit.spins), 3)
            self.assertEqual(
                [label.text() for label in tab.rigid_shape_size_edit.axis_labels],
                ["X:", "Y:", "Z:"],
            )
            self.assertEqual(
                [not spin.isHidden() for spin in tab.rigid_shape_size_edit.spins],
                [True, True, False],
            )
            self.assertEqual(
                [not label.isHidden() for label in tab.rigid_shape_size_edit.axis_labels],
                [True, True, False],
            )
            tab.rigid_shape_combo.setCurrentIndex(0)
            self.assertEqual(
                [not spin.isHidden() for spin in tab.rigid_shape_size_edit.spins],
                [True, False, False],
            )
            self.assertEqual(
                [not label.isHidden() for label in tab.rigid_shape_size_edit.axis_labels],
                [True, False, False],
            )
            tab.rigid_shape_combo.setCurrentIndex(1)
            self.assertEqual(
                [not spin.isHidden() for spin in tab.rigid_shape_size_edit.spins],
                [True, True, True],
            )
            self.assertEqual(tab.rigid_shape_size_edit.values(), (0.5, 1.0, 1.5))
            tab.rigid_shape_combo.setCurrentIndex(2)
            self.assertEqual(tab.rigid_collision_group_spin.value(), 1)
            self.assertEqual(tab.rigid_collision_mask_spin.value(), 2)
            self.assertEqual(tab.rigid_collision_group_spin.minimum(), 0)
            self.assertEqual(tab.rigid_collision_group_spin.maximum(), 15)
            self.assertEqual(len(tab.rigid_collision_mask_spin.buttons), 16)
            self.assertAlmostEqual(tab.rigid_mass_edit.value(), 0.5)
            self.assertEqual(tab.rigid_mass_edit.slider.maximum(), 1000)
            self.assertEqual(tab.rigid_related_bone_combo.objectName(), "rigidRelatedBoneCombo")
            self.assertEqual(tab.binding_selection("rigid_related_bone"), (bone, 4))
            tab.set_physics_form("rigid", {"related_bone": (bone, 999)})
            self.assertEqual(tab.binding_selection("rigid_related_bone"), (bone, 4))

            tab.set_physics_form(
                "joint",
                {
                    "name": "右髪２",
                    "name_english": "HairJointR2",
                    "joint_type": 0,
                    "rigid_body_a": (body_a, 1),
                    "rigid_body_b": (body_b, 2),
                    "pmx_position": "4, 5, 6",
                    "pmx_rotation_degrees": "40, 50, 60",
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
            self.assertEqual(tab.joint_type_combo.currentIndex(), 0)
            self.assertEqual(
                tab.joint_type_combo.currentText(),
                tab.tr("physics_joint_spring_6dof", "options"),
            )
            self.assertEqual(tab.joint_type_combo.count(), 6)
            self.assertTrue(tab.joint_rotation_max_edit.isEnabled())
            self.assertEqual(tab.joint_position_edit.values(), (4.0, 5.0, 6.0))
            self.assertEqual(tab.joint_body_a_combo.objectName(), "jointRigidBodyACombo")
            self.assertEqual(tab.joint_body_b_combo.objectName(), "jointRigidBodyBCombo")
            self.assertEqual(tab.binding_selection("joint_body_a"), (body_a, 1))
            self.assertEqual(tab.binding_selection("joint_body_b"), (body_b, 2))
            for hidden_key in (
                "joint_linear_states", "joint_angular_states",
                "joint_spring_translation_enabled", "joint_spring_rotation_enabled",
            ):
                self.assertNotIn(hidden_key, tab._form_labels)
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
            # Refresh is an icon-only SymbolToolButton; localization lives in
            # its tooltip/accessibility contract rather than visible text.
            en_refresh_tooltip = tab.refresh_btn.toolTip()
            en_refresh_accessible = tab.refresh_btn.accessibleName()
            en_mass = tab._form_labels["rigid_mass"][1].text()
            en_physics_enable = tab.physics_enable_check.text()
            en_none = tab.rigid_related_bone_combo.itemText(0)
            self.assertEqual(
                en_physics_enable,
                translator.translate("enable_physics", "checkboxes"),
            )

            translator.set_language("ja")
            tab.retranslateUi()
            self.assertNotEqual(tab.refresh_btn.toolTip(), en_refresh_tooltip)
            self.assertNotEqual(tab.refresh_btn.accessibleName(), en_refresh_accessible)
            self.assertEqual(tab.refresh_btn.toolTip(), translator.translate("refresh", "buttons"))
            self.assertEqual(tab.refresh_btn.accessibleName(), translator.translate("refresh", "buttons"))
            self.assertNotEqual(tab._form_labels["rigid_mass"][1].text(), en_mass)
            self.assertNotEqual(tab.physics_enable_check.text(), en_physics_enable)
            self.assertNotEqual(tab.rigid_related_bone_combo.itemText(0), en_none)
            self.assertEqual(
                tab.physics_enable_check.text(),
                translator.translate("enable_physics", "checkboxes"),
            )
            self.assertEqual(tab.list_tabs.tabText(0), translator.translate("rigid_bodies", "tabs"))
            self.assertEqual(tab.list_tabs.tabText(1), translator.translate("joints", "tabs"))
        finally:
            translator.set_language(previous_language)
            tab.deleteLater()
            QApplication.processEvents()

    @unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
    def test_namespaced_widget_apply_undo_descriptor(self):
        """One production Apply updates the Maya descriptor and one Undo restores it."""
        cmds.file(new=True, force=True)
        if "mmdRigidBodyShape" not in (cmds.allNodeTypes() or []):
            cmds.loadPlugin("plugin_main.py")
        root = _import_fixture(FIXTURE_PATH, "Base")
        status_messages = []
        app_state = SimpleNamespace(current_model_root=root, emit_status=status_messages.append)
        tab = PhysicsTab()
        PhysicsPresenter(tab, app_state)
        try:
            tab.show()
            tab.rigid_body_list.setCurrentRow(0)
            QApplication.processEvents()
            rigid_shape = _shape_from_item(tab.rigid_body_list.currentItem())
            original = cmds.getAttr(f"{rigid_shape}.nameEn")
            version_before = cmds.getAttr(f"{rigid_shape}.outDescriptorVersion")
            tab.rigid_name_english_edit.setText("UIEditedRigid")
            apply_spy = QtSignalInvocationSpy(
                "PhysicsPresenter.apply_changes", tab.apply_btn.clicked, tab.apply_btn
            )
            tab.apply_btn.click()
            QApplication.processEvents()
            self.assertEqual(apply_spy.action_count, 1)
            self.assertEqual(cmds.getAttr(f"{rigid_shape}.nameEn"), "UIEditedRigid")
            self.assertGreater(cmds.getAttr(f"{rigid_shape}.outDescriptorVersion"), version_before)
            cmds.undo()
            self.assertEqual(cmds.getAttr(f"{rigid_shape}.nameEn"), original)
            self.assertTrue(status_messages)
        finally:
            tab.close()
            tab.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
