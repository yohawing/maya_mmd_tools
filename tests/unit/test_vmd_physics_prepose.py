"""PHS-VMD-PREPOSE-1: Document how VMD import breaks physics driver connections.

These source-inspection tests verify the known gap: VMD import paths do not
recognize mmdPhysicsBoneDriver connections and will disconnect or delete them
when importing bone animation onto joints that are physics-driven.

This is NOT a bug to fix here — the fix is PHS-VMD-LIVE-RECOVERY-0 (temporary
reconnection) and ultimately PHS-POSE-SOURCE-1 (pre-physics proxy).  These
tests exist to:
1. Document the exact mechanism of breakage
2. Alert if someone accidentally makes the breakage silent
3. Serve as regression oracles for the eventual fix
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

RUNTIME_RIG_HELPER_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "converters" / "vmd_runtime_rig_helper.py"
MAYA_ANIM_UTILS_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "core" / "maya_animation_utils.py"
PHYSICS_SCENE_BUILDER_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "converters" / "physics_scene_builder.py"
BONE_DRIVER_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes" / "mmd_physics_bone_driver_node.py"


class TestVmdPhysicsDriverUnawareness(unittest.TestCase):
    """Verify that VMD import paths do NOT recognize physics driver nodes."""

    def setUp(self):
        self.rig_helper_source = RUNTIME_RIG_HELPER_PATH.read_text(encoding="utf-8")
        self.anim_utils_source = MAYA_ANIM_UTILS_PATH.read_text(encoding="utf-8")

    def test_runtime_rig_helper_does_not_mention_physics_driver(self):
        self.assertNotIn("mmdPhysicsBoneDriver", self.rig_helper_source)
        self.assertNotIn("mmdPhysicsSolver", self.rig_helper_source)

    def test_runtime_rig_helper_only_scans_append_and_ik(self):
        self.assertIn("mmdAppend", self.rig_helper_source)
        self.assertIn("mmdCcdIk", self.rig_helper_source)

    def test_maya_animation_utils_does_not_mention_physics_driver(self):
        self.assertNotIn("mmdPhysicsBoneDriver", self.anim_utils_source)
        self.assertNotIn("mmdPhysicsSolver", self.anim_utils_source)

    def test_restore_joints_disconnects_all_sources_indiscriminately(self):
        self.assertIn("disconnectAttr", self.rig_helper_source)
        self.assertIn("listConnections", self.rig_helper_source)


class TestPhysicsDriverOutputPlugs(unittest.TestCase):
    """Verify the exact plugs that physics drivers connect to on joints."""

    def setUp(self):
        self.builder_source = PHYSICS_SCENE_BUILDER_PATH.read_text(encoding="utf-8")
        self.driver_source = BONE_DRIVER_PATH.read_text(encoding="utf-8")

    def test_driver_outputs_translate_and_rotate(self):
        self.assertIn("outTranslate", self.driver_source)
        self.assertIn("outRotate", self.driver_source)

    def test_live_graph_connects_to_joint_translate_rotate(self):
        self.assertIn(".outTranslate", self.builder_source)
        self.assertIn(".outRotate", self.builder_source)
        self.assertIn(".translate", self.builder_source)
        self.assertIn(".rotate", self.builder_source)

    def test_vmd_bone_keying_targets_same_attributes(self):
        vmd_bone_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "converters" / "vmd_bone_animation.py"
        vmd_source = vmd_bone_path.read_text(encoding="utf-8")
        for attr in ["translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"]:
            self.assertIn(attr, vmd_source, f"VMD keying must target {attr}")


class TestSolverDoesNotReadMayaJointPose(unittest.TestCase):
    """Verify that the solver currently uses rest pose, not Maya joint values."""

    def setUp(self):
        solver_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes" / "mmd_physics_solver_node.py"
        self.source = solver_path.read_text(encoding="utf-8")

    def test_solver_uses_rest_pose_not_current_pose(self):
        self.assertIn("evaluate_rest_pose", self.source)

    def test_solver_has_no_bone_pose_input_attribute(self):
        tree = ast.parse(self.source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                        attr_name = target.attr
                        self.assertNotIn("InBonePose", attr_name,
                                         "Solver should not yet have a bone pose input")
                        self.assertNotIn("InPrePhysics", attr_name,
                                         "Solver should not yet have a pre-physics input")


if __name__ == "__main__":
    unittest.main()
