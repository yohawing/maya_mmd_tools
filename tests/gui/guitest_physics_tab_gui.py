"""PhysicsTab GUI contract tests.

These tests run only with a real Qt application. Scene collection and Maya
selection behavior remain covered by the presenter unit tests.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from maya import cmds
import maya.api.OpenMaya as om

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.nodes.mmd_rigid_body_draw_override import MmdRigidBodyDrawOverride, _color_for
from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter
from mmd_tools.ui.tabs.physics_tab import PhysicsTab
from mmd_tools.ui.translations import UITranslator


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


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
    def test_namespaced_widget_apply_undo_descriptor_and_pmx_roundtrip(self):
        cmds.file(new=True, force=True)
        if "mmdRigidBodyShape" not in (cmds.allNodeTypes() or []):
            cmds.loadPlugin("plugin_main.py")

        root = _import_fixture(FIXTURE_PATH, "Base")
        status_messages = []
        app_state = SimpleNamespace(current_model_root=root, emit_status=status_messages.append)
        tab = PhysicsTab()
        _presenter = PhysicsPresenter(tab, app_state)
        try:
            QApplication.processEvents()
            for button in (tab.create_btn, tab.duplicate_btn, tab.delete_btn):
                self.assertTrue(button.isHidden())
            rigid_dag_count = len(
                cmds.listRelatives(root, allDescendents=True, type="mmdRigidBodyShape") or []
            )
            joint_dag_count = len(
                cmds.listRelatives(root, allDescendents=True, type="mmdPhysicsJointShape") or []
            )
            self.assertEqual(tab.rigid_body_list.count(), rigid_dag_count)
            self.assertEqual(tab.joint_list.count(), joint_dag_count)
            self.assertRegex(tab.rigid_body_list.item(0).text(), r"^\d+:G(?:[1-9]|1[0-6]) .+ - \[.+\]$")

            tab.rigid_body_list.setCurrentRow(0)
            QApplication.processEvents()
            self.assertTrue(tab.physics_details_content.isEnabled())
            self.assertTrue(tab.apply_btn.isEnabled())
            self.assertTrue(tab.reset_btn.isEnabled())
            for button in (tab.create_btn, tab.duplicate_btn, tab.delete_btn):
                self.assertTrue(button.isHidden())
            rigid_shape = _shape_from_item(tab.rigid_body_list.currentItem())
            rigid_original = {
                attr: cmds.getAttr(f"{rigid_shape}.{attr}")
                for attr in (
                    "nameJp", "nameEn", "shapeType", "physicsMode", "collisionGroup",
                    "collisionMask", "mass", "linearDamping", "angularDamping",
                    "restitution", "friction",
                )
            }
            rigid_version_before = cmds.getAttr(f"{rigid_shape}.outDescriptorVersion")
            rigid_values = {
                "nameJp": "UI編集剛体",
                "nameEn": "UIEditedRigid",
                "shapeType": 1,
                "physicsMode": 2,
                "collisionGroup": 7,
                "collisionMask": 0x5A5A,
                "mass": 2.75,
                "linearDamping": 0.21,
                "angularDamping": 0.32,
                "restitution": 0.43,
                "friction": 0.54,
            }
            tab.rigid_name_edit.setText(rigid_values["nameJp"])
            tab.rigid_name_english_edit.setText(rigid_values["nameEn"])
            tab.rigid_shape_combo.setCurrentIndex(rigid_values["shapeType"])
            tab.rigid_physics_mode_combo.setCurrentIndex(rigid_values["physicsMode"])
            tab.rigid_collision_group_spin.setValue(rigid_values["collisionGroup"])
            tab.rigid_collision_mask_spin.setText(hex(rigid_values["collisionMask"]))
            tab.rigid_mass_edit.setText(str(rigid_values["mass"]))
            tab.rigid_linear_damping_edit.setText(str(rigid_values["linearDamping"]))
            tab.rigid_angular_damping_edit.setText(str(rigid_values["angularDamping"]))
            tab.rigid_restitution_edit.setText(str(rigid_values["restitution"]))
            tab.rigid_friction_edit.setText(str(rigid_values["friction"]))
            tab.apply_btn.click()
            QApplication.processEvents()
            for attr, expected in rigid_values.items():
                actual = cmds.getAttr(f"{rigid_shape}.{attr}")
                if isinstance(expected, float):
                    self.assertAlmostEqual(actual, expected, places=5, msg=attr)
                else:
                    self.assertEqual(actual, expected, attr)
            self.assertGreater(
                cmds.getAttr(f"{rigid_shape}.outDescriptorVersion"), rigid_version_before
            )

            cmds.undo()
            for attr, expected in rigid_original.items():
                actual = cmds.getAttr(f"{rigid_shape}.{attr}")
                if isinstance(expected, float):
                    self.assertAlmostEqual(actual, expected, places=5, msg=f"undo.{attr}")
                else:
                    self.assertEqual(actual, expected, f"undo.{attr}")
            tab.apply_btn.click()
            QApplication.processEvents()

            cmds.select(clear=True)
            selection = om.MSelectionList()
            selection.add(rigid_shape)
            rigid_path = selection.getDagPath(0)
            override = MmdRigidBodyDrawOverride.creator(rigid_path.node())
            draw_data = override.prepareForDraw(rigid_path, om.MDagPath(), None, None)
            self.assertEqual(draw_data.shape_type, rigid_values["shapeType"])
            self.assertEqual(draw_data.physics_mode, rigid_values["physicsMode"])
            self.assertEqual(draw_data.collision_group, rigid_values["collisionGroup"])
            self.assertFalse(draw_data.selected)
            expected_color = (0.498, 0.498, 0.498, 0.66)
            for actual, expected in zip(tuple(_color_for(draw_data)), expected_color):
                self.assertAlmostEqual(actual, expected, places=5)
            bbox = override.boundingBox(rigid_path, om.MDagPath())
            size = tuple(cmds.getAttr(f"{rigid_shape}.shapeSize{axis}") for axis in "XYZ")
            expected_half_extents = tuple(max(float(value), 0.001) for value in size)
            actual_half_extents = tuple(
                (bbox.max[i] - bbox.min[i]) * 0.5 for i in range(3)
            )
            for actual, expected in zip(actual_half_extents, expected_half_extents):
                self.assertAlmostEqual(actual, expected, places=5)

            tab.list_tabs.setCurrentIndex(1)
            tab.joint_list.setCurrentRow(0)
            QApplication.processEvents()
            joint_shape = _shape_from_item(tab.joint_list.currentItem())
            joint_attrs = (
                "nameJp", "nameEn", "jointType",
                "translationLimitMinX", "translationLimitMinY", "translationLimitMinZ",
                "translationLimitMaxX", "translationLimitMaxY", "translationLimitMaxZ",
                "rotationLimitMinX", "rotationLimitMinY", "rotationLimitMinZ",
                "rotationLimitMaxX", "rotationLimitMaxY", "rotationLimitMaxZ",
                "springTranslationX", "springTranslationY", "springTranslationZ",
                "springRotationX", "springRotationY", "springRotationZ",
            )
            joint_original = {attr: cmds.getAttr(f"{joint_shape}.{attr}") for attr in joint_attrs}
            joint_version_before = cmds.getAttr(f"{joint_shape}.outDescriptorVersion")
            # ScalarSliderEditor rejects malformed text before it reaches the
            # presenter, so use an actual user-reachable invalid joint form.
            # A lower translation limit above its upper limit must fail closed;
            # Reset must restore the selected joint's authored values.
            status_count_before = len(status_messages)
            tab.joint_translation_min_edit.setText("1, 0, 0")
            tab.joint_translation_max_edit.setText("0, 0, 0")
            tab.apply_btn.click()
            QApplication.processEvents()
            self.assertGreater(len(status_messages), status_count_before)
            self.assertTrue(status_messages[-1])
            for attr, expected in joint_original.items():
                actual = cmds.getAttr(f"{joint_shape}.{attr}")
                if isinstance(expected, float):
                    self.assertAlmostEqual(actual, expected, places=5, msg=f"invalid.{attr}")
                else:
                    self.assertEqual(actual, expected, f"invalid.{attr}")
            self.assertEqual(
                cmds.getAttr(f"{joint_shape}.outDescriptorVersion"), joint_version_before
            )
            tab.reset_btn.click()
            QApplication.processEvents()
            for actual, expected in zip(
                tab.joint_translation_min_edit.values(),
                (
                    joint_original["translationLimitMinX"],
                    joint_original["translationLimitMinY"],
                    joint_original["translationLimitMinZ"],
                ),
            ):
                self.assertAlmostEqual(actual, expected, places=5)
            for actual, expected in zip(
                tab.joint_translation_max_edit.values(),
                (
                    joint_original["translationLimitMaxX"],
                    joint_original["translationLimitMaxY"],
                    joint_original["translationLimitMaxZ"],
                ),
            ):
                self.assertAlmostEqual(actual, expected, places=5)
            tab.joint_name_edit.setText("UI編集ジョイント")
            tab.joint_name_english_edit.setText("UIEditedJoint")
            tab.joint_type_combo.setCurrentIndex(2)
            tab.joint_translation_min_edit.setText("-1.1, -1.2, -1.3")
            tab.joint_translation_max_edit.setText("1.1, 1.2, 1.3")
            tab.joint_rotation_min_edit.setText("-11, -12, -13")
            tab.joint_rotation_max_edit.setText("11, 12, 13")
            tab.joint_spring_translation_edit.setText("2.1, 2.2, 2.3")
            tab.joint_spring_rotation_edit.setText("3.1, 3.2, 3.3")
            tab.apply_btn.click()
            QApplication.processEvents()
            for attr, expected in joint_original.items():
                actual = cmds.getAttr(f"{joint_shape}.{attr}")
                if isinstance(expected, float):
                    self.assertAlmostEqual(actual, expected, places=5, msg=f"unsupported.{attr}")
                else:
                    self.assertEqual(actual, expected, f"unsupported.{attr}")
            self.assertEqual(
                cmds.getAttr(f"{joint_shape}.outDescriptorVersion"), joint_version_before
            )

            tab.joint_type_combo.setCurrentIndex(0)
            tab.apply_btn.click()
            QApplication.processEvents()
            self.assertEqual(cmds.getAttr(f"{joint_shape}.nameJp"), "UI編集ジョイント")
            self.assertEqual(cmds.getAttr(f"{joint_shape}.nameEn"), "UIEditedJoint")
            self.assertEqual(cmds.getAttr(f"{joint_shape}.jointType"), 0)
            self.assertGreater(
                cmds.getAttr(f"{joint_shape}.outDescriptorVersion"), joint_version_before
            )
            cmds.undo()
            for attr, expected in joint_original.items():
                actual = cmds.getAttr(f"{joint_shape}.{attr}")
                if isinstance(expected, float):
                    self.assertAlmostEqual(actual, expected, places=5, msg=f"undo.{attr}")
                else:
                    self.assertEqual(actual, expected, f"undo.{attr}")
            tab.apply_btn.click()
            QApplication.processEvents()

            before_export = ExportSceneCollector().collect_from_model_root(root)
            expected_rigid = next(
                (
                    body for body in before_export["rigid_bodies"]
                    if body["name"] == rigid_values["nameJp"]
                ),
                None,
            )
            self.assertIsNotNone(
                expected_rigid,
                [body["name"] for body in before_export["rigid_bodies"]],
            )
            expected_joint = next(
                (
                    joint for joint in before_export["joints"]
                    if joint["name"] == "UI編集ジョイント"
                ),
                None,
            )
            self.assertIsNotNone(
                expected_joint,
                [joint["name"] for joint in before_export["joints"]],
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                export_path = Path(temp_dir) / "ui_physics_roundtrip.pmx"
                PmxExporter().export_pmx_model(str(export_path), before_export)
                parsed = parse_pmx_file(str(export_path), use_native_pmx_parse=False)
                parsed_rigid = next(
                    body for body in parsed.rigid_bodies if body.name == rigid_values["nameJp"]
                )
                parsed_joint = next(
                    joint for joint in parsed.joints if joint.name == "UI編集ジョイント"
                )
                _assert_mapping_almost_equal(
                    self,
                    vars(parsed_rigid),
                    expected_rigid,
                    (
                        "name", "name_english", "shape_type", "physics_mode", "group",
                        "collision_mask", "mass", "velocity_attenuation",
                        "rotation_attenuation", "elasticity", "friction",
                    ),
                )
                _assert_mapping_almost_equal(
                    self,
                    vars(parsed_joint),
                    expected_joint,
                    (
                        "name", "name_english", "joint_type", "translation_limit_min",
                        "translation_limit_max", "rotation_limit_min", "rotation_limit_max",
                        "spring_translation", "spring_rotation",
                    ),
                )

                tab.deleteLater()
                QApplication.processEvents()
                cmds.file(new=True, force=True)
                reopened_root = _import_fixture(export_path, "Reopened")
                reopened = ExportSceneCollector().collect_from_model_root(reopened_root)
                _assert_mapping_almost_equal(
                    self,
                    next(
                        body for body in reopened["rigid_bodies"]
                        if body["name"] == rigid_values["nameJp"]
                    ),
                    expected_rigid,
                    tuple(expected_rigid),
                )
                _assert_mapping_almost_equal(
                    self,
                    next(
                        joint for joint in reopened["joints"]
                        if joint["name"] == "UI編集ジョイント"
                    ),
                    expected_joint,
                    tuple(expected_joint),
                )
        finally:
            tab.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
