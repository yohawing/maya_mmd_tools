import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.core import maya_viewport_utils  # noqa: E402


class _FakeCmds:
    def __init__(
        self,
        focus_panel="modelPanel4",
        focus_type="modelPanel",
        model_panels=None,
        batch=False,
        about_error=False,
    ):
        self.focus_panel = focus_panel
        self.focus_type = focus_type
        self.model_panels = list(model_panels if model_panels is not None else ["modelPanel1"])
        self.batch = batch
        self.about_error = about_error
        self.model_editor_calls = []

    def getPanel(self, **kwargs):
        if kwargs.get("withFocus"):
            return self.focus_panel
        if "typeOf" in kwargs:
            return self.focus_type
        if kwargs.get("type") == "modelPanel":
            return list(self.model_panels)
        return None

    def about(self, **kwargs):
        if self.about_error:
            raise RuntimeError("about unavailable")
        if kwargs.get("batch"):
            return self.batch
        return None

    def modelEditor(self, panel_name, **kwargs):
        self.model_editor_calls.append((panel_name, kwargs))


class _FakeColorManagementCmds(_FakeCmds):
    def __init__(self):
        super().__init__()
        self.rendering_space = "ACEScg"
        self.view_transform = "ACES 1.0 SDR-video"
        self.cm_enabled = True
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
        if kwargs.get("q") and kwargs.get("cmEnabled"):
            return self.cm_enabled
        if kwargs.get("e") and "cmEnabled" in kwargs:
            self.cm_enabled = bool(kwargs["cmEnabled"])
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


class _FakeHardwareViewportCmds(_FakeCmds):
    def __init__(self, model_panels, states=None, failing_panels=None):
        super().__init__(focus_panel="modelPanel4", focus_type="modelPanel", model_panels=model_panels)
        self.states = {
            panel: {"displayAppearance": "wireframe", "displayTextures": False}
            for panel in self.model_panels
        }
        self.states.update(states or {})
        self.failing_panels = set(failing_panels or [])

    def modelEditor(self, panel_name, **kwargs):
        self.model_editor_calls.append((panel_name, kwargs))
        if panel_name in self.failing_panels:
            raise RuntimeError(f"panel unavailable: {panel_name}")
        if kwargs.get("query") or kwargs.get("q"):
            if kwargs.get("displayTextures"):
                return self.states[panel_name]["displayTextures"]
            return None
        if kwargs.get("edit"):
            if "displayTextures" in kwargs:
                self.states[panel_name]["displayTextures"] = kwargs["displayTextures"]


class _FakeDx11ShaderCmds:
    def __init__(self, values):
        self.values = dict(values)
        self.set_attr_calls = []

    def ls(self, **kwargs):
        return list(self.values) if kwargs.get("type") == "dx11Shader" else []

    def attributeQuery(self, attr, **kwargs):
        return attr == "DevicePixelRatio" and kwargs.get("node") in self.values

    def getAttr(self, plug):
        return self.values[plug.split(".", 1)[0]]

    def setAttr(self, plug, value):
        shader = plug.split(".", 1)[0]
        self.values[shader] = value
        self.set_attr_calls.append((plug, value))


class TestMayaViewportUtils(unittest.TestCase):
    def setUp(self):
        maya_viewport_utils._LAST_DEVICE_PIXEL_RATIO = None

    def test_device_pixel_ratio_uses_active_view_value(self):
        view = SimpleNamespace(devicePixelRatio=lambda: 2.0)

        self.assertEqual(maya_viewport_utils.get_device_pixel_ratio(view), 2.0)

    def test_device_pixel_ratio_rejects_invalid_values(self):
        for value in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(value=value):
                view = SimpleNamespace(devicePixelRatio=lambda value=value: value)
                self.assertEqual(maya_viewport_utils.get_device_pixel_ratio(view), 1.0)

    def test_device_pixel_ratio_falls_back_when_view_query_fails(self):
        view = SimpleNamespace()

        self.assertEqual(maya_viewport_utils.get_device_pixel_ratio(view, default=1.5), 1.5)

    def test_dx11_device_pixel_ratio_sync_updates_all_existing_shaders_once(self):
        fake_cmds = _FakeDx11ShaderCmds({"shaderA": 1.0, "shaderB": 2.0})

        with patch.object(maya_viewport_utils, "cmds", fake_cmds), patch.object(
            maya_viewport_utils, "get_device_pixel_ratio", return_value=2.0
        ):
            self.assertEqual(maya_viewport_utils.sync_dx11_shader_device_pixel_ratio(), 1)
            self.assertEqual(maya_viewport_utils.sync_dx11_shader_device_pixel_ratio(), 0)

        self.assertEqual(fake_cmds.set_attr_calls, [("shaderA.DevicePixelRatio", 2.0)])

    def test_forced_dx11_device_pixel_ratio_sync_finds_new_scene_shaders(self):
        fake_cmds = _FakeDx11ShaderCmds({"shaderA": 2.0})

        with patch.object(maya_viewport_utils, "cmds", fake_cmds), patch.object(
            maya_viewport_utils, "get_device_pixel_ratio", return_value=2.0
        ):
            self.assertEqual(maya_viewport_utils.sync_dx11_shader_device_pixel_ratio(), 0)
            fake_cmds.values["shaderB"] = 1.0
            self.assertEqual(
                maya_viewport_utils.sync_dx11_shader_device_pixel_ratio(force=True),
                1,
            )

        self.assertEqual(fake_cmds.values["shaderB"], 2.0)

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

        with patch.object(maya_viewport_utils, "cmds", fake_cmds), patch.object(
            maya_viewport_utils.logger, "warning"
        ) as warning:
            self.assertFalse(maya_viewport_utils.set_viewport_backface_culling(False))

        self.assertEqual(fake_cmds.model_editor_calls, [])
        warning.assert_called_once_with("No model panels found")

    def test_set_viewport_backface_culling_is_debug_only_without_model_panel_in_batch(self):
        fake_cmds = _FakeCmds(
            focus_panel="outlinerPanel1",
            focus_type="outlinerPanel",
            model_panels=[],
            batch=True,
        )

        with patch.object(maya_viewport_utils, "cmds", fake_cmds), patch.object(
            maya_viewport_utils.logger, "warning"
        ) as warning, patch.object(maya_viewport_utils.logger, "debug") as debug:
            self.assertFalse(maya_viewport_utils.set_viewport_backface_culling(False))

        warning.assert_not_called()
        debug.assert_called_once_with("No model panels found in Maya batch mode")

    def test_set_viewport_backface_culling_warns_when_batch_mode_query_fails(self):
        fake_cmds = _FakeCmds(
            focus_panel="outlinerPanel1",
            focus_type="outlinerPanel",
            model_panels=[],
            about_error=True,
        )

        with patch.object(maya_viewport_utils, "cmds", fake_cmds), patch.object(
            maya_viewport_utils.logger, "warning"
        ) as warning:
            self.assertFalse(maya_viewport_utils.set_viewport_backface_culling(False))

        warning.assert_called_once_with(
            "Could not determine Maya batch mode; no model panels found",
            exc_info=True,
        )

    def test_setup_mmd_color_management_sets_supported_mmd_view_settings(self):
        fake_cmds = _FakeColorManagementCmds()

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            self.assertTrue(maya_viewport_utils.setup_mmd_color_management())

        self.assertEqual(fake_cmds.rendering_space, "scene-linear Rec.709-sRGB")
        self.assertEqual(fake_cmds.view_transform, "Un-tone-mapped (sRGB)")

    def test_setup_mmd_native_color_management_disables_color_management(self):
        fake_cmds = _FakeColorManagementCmds()

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            self.assertTrue(maya_viewport_utils.setup_mmd_native_color_management())

        self.assertFalse(fake_cmds.cm_enabled)

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

    def test_setup_mmd_hardware_viewport_updates_every_model_panel(self):
        fake_cmds = _FakeHardwareViewportCmds(["modelPanel1", "modelPanel4", "modelPanel5"])

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            changed = maya_viewport_utils.setup_mmd_hardware_viewport()

        self.assertEqual(changed, 3)
        self.assertEqual(
            {
                panel: state
                for panel, state in fake_cmds.states.items()
            },
            {
                "modelPanel1": {"displayAppearance": "wireframe", "displayTextures": True},
                "modelPanel4": {"displayAppearance": "wireframe", "displayTextures": True},
                "modelPanel5": {"displayAppearance": "wireframe", "displayTextures": True},
            },
        )
        edit_calls = [kwargs for _, kwargs in fake_cmds.model_editor_calls if kwargs.get("edit")]
        self.assertEqual(edit_calls, [{"edit": True, "displayTextures": True}] * 3)

    def test_setup_mmd_hardware_viewport_skips_panels_already_enabled(self):
        panels = ["modelPanel1", "modelPanel4"]
        states = {
            panel: {"displayAppearance": "wireframe", "displayTextures": True}
            for panel in panels
        }
        fake_cmds = _FakeHardwareViewportCmds(panels, states=states)

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            changed = maya_viewport_utils.setup_mmd_hardware_viewport()

        self.assertEqual(changed, 0)
        self.assertFalse(any(call[1].get("edit") for call in fake_cmds.model_editor_calls))

    def test_setup_mmd_hardware_viewport_continues_after_panel_error(self):
        fake_cmds = _FakeHardwareViewportCmds(
            ["modelPanel1", "modelPanel4", "modelPanel5"],
            failing_panels={"modelPanel4"},
        )

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            changed = maya_viewport_utils.setup_mmd_hardware_viewport()

        self.assertEqual(changed, 2)
        self.assertEqual(
            fake_cmds.states["modelPanel1"],
            {"displayAppearance": "wireframe", "displayTextures": True},
        )
        self.assertEqual(
            fake_cmds.states["modelPanel5"],
            {"displayAppearance": "wireframe", "displayTextures": True},
        )

    def test_setup_mmd_hardware_viewport_returns_zero_without_model_panels(self):
        fake_cmds = _FakeHardwareViewportCmds([])

        with patch.object(maya_viewport_utils, "cmds", fake_cmds):
            changed = maya_viewport_utils.setup_mmd_hardware_viewport()

        self.assertEqual(changed, 0)
        self.assertEqual(fake_cmds.model_editor_calls, [])


class TestMmdOrderedViewport(unittest.TestCase):
    def setUp(self):
        env = patch.dict(maya_viewport_utils.os.environ)
        env.start()
        self.addCleanup(env.stop)
        maya_viewport_utils.os.environ.pop("MMD_TOOLS_CPP_ENABLE_ORDERED_RENDER", None)
        renderer_patch = patch("maya.api.OpenMayaRender.MRenderer")
        self.renderer = renderer_patch.start()
        self.addCleanup(renderer_patch.stop)
        self.renderer.kDirectX11 = 2
        self.renderer.drawAPI.return_value = 2
        self.cmds = Mock()
        self.cmds.getPanel.return_value = ["panelA", "panelB"]
        self.states = {"panelA": "", "panelB": ""}
        self.available = ["mmdOrdered"]
        self.renderer_name = "vp2Renderer"
        self.cmds.modelEditor.side_effect = self.model_editor
        cmds_patch = patch.object(maya_viewport_utils, "cmds", self.cmds)
        cmds_patch.start()
        self.addCleanup(cmds_patch.stop)

    def model_editor(self, panel, **kwargs):
        if kwargs.get("edit"):
            self.states[panel] = kwargs["rendererOverrideName"]
        elif kwargs.get("rendererName"):
            return self.renderer_name
        elif kwargs.get("rendererOverrideList"):
            return self.available
        elif kwargs.get("rendererOverrideName"):
            return self.states[panel]

    def test_default_selects_all_panels_and_repeated_setup_is_noop(self):
        self.assertEqual(maya_viewport_utils.setup_mmd_ordered_viewport(), 2)
        self.assertEqual(self.states, {"panelA": "mmdOrdered", "panelB": "mmdOrdered"})
        self.cmds.modelEditor.reset_mock()
        self.assertEqual(maya_viewport_utils.setup_mmd_ordered_viewport(), 0)
        self.assertFalse(any(call.kwargs.get("edit") for call in self.cmds.modelEditor.call_args_list))

    def test_explicit_optout_does_not_query_or_change_viewports(self):
        maya_viewport_utils.os.environ["MMD_TOOLS_CPP_ENABLE_ORDERED_RENDER"] = "0"
        self.assertEqual(maya_viewport_utils.setup_mmd_ordered_viewport(), 0)
        self.renderer.drawAPI.assert_not_called()
        self.cmds.getPanel.assert_not_called()

    def test_opengl_does_not_change_viewports(self):
        self.renderer.drawAPI.return_value = 4
        self.assertEqual(maya_viewport_utils.setup_mmd_ordered_viewport(), 0)
        self.cmds.modelEditor.assert_not_called()

    def test_unavailable_override_and_non_vp2_panel_are_preserved(self):
        for renderer, available in (("vp2Renderer", []), ("base_OpenGL_Renderer", ["mmdOrdered"])):
            with self.subTest(renderer=renderer):
                self.renderer_name = renderer
                self.available = available
                self.assertEqual(maya_viewport_utils.setup_mmd_ordered_viewport(), 0)
                self.assertEqual(self.states, {"panelA": "", "panelB": ""})

    def test_device_query_failure_skips_setup(self):
        self.renderer.drawAPI.side_effect = RuntimeError("device unavailable")
        self.assertEqual(maya_viewport_utils.setup_mmd_ordered_viewport(), 0)
        self.cmds.modelEditor.assert_not_called()

    def test_one_panel_failure_does_not_block_other_panels(self):
        def fail_first_panel(panel, **kwargs):
            if panel == "panelA":
                raise RuntimeError("panel unavailable")
            return self.model_editor(panel, **kwargs)

        self.cmds.modelEditor.side_effect = fail_first_panel
        self.assertEqual(maya_viewport_utils.setup_mmd_ordered_viewport(), 1)
        self.assertEqual(self.states, {"panelA": "", "panelB": "mmdOrdered"})

    def test_no_panels_returns_zero(self):
        self.cmds.getPanel.return_value = []
        self.assertEqual(maya_viewport_utils.setup_mmd_ordered_viewport(), 0)
        self.cmds.modelEditor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
