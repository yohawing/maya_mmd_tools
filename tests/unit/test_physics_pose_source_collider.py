"""PHS-POSE-SOURCE-1 + PHS-COLLISION-ANIM-1: Source-inspection tests.

Verifies (via AST/source inspection, no Maya dependency):

1. Solver ``inputMode`` attribute (rest-only=0 / maya-pose=1) controls
   kinematic bone injection: ``_forward_step`` and ``_reset_world`` only
   call ``_inject_kinematic_poses`` when ``input_mode == INPUT_MODE_MAYA_POSE``.

2. Solver module-level ``_SIMULATED_RB_CACHE`` and ``_update_rigid_body_visual_cache``
   populate per-shape simulated world matrices from ``copy_rigidbody_states``.

3. The public draw override remains authoring/rest-pose only even though the
   unsupported internal solver cache still exists.

4. Scene builder sets ``inputMode`` to 1 (maya-pose) when creating the solver.

5. Pure-Python quaternion Z-reflection test confirms the conversion is correct.
"""

from __future__ import annotations

import ast
import math
import unittest
from pathlib import Path

SOLVER_NODE_PATH = (
    Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes" / "mmd_physics_solver_node.py"
)
DRAW_OVERRIDE_PATH = (
    Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes" / "mmd_rigid_body_draw_override.py"
)
SCENE_BUILDER_PATH = (
    Path(__file__).resolve().parents[2] / "mmd_tools" / "converters" / "physics_scene_builder.py"
)


def _function_names_in_class(tree: ast.AST, class_name: str) -> set:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return set()


def _get_function_source(tree: ast.AST, source_lines: list, func_name: str, class_name: str = None) -> str:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            if class_name is not None:
                parent_classes = [
                    n for n in ast.walk(tree)
                    if isinstance(n, ast.ClassDef) and node in ast.walk(n)
                ]
                if not any(n.name == class_name for n in parent_classes):
                    continue
            start = node.lineno - 1
            end = node.end_lineno
            return "\n".join(source_lines[start:end])
    raise AssertionError(f"function {func_name!r} not found (class={class_name!r})")


# ---------------------------------------------------------------------------
# PHS-POSE-SOURCE-1: Solver inputMode attribute and mode-conditioned injection
# ---------------------------------------------------------------------------

class TestSolverInputModeAttribute(unittest.TestCase):
    """Verify MmdPhysicsSolverNode has an inputMode enum attribute."""

    def setUp(self):
        self.source = SOLVER_NODE_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.source_lines = self.source.splitlines()

    def test_input_mode_constants_defined(self):
        self.assertIn("INPUT_MODE_REST", self.source)
        self.assertIn("INPUT_MODE_MAYA_POSE", self.source)

    def test_aInputMode_class_attribute_exists(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == "MmdPhysicsSolverNode":
                class_src = "\n".join(self.source_lines[node.lineno - 1 : node.end_lineno])
                self.assertIn("aInputMode", class_src)
                return
        self.fail("MmdPhysicsSolverNode class not found")

    def test_initialize_creates_input_mode_enum(self):
        init_src = _get_function_source(self.tree, self.source_lines, "initialize")
        self.assertIn("inputMode", init_src)
        self.assertIn("MFnEnumAttribute", init_src)
        self.assertIn("rest-only", init_src)
        self.assertIn("maya-pose", init_src)

    def test_compute_reads_input_mode(self):
        compute_src = _get_function_source(
            self.tree, self.source_lines, "compute", "MmdPhysicsSolverNode"
        )
        self.assertIn("aInputMode", compute_src)
        self.assertIn("input_mode", compute_src)

    def test_forward_step_accepts_input_mode_parameter(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_forward_step":
                arg_names = [a.arg for a in node.args.args]
                self.assertIn("input_mode", arg_names)
                return
        self.fail("_forward_step not found")

    def test_reset_world_accepts_input_mode_parameter(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_reset_world":
                arg_names = [a.arg for a in node.args.args]
                self.assertIn("input_mode", arg_names)
                return
        self.fail("_reset_world not found")

    def test_forward_step_conditions_on_input_mode(self):
        src = _get_function_source(
            self.tree, self.source_lines, "_forward_step", "MmdPhysicsSolverNode"
        )
        self.assertIn("INPUT_MODE_MAYA_POSE", src)

    def test_reset_world_conditions_on_input_mode(self):
        src = _get_function_source(
            self.tree, self.source_lines, "_reset_world", "MmdPhysicsSolverNode"
        )
        self.assertIn("INPUT_MODE_MAYA_POSE", src)

    def test_input_mode_affects_outputs(self):
        init_src = _get_function_source(self.tree, self.source_lines, "initialize")
        self.assertIn("aInputMode", init_src)
        self.assertIn("attributeAffects", init_src)


# ---------------------------------------------------------------------------
# PHS-COLLISION-ANIM-1: Rigid body visual cache
# ---------------------------------------------------------------------------

class TestSolverRigidBodyVisualCache(unittest.TestCase):
    """Verify solver populates per-shape simulated world matrix cache."""

    def setUp(self):
        self.source = SOLVER_NODE_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.source_lines = self.source.splitlines()
        self.solver_methods = _function_names_in_class(self.tree, "MmdPhysicsSolverNode")

    def test_simulated_rb_cache_module_level(self):
        self.assertIn("_SIMULATED_RB_CACHE", self.source)

    def test_build_rigid_body_shape_mapping_exists(self):
        self.assertIn("_build_rigid_body_shape_mapping", self.solver_methods)

    def test_update_rigid_body_visual_cache_exists(self):
        self.assertIn("_update_rigid_body_visual_cache", self.solver_methods)

    def test_rb_shape_paths_instance_var(self):
        init_src = _get_function_source(
            self.tree, self.source_lines, "__init__", "MmdPhysicsSolverNode"
        )
        self.assertIn("_rb_shape_paths", init_src)

    def test_try_initialize_calls_build_rb_mapping(self):
        src = _get_function_source(
            self.tree, self.source_lines, "_try_initialize", "MmdPhysicsSolverNode"
        )
        self.assertIn("_build_rigid_body_shape_mapping", src)

    def test_update_visual_cache_calls_copy_rigidbody_states(self):
        src = _get_function_source(
            self.tree, self.source_lines, "_update_rigid_body_visual_cache", "MmdPhysicsSolverNode"
        )
        self.assertIn("copy_rigidbody_states", src)

    def test_update_visual_cache_uses_mmd_point_to_maya(self):
        src = _get_function_source(
            self.tree, self.source_lines, "_update_rigid_body_visual_cache", "MmdPhysicsSolverNode"
        )
        self.assertIn("mmd_point_to_maya", src)

    def test_update_visual_cache_applies_quaternion_z_reflection(self):
        """Quaternion Z-reflection: q_maya = (-qx, -qy, qz, qw)."""
        src = _get_function_source(
            self.tree, self.source_lines, "_update_rigid_body_visual_cache", "MmdPhysicsSolverNode"
        )
        self.assertIn("MQuaternion", src)
        self.assertIn("-qx", src)
        self.assertIn("-qy", src)

    def test_compute_calls_update_visual_cache(self):
        src = _get_function_source(
            self.tree, self.source_lines, "compute", "MmdPhysicsSolverNode"
        )
        self.assertIn("_update_rigid_body_visual_cache", src)

    def test_free_handles_clears_rb_shape_paths(self):
        src = _get_function_source(
            self.tree, self.source_lines, "_free_handles", "MmdPhysicsSolverNode"
        )
        self.assertIn("_rb_shape_paths", src)

    def test_build_rb_mapping_reads_pmx_index(self):
        src = _get_function_source(
            self.tree, self.source_lines, "_build_rigid_body_shape_mapping", "MmdPhysicsSolverNode"
        )
        self.assertIn("pmxIndex", src)
        self.assertIn("mmdRigidBodyShape", src)


# ---------------------------------------------------------------------------
# PHS-COLLISION-ANIM-1: Draw override reads simulated matrix
# ---------------------------------------------------------------------------

class TestDrawOverrideAuthoringMatrix(unittest.TestCase):
    """Verify public collider drawing does not consume live solver state."""

    def setUp(self):
        self.source = DRAW_OVERRIDE_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.source_lines = self.source.splitlines()

    def test_collider_draw_data_has_no_simulated_offset(self):
        init_src = _get_function_source(
            self.tree, self.source_lines, "__init__", "ColliderDrawData"
        )
        self.assertNotIn("simulated_offset", init_src)

    def test_prepare_for_draw_does_not_import_cache(self):
        src = _get_function_source(
            self.tree, self.source_lines, "prepareForDraw", "MmdRigidBodyDrawOverride"
        )
        self.assertNotIn("_SIMULATED_RB_CACHE", src)

    def test_prepare_for_draw_does_not_compute_live_offset(self):
        src = _get_function_source(
            self.tree, self.source_lines, "prepareForDraw", "MmdRigidBodyDrawOverride"
        )
        self.assertNotIn("simulated_offset", src)

    def test_add_ui_drawables_uses_object_local_origin(self):
        src = _get_function_source(
            self.tree, self.source_lines, "addUIDrawables", "MmdRigidBodyDrawOverride"
        )
        self.assertIn("MPoint(0.0, 0.0, 0.0)", src)
        self.assertNotIn("simulated_offset", src)

    def test_add_ui_drawables_does_not_extract_live_rotation(self):
        src = _get_function_source(
            self.tree, self.source_lines, "addUIDrawables", "MmdRigidBodyDrawOverride"
        )
        self.assertNotIn("MTransformationMatrix", src)
        self.assertNotIn("rotateBy", src)


# ---------------------------------------------------------------------------
# Scene builder wiring
# ---------------------------------------------------------------------------

class TestSceneBuilderInputMode(unittest.TestCase):
    """Verify scene builder sets inputMode=1 on created solver."""

    def setUp(self):
        self.source = SCENE_BUILDER_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.source_lines = self.source.splitlines()

    def test_live_graph_sets_input_mode(self):
        src = _get_function_source(
            self.tree, self.source_lines, "build_physics_live_graph"
        )
        self.assertIn("inputMode", src)


# ---------------------------------------------------------------------------
# Pure-Python quaternion Z-reflection verification
# ---------------------------------------------------------------------------

class TestQuaternionZReflection(unittest.TestCase):
    """Verify quaternion Z-reflection: mmd (qx,qy,qz,qw) → maya (-qx,-qy,qz,qw).

    The Z-reflection matrix P = diag(1,1,-1) conjugates a rotation matrix as
    P R P.  For quaternion representation this corresponds to negating the X
    and Y components while keeping Z and W unchanged.
    """

    def _quat_to_rotation_matrix_flat(self, qx, qy, qz, qw):
        """Build a 3x3 rotation matrix from quaternion, returned as 9 floats."""
        xx, yy, zz = qx*qx, qy*qy, qz*qz
        xy, xz, yz = qx*qy, qx*qz, qy*qz
        wx, wy, wz = qw*qx, qw*qy, qw*qz
        return [
            1-2*(yy+zz), 2*(xy+wz), 2*(xz-wy),
            2*(xy-wz), 1-2*(xx+zz), 2*(yz+wx),
            2*(xz+wy), 2*(yz-wx), 1-2*(xx+yy),
        ]

    def _conjugate_3x3_by_z_reflection(self, r):
        """Apply P R P where P = diag(1,1,-1) to a flat 3x3 matrix."""
        signs = [1, 1, -1]
        result = [0.0] * 9
        for i in range(3):
            for j in range(3):
                result[i*3+j] = r[i*3+j] * signs[i] * signs[j]
        return result

    def test_rotation_around_x(self):
        angle = math.pi / 4
        qx, qy, qz, qw = math.sin(angle/2), 0, 0, math.cos(angle/2)
        r_mmd = self._quat_to_rotation_matrix_flat(qx, qy, qz, qw)
        r_expected = self._conjugate_3x3_by_z_reflection(r_mmd)
        r_maya = self._quat_to_rotation_matrix_flat(-qx, -qy, qz, qw)
        for a, b in zip(r_expected, r_maya):
            self.assertAlmostEqual(a, b, places=10)

    def test_rotation_around_y(self):
        angle = math.pi / 3
        qx, qy, qz, qw = 0, math.sin(angle/2), 0, math.cos(angle/2)
        r_mmd = self._quat_to_rotation_matrix_flat(qx, qy, qz, qw)
        r_expected = self._conjugate_3x3_by_z_reflection(r_mmd)
        r_maya = self._quat_to_rotation_matrix_flat(-qx, -qy, qz, qw)
        for a, b in zip(r_expected, r_maya):
            self.assertAlmostEqual(a, b, places=10)

    def test_rotation_around_z(self):
        angle = math.pi / 6
        qx, qy, qz, qw = 0, 0, math.sin(angle/2), math.cos(angle/2)
        r_mmd = self._quat_to_rotation_matrix_flat(qx, qy, qz, qw)
        r_expected = self._conjugate_3x3_by_z_reflection(r_mmd)
        r_maya = self._quat_to_rotation_matrix_flat(-qx, -qy, qz, qw)
        for a, b in zip(r_expected, r_maya):
            self.assertAlmostEqual(a, b, places=10)

    def test_arbitrary_rotation(self):
        """Non-axis-aligned rotation: ensures formula works generally."""
        qx, qy, qz, qw = 0.3, 0.4, 0.5, 0.6736
        norm = math.sqrt(qx**2 + qy**2 + qz**2 + qw**2)
        qx, qy, qz, qw = qx/norm, qy/norm, qz/norm, qw/norm
        r_mmd = self._quat_to_rotation_matrix_flat(qx, qy, qz, qw)
        r_expected = self._conjugate_3x3_by_z_reflection(r_mmd)
        r_maya = self._quat_to_rotation_matrix_flat(-qx, -qy, qz, qw)
        for a, b in zip(r_expected, r_maya):
            self.assertAlmostEqual(a, b, places=10)

    def test_identity_stays_identity(self):
        """Identity quaternion (0,0,0,1) should remain identity after conversion."""
        r_maya = self._quat_to_rotation_matrix_flat(0, 0, 0, 1)
        identity = [1,0,0, 0,1,0, 0,0,1]
        for a, b in zip(r_maya, identity):
            self.assertAlmostEqual(a, b, places=10)


if __name__ == "__main__":
    unittest.main()
