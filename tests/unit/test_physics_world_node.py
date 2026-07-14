"""Unit tests for mmdPhysicsWorldShape node (no Maya required).

Validates module structure, attribute names, TypeId allocation, and the
register/deregister interface via AST/source inspection.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

WORLD_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes" / "mmd_physics_world_shape.py"
PLUGIN_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"


class TestWorldShapeStructure(unittest.TestCase):

    def setUp(self):
        self.source = WORLD_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_module_parses(self):
        self.assertIsNotNone(self.tree)

    def test_has_world_shape_class(self):
        class_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)]
        self.assertIn("MmdPhysicsWorldShape", class_names)

    def test_type_name(self):
        self.assertIn('kTypeName = "mmdPhysicsWorldShape"', self.source)

    def test_type_id(self):
        self.assertIn("0x0012800A", self.source)

    def test_classify(self):
        self.assertIn(
            'kClassify = "drawdb/geometry/mmdPhysicsWorldShape:utility/general"', self.source
        )

    def test_input_attributes(self):
        for attr in [
            "aEnable",
            "aGravity",
            "aGravityX",
            "aGravityY",
            "aGravityZ",
            "aFixedTimestep",
            "aMaxSubsteps",
            "aTimeScale",
            "aStartFrame",
            "aResetGeneration",
            "aPhysicsMode",
        ]:
            self.assertIn(attr, self.source, f"Missing input attribute: {attr}")

    def test_output_attribute(self):
        self.assertIn("aOutSettingsVersion", self.source)

    def test_has_compute(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("compute", func_names)

    def test_has_maya_use_new_api(self):
        self.assertIn("maya_useNewAPI", self.source)

    def test_has_register_deregister(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("register", func_names)
        self.assertIn("deregister", func_names)
        self.assertIn("creator", func_names)
        self.assertIn("initialize", func_names)

    def test_attribute_affects_declared(self):
        self.assertIn("attributeAffects", self.source)

    def test_register_is_unconditional(self):
        register_src = re.search(r"def register\(plugin_fn\):(.*?)\ndef deregister", self.source, re.S)
        self.assertIsNotNone(register_src)
        self.assertNotIn("is_native_physics_available", register_src.group(1))

    def test_uses_locator_node(self):
        self.assertIn("kLocatorNode", self.source)

    def test_no_type_id_collision_with_other_nodes(self):
        nodes_dir = WORLD_PATH.parent
        for py_file in nodes_dir.glob("*.py"):
            if py_file.name in ("__init__.py", WORLD_PATH.name):
                continue
            other_source = py_file.read_text(encoding="utf-8")
            self.assertNotIn(
                "0x0012800A", other_source, f"TypeId 0x0012800A collides with {py_file.name}"
            )


class TestPluginRegistration(unittest.TestCase):

    def setUp(self):
        self.source = PLUGIN_PATH.read_text(encoding="utf-8")

    def test_imports_world_shape(self):
        self.assertIn("mmd_physics_world_shape", self.source)

    def test_registers_world_shape_under_physics_gate(self):
        self.assertIn("mmd_physics_world_shape.register", self.source)

    def test_deregisters_world_shape(self):
        self.assertIn("mmd_physics_world_shape.deregister", self.source)

    def test_world_shape_registered_first(self):
        register_index = self.source.index("mmd_physics_world_shape.register")
        rigid_body_index = self.source.index("mmd_rigid_body_shape.register")
        self.assertLess(register_index, rigid_body_index)

    def test_world_shape_deregistered_last(self):
        deregister_index = self.source.index("mmd_physics_world_shape.deregister")
        rigid_body_index = self.source.index("mmd_rigid_body_shape.deregister")
        self.assertGreater(deregister_index, rigid_body_index)


class TestTypeIdUniqueness(unittest.TestCase):
    """Verify no TypeId collision across all node modules."""

    def test_all_type_ids_unique(self):
        nodes_dir = Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes"
        type_ids = {}
        for py_file in nodes_dir.glob("*.py"):
            if py_file.name.startswith("__"):
                continue
            source = py_file.read_text(encoding="utf-8")
            for match in re.finditer(r"MTypeId\((0x[0-9a-fA-F]+)\)", source):
                tid = match.group(1).lower()
                if tid in type_ids:
                    self.fail(
                        f"TypeId {tid} used in both {type_ids[tid]} and {py_file.name}"
                    )
                type_ids[tid] = py_file.name
        self.assertGreater(len(type_ids), 0, "Should find at least one TypeId")
        self.assertIn("0x0012800a", type_ids)


if __name__ == "__main__":
    unittest.main()
