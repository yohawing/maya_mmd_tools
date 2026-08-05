"""Focused lifecycle tests for the opt-in R1 render override.

The Maya API classes are deliberately local stubs: these tests cover operation
ordering and ownership without pretending to be a VP2.0 GUI/render gate.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
import ctypes
from unittest.mock import MagicMock


class _Operation:
    operation_type = 0

    def __init__(self, name: str):
        self._name = name

    def name(self):
        return self._name

    def operationType(self):
        return self.operation_type


class _MSceneRender(_Operation):
    operation_type = 1
    kRenderShadedItems = 7

    def __init__(self, name):
        super().__init__(name)


class _MPassContext:
    kColorPassSemantic = "colorPass"


class _MHUDRender(_Operation):
    operation_type = 5

    def __init__(self):
        super().__init__("standardHUD")


class _MPresentTarget(_Operation):
    operation_type = 6

    def __init__(self, name):
        super().__init__(name)


class _MRenderTargetDescription:
    def __init__(self, name, width, height, samples, raster_format, _slices, _cube):
        self._name = name
        self._width = width
        self._height = height
        self._samples = samples
        self._raster_format = raster_format

    def name(self):
        return self._name

    def width(self):
        return self._width

    def height(self):
        return self._height

    def multiSampleCount(self):
        return self._samples

    def rasterFormat(self):
        return self._raster_format


class _MRenderTarget:
    def __init__(self):
        self.updated_descriptions = []
        self._clear_sample = ctypes.c_float(1.0)

    def updateDescription(self, description):
        self.updated_descriptions.append(description)

    def rawData(self):
        return [ctypes.addressof(self._clear_sample), 4, 4]


class _OccupancyRenderTarget:
    """Tiny R32F target double with a valid row/slice pitch contract."""

    def __init__(self, samples, width, height):
        self._samples = (ctypes.c_float * len(samples))(*samples)
        self._width = width
        self._height = height

    def updateDescription(self, _description):
        return None

    def rawData(self):
        row_pitch = self._width * ctypes.sizeof(ctypes.c_float)
        return [ctypes.addressof(self._samples), row_pitch, row_pitch * self._height]


class _MRenderTargetManager:
    def __init__(self):
        self.acquired = []
        self.released = []

    def acquireRenderTarget(self, description):
        target = _MRenderTarget()
        self.acquired.append((description, target))
        return target

    def releaseRenderTarget(self, target):
        self.released.append(target)


class _OccupancyRenderTargetManager(_MRenderTargetManager):
    def __init__(self, samples, width, height):
        super().__init__()
        self._samples = samples
        self._width = width
        self._height = height

    def acquireRenderTarget(self, description):
        if description.name() == render_override.SHADOW_COLOR_TARGET_NAME:
            target = _OccupancyRenderTarget(self._samples, self._width, self._height)
        else:
            target = _MRenderTarget()
        self.acquired.append((description, target))
        return target


class _MRenderTargetApi:
    @staticmethod
    def freeRawData(_raw_data):
        return None


class _MSelectionList:
    """Small OpenMaya selection-list stand-in for target routing tests."""

    fail_on_add = None

    def __init__(self):
        self.items = []

    def add(self, component):
        if self.fail_on_add is not None and component == self.fail_on_add:
            raise RuntimeError("selection add failed")
        self.items.append(component)


class _MRenderOverride:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class _PassContext:
    def __init__(self, semantics):
        self._semantics = list(semantics)

    def passSemantics(self):
        return list(self._semantics)


class _DrawContext:
    def __init__(self, semantics):
        self._pass_context = _PassContext(semantics)

    def getPassContext(self):
        return self._pass_context


class _MRenderer:
    kOpenGL = 1
    kDirectX11 = 2
    kOpenGLCoreProfile = 4
    kR32_FLOAT = 41
    kD32_FLOAT = 50
    registerOverride = MagicMock()
    deregisterOverride = MagicMock()
    getRenderTargetManager = MagicMock()


class _CasterCmds:
    """Minimal cmds double for the data-only MMD caster selection contract."""

    def __init__(self):
        self.nodes = ["|Mmd_root", "|Other_root", "|plainGroup"]
        self.attributes = {
            ("|Mmd_root", "mmd_model_name"): True,
            ("matCast", "mmd_draw_flags"): True,
            ("matNoCast", "mmd_draw_flags"): True,
            ("matBroken", "mmd_draw_flags"): True,
        }
        self.values = {
            "matCast.mmd_draw_flags": 0x04,
            "matNoCast.mmd_draw_flags": 0x08,
            "matBroken.mmd_draw_flags": "not-a-number",
        }

    def ls(self, *items, **kwargs):
        if items:
            assert kwargs == {"long": True}
            return {
            "bodyShape.f[0:9]": ["|Mmd_root|bodyShape.f[0:9]"],
            "faceTransform.f[10:19]": ["|Mmd_root|faceTransform.f[10:19]"],
            "foreignShape.f[0:9]": ["|Other_root|foreignShape.f[0:9]"],
            }[items[0]]
        assert kwargs == {"type": "transform", "long": True}
        return self.nodes

    def attributeQuery(self, attribute, node, exists):
        assert exists
        return self.attributes.get((node, attribute), False)

    def listRelatives(self, root, **kwargs):
        if root == "|Mmd_root":
            assert kwargs == {"allDescendents": True, "type": "mesh", "fullPath": True}
            return ["|Mmd_root|bodyShape", "|Mmd_root|faceShape"]
        assert kwargs == {"parent": True, "fullPath": True}
        return {
            "|Mmd_root|bodyShape": ["|Mmd_root|bodyTransform"],
            "|Mmd_root|faceShape": ["|Mmd_root|faceTransform"],
        }[root]

    def listConnections(self, item, **kwargs):
        if item == "|Mmd_root|bodyShape":
            assert kwargs == {"type": "shadingEngine"}
            return ["bodySG"]
        if item == "|Mmd_root|faceShape":
            assert kwargs == {"type": "shadingEngine"}
            return ["faceSG", "sharedCasterSG", "brokenSG"]
        if item == "bodySG.surfaceShader":
            assert kwargs == {"source": True, "destination": False}
            return ["matCast"]
        if item == "faceSG.surfaceShader":
            assert kwargs == {"source": True, "destination": False}
            return ["matNoCast"]
        if item == "sharedCasterSG.surfaceShader":
            assert kwargs == {"source": True, "destination": False}
            return ["matCast"]
        if item == "brokenSG.surfaceShader":
            assert kwargs == {"source": True, "destination": False}
            return ["matBroken"]
        raise AssertionError(item)

    def getAttr(self, plug):
        return self.values[plug]

    def sets(self, shading_group, query):
        assert query
        if shading_group == "bodySG":
            return ["bodyShape.f[0:9]", "foreignShape.f[0:9]"]
        if shading_group == "sharedCasterSG":
            return ["faceTransform.f[10:19]"]
        return []


def _import_with_stub():
    """Import render_override against subclassable local Maya API stubs."""
    original_omr = sys.modules.get("maya.api.OpenMayaRender")
    original_api = sys.modules.get("maya.api")
    stub = types.ModuleType("maya.api.OpenMayaRender")
    stub.MSceneRender = _MSceneRender
    stub.MPassContext = _MPassContext
    stub.MHUDRender = _MHUDRender
    stub.MPresentTarget = _MPresentTarget
    stub.MRenderOverride = _MRenderOverride
    stub.MRenderer = _MRenderer
    stub.MRenderTargetDescription = _MRenderTargetDescription
    stub.MRenderTarget = _MRenderTargetApi
    api = original_api or types.ModuleType("maya.api")
    api.OpenMayaRender = stub
    sys.modules["maya.api"] = api
    sys.modules["maya.api.OpenMayaRender"] = stub
    try:
        module = importlib.import_module("mmd_tools.view.render_override")
        return module
    finally:
        if original_omr is None:
            sys.modules.pop("maya.api.OpenMayaRender", None)
        else:
            sys.modules["maya.api.OpenMayaRender"] = original_omr
        if original_api is None:
            sys.modules.pop("maya.api", None)
        else:
            sys.modules["maya.api"] = original_api


render_override = _import_with_stub()


class RenderOverrideLifecycleTest(unittest.TestCase):
    def setUp(self):
        render_override._registered_override = None
        _MRenderer.registerOverride.reset_mock()
        _MRenderer.deregisterOverride.reset_mock()
        _MRenderer.getRenderTargetManager.reset_mock()

    def test_scene_hud_present_order_and_iterator_reset(self):
        override = render_override.PassthroughRenderOverride()

        self.assertEqual(override.operation_roles, ("scene", "hud", "present"))
        self.assertEqual(
            override.operation_names,
            (
                render_override.SCENE_OPERATION_NAME,
                "standardHUD",
                render_override.PRESENT_OPERATION_NAME,
            ),
        )
        self.assertTrue(override.startOperationIterator())

        seen = []
        while True:
            operation = override.renderOperation()
            if operation is None:
                break
            seen.append(operation.operationType())
            if not override.nextRenderOperation():
                break
        self.assertEqual(seen, [1, 5, 6])
        self.assertIsNone(override.renderOperation())

        override.cleanup()
        self.assertEqual(override.cleanup_count, 1)
        self.assertIsNone(override.renderOperation())
        self.assertTrue(override.startOperationIterator())
        self.assertEqual(override.renderOperation().operationType(), 1)

    def test_registration_is_idempotent_and_owned(self):
        first = render_override.initializePlugin(object())
        second = render_override.initializePlugin(object())
        self.assertIs(first, second)
        _MRenderer.registerOverride.assert_called_once_with(first)
        self.assertTrue(render_override.is_registered())

        render_override.uninitializePlugin(object())
        render_override.uninitializePlugin(object())
        _MRenderer.deregisterOverride.assert_called_once_with(first)
        self.assertFalse(render_override.is_registered())

    def test_registration_failure_rolls_back_partial_instance(self):
        _MRenderer.registerOverride.side_effect = RuntimeError("renderer unavailable")

        with self.assertRaisesRegex(RuntimeError, "Failed to register"):
            render_override.initializePlugin(object())

        _MRenderer.deregisterOverride.assert_called_once()
        self.assertFalse(render_override.is_registered())
        _MRenderer.registerOverride.side_effect = None

    def test_target_resources_use_fixed_formats_and_release_all_owned_targets(self):
        manager = _MRenderTargetManager()
        _MRenderer.getRenderTargetManager.return_value = manager
        resources = render_override.ShadowTargetResources()

        self.assertEqual(len(resources.acquire()), 2)
        resources.capture_color_clear_sample()
        resources.release()

        report = resources.report()
        self.assertEqual(report["color"]["name"], render_override.SHADOW_COLOR_TARGET_NAME)
        self.assertEqual(report["color"]["width"], 2048)
        self.assertEqual(report["color"]["rasterFormat"], _MRenderer.kR32_FLOAT)
        self.assertEqual(report["depth"]["rasterFormat"], _MRenderer.kD32_FLOAT)
        self.assertEqual(report["clearDepth"], 1.0)
        self.assertEqual(report["colorClearSample"], 1.0)
        self.assertEqual(report["readbackCount"], 1)
        self.assertEqual(report["acquireCount"], 2)
        self.assertEqual(report["releaseCount"], 2)
        self.assertTrue(report["balanced"])

    def test_target_manager_loss_drops_stale_target_wrappers(self):
        manager = _MRenderTargetManager()
        _MRenderer.getRenderTargetManager.return_value = manager
        resources = render_override.ShadowTargetResources()
        resources.acquire()

        _MRenderer.getRenderTargetManager.return_value = None
        resources.release()
        self.assertIn("disappeared", resources.report()["lastError"])

        _MRenderer.getRenderTargetManager.return_value = manager
        self.assertEqual(len(resources.acquire()), 2)
        self.assertEqual(len(manager.acquired), 4)

    def test_target_occupancy_requires_non_clear_evidence_for_selected_caster(self):
        manager = _OccupancyRenderTargetManager([1.0, 1.0, 1.0, 1.0], 2, 2)
        _MRenderer.getRenderTargetManager.return_value = manager
        resources = render_override.ShadowTargetResources()
        resources._descriptions["color"] = _MRenderTargetDescription(
            render_override.SHADOW_COLOR_TARGET_NAME,
            2,
            2,
            1,
            _MRenderer.kR32_FLOAT,
            0,
            False,
        )
        resources.acquire()

        report = resources.capture_color_occupancy(
            {
                "status": "ok",
                "reason": "components-added",
                "components": ["|Mmd_root|bodyShape.f[0:9]"],
                "count": 1,
            }
        )

        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(report["reason"], "r32f-occupancy-scan-disabled")
        self.assertEqual(report["selectedCasterCount"], 1)
        self.assertEqual(report["sampleCount"], 1)
        self.assertEqual(report["nonClearSampleCount"], 0)
        self.assertEqual(resources.report()["occupancy"], report)

    def test_target_color_readback_does_not_scan_for_occupancy(self):
        manager = _OccupancyRenderTargetManager([1.0, 0.25, 1.0, 1.0], 2, 2)
        _MRenderer.getRenderTargetManager.return_value = manager
        resources = render_override.ShadowTargetResources()
        resources._descriptions["color"] = _MRenderTargetDescription(
            render_override.SHADOW_COLOR_TARGET_NAME,
            2,
            2,
            1,
            _MRenderer.kR32_FLOAT,
            0,
            False,
        )
        resources.acquire()

        report = resources.capture_color_occupancy(
            {"status": "ok", "reason": "components-added", "count": 1}
        )

        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(report["reason"], "r32f-occupancy-scan-disabled")
        self.assertEqual(report["sampleCount"], 1)
        self.assertEqual(report["firstSample"], 1.0)
        self.assertEqual(resources.report()["readbackCount"], 1)

    def test_target_occupancy_is_empty_without_selected_casters_and_safe_on_missing_target(self):
        manager = _MRenderTargetManager()
        _MRenderer.getRenderTargetManager.return_value = manager
        resources = render_override.ShadowTargetResources()

        empty = resources.capture_color_occupancy(
            {"status": "empty", "reason": "no-components", "count": 0}
        )
        self.assertEqual(empty["status"], "empty")
        self.assertEqual(empty["reason"], "no-components")
        self.assertEqual(resources.report()["readbackCount"], 0)

        unsupported = resources.capture_color_occupancy(
            {"status": "ok", "reason": "components-added", "count": 1}
        )
        self.assertEqual(unsupported["status"], "unsupported")
        self.assertEqual(unsupported["reason"], "color-readback-unavailable")
        self.assertEqual(unsupported["sampleCount"], 0)

    def test_target_occupancy_uses_d32_draw_witness_and_keeps_r32f_claim_separate(self):
        manager = _OccupancyRenderTargetManager([1.0, 1.0, 1.0, 1.0], 2, 2)
        _MRenderer.getRenderTargetManager.return_value = manager
        resources = render_override.ShadowTargetResources()
        for role in ("color", "depth"):
            format_value = (
                _MRenderer.kR32_FLOAT if role == "color" else _MRenderer.kD32_FLOAT
            )
            resources._descriptions[role] = _MRenderTargetDescription(
                render_override.SHADOW_COLOR_TARGET_NAME
                if role == "color"
                else render_override.SHADOW_DEPTH_TARGET_NAME,
                2,
                2,
                1,
                format_value,
                0,
                False,
            )
        resources.acquire()
        depth_target = _OccupancyRenderTarget([1.0, 0.5, 1.0, 1.0], 2, 2)
        resources._targets["depth"] = depth_target

        report = resources.capture_target_occupancy(
            {"status": "ok", "reason": "components-added", "count": 1}
        )

        self.assertEqual(report["status"], "occupied")
        self.assertEqual(report["evidenceTarget"], render_override.SHADOW_DEPTH_TARGET_NAME)
        self.assertEqual(report["depthOccupancy"]["status"], "occupied")
        self.assertEqual(report["colorOccupancy"]["status"], "unsupported")
        self.assertEqual(
            report["colorOccupancy"]["reason"], "r32f-occupancy-scan-disabled"
        )
        again = resources.capture_target_occupancy(
            {"status": "ok", "reason": "components-added", "count": 1}
        )
        self.assertEqual(again, report)
        self.assertEqual(resources.report()["readbackCount"], 2)

    def test_caster_selection_uses_only_mmd_flagged_mesh_components(self):
        selection = render_override.discover_self_shadow_caster_components(_CasterCmds())

        self.assertEqual(selection.roots, ("|Mmd_root",))
        self.assertEqual(selection.flagged_materials, ("matCast",))
        self.assertEqual(
            selection.components,
            (
                "|Mmd_root|bodyShape.f[0:9]",
                "|Mmd_root|faceTransform.f[10:19]",
            ),
        )
        self.assertEqual(selection.skipped_materials, ("matBroken",))

    def test_target_operation_propagates_discovered_components_to_selection_list(self):
        openmaya = types.ModuleType("maya.api.OpenMaya")
        openmaya.MSelectionList = _MSelectionList
        original_api = sys.modules.get("maya.api")
        api = original_api or types.ModuleType("maya.api")
        original_openmaya = sys.modules.get("maya.api.OpenMaya")
        original_attr = getattr(api, "OpenMaya", None)
        api.OpenMaya = openmaya
        sys.modules["maya.api"] = api
        sys.modules["maya.api.OpenMaya"] = openmaya
        try:
            caster_selection = render_override.discover_self_shadow_caster_components(
                _CasterCmds()
            )
            operation = render_override.ShadowTargetClearRender(
                object(), selection_provider=lambda: caster_selection
            )

            self.assertEqual(
                operation.renderFilterOverride(), _MSceneRender.kRenderShadedItems
            )
            selected = operation.objectSetOverride()

            self.assertIsInstance(selected, _MSelectionList)
            self.assertEqual(
                selected.items,
                [
                    "|Mmd_root|bodyShape.f[0:9]",
                    "|Mmd_root|faceTransform.f[10:19]",
                ],
            )
            self.assertEqual(
                operation.selection_report(),
                {
                    "status": "ok",
                    "reason": "components-added",
                    "components": selected.items,
                    "count": 2,
                },
            )
        finally:
            if original_openmaya is None:
                sys.modules.pop("maya.api.OpenMaya", None)
            else:
                sys.modules["maya.api.OpenMaya"] = original_openmaya
            if original_attr is None:
                try:
                    delattr(api, "OpenMaya")
                except AttributeError:
                    pass
            else:
                api.OpenMaya = original_attr
            if original_api is None:
                sys.modules.pop("maya.api", None)
            else:
                sys.modules["maya.api"] = original_api

    def test_target_operation_returns_empty_selection_when_add_fails(self):
        openmaya = types.ModuleType("maya.api.OpenMaya")
        openmaya.MSelectionList = _MSelectionList
        original_api = sys.modules.get("maya.api")
        api = original_api or types.ModuleType("maya.api")
        original_openmaya = sys.modules.get("maya.api.OpenMaya")
        original_attr = getattr(api, "OpenMaya", None)
        api.OpenMaya = openmaya
        sys.modules["maya.api"] = api
        sys.modules["maya.api.OpenMaya"] = openmaya
        _MSelectionList.fail_on_add = "|Mmd_root|faceTransform.f[10:19]"
        try:
            caster_selection = render_override.discover_self_shadow_caster_components(
                _CasterCmds()
            )
            operation = render_override.ShadowTargetClearRender(
                object(), selection_provider=lambda: caster_selection
            )

            selected = operation.objectSetOverride()

            self.assertIsInstance(selected, _MSelectionList)
            self.assertEqual(selected.items, [])
            self.assertEqual(
                operation.selection_report(),
                {
                    "status": "error",
                    "reason": "add-failed",
                    "components": [
                        "|Mmd_root|bodyShape.f[0:9]",
                        "|Mmd_root|faceTransform.f[10:19]",
                    ],
                    "count": 2,
                },
            )
        finally:
            _MSelectionList.fail_on_add = None
            if original_openmaya is None:
                sys.modules.pop("maya.api.OpenMaya", None)
            else:
                sys.modules["maya.api.OpenMaya"] = original_openmaya
            if original_attr is None:
                try:
                    delattr(api, "OpenMaya")
                except AttributeError:
                    pass
            else:
                api.OpenMaya = original_attr
            if original_api is None:
                sys.modules.pop("maya.api", None)
            else:
                sys.modules["maya.api"] = original_api

    def test_target_operation_reads_once_on_color_pass_and_requires_explicit_manual_path(self):
        resources = MagicMock()
        resources.report.return_value = {"occupancy": {"status": "not-run"}}
        operation = render_override.ShadowTargetClearRender(resources)

        operation.postSceneRender(_DrawContext([_MPassContext.kColorPassSemantic]))
        resources.capture_target_occupancy.assert_called_once()
        operation.postSceneRender(_DrawContext(["shadowPass"]))
        operation.postSceneRender(None)
        resources.capture_target_occupancy.assert_called_once()

        operation.set_targets((object(), object()))
        operation.manual_target_occupancy()
        self.assertEqual(resources.capture_target_occupancy.call_count, 2)


class PluginRenderOverrideGateTest(unittest.TestCase):
    """Verify plugin_main's opt-in gate without starting Maya or VP2."""

    def setUp(self):
        plugin_parent = importlib.import_module("mmd_tools")
        self._plugin_parent = plugin_parent
        self._plugin_parent_had_attr = hasattr(plugin_parent, "plugin_main")
        self._plugin_parent_original = getattr(plugin_parent, "plugin_main", None)
        self._original_plugin_main = sys.modules.pop("mmd_tools.plugin_main", None)
        self._original_shader = sys.modules.get("mmd_tools.view.shader_override")
        shader = types.ModuleType("mmd_tools.view.shader_override")
        shader.initializePlugin = MagicMock()
        shader.uninitializePlugin = MagicMock()
        sys.modules["mmd_tools.view.shader_override"] = shader

        # plugin_main imports these modules eagerly.  Keep this gate test
        # independent from their Maya API inheritance requirements.
        self._injected_modules = []
        nodes_package = importlib.import_module("mmd_tools.nodes")
        self._original_node_attrs = {}
        for module_name in (
            "mmd_append_node",
            "mmd_bone_morph_accum_node",
            "mmd_ccd_ik_node",
            "mmd_material_morph_eval_node",
            "mmd_morph_controller_node",
            "mmd_rigid_body_shape",
            "mmd_rigid_body_draw_override",
            "mmd_physics_joint_shape",
            "mmd_physics_solver_node",
            "mmd_physics_bone_driver_node",
            "mmd_physics_world_shape",
        ):
            full_name = f"mmd_tools.nodes.{module_name}"
            self._injected_modules.append((full_name, sys.modules.get(full_name)))
            self._original_node_attrs[module_name] = getattr(
                nodes_package, module_name, None
            )
            fake_module = types.ModuleType(full_name)
            fake_module.register = MagicMock()
            fake_module.deregister = MagicMock()
            sys.modules[full_name] = fake_module
            setattr(nodes_package, module_name, fake_module)

        drag_drop = types.ModuleType("mmd_tools.ui.drag_drop_importer")
        drag_drop.install_drag_drop_importer = MagicMock()
        drag_drop.uninstall_drag_drop_importer = MagicMock()
        self._original_drag_drop = sys.modules.get("mmd_tools.ui.drag_drop_importer")
        sys.modules["mmd_tools.ui.drag_drop_importer"] = drag_drop

        self.plugin_main = importlib.import_module("mmd_tools.plugin_main")
        self.fake_render = types.SimpleNamespace(
            initializePlugin=MagicMock(),
            uninitializePlugin=MagicMock(),
        )
        self.plugin_main._render_override_module = self.fake_render
        self.plugin_main._render_override_registered = False
        self._old_env = {
            key: os.environ.get(key)
            for key in ("MMD_TOOLS_ENABLE_RENDER_OVERRIDE", "MMD_TOOLS_SKIP_SHADER_OVERRIDE")
        }
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"

        self._patches = []
        self._patches.extend(
            [
                self._replace("install_mmd_menu"),
                self._replace("uninstall_mmd_menu"),
                self._replace("install_drag_drop_importer"),
                self._replace("uninstall_drag_drop_importer"),
                self._replace("_soft_check_bone_morph_accum_availability"),
                self._replace("_soft_sync_existing_glsl_diffuse_contracts"),
                self._replace("_register_after_open_callback"),
                self._replace("_register_active_view_callback"),
                self._replace("_register_humanik_control_rig_watch"),
                self._replace("_deregister_humanik_control_rig_watch"),
                self._replace("_remove_active_view_callback"),
                self._replace("_remove_after_open_callback"),
                self._replace("close_control_rig_manager"),
                self._replace("close_animator_toolset"),
                self._replace("close_main_window"),
                self._replace("_close_humanik_window"),
                self._replace("_reset_humanik_session_after_scene_change"),
                self._replace("_reset_humanik_menu_session", return_value=True),
                self._replace("_scene_file_is_being_read", return_value=True),
                self._replace("_soft_sync_dx11_device_pixel_ratio"),
            ]
        )
        self.plugin_main.cmds = MagicMock()
        self.plugin_main.cmds.allNodeTypes.return_value = []
        self.plugin_main.om.MFnPlugin = MagicMock(return_value=MagicMock())
        for module_name in (
            "mmd_append_node",
            "mmd_bone_morph_accum_node",
            "mmd_ccd_ik_node",
            "mmd_material_morph_eval_node",
            "mmd_morph_controller_node",
            "mmd_rigid_body_shape",
            "mmd_rigid_body_draw_override",
            "mmd_physics_joint_shape",
            "mmd_physics_solver_node",
            "mmd_physics_bone_driver_node",
            "mmd_physics_world_shape",
        ):
            module = getattr(self.plugin_main, module_name)
            module.register = MagicMock()
            module.deregister = MagicMock()

        ae_module = types.ModuleType("mmd_tools.ui.morph_controller_ae")
        ae_module.install = MagicMock()
        self._original_ae = sys.modules.get("mmd_tools.ui.morph_controller_ae")
        sys.modules["mmd_tools.ui.morph_controller_ae"] = ae_module

    def _replace(self, name, **kwargs):
        replacement = MagicMock(**kwargs)
        original = getattr(self.plugin_main, name)
        setattr(self.plugin_main, name, replacement)
        return (name, original)

    def tearDown(self):
        self.plugin_main._render_override_registered = False
        self.plugin_main._render_override_module = None
        for name, original in self._patches:
            setattr(self.plugin_main, name, original)
        if self._original_ae is None:
            sys.modules.pop("mmd_tools.ui.morph_controller_ae", None)
        else:
            sys.modules["mmd_tools.ui.morph_controller_ae"] = self._original_ae
        if self._original_drag_drop is None:
            sys.modules.pop("mmd_tools.ui.drag_drop_importer", None)
        else:
            sys.modules["mmd_tools.ui.drag_drop_importer"] = self._original_drag_drop
        nodes_package = importlib.import_module("mmd_tools.nodes")
        for module_name, original in self._original_node_attrs.items():
            if original is None:
                try:
                    delattr(nodes_package, module_name)
                except AttributeError:
                    pass
            else:
                setattr(nodes_package, module_name, original)
        for full_name, original in self._injected_modules:
            if original is None:
                sys.modules.pop(full_name, None)
            else:
                sys.modules[full_name] = original
        sys.modules.pop("mmd_tools.plugin_main", None)
        if self._original_plugin_main is not None:
            sys.modules["mmd_tools.plugin_main"] = self._original_plugin_main
        if self._plugin_parent_had_attr:
            self._plugin_parent.plugin_main = self._plugin_parent_original
        elif hasattr(self._plugin_parent, "plugin_main"):
            delattr(self._plugin_parent, "plugin_main")
        if self._original_shader is None:
            sys.modules.pop("mmd_tools.view.shader_override", None)
        else:
            sys.modules["mmd_tools.view.shader_override"] = self._original_shader
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_default_plugin_load_does_not_register_or_deregister_override(self):
        os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE"] = "0"
        self.plugin_main.initializePlugin(object())
        self.fake_render.initializePlugin.assert_not_called()
        self.assertFalse(self.plugin_main._render_override_registered)

        self.plugin_main.uninitializePlugin(object())
        self.fake_render.uninitializePlugin.assert_not_called()

    def test_opt_in_plugin_load_registers_once_and_unload_deregisters_owned_override(self):
        os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE"] = "1"
        self.plugin_main.initializePlugin(object())
        self.fake_render.initializePlugin.assert_called_once()
        self.assertTrue(self.plugin_main._render_override_registered)

        self.plugin_main.uninitializePlugin(object())
        self.fake_render.uninitializePlugin.assert_called_once()
        self.assertFalse(self.plugin_main._render_override_registered)


if __name__ == "__main__":
    unittest.main()
