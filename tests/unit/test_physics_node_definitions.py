"""Unit tests for physics node module definitions (no Maya required).

Validates that the node modules parse correctly, export the expected
symbols, and have consistent TypeId/typeName conventions.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

NODES_DIR = Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes"


class TestMmdRigidBodyShapeModule(unittest.TestCase):
    """Validate mmd_rigid_body_shape.py structure without importing Maya."""

    def setUp(self):
        self.source = (NODES_DIR / "mmd_rigid_body_shape.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_module_parses(self):
        self.assertIsNotNone(self.tree)

    def test_has_maya_use_new_api(self):
        func_names = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        ]
        self.assertIn("maya_useNewAPI", func_names)

    def test_has_required_functions(self):
        func_names = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        ]
        for name in ("creator", "initialize", "register", "deregister"):
            self.assertIn(name, func_names, f"Missing function: {name}")

    def test_has_class_with_expected_name(self):
        class_names = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.ClassDef)
        ]
        self.assertIn("MmdRigidBodyShape", class_names)

    def test_typeid_is_0x00128005(self):
        self.assertIn("0x00128005", self.source)

    def test_typename_is_mmd_rigid_body_shape(self):
        self.assertIn('"mmdRigidBodyShape"', self.source)

    def test_has_all_pmx_rigid_body_attributes(self):
        expected_attrs = [
            "pmxIndex", "nameJp", "nameEn", "enable", "shapeType",
            "shapeSize", "position", "rotation",
            "physicsMode", "mass", "linearDamping",
            "angularDamping", "friction", "restitution",
            "collisionGroup", "collisionMask", "relatedBoneIndex",
            "relatedBone", "outDescriptorVersion",
        ]
        for attr in expected_attrs:
            self.assertIn(attr, self.source, f"Missing attribute: {attr}")

    def test_locator_node_registration(self):
        self.assertIn("kLocatorNode", self.source)

    def test_rotation_uses_angle_unit(self):
        self.assertIn("kAngle", self.source)


class TestMmdPhysicsJointShapeModule(unittest.TestCase):
    """Validate mmd_physics_joint_shape.py structure without importing Maya."""

    def setUp(self):
        self.source = (NODES_DIR / "mmd_physics_joint_shape.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_module_parses(self):
        self.assertIsNotNone(self.tree)

    def test_has_maya_use_new_api(self):
        func_names = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        ]
        self.assertIn("maya_useNewAPI", func_names)

    def test_has_required_functions(self):
        func_names = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        ]
        for name in ("creator", "initialize", "register", "deregister"):
            self.assertIn(name, func_names, f"Missing function: {name}")

    def test_has_class_with_expected_name(self):
        class_names = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.ClassDef)
        ]
        self.assertIn("MmdPhysicsJointShape", class_names)

    def test_typeid_is_0x00128007(self):
        self.assertIn("0x00128007", self.source)

    def test_typename_is_mmd_physics_joint_shape(self):
        self.assertIn('"mmdPhysicsJointShape"', self.source)

    def test_has_all_pmx_joint_attributes(self):
        expected_attrs = [
            "pmxIndex", "nameJp", "nameEn", "enable", "jointType",
            "position", "rotation",
            "translationLimitMin", "translationLimitMax",
            "rotationLimitMin", "rotationLimitMax",
            "springTranslation", "springRotation",
            "rigidBodyAIndex", "rigidBodyBIndex",
            "rigidBodyA", "rigidBodyB", "outDescriptorVersion",
        ]
        for attr in expected_attrs:
            self.assertIn(attr, self.source, f"Missing attribute: {attr}")

    def test_locator_node_registration(self):
        self.assertIn("kLocatorNode", self.source)

    def test_rotation_uses_angle_unit(self):
        self.assertIn("kAngle", self.source)


class TestTypeIdUniqueness(unittest.TestCase):
    """Ensure physics node TypeIds don't collide with each other or existing nodes."""

    def test_rigid_body_and_joint_have_different_typeids(self):
        rb_src = (NODES_DIR / "mmd_rigid_body_shape.py").read_text(encoding="utf-8")
        jt_src = (NODES_DIR / "mmd_physics_joint_shape.py").read_text(encoding="utf-8")
        self.assertIn("0x00128005", rb_src)
        self.assertIn("0x00128007", jt_src)
        self.assertNotIn("0x00128007", rb_src)
        self.assertNotIn("0x00128005", jt_src)


if __name__ == "__main__":
    unittest.main()
