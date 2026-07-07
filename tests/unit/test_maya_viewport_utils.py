import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.core import maya_viewport_utils  # noqa: E402


class _FakeCmds:
    def __init__(self, focus_panel="modelPanel4", focus_type="modelPanel", model_panels=None):
        self.focus_panel = focus_panel
        self.focus_type = focus_type
        self.model_panels = list(model_panels if model_panels is not None else ["modelPanel1"])
        self.model_editor_calls = []

    def getPanel(self, **kwargs):
        if kwargs.get("withFocus"):
            return self.focus_panel
        if "typeOf" in kwargs:
            return self.focus_type
        if kwargs.get("type") == "modelPanel":
            return list(self.model_panels)
        return None

    def modelEditor(self, panel_name, **kwargs):
        self.model_editor_calls.append((panel_name, kwargs))


class TestMayaViewportUtils(unittest.TestCase):
    def test_set_viewport_backface_culling_uses_focused_model_panel(self):
        fake_cmds = _FakeCmds(focus_panel="modelPanel4", focus_type="modelPanel")

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            self.assertTrue(maya_viewport_utils.set_viewport_backface_culling(False))

        self.assertEqual(fake_cmds.model_editor_calls, [("modelPanel4", {"edit": True, "backfaceCulling": False})])

    def test_set_viewport_backface_culling_falls_back_to_first_model_panel(self):
        fake_cmds = _FakeCmds(focus_panel="outlinerPanel1", focus_type="outlinerPanel", model_panels=["modelPanel2"])

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            self.assertTrue(maya_viewport_utils.set_viewport_backface_culling(True))

        self.assertEqual(fake_cmds.model_editor_calls, [("modelPanel2", {"edit": True, "backfaceCulling": True})])

    def test_set_viewport_backface_culling_returns_false_without_model_panel(self):
        fake_cmds = _FakeCmds(focus_panel="outlinerPanel1", focus_type="outlinerPanel", model_panels=[])

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            self.assertFalse(maya_viewport_utils.set_viewport_backface_culling(False))

        self.assertEqual(fake_cmds.model_editor_calls, [])


if __name__ == "__main__":
    unittest.main()
