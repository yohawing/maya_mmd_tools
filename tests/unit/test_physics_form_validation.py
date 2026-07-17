"""Physics form parsing and validation tests (Maya/Qt independent)."""

import math
import json
import unittest
from pathlib import Path

from mmd_tools.core.physics_form_validation import (
    JointFormValues,
    PhysicsFormValidationError,
    RigidBodyFormValues,
    parse_joint_form,
    parse_rigid_body_form,
)


def _rigid_values():
    return {
        "name": "skirt",
        "name_english": "Skirt",
        "shape": 1,
        "physics_mode": 2,
        "related_bone": 9,
        "shape_size": "0.5, 1.25, 2.0",
        "pmx_position": "1, 2, 3",
        "pmx_rotation_degrees": "10, 20, 30",
        "collision_group": 7,
        "collision_mask": 0xFF7F,
        "mass": "2.5",
        "linear_damping": "0.15",
        "angular_damping": "0.25",
        "restitution": "0.35",
        "friction": "0.45",
    }


def _joint_values():
    return {
        "name": "joint",
        "name_english": "Joint",
        "joint_type": 4,
        "rigid_body_a": 2,
        "rigid_body_b": 5,
        "pmx_position": "4, 5, 6",
        "pmx_rotation_degrees": "40, 50, 60",
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
    }


class TestRigidBodyFormValidation(unittest.TestCase):
    def test_parses_valid_values_to_typed_cache(self):
        parsed = parse_rigid_body_form(_rigid_values())

        self.assertIsInstance(parsed, RigidBodyFormValues)
        self.assertEqual(parsed.shape_type, 1)
        self.assertEqual(parsed.physics_mode, 2)
        self.assertEqual(parsed.collision_mask, 0xFF7F)
        self.assertEqual(parsed.shape_size, (0.5, 1.25, 2.0))
        self.assertEqual(parsed.pmx_position, (1.0, 2.0, 3.0))
        self.assertEqual(parsed.pmx_rotation_degrees, (10.0, 20.0, 30.0))
        self.assertEqual(parsed.mass, 2.5)
        self.assertEqual(parsed.linear_damping, 0.15)

    def test_rejects_invalid_ranges_and_non_finite_numbers(self):
        cases = (
            ("shape", 3, "physics_validation_range"),
            ("physics_mode", -1, "physics_validation_range"),
            ("related_bone", -2, "physics_validation_minimum"),
            ("collision_group", 16, "physics_validation_range"),
            ("collision_mask", 0x10000, "physics_validation_range"),
            ("mass", -0.1, "physics_validation_minimum"),
            ("shape_size", "0.5, -1, 2", "physics_validation_minimum"),
            ("shape_size", "0.5, 1", "physics_validation_vector_length"),
            ("pmx_position", "0, inf, 1", "physics_validation_finite"),
            ("pmx_rotation_degrees", "0, nan, 1", "physics_validation_finite"),
            ("mass", math.inf, "physics_validation_finite"),
            ("linear_damping", "nan", "physics_validation_finite"),
            ("friction", "not-a-number", "physics_validation_number"),
        )
        for field, value, message_key in cases:
            with self.subTest(field=field, value=value):
                values = _rigid_values()
                values[field] = value
                with self.assertRaises(PhysicsFormValidationError) as caught:
                    parse_rigid_body_form(values)
                self.assertEqual(caught.exception.field_key, field)
                self.assertEqual(caught.exception.message_key, message_key)

    def test_damping_uses_finite_only_not_a_guessed_static_bullet_range(self):
        values = _rigid_values()
        values["linear_damping"] = -12.5
        values["angular_damping"] = 1000.0

        parsed = parse_rigid_body_form(values)

        self.assertEqual(parsed.linear_damping, -12.5)
        self.assertEqual(parsed.angular_damping, 1000.0)


class TestJointFormValidation(unittest.TestCase):
    def test_parses_valid_vectors_to_typed_cache(self):
        parsed = parse_joint_form(_joint_values())

        self.assertIsInstance(parsed, JointFormValues)
        self.assertEqual(parsed.linear_constraint_states, (0, 1, 2))
        self.assertEqual(parsed.translation_limit_min, (-1.0, -2.0, -3.0))
        self.assertEqual(parsed.pmx_position, (4.0, 5.0, 6.0))
        self.assertEqual(parsed.pmx_rotation_degrees, (40.0, 50.0, 60.0))
        self.assertEqual(parsed.spring_translation_enabled, (True, False, True))

    def test_rejects_invalid_joint_values(self):
        cases = (
            ("joint_type", 7, "physics_validation_range"),
            ("rigid_body_a", -2, "physics_validation_minimum"),
            ("linear_constraint_states", "0, 1", "physics_validation_vector_length"),
            ("angular_constraint_states", "0, 3, 1", "physics_validation_range"),
            ("translation_limit_min", "0, nan, 1", "physics_validation_finite"),
            ("pmx_position", "0, 1", "physics_validation_vector_length"),
            ("pmx_rotation_degrees", "0, inf, 1", "physics_validation_finite"),
            ("spring_rotation", "0, nope, 1", "physics_validation_number"),
            ("spring_translation_enabled", "1, true, 0", "physics_validation_bool"),
            ("spring_rotation_enabled", "1, 2, 0", "physics_validation_bool"),
        )
        for field, value, message_key in cases:
            with self.subTest(field=field, value=value):
                values = _joint_values()
                values[field] = value
                with self.assertRaises(PhysicsFormValidationError) as caught:
                    parse_joint_form(values)
                self.assertEqual(caught.exception.field_key, field)
                self.assertEqual(caught.exception.message_key, message_key)


class TestPhysicsValidationTranslations(unittest.TestCase):
    def test_all_locales_define_form_validation_messages(self):
        required = {
            "physics_validation_error",
            "physics_validation_required",
            "physics_validation_text",
            "physics_validation_integer",
            "physics_validation_number",
            "physics_validation_finite",
            "physics_validation_minimum",
            "physics_validation_maximum",
            "physics_validation_range",
            "physics_validation_vector_length",
            "physics_validation_bool",
            "physics_write_node_missing",
            "physics_write_attribute_missing",
            "physics_write_failed",
            "physics_write_rollback_failed",
            "physics_write_stale_form",
            "physics_write_preflight_failed",
            "physics_write_undo_disabled",
            "physics_write_attribute_not_settable",
        }
        required_fields = {"shape_size", "pmx_position", "pmx_rotation_degrees"}
        translations = Path("mmd_tools/ui/translations")
        for locale in ("en", "ja", "zh_cn", "zh_tw"):
            with self.subTest(locale=locale):
                data = json.loads((translations / f"{locale}.json").read_text(encoding="utf-8"))
                self.assertTrue(required.issubset(data["messages"]))
                self.assertTrue(required_fields.issubset(data["fields"]))


if __name__ == "__main__":
    unittest.main()
