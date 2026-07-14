"""Unit tests for mmdPhysicsSolver and mmdPhysicsBoneDriver nodes (no Maya required).

Validates module structure, attribute names, TypeId allocation, and the
register/deregister interface via AST/source inspection.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SOLVER_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes" / "mmd_physics_solver_node.py"
DRIVER_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes" / "mmd_physics_bone_driver_node.py"
PLUGIN_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"


class TestSolverNodeStructure(unittest.TestCase):

    def setUp(self):
        self.source = SOLVER_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_module_parses(self):
        self.assertIsNotNone(self.tree)

    def test_has_solver_class(self):
        class_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)]
        self.assertIn("MmdPhysicsSolverNode", class_names)

    def test_type_name(self):
        self.assertIn('kTypeName = "mmdPhysicsSolver"', self.source)

    def test_type_id(self):
        self.assertIn("0x00128008", self.source)

    def test_input_attributes(self):
        self.assertIn("aEnable", self.source)
        self.assertIn("aInTime", self.source)
        self.assertIn("aModelRoot", self.source)
        self.assertIn("aInWorldSettings", self.source)

    def test_output_attributes(self):
        self.assertIn("aOutBoneMatrices", self.source)
        self.assertIn("aOutBoneCount", self.source)
        self.assertIn("aOutStatus", self.source)
        self.assertIn("aOutSolved", self.source)

    def test_has_compute(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("compute", func_names)

    def test_has_time_state_machine(self):
        self.assertIn("_forward_step", self.source)
        self.assertIn("_reset_world", self.source)

    def test_has_register_deregister(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("register", func_names)
        self.assertIn("deregister", func_names)
        self.assertIn("creator", func_names)
        self.assertIn("initialize", func_names)

    def test_has_maya_use_new_api(self):
        self.assertIn("maya_useNewAPI", self.source)

    def test_has_handle_cleanup(self):
        self.assertIn("_free_handles", self.source)

    def test_uses_native_physics(self):
        self.assertIn("is_native_physics_available", self.source)
        self.assertIn("MmdRuntimePhysicsWorld", self.source)
        self.assertIn("MmdRuntimeModel", self.source)
        self.assertIn("MmdRuntimeInstance", self.source)

    def test_uses_coordinate_transform(self):
        self.assertIn("mmd_matrix_to_maya", self.source)

    def test_same_time_idempotent(self):
        self.assertIn("_TIME_EPSILON", self.source)
        self.assertIn("cached", self.source)

    def test_attribute_affects_declared(self):
        self.assertIn("attributeAffects", self.source)


class TestDriverNodeStructure(unittest.TestCase):

    def setUp(self):
        self.source = DRIVER_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_module_parses(self):
        self.assertIsNotNone(self.tree)

    def test_has_driver_class(self):
        class_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)]
        self.assertIn("MmdPhysicsBoneDriverNode", class_names)

    def test_type_name(self):
        self.assertIn('kTypeName = "mmdPhysicsBoneDriver"', self.source)

    def test_type_id(self):
        self.assertIn("0x00128009", self.source)

    def test_no_type_id_collision_with_solver(self):
        solver_src = SOLVER_PATH.read_text(encoding="utf-8")
        self.assertNotEqual(
            solver_src.count("0x00128008"),
            0,
            "solver should use 0x00128008",
        )
        self.assertEqual(
            self.source.count("0x00128008"),
            0,
            "driver must NOT use solver's TypeId 0x00128008",
        )

    def test_input_attributes(self):
        for attr in [
            "aInSolverBoneMatrices",
            "aInSolverBoneCount",
            "aInBoneIndex",
            "aInParentBoneIndex",
            "aInParentInverseMatrix",
            "aInJointOrient",
            "aInRotateAxis",
            "aInRotateOrder",
            "aInSolved",
            "aEnable",
        ]:
            self.assertIn(attr, self.source, f"Missing input attribute: {attr}")

    def test_output_attributes(self):
        for attr in ["aOutTranslate", "aOutRotate"]:
            self.assertIn(attr, self.source, f"Missing output attribute: {attr}")

    def test_output_components(self):
        for comp in [
            "aOutTranslateX", "aOutTranslateY", "aOutTranslateZ",
            "aOutRotateX", "aOutRotateY", "aOutRotateZ",
        ]:
            self.assertIn(comp, self.source, f"Missing output component: {comp}")

    def test_has_compute(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("compute", func_names)

    def test_has_register_deregister(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("register", func_names)
        self.assertIn("deregister", func_names)
        self.assertIn("creator", func_names)
        self.assertIn("initialize", func_names)

    def test_has_maya_use_new_api(self):
        self.assertIn("maya_useNewAPI", self.source)

    def test_handles_joint_orient_decomposition(self):
        self.assertIn("q_jo", self.source)
        self.assertIn("asQuaternion", self.source)

    def test_handles_rotate_axis(self):
        self.assertIn("q_ra", self.source)

    def test_handles_rotate_order(self):
        self.assertIn("reorderIt", self.source)
        self.assertIn("_ROTATE_ORDERS", self.source)

    def test_extracts_matrix_from_flat_array(self):
        self.assertIn("_extract_matrix", self.source)

    def test_attribute_affects_declared(self):
        self.assertIn("attributeAffects", self.source)


class TestPluginRegistration(unittest.TestCase):

    def setUp(self):
        self.source = PLUGIN_PATH.read_text(encoding="utf-8")

    def test_imports_solver_node(self):
        self.assertIn("mmd_physics_solver_node", self.source)

    def test_imports_driver_node(self):
        self.assertIn("mmd_physics_bone_driver_node", self.source)

    def test_registers_solver_under_physics_gate(self):
        self.assertIn("mmd_physics_solver_node.register", self.source)
        self.assertIn("MMD_TOOLS_PHYSICS_NODES", self.source)

    def test_registers_driver_under_physics_gate(self):
        self.assertIn("mmd_physics_bone_driver_node.register", self.source)

    def test_deregisters_solver(self):
        self.assertIn("mmd_physics_solver_node.deregister", self.source)

    def test_deregisters_driver(self):
        self.assertIn("mmd_physics_bone_driver_node.deregister", self.source)


class TestTypeIdUniqueness(unittest.TestCase):
    """Verify no TypeId collision across all node modules."""

    def test_all_type_ids_unique(self):
        nodes_dir = Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes"
        type_ids = {}
        for py_file in nodes_dir.glob("*.py"):
            if py_file.name.startswith("__"):
                continue
            source = py_file.read_text(encoding="utf-8")
            import re
            for match in re.finditer(r"MTypeId\((0x[0-9a-fA-F]+)\)", source):
                tid = match.group(1).lower()
                if tid in type_ids:
                    self.fail(
                        f"TypeId {tid} used in both {type_ids[tid]} and {py_file.name}"
                    )
                type_ids[tid] = py_file.name
        self.assertGreater(len(type_ids), 0, "Should find at least one TypeId")


if __name__ == "__main__":
    unittest.main()
