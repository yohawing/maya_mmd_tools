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


class _FakeColorManagementCmds(_FakeCmds):
    def __init__(self):
        super().__init__()
        self.rendering_space = "ACEScg"
        self.view_transform = "ACES 1.0 SDR-video"
        self.color_management_calls = []

    def colorManagementPrefs(self, **kwargs):
        self.color_management_calls.append(kwargs)
        if kwargs.get("q") and kwargs.get("renderingSpaceNames"):
            return ["ACEScg", "scene-linear Rec.709-sRGB"]
        if kwargs.get("q") and kwargs.get("renderingSpaceName"):
            return self.rendering_space
        if kwargs.get("e") and "renderingSpaceName" in kwargs:
            self.rendering_space = kwargs["renderingSpaceName"]
            return None
        if kwargs.get("q") and kwargs.get("viewTransformNames"):
            return ["ACES 1.0 SDR-video", "Un-tone-mapped (sRGB)"]
        if kwargs.get("q") and kwargs.get("viewTransformName"):
            return self.view_transform
        if kwargs.get("e") and "viewTransformName" in kwargs:
            self.view_transform = kwargs["viewTransformName"]
            return None
        return None


class _FakeTransparencyCmds(_FakeCmds):
    def __init__(self, exists=True, has_attr=True, current=1):
        super().__init__()
        self.exists = exists
        self.has_attr = has_attr
        self.current = current
        self.set_attr_calls = []

    def objExists(self, node):
        return self.exists and node == "hardwareRenderingGlobals"

    def attributeQuery(self, attr, **kwargs):
        return attr == "transparencyAlgorithm" and kwargs.get("node") == "hardwareRenderingGlobals" and self.has_attr

    def getAttr(self, attr):
        if attr == "hardwareRenderingGlobals.transparencyAlgorithm":
            return self.current
        return None

    def setAttr(self, attr, value):
        self.set_attr_calls.append((attr, value))
        if attr == "hardwareRenderingGlobals.transparencyAlgorithm":
            self.current = value


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

    def test_setup_mmd_color_management_sets_supported_mmd_view_settings(self):
        fake_cmds = _FakeColorManagementCmds()

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            self.assertTrue(maya_viewport_utils.setup_mmd_color_management())

        self.assertEqual(fake_cmds.rendering_space, "scene-linear Rec.709-sRGB")
        self.assertEqual(fake_cmds.view_transform, "Un-tone-mapped (sRGB)")

    def test_setup_mmd_transparency_sets_depth_peeling(self):
        fake_cmds = _FakeTransparencyCmds(current=1)

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            self.assertTrue(maya_viewport_utils.setup_mmd_transparency())

        self.assertEqual(
            fake_cmds.set_attr_calls,
            [("hardwareRenderingGlobals.transparencyAlgorithm", maya_viewport_utils.TRANSPARENCY_ALGORITHM_DEPTH_PEELING)],
        )

    def test_setup_mmd_transparency_returns_false_without_attribute(self):
        fake_cmds = _FakeTransparencyCmds(has_attr=False)

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            self.assertFalse(maya_viewport_utils.setup_mmd_transparency())

        self.assertEqual(fake_cmds.set_attr_calls, [])


if __name__ == "__main__":
    unittest.main()
