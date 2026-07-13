"""Unit tests for physics_presenter (no Maya required).

Validates module structure, field-key consistency with physics_tab.py,
and the presenter's public interface via AST inspection.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
