"""Unit tests for mmdRigidBodyShape VP2 DrawOverride (no Maya required).

Validates module structure, draw override class, registration interface,
color definitions, and shape handling via AST/source inspection.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

DRAW_OVERRIDE_PATH = (
    Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes" / "mmd_rigid_body_draw_override.py"
)
RIGID_BODY_PATH = (
    Path(__file__).resolve().parents[2] / "mmd_tools" / "nodes" / "mmd_rigid_body_shape.py"
)
PLUGIN_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"


class TestDrawOverrideStructure(unittest.TestCase):

    def setUp(self):
        self.source = DRAW_OVERRIDE_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_module_parses(self):
        self.assertIsNotNone(self.tree)

    def test_has_draw_override_class(self):
        class_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)]
        self.assertIn("MmdRigidBodyDrawOverride", class_names)

    def test_has_draw_data_class(self):
        class_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef)]
        self.assertIn("ColliderDrawData", class_names)

    def test_has_maya_use_new_api(self):
        self.assertIn("maya_useNewAPI", self.source)

    def test_has_register_deregister(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("register", func_names)
        self.assertIn("deregister", func_names)

    def test_has_creator_static_method(self):
        self.assertIn("creator", self.source)

    def test_classification_matches_rigid_body_shape(self):
        rb_source = RIGID_BODY_PATH.read_text(encoding="utf-8")
        rb_classify = re.search(r'kClassify\s*=\s*"([^"]+)"', rb_source)
        self.assertIsNotNone(rb_classify, "Rigid body shape must have kClassify")
        drawdb_part = rb_classify.group(1).split(":")[0]
        self.assertIn(drawdb_part, self.source)

    def test_uses_mpx_draw_override(self):
        self.assertIn("MPxDrawOverride", self.source)

    def test_has_ui_drawables(self):
        self.assertIn("hasUIDrawables", self.source)
        self.assertIn("addUIDrawables", self.source)

    def test_has_prepare_for_draw(self):
        func_names = [n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("prepareForDraw", func_names)

    def test_has_supported_draw_apis(self):
        self.assertIn("supportedDrawAPIs", self.source)

    def test_uses_draw_registry(self):
        self.assertIn("MDrawRegistry", self.source)
        self.assertIn("registerDrawOverrideCreator", self.source)
        self.assertIn("deregisterDrawOverrideCreator", self.source)

    def test_handles_all_shape_types(self):
        self.assertIn("sphere", self.source.lower())
        self.assertIn("box", self.source.lower())
        self.assertIn("capsule", self.source.lower())

    def test_defines_physics_mode_colors(self):
        for mode_keyword in ["static", "dynamic"]:
            self.assertTrue(
                mode_keyword.lower() in self.source.lower(),
                f"Missing color definition for mode: {mode_keyword}",
            )

    def test_reads_shape_attributes(self):
        for attr in ["shapeType", "shapeSizeX", "shapeSizeY", "shapeSizeZ", "physicsMode", "enable"]:
            self.assertIn(attr, self.source, f"Missing attribute read: {attr}")

    def test_uses_begin_end_drawable(self):
        self.assertIn("beginDrawable", self.source)
        self.assertIn("endDrawable", self.source)


class TestPluginRegistration(unittest.TestCase):

    def setUp(self):
        self.source = PLUGIN_PATH.read_text(encoding="utf-8")

    def test_imports_draw_override(self):
        self.assertIn("mmd_rigid_body_draw_override", self.source)

    def test_registers_draw_override(self):
        self.assertIn("mmd_rigid_body_draw_override.register", self.source)

    def test_deregisters_draw_override(self):
        self.assertIn("mmd_rigid_body_draw_override.deregister", self.source)

    def test_draw_override_registered_after_rigid_body(self):
        rigid_body_register = self.source.index("mmd_rigid_body_shape.register")
        draw_register = self.source.index("mmd_rigid_body_draw_override.register")
        self.assertGreater(draw_register, rigid_body_register)

    def test_draw_override_deregistered_before_rigid_body(self):
        draw_deregister = self.source.index("mmd_rigid_body_draw_override.deregister")
        rigid_body_deregister = self.source.index("mmd_rigid_body_shape.deregister")
        self.assertLess(draw_deregister, rigid_body_deregister)


if __name__ == "__main__":
    unittest.main()
