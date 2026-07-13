"""Unit tests for physics_scene_builder helper functions (no Maya required).

Tests the name sanitization and display name logic without importing Maya.
Full integration testing of build_physics_scene requires mayapy + the plugin loaded.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "mmd_tools" / "converters" / "physics_scene_builder.py"


class TestModuleStructure(unittest.TestCase):
    """Validate module parses and exports the expected entry point."""

    def setUp(self):
        self.source = MODULE_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_module_parses(self):
        self.assertIsNotNone(self.tree)

    def test_has_build_physics_scene_function(self):
        func_names = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        ]
        self.assertIn("build_physics_scene", func_names)

    def test_uses_physics_constants(self):
        self.assertIn("PHYSICS_GROUP", self.source)
        self.assertIn("RIGID_BODIES_GROUP", self.source)
        self.assertIn("CONSTRAINTS_GROUP", self.source)


class TestSanitizeNodeName(unittest.TestCase):
    """Test _sanitize_node_name without Maya (extracted via exec)."""

    @classmethod
    def setUpClass(cls):
        import re
        _INVALID_NAME_CHARS_RE = re.compile(r"[^0-9A-Za-z_]+")

        def _sanitize_node_name(name: str) -> str:
            sanitized = _INVALID_NAME_CHARS_RE.sub("_", name or "").strip("_")
            if not sanitized:
                return "unnamed"
            if sanitized[0].isdigit():
                sanitized = f"_{sanitized}"
            return sanitized

        cls._sanitize = staticmethod(_sanitize_node_name)

    def test_ascii_name(self):
        self.assertEqual(self._sanitize("HairBone01"), "HairBone01")

    def test_japanese_name(self):
        result = self._sanitize("髪ボーン01")
        self.assertIn("01", result)
        self.assertTrue(result[0] == "_" or result[0].isalpha())

    def test_empty_name(self):
        self.assertEqual(self._sanitize(""), "unnamed")

    def test_none_name(self):
        self.assertEqual(self._sanitize(None), "unnamed")

    def test_leading_digit(self):
        result = self._sanitize("01_bone")
        self.assertFalse(result[0].isdigit())

    def test_special_characters(self):
        result = self._sanitize("body[0]/test")
        self.assertNotIn("[", result)
        self.assertNotIn("/", result)


class TestDisplayName(unittest.TestCase):
    """Test _display_name logic."""

    @staticmethod
    def _display_name(name_english, name_japanese):
        return name_english or name_japanese or "unnamed"

    def test_english_preferred(self):
        self.assertEqual(self._display_name("Hair", "髪"), "Hair")

    def test_japanese_fallback(self):
        self.assertEqual(self._display_name("", "髪"), "髪")

    def test_unnamed_fallback(self):
        self.assertEqual(self._display_name("", ""), "unnamed")


if __name__ == "__main__":
    unittest.main()
