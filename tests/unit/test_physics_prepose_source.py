"""PHS-3a: Source-inspection tests for kinematic-bone pre-physics pose injection.

These tests verify (via AST/source inspection, no Maya dependency) the shape
of three related changes that let physics-driven rigs read kinematic
(physicsMode=0 / "follows bone") joint poses from Maya before each physics
step, instead of relying solely on the mmd-anim rest pose:

1. ``mmd_tools/core/coordinate_transform.py`` gained ``maya_matrix_to_mmd``,
   an alias of ``mmd_matrix_to_maya`` (the Z-reflection conversion is its own
   inverse, so the same function converts both directions).
2. ``mmd_tools/core/native/mmd_anim_runtime_handles.py``'s
   ``MmdRuntimeInstance`` gained ``evaluate_current_pose_before_physics``
   and ``apply_physics_world_matrices`` to let a caller push external world
   matrices into the runtime and re-evaluate the pre-physics pose chain
   (IK/append) before the next physics step.
3. ``mmd_tools/nodes/mmd_physics_solver_node.py``'s ``MmdPhysicsSolverNode``
   gained kinematic bone discovery, bind-correction precomputation, and
   per-step injection of kinematic bone poses ahead of stepping/resetting
   the physics world.

A separate functional test (``TestMayaToMmdTransformInvolution``) exercises
the coordinate_transform module directly (it has no Maya dependency) to
confirm the round-trip identity ``maya_matrix_to_mmd(mmd_matrix_to_maya(M)) == M``.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

COORDINATE_TRANSFORM_PATH = (
    Path(__file__).resolve().parents[2] / "mmd_tools" / "core" / "coordinate_transform.py"
)
RUNTIME_HANDLES_PATH = (
    Path(__file__).resolve().parents[2]
    / "mmd_tools"
    / "core"
    / "native"
    / "mmd_anim_runtime_handles.py"
)
SOLVER_NODE_PATH = (
    Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes" / "mmd_physics_solver_node.py"
)


def _function_names_in_class(tree: ast.AST, class_name: str) -> set:
    """Return the set of method names defined directly on ``class_name`` in ``tree``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return set()


def _get_function_source(tree: ast.AST, source_lines: list, func_name: str, class_name: str = None) -> str:
    """Return the source text of a top-level or class method function by name."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            if class_name is not None:
                # Confirm this function node belongs to the requested class.
                parent_classes = [
                    n
                    for n in ast.walk(tree)
                    if isinstance(n, ast.ClassDef) and node in ast.walk(n)
                ]
                if not any(n.name == class_name for n in parent_classes):
                    continue
            start = node.lineno - 1
            end = node.end_lineno
            return "\n".join(source_lines[start:end])
    raise AssertionError(f"function {func_name!r} not found (class={class_name!r})")


class TestCoordinateTransformMayaToMmd(unittest.TestCase):
    """Verify maya_matrix_to_mmd exists as a callable alias in coordinate_transform.py."""

    def setUp(self):
        self.source = COORDINATE_TRANSFORM_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_maya_matrix_to_mmd_is_defined(self):
        self.assertIn("maya_matrix_to_mmd", self.source)

    def test_maya_matrix_to_mmd_aliases_mmd_matrix_to_maya(self):
        # It's declared as a module-level assignment: maya_matrix_to_mmd = mmd_matrix_to_maya
        found = False
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "maya_matrix_to_mmd":
                        self.assertIsInstance(node.value, ast.Name)
                        self.assertEqual(node.value.id, "mmd_matrix_to_maya")
                        found = True
        self.assertTrue(found, "expected 'maya_matrix_to_mmd = mmd_matrix_to_maya' assignment")

    def test_maya_matrix_to_mmd_is_actually_callable(self):
        # Import the real module (no Maya dependency) and confirm it's callable.
        from mmd_tools.core.coordinate_transform import maya_matrix_to_mmd, mmd_matrix_to_maya

        self.assertTrue(callable(maya_matrix_to_mmd))
        self.assertIs(maya_matrix_to_mmd, mmd_matrix_to_maya)


class TestRuntimeHandlesPrePhysicsApi(unittest.TestCase):
    """Verify MmdRuntimeInstance exposes the new pre-physics pose injection API."""

    def setUp(self):
        self.source = RUNTIME_HANDLES_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.instance_methods = _function_names_in_class(self.tree, "MmdRuntimeInstance")

    def test_evaluate_current_pose_before_physics_method_exists(self):
        self.assertIn("evaluate_current_pose_before_physics", self.instance_methods)

    def test_apply_physics_world_matrices_method_exists(self):
        self.assertIn("apply_physics_world_matrices", self.instance_methods)

    def test_evaluate_current_pose_before_physics_calls_expected_abi(self):
        source_lines = self.source.splitlines()
        func_src = _get_function_source(
            self.tree, source_lines, "evaluate_current_pose_before_physics", "MmdRuntimeInstance"
        )
        self.assertIn("mmd_runtime_instance_evaluate_current_pose_before_physics", func_src)

    def test_apply_physics_world_matrices_calls_expected_abi(self):
        source_lines = self.source.splitlines()
        func_src = _get_function_source(
            self.tree, source_lines, "apply_physics_world_matrices", "MmdRuntimeInstance"
        )
        self.assertIn("mmd_runtime_instance_apply_physics_world_matrices", func_src)

    def test_apply_physics_world_matrices_accepts_matrices_and_mask(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "apply_physics_world_matrices":
                arg_names = [a.arg for a in node.args.args]
                self.assertIn("matrices_flat", arg_names)
                self.assertIn("mask", arg_names)
                return
        self.fail("apply_physics_world_matrices function definition not found")


class TestSolverKinematicPoseInjection(unittest.TestCase):
    """Verify MmdPhysicsSolverNode discovers and injects kinematic bone poses."""

    def setUp(self):
        self.source = SOLVER_NODE_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.solver_methods = _function_names_in_class(self.tree, "MmdPhysicsSolverNode")
        self.source_lines = self.source.splitlines()

    def test_build_kinematic_pose_data_method_exists(self):
        self.assertIn("_build_kinematic_pose_data", self.solver_methods)

    def test_find_physics_bone_indices_method_exists(self):
        self.assertIn("_find_physics_bone_indices", self.solver_methods)

    def test_inject_kinematic_poses_method_exists(self):
        self.assertIn("_inject_kinematic_poses", self.solver_methods)

    def test_forward_step_calls_evaluate_current_pose_before_physics(self):
        forward_step_src = _get_function_source(
            self.tree, self.source_lines, "_forward_step", "MmdPhysicsSolverNode"
        )
        self.assertIn("evaluate_current_pose_before_physics", forward_step_src)

    def test_forward_step_calls_inject_kinematic_poses(self):
        forward_step_src = _get_function_source(
            self.tree, self.source_lines, "_forward_step", "MmdPhysicsSolverNode"
        )
        self.assertIn("_inject_kinematic_poses", forward_step_src)

    def test_reset_world_calls_evaluate_current_pose_before_physics(self):
        reset_world_src = _get_function_source(
            self.tree, self.source_lines, "_reset_world", "MmdPhysicsSolverNode"
        )
        self.assertIn("evaluate_current_pose_before_physics", reset_world_src)

    def test_reset_world_calls_inject_kinematic_poses(self):
        reset_world_src = _get_function_source(
            self.tree, self.source_lines, "_reset_world", "MmdPhysicsSolverNode"
        )
        self.assertIn("_inject_kinematic_poses", reset_world_src)

    def test_build_kinematic_pose_data_delegates_discovery(self):
        build_src = _get_function_source(
            self.tree, self.source_lines, "_build_kinematic_pose_data", "MmdPhysicsSolverNode"
        )
        self.assertIn("_find_physics_bone_indices", build_src)
        find_src = _get_function_source(
            self.tree, self.source_lines, "_find_physics_bone_indices", "MmdPhysicsSolverNode"
        )
        self.assertIn("relatedBoneIndex", find_src)

    def test_inject_kinematic_poses_calls_apply_physics_world_matrices(self):
        inject_src = _get_function_source(
            self.tree, self.source_lines, "_inject_kinematic_poses", "MmdPhysicsSolverNode"
        )
        self.assertIn("apply_physics_world_matrices", inject_src)

    def test_inject_kinematic_poses_calls_maya_matrix_to_mmd(self):
        inject_src = _get_function_source(
            self.tree, self.source_lines, "_inject_kinematic_poses", "MmdPhysicsSolverNode"
        )
        self.assertIn("maya_matrix_to_mmd", inject_src)

    def test_instance_has_kinematic_state_vars(self):
        # __init__ should initialize the two new instance vars documented in PHS-3a.
        init_src = _get_function_source(
            self.tree, self.source_lines, "__init__", "MmdPhysicsSolverNode"
        )
        self.assertIn("_bone_joints", init_src)
        self.assertIn("_kinematic_corrections", init_src)

    def test_find_physics_bone_indices_is_static_method(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == "MmdPhysicsSolverNode":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "_find_physics_bone_indices":
                        decorator_names = [
                            d.id for d in item.decorator_list if isinstance(d, ast.Name)
                        ]
                        self.assertIn("staticmethod", decorator_names)
                        return
        self.fail("_find_physics_bone_indices function definition not found")

    def test_find_physics_bone_indices_queries_rigid_body_shapes(self):
        find_src = _get_function_source(
            self.tree, self.source_lines, "_find_physics_bone_indices", "MmdPhysicsSolverNode"
        )
        self.assertIn("mmdRigidBodyShape", find_src)
        self.assertIn("relatedBoneIndex", find_src)
        self.assertIn("physicsMode", find_src)


class TestMayaToMmdTransformInvolution(unittest.TestCase):
    """Pure-Python functional test: the maya<->mmd matrix conversion is its own inverse."""

    def test_round_trip_recovers_original_matrix(self):
        from mmd_tools.core.coordinate_transform import maya_matrix_to_mmd, mmd_matrix_to_maya

        # An arbitrary, non-trivial 4x4 flat (column-major) matrix: rotation-ish
        # values plus a translation in the last row-slot, nothing symmetric that
        # would hide a sign error.
        matrix = [
            0.8, 0.1, 0.2, 0.0,
            0.3, 0.9, 0.4, 0.0,
            0.5, 0.6, 0.7, 0.0,
            1.5, -2.5, 3.5, 1.0,
        ]

        converted = mmd_matrix_to_maya(matrix)
        round_tripped = maya_matrix_to_mmd(converted)

        for original, restored in zip(matrix, round_tripped):
            self.assertAlmostEqual(original, restored, places=9)

        # Sanity: the conversion must actually change values that touch the
        # Z axis (row/col index 2), otherwise this test would trivially pass.
        self.assertNotEqual(matrix, converted)

    def test_maya_matrix_to_mmd_rejects_wrong_length(self):
        from mmd_tools.core.coordinate_transform import maya_matrix_to_mmd

        with self.assertRaises(ValueError):
            maya_matrix_to_mmd([0.0] * 15)


if __name__ == "__main__":
    unittest.main()
