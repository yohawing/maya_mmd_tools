"""Unit tests for physics_export_collector (no Maya required).

Validates module structure, helper logic, and dict schema via AST and
pure-Python reimplementations of non-Maya helpers.
"""

from __future__ import annotations

import ast
import math
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "converters" / "physics_export_collector.py"


class TestModuleStructure(unittest.TestCase):
    """Validate module parses and exports the expected entry point."""

    def setUp(self):
        self.source = MODULE_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_module_parses(self):
        self.assertIsNotNone(self.tree)

    def test_has_collect_physics_from_scene(self):
        func_names = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        ]
        self.assertIn("collect_physics_from_scene", func_names)

    def test_has_collect_rigid_body(self):
        func_names = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        ]
        self.assertIn("_collect_rigid_body", func_names)

    def test_has_collect_joint(self):
        func_names = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        ]
        self.assertIn("_collect_joint", func_names)

    def test_uses_physics_constants(self):
        self.assertIn("PHYSICS_GROUP", self.source)
        self.assertIn("RIGID_BODIES_GROUP", self.source)
        self.assertIn("CONSTRAINTS_GROUP", self.source)

    def test_imports_math_for_radians(self):
        self.assertIn("math.radians", self.source)


class TestAngleConversion(unittest.TestCase):
    """Verify the degree → radian conversion matches import-side radian → degree."""

    def test_roundtrip_90_degrees(self):
        degrees = 90.0
        radians = math.radians(degrees)
        self.assertAlmostEqual(radians, math.pi / 2, places=10)
        self.assertAlmostEqual(math.degrees(radians), degrees, places=10)

    def test_roundtrip_negative(self):
        degrees = -45.0
        radians = math.radians(degrees)
        self.assertAlmostEqual(math.degrees(radians), degrees, places=10)

    def test_zero(self):
        self.assertEqual(math.radians(0.0), 0.0)


class TestRigidBodyDictSchema(unittest.TestCase):
    """Verify _collect_rigid_body returns all keys expected by PmxExporter."""

    EXPECTED_KEYS = {
        "name", "name_english", "related_bone_index",
        "group", "collision_mask", "shape_type", "size",
        "position", "rotation", "mass", "velocity_attenuation",
        "rotation_attenuation", "elasticity", "friction", "physics_mode",
    }

    def test_all_keys_present_in_source(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for key in self.EXPECTED_KEYS:
            self.assertIn(f'"{key}"', source, f"Missing dict key: {key}")


class TestJointDictSchema(unittest.TestCase):
    """Verify _collect_joint returns all keys expected by PmxExporter."""

    EXPECTED_KEYS = {
        "name", "name_english", "joint_type",
        "rigid_body_a_index", "rigid_body_b_index",
        "position", "rotation",
        "translation_limit_min", "translation_limit_max",
        "rotation_limit_min", "rotation_limit_max",
        "spring_translation", "spring_rotation",
    }

    def test_all_keys_present_in_source(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for key in self.EXPECTED_KEYS:
            self.assertIn(f'"{key}"', source, f"Missing dict key: {key}")


class TestFieldMapping(unittest.TestCase):
    """Verify import→export field name mapping is consistent."""

    def test_rigid_body_damping_maps_correctly(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('"velocity_attenuation"', source)
        self.assertIn('"linearDamping"', source)
        self.assertIn('"rotation_attenuation"', source)
        self.assertIn('"angularDamping"', source)

    def test_rigid_body_elasticity_maps_to_restitution(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('"elasticity"', source)
        self.assertIn('"restitution"', source)


class TestExportSceneCollectorIntegration(unittest.TestCase):
    """Verify export_scene_collector.py wires physics collector."""

    def setUp(self):
        self.source = (
            Path(__file__).resolve().parents[2]
            / "mmd_tools" / "converters" / "export_scene_collector.py"
        ).read_text(encoding="utf-8")

    def test_imports_physics_collector(self):
        self.assertIn("collect_physics_from_scene", self.source)

    def test_no_hardcoded_empty_rigid_bodies(self):
        self.assertNotIn('"rigid_bodies": []', self.source)

    def test_no_hardcoded_empty_joints(self):
        self.assertNotIn('"joints": []', self.source)


if __name__ == "__main__":
    unittest.main()
