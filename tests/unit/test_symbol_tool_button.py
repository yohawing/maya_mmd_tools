"""Headless API contracts for Animator visibility tool buttons."""

import inspect
import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.components.symbol_tool_button import (  # noqa: E402
    MaterialSymbolToolButton,
)


class MaterialSymbolToolButtonContractTest(unittest.TestCase):
    def test_public_tri_state_capability_and_state_api(self):
        signature = inspect.signature(MaterialSymbolToolButton.__init__)
        self.assertEqual(signature.parameters["checkable"].default, False)
        self.assertEqual(signature.parameters["tri_state"].default, False)
        for method_name in (
            "setVisibilityState",
            "cycleVisibilityState",
            "setVisibilityAvailable",
            "setVisibilityLabels",
        ):
            self.assertTrue(hasattr(MaterialSymbolToolButton, method_name))
        self.assertTrue(hasattr(MaterialSymbolToolButton, "is_tri_state"))
        self.assertTrue(hasattr(MaterialSymbolToolButton, "isTriState"))

    def test_legacy_checkable_constructor_remains_bool_compatible(self):
        source = inspect.getsource(MaterialSymbolToolButton.__init__)
        self.assertIn("self.setCheckable(bool(checkable) and not self._tri_state)", source)
        self.assertIn("self.toggled.connect", source)
        self.assertIn("self.stateChanged.emit", source)

    def test_state_badge_uses_painter_shapes_without_opacity_qss(self):
        source = inspect.getsource(MaterialSymbolToolButton._refresh_visibility_presentation)
        self.assertIn("QPainter", source)
        self.assertIn("drawEllipse", source)
        self.assertIn("drawRect", source)
        self.assertIn("drawLine", source)
        self.assertNotIn("opacity", source)


if __name__ == "__main__":
    unittest.main()
