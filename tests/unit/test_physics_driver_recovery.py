"""PHS-VMD-LIVE-RECOVERY-0: Physics driver recovery after VMD import.

Source-inspection tests verifying the recovery mechanism exists and is
properly gated behind development mode.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SCENE_BUILDER_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "converters" / "physics_scene_builder.py"
VMD_IMPORTER_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "io" / "vmd_importer.py"


class TestRecoverPhysicsDriverConnectionsExists(unittest.TestCase):

    def setUp(self):
        self.source = SCENE_BUILDER_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_function_exists(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("recover_physics_driver_connections", func_names)

    def test_checks_node_type_availability(self):
        self.assertIn("mmdPhysicsBoneDriver", self.source)
        self.assertIn("allNodeTypes", self.source)

    def test_reads_target_joint_attribute(self):
        self.assertIn("mmd_target_joint", self.source)

    def test_reads_model_root_connection(self):
        self.assertIn("mmd_model_root", self.source)

    def test_reconnects_translate_and_rotate(self):
        self.assertIn("outTranslate", self.source)
        self.assertIn("outRotate", self.source)
        self.assertIn("translate", self.source)
        self.assertIn("rotate", self.source)

    def test_returns_summary_dict(self):
        self.assertIn("recovered", self.source)
        self.assertIn("skipped", self.source)


class TestVmdImporterRecoveryHook(unittest.TestCase):

    def setUp(self):
        self.source = VMD_IMPORTER_PATH.read_text(encoding="utf-8")

    def test_calls_recovery_after_success(self):
        self.assertIn("_try_recover_physics_drivers", self.source)

    def test_recovery_is_dev_mode_gated(self):
        self.assertIn("is_development_mode", self.source)

    def test_recovery_imports_from_scene_builder(self):
        self.assertIn("recover_physics_driver_connections", self.source)

    def test_recovery_is_fail_soft(self):
        lines = self.source.splitlines()
        in_recovery_func = False
        has_except = False
        for line in lines:
            if "def _try_recover_physics_drivers" in line:
                in_recovery_func = True
            elif in_recovery_func and line.strip().startswith("def "):
                break
            elif in_recovery_func and "except" in line:
                has_except = True
        self.assertTrue(has_except, "_try_recover_physics_drivers must be fail-soft (has except)")


if __name__ == "__main__":
    unittest.main()
