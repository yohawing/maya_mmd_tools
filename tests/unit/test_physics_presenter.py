"""Unit tests for physics_presenter (no Maya required).

Validates module structure, field-key consistency with physics_tab.py,
and the presenter's public interface via AST inspection.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.presenters.physics_presenter import (  # noqa: E402
    _candidate_display,
    _related_bone_display,
)
from mmd_tools.ui.translations import UITranslator  # noqa: E402

PRESENTER_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "ui" / "presenters" / "physics_presenter.py"
TAB_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "ui" / "tabs" / "physics_tab.py"


class TestPresenterModuleStructure(unittest.TestCase):

    def setUp(self):
        self.source = PRESENTER_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_module_parses(self):
        self.assertIsNotNone(self.tree)

    def test_has_physics_presenter_class(self):
        class_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)]
        self.assertIn("PhysicsPresenter", class_names)

    def test_has_refresh_physics(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("refresh_physics", func_names)

    def test_has_filter_methods(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("filter_rigid_bodies", func_names)
        self.assertIn("filter_joints", func_names)

    def test_has_selection_handlers(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("_on_rigid_body_selected", func_names)
        self.assertIn("_on_joint_selected", func_names)

    def test_has_read_value_methods(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("_read_rigid_body_values", func_names)
        self.assertIn("_read_joint_values", func_names)

    def test_has_apply_reset_methods(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("apply_changes", func_names)
        self.assertIn("reset_changes", func_names)

    def test_has_write_back_helpers(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("_apply_validated_rigid_body", func_names)
        self.assertIn("_apply_validated_joint", func_names)

    def test_has_collect_form_values(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("_collect_rigid_body_form_values", func_names)
        self.assertIn("_collect_joint_form_values", func_names)

    def test_uses_validation(self):
        self.assertIn("parse_rigid_body_form", self.source)
        self.assertIn("parse_joint_form", self.source)
        self.assertIn("PhysicsFormValidationError", self.source)

    def test_has_create_delete_duplicate_methods(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("create_item", func_names)
        self.assertIn("duplicate_item", func_names)
        self.assertIn("delete_item", func_names)
        self.assertIn("_create_rigid_body", func_names)
        self.assertIn("_create_joint", func_names)
        self.assertIn("_duplicate_rigid_body", func_names)
        self.assertIn("_duplicate_joint", func_names)

    def test_has_visibility_methods(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("_on_collider_visibility_changed", func_names)
        self.assertIn("_sync_collider_visibility_checkbox", func_names)

    def test_has_scene_global_physics_enable_methods(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("_on_physics_enable_changed", func_names)
        self.assertIn("_sync_physics_enable_checkbox", func_names)
        self.assertIn("_find_physics_world_shape", func_names)
        self.assertIn('chunkName="MMD Physics Enable"', self.source)

    def test_model_solver_lookup_uses_registry_with_legacy_fallback(self):
        self.assertIn("_model_physics_solvers", self.source)
        self.assertIn("REGISTRY_CATEGORY_PHYSICS", self.source)
        self.assertIn("list_model_registry_members", self.source)

    def test_has_parse_vector_str(self):
        self.assertIn("_parse_vector_str", self.source)

    def test_uses_undo_info(self):
        self.assertIn("undoInfo", self.source)

    def test_uses_list_presenter_helpers(self):
        self.assertIn("apply_list_filter", self.source)
        self.assertIn("select_existing_user_role_nodes", self.source)
        self.assertIn("reload_for_current_model_change", self.source)

    def test_uses_physics_constants(self):
        self.assertIn("PHYSICS_GROUP", self.source)
        self.assertIn("RIGID_BODIES_GROUP", self.source)
        self.assertIn("CONSTRAINTS_GROUP", self.source)

    def test_dirty_tracking_connects_one_semantic_signal_per_editor(self):
        connect_loop = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "signal_name"
        )
        signal_names = ast.literal_eval(connect_loop.iter)
        self.assertEqual(
            signal_names,
            ("currentIndexChanged", "valueChanged", "textChanged"),
        )
        self.assertTrue(
            any(isinstance(node, ast.Break) for node in ast.walk(connect_loop)),
            "dirty tracking must stop after the first supported semantic signal",
        )


class TestPhysicsBindingCandidateDisplay(unittest.TestCase):
    def setUp(self):
        self.translator = UITranslator.instance()
        self.previous_language = self.translator.get_language()
        self.translator.set_language("en")

    def tearDown(self):
        self.translator.set_language(self.previous_language)

    def test_empty_english_name_uses_ascii_maya_leaf_for_non_ascii_japanese_name(self):
        self.assertEqual(
            _candidate_display(3, "笑い", "", "|char:rig|char:joint"),
            "3: joint",
        )

    def test_rigid_body_list_prefix_keeps_ascii_leaf_fallback(self):
        self.assertEqual(
            _candidate_display(
                4,
                "剛体",
                "",
                "|char:physics|char:rigid_body_4",
                prefix="G2 ",
            ),
            "4: G2 rigid_body_4",
        )

    def test_related_bone_never_exposes_japanese_name_in_english_ui(self):
        self.assertEqual(
            _related_bone_display("右腕", "", "|char:rig|char:right_arm"),
            "right_arm",
        )

    def test_related_bone_prefers_english_metadata(self):
        self.assertEqual(
            _related_bone_display("右腕", "Right Arm", "|char:rig|char:right_arm"),
            "Right Arm",
        )


class TestRigidBodyFieldKeys(unittest.TestCase):
    """Verify presenter returns field keys matching the tab's _physics_editors."""

    def setUp(self):
        self.presenter_src = PRESENTER_PATH.read_text(encoding="utf-8")

    def test_rigid_body_value_keys_present(self):
        expected_keys = [
            '"name"', '"name_english"', '"shape"', '"physics_mode"',
            '"related_bone"', '"collision_group"', '"collision_mask"',
            '"mass"', '"linear_damping"', '"angular_damping"',
            '"restitution"', '"friction"', '"node"',
            '"shape_size"', '"pmx_position"', '"pmx_rotation_degrees"',
        ]
        for key in expected_keys:
            self.assertIn(key, self.presenter_src, f"Missing rigid body field key: {key}")


class TestJointFieldKeys(unittest.TestCase):
    """Verify presenter returns field keys matching the tab's joint form."""

    def setUp(self):
        self.presenter_src = PRESENTER_PATH.read_text(encoding="utf-8")

    def test_joint_value_keys_present(self):
        expected_keys = [
            '"name"', '"name_english"', '"joint_type"',
            '"rigid_body_a"', '"rigid_body_b"',
            '"translation_limit_min"', '"translation_limit_max"',
            '"rotation_limit_min_degrees"', '"rotation_limit_max_degrees"',
            '"spring_translation"', '"spring_rotation"',
            '"pmx_position"', '"pmx_rotation_degrees"',
            '"node"',
        ]
        for key in expected_keys:
            self.assertIn(key, self.presenter_src, f"Missing joint field key: {key}")


class TestTabStructure(unittest.TestCase):
    """Verify the tab's structure is preserved correctly."""

    def setUp(self):
        self.tab_source = TAB_PATH.read_text(encoding="utf-8")

    def test_has_set_physics_form(self):
        self.assertIn("set_physics_form", self.tab_source)

    def test_has_set_physics_details_enabled(self):
        self.assertIn("set_physics_details_enabled", self.tab_source)

    def test_has_apply_reset_buttons(self):
        self.assertIn("apply_btn", self.tab_source)
        self.assertIn("reset_btn", self.tab_source)

    def test_has_create_delete_duplicate_buttons(self):
        self.assertIn("create_btn", self.tab_source)
        self.assertIn("duplicate_btn", self.tab_source)
        self.assertIn("delete_btn", self.tab_source)

    def test_has_persistent_physics_enable_checkbox_next_to_collider_toggle(self):
        self.assertIn("physics_enable_check", self.tab_source)
        collider_index = self.tab_source.index("toolbar_layout.addWidget(self.collider_visible_check)")
        enable_index = self.tab_source.index("toolbar_layout.addWidget(self.physics_enable_check)")
        self.assertLess(collider_index, enable_index)

    def test_physics_enable_checkbox_is_translated_in_all_supported_languages(self):
        self.assertIn(
            '("physics_enable_check", "setText", "enable_physics", "checkboxes")',
            self.tab_source,
        )
        self.assertNotIn('QCheckBox("物理を有効化")', self.tab_source)
        translation_dir = TAB_PATH.parents[1] / "translations"
        for language in ("ja", "en", "zh_cn", "zh_tw"):
            text = (translation_dir / f"{language}.json").read_text(encoding="utf-8")
            self.assertIn('"enable_physics":', text, language)

    def test_authoring_only_notice_widget_and_translation_key_are_removed(self):
        self.assertNotIn("scope_notice_label", self.tab_source)
        self.assertNotIn("physics_authoring_only_notice", self.tab_source)
        translation_dir = TAB_PATH.parents[1] / "translations"
        for language in ("en", "ja"):
            text = (translation_dir / f"{language}.json").read_text(encoding="utf-8")
            self.assertNotIn("physics_authoring_only_notice", text)

    def test_supported_pose_rows_exist_and_unsupported_rows_are_not_added(self):
        for editor_key in (
            "rigid_shape_size", "rigid_position", "rigid_rotation",
            "joint_position", "joint_rotation",
        ):
            self.assertIn(f'"{editor_key}"', self.tab_source)

        for editor_key in (
            "joint_linear_states", "joint_angular_states",
            "joint_spring_translation_enabled", "joint_spring_rotation_enabled",
        ):
            self.assertNotIn(f'_add_editor_row(layout, "{editor_key}"', self.tab_source)

    def test_binding_combo_rows_are_exposed_with_stable_object_names(self):
        expected = {
            "rigid_related_bone": "rigidRelatedBoneCombo",
            "joint_body_a": "jointRigidBodyACombo",
            "joint_body_b": "jointRigidBodyBCombo",
        }
        for editor_key, object_name in expected.items():
            self.assertIn(f'"{editor_key}"', self.tab_source)
            self.assertIn(f'"{object_name}"', self.tab_source)
        self.assertIn("editor.setObjectName(object_name)", self.tab_source)

    def test_binding_combo_helpers_preserve_node_identity_and_translation(self):
        for method in (
            "set_binding_options",
            "binding_selection",
            "_retranslate_binding_none_items",
        ):
            self.assertIn(f"def {method}", self.tab_source)

if __name__ == "__main__":
    unittest.main()
