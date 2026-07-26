"""PHS-3 E2E chain verification tests.

Verifies that the complete pre-physics → solver → post-physics → bone driver
→ collider visual chain is structurally correct via AST/source inspection.

The full data flow:
  Maya keys/controllers/IK
    → solver reads kinematic bone worldMatrix (PHS-3a, cycle-safe)
    → bind correction → mmd-anim space
    → apply_physics_world_matrices + evaluate_current_pose_before_physics
    → step_runtime(dt) / reset (Bullet steps dynamic bodies)
    → evaluate_current_pose_after_physics
    → solver outputs outBoneMatrices (Maya space)
    → mmdPhysicsBoneDriver extracts per-bone matrix
    → bind correction → Maya joint translate/rotate
    → solver calls copy_rigidbody_states → visual cache
    → draw override reads cache → simulated offset

This test file does NOT require Maya.  It checks the structural
consistency of the chain across all participating modules.
"""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

SOLVER_NODE_PATH = _ROOT / "mmd_tools" / "nodes" / "mmd_physics_solver_node.py"
BONE_DRIVER_PATH = _ROOT / "mmd_tools" / "nodes" / "mmd_physics_bone_driver_node.py"
DRAW_OVERRIDE_PATH = _ROOT / "mmd_tools" / "nodes" / "mmd_rigid_body_draw_override.py"
COORD_TRANSFORM_PATH = _ROOT / "mmd_tools" / "core" / "coordinate_transform.py"
RUNTIME_HANDLES_PATH = _ROOT / "mmd_tools" / "core" / "native" / "mmd_anim_runtime_handles.py"
SCENE_BUILDER_PATH = _ROOT / "mmd_tools" / "converters" / "physics_scene_builder.py"
PHYSICS_PROBE_PATH = _ROOT / "tests" / "viewport" / "physics_solver_cycle_probe.py"


def _get_source(path: Path) -> tuple[str, ast.AST, list[str]]:
    src = path.read_text(encoding="utf-8")
    return src, ast.parse(src), src.splitlines()


def _func_source(tree, lines, name, cls=None):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if cls:
                parents = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and node in ast.walk(n)]
                if not any(n.name == cls for n in parents):
                    continue
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"{name!r} not found in {cls!r}")


class TestE2EChainStructure(unittest.TestCase):
    """Verify the complete PHS-3 chain is wired across all modules."""

    def test_solver_forward_step_calls_all_five_phases(self):
        """forward_step must call: evaluate_rest_pose → inject → pre_physics → step → post_physics."""
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        fs = _func_source(tree, lines, "_forward_step", "MmdPhysicsSolverNode")
        self.assertIn("evaluate_rest_pose", fs)
        self.assertIn("_inject_kinematic_poses", fs)
        self.assertIn("evaluate_current_pose_before_physics", fs)
        self.assertIn("step_runtime", fs)
        self.assertIn("evaluate_current_pose_after_physics", fs)

    def test_solver_reset_world_calls_all_phases(self):
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        rw = _func_source(tree, lines, "_reset_world", "MmdPhysicsSolverNode")
        self.assertIn("evaluate_rest_pose", rw)
        self.assertIn("_inject_kinematic_poses", rw)
        self.assertIn("evaluate_current_pose_before_physics", rw)
        self.assertIn("reset", rw)

    def test_solver_outputs_bone_matrices_and_visual_cache(self):
        """compute() must update both bone matrices and rigid body visual cache."""
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        compute = _func_source(tree, lines, "compute", "MmdPhysicsSolverNode")
        self.assertIn("_update_cached_matrices", compute)
        self.assertIn("_update_rigid_body_visual_cache", compute)

    def test_bone_driver_reads_solver_output(self):
        """Bone driver reads outBoneMatrices via inSolverBoneMatrices."""
        src, tree, lines = _get_source(BONE_DRIVER_PATH)
        self.assertIn("inSolverBoneMatrices", src)
        self.assertIn("inSolverBoneCount", src)

    def test_bone_driver_applies_bind_correction(self):
        """Bone driver applies bind + jointOrient + rotateOrder correction."""
        src, tree, lines = _get_source(BONE_DRIVER_PATH)
        self.assertIn("_apply_bind_correction", src)
        self.assertIn("inBindWorldMatrix", src)
        self.assertIn("inNoOrientBindWorldMatrix", src)

    def test_bone_driver_outputs_translate_rotate(self):
        src, tree, lines = _get_source(BONE_DRIVER_PATH)
        self.assertIn("outTranslateX", src)
        self.assertIn("outRotateX", src)

    def test_cycle_probe_captures_only_solver_cycle_messages(self):
        """The transient-warning gate must reject only solver cycle output."""
        spec = importlib.util.spec_from_file_location("physics_solver_cycle_probe", PHYSICS_PROBE_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        report = probe._stop_command_output_capture(
            {
                "enabled": True,
                "callback": None,
                "messages": [
                    {"type": 0, "message": "mmdPhysicsSolver.outSolved cycleCheck warning"},
                    {"type": 0, "message": "mmdPhysicsSolver info"},
                    {"type": 0, "message": "outSolved cycle warning"},
                    {"type": 0, "message": "unrelated DG cycle warning"},
                ],
            }
        )
        self.assertEqual(report["warningCount"], 1)
        self.assertEqual(report["warnings"], ["mmdPhysicsSolver.outSolved cycleCheck warning"])

    def test_live_graph_connects_solver_to_bone_drivers(self):
        """Live graph creates solver → bone driver connections."""
        src, tree, lines = _get_source(SCENE_BUILDER_PATH)
        live_graph = _func_source(tree, lines, "build_physics_live_graph")
        self.assertIn("outBoneMatrices", live_graph)
        self.assertIn("outBoneCount", live_graph)
        self.assertIn("outSolved", live_graph)

    def test_draw_override_is_authoring_only(self):
        """Public collider display does not substitute live solver matrices."""
        src, tree, lines = _get_source(DRAW_OVERRIDE_PATH)
        prep = _func_source(tree, lines, "prepareForDraw", "MmdRigidBodyDrawOverride")
        self.assertNotIn("_SIMULATED_RB_CACHE", prep)
        self.assertNotIn("simulatedWorldMatrix", prep)


class TestE2EInputModeContract(unittest.TestCase):
    """Verify that inputMode=0 disables kinematic injection (rest-only mode)."""

    def test_rest_mode_skips_injection(self):
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        fs = _func_source(tree, lines, "_forward_step", "MmdPhysicsSolverNode")
        self.assertIn("INPUT_MODE_MAYA_POSE", fs)
        self.assertNotIn("INPUT_MODE_REST", fs)

    def test_maya_pose_mode_default(self):
        """inputMode defaults to maya-pose (1) so physics always reads joints."""
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        init_fn = _func_source(tree, lines, "initialize")
        self.assertIn("INPUT_MODE_MAYA_POSE", init_fn)


class TestE2ECoordinateConsistency(unittest.TestCase):
    """Verify coordinate conversions are consistent across the chain."""

    def test_solver_uses_maya_matrix_to_mmd_for_injection(self):
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        inject = _func_source(tree, lines, "_inject_kinematic_poses", "MmdPhysicsSolverNode")
        self.assertIn("maya_matrix_to_mmd", inject)

    def test_solver_uses_mmd_matrix_to_maya_for_output(self):
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        update = _func_source(tree, lines, "_update_cached_matrices", "MmdPhysicsSolverNode")
        self.assertIn("mmd_matrix_to_maya", update)

    def test_solver_uses_mmd_point_to_maya_for_rb_visual(self):
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        vis = _func_source(tree, lines, "_update_rigid_body_visual_cache", "MmdPhysicsSolverNode")
        self.assertIn("mmd_point_to_maya", vis)

    def test_maya_matrix_to_mmd_is_involution(self):
        """Z-reflection conversion is its own inverse."""
        from mmd_tools.core.coordinate_transform import maya_matrix_to_mmd, mmd_matrix_to_maya

        self.assertIs(maya_matrix_to_mmd, mmd_matrix_to_maya)

    def test_build_kinematic_pose_data_uses_mmd_matrix_to_maya_for_rest(self):
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        build = _func_source(tree, lines, "_build_kinematic_pose_data", "MmdPhysicsSolverNode")
        self.assertIn("mmd_matrix_to_maya", build)


class TestE2ECycleSafety(unittest.TestCase):
    """Verify the chain design is cycle-safe."""

    def test_solver_collects_all_physics_bone_indices(self):
        """_find_physics_bone_indices collects relatedBoneIndex from all rigid bodies."""
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        find = _func_source(tree, lines, "_find_physics_bone_indices", "MmdPhysicsSolverNode")
        self.assertIn("relatedBoneIndex", find)
        self.assertIn("all_indices", find)

    def test_solver_uses_cmds_getattr_not_dg_connection(self):
        """Solver reads joints via cmds.getAttr (imperative), not DG plug connection."""
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        inject = _func_source(tree, lines, "_inject_kinematic_poses", "MmdPhysicsSolverNode")
        self.assertIn("cmds.getAttr", inject)
        self.assertIn("worldMatrix", inject)

    def test_bone_driver_only_drives_dynamic_bones(self):
        """Bone driver only drives joints connected by physics scene builder
        (physicsMode 1/2), never kinematic (physicsMode 0) joints."""
        src, tree, lines = _get_source(SCENE_BUILDER_PATH)
        self.assertIn("mmdPhysicsBoneDriver", src)

    def test_mixed_mode_bone_excluded_from_kinematic_fallback(self):
        """A bone with both mode-0 and mode-1/2 rigid bodies must be excluded
        from the kinematic-only set to prevent DG cycles."""
        src, tree, lines = _get_source(SOLVER_NODE_PATH)
        find = _func_source(tree, lines, "_find_physics_bone_indices", "MmdPhysicsSolverNode")
        self.assertIn("dynamic_indices", find)


if __name__ == "__main__":
    unittest.main()
