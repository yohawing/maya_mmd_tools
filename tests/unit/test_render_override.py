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
from unittest import mock
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


class _MUserRenderOperation(_Operation):
    operation_type = 3
    kUserDefined = operation_type

    def __init__(self, name):
        super().__init__(name)


class _MQuadRender(_Operation):
    operation_type = 2
    kQuadRender = operation_type

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


class _ReceiverRenderTarget:
    def __init__(self):
        self._samples = (ctypes.c_float * 16)(*([1.0] * 16))

    def updateDescription(self, _description):
        return None

    def rawData(self):
        return [ctypes.addressof(self._samples), 16, 64]


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


class _PackedOccupancyRenderTarget:
    """Tiny RGBA8 target double with a valid row/slice pitch contract."""

    def __init__(self, samples, width, height):
        self._samples = (ctypes.c_ubyte * len(samples))(*samples)
        self._width = width
        self._height = height

    def updateDescription(self, _description):
        return None

    def rawData(self):
        row_pitch = self._width * 4
        return [ctypes.addressof(self._samples), row_pitch, row_pitch * self._height]


class _MRenderTargetManager:
    def __init__(self, events=None):
        self.acquired = []
        self.released = []
        self.events = events if events is not None else []

    def acquireRenderTarget(self, description):
        target = (
            _ReceiverRenderTarget()
            if description.name() == render_override.RECEIVER_PROBE_TARGET_NAME
            else _MRenderTarget()
        )
        self.acquired.append((description, target))
        return target

    def releaseRenderTarget(self, target):
        self.released.append(target)
        self.events.append(("target", target))


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


class _BindingProbeShader:
    """MShaderInstance double exposing exactly one R32F target parameter."""

    def __init__(self, parameters=None, set_parameter_error=None):
        self._parameters = list(
            parameters
            if parameters is not None
            else [render_override.R32F_BINDING_PROBE_PARAMETER]
        )
        self._set_parameter_error = set_parameter_error
        self.parameter_calls = []

    def parameterList(self):
        return list(self._parameters)

    def setParameter(self, name, value):
        self.parameter_calls.append((name, value))
        if self._set_parameter_error is not None:
            raise self._set_parameter_error


class _MShaderManager:
    def __init__(self, shader=None, create_error=None, events=None):
        self.shader = shader if shader is not None else _BindingProbeShader()
        self.create_error = create_error
        self.requests = []
        self.released = []
        self.events = events if events is not None else []

    def getEffectsFileShader(self, path, technique, *args):
        self.requests.append((path, technique))
        if self.create_error is not None:
            raise self.create_error
        return self.shader

    def releaseShader(self, shader):
        self.released.append(shader)
        self.events.append(("shader", shader))


class _MSelectionList:
    """Small OpenMaya selection-list stand-in for target routing tests."""

    fail_on_add = None

    def __init__(self):
        self.items = []

    def add(self, component):
        if self.fail_on_add is not None and component == self.fail_on_add:
            raise RuntimeError("selection add failed")
        self.items.append(component)

    def getDependNode(self, index):
        return ("dependNode", self.items[index])


class _CameraDagPath:
    def __init__(self, name):
        self._name = name

    def fullPathName(self):
        return self._name


class _CameraSelectionList(_MSelectionList):
    def getDagPath(self, index):
        return _CameraDagPath(self.items[index])


class _CameraOverride:
    def __init__(self):
        self.mCameraPath = None
        self.mUseNearClippingPlane = False
        self.mNearClippingPlane = None
        self.mUseFarClippingPlane = False
        self.mFarClippingPlane = None


class _CameraRenderModule:
    MCameraOverride = _CameraOverride


class _ContextLightPath:
    def fullPathName(self):
        return "|renderOverrideParityLight|renderOverrideParityLightShape"

    def node(self):
        return ("context-light", 0)


class _ContextLightInfo:
    def lightType(self):
        return "directionalLight"

    def lightPath(self):
        return _ContextLightPath()


class _NativeShadowDrawContext:
    def numberOfActiveLights(self):
        return 1

    def getLightParameterInformation(self, _index):
        return _ContextLightInfo()


class _LightSpaceCmds:
    def __init__(self, *, directional=True):
        self.directional = directional
        self.deleted = []
        self.transforms = []

    def ls(self, *names, **kwargs):
        if names:
            return []
        if kwargs.get("type") == "directionalLight":
            return ["|renderOverrideParityLight|renderOverrideParityLightShape"] if self.directional else []
        if kwargs.get("type") == "transform":
            return ["|Mmd_root"]
        return []

    def listRelatives(self, node, **kwargs):
        if node.endswith("LightShape"):
            return ["|renderOverrideParityLight"]
        return []

    def attributeQuery(self, attribute, *, node, exists):
        return node == "|Mmd_root" and attribute == render_override.ATTR_MMD_MODEL_NAME

    def exactWorldBoundingBox(self, _node, **kwargs):
        return [-1.0, 0.0, -2.0, 3.0, 4.0, 2.0]

    def xform(self, node, **kwargs):
        if kwargs.get("matrix"):
            return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        if kwargs.get("rotation") is True:
            return [-45.0, -30.0, 0.0]
        self.transforms.append((node, kwargs))
        return None

    def camera(self, **kwargs):
        return (
            "|mmdToolsR32FLightSpaceCamera",
            "|mmdToolsR32FLightSpaceCamera|mmdToolsR32FLightSpaceCameraShape",
        )

    def setAttr(self, *_args):
        return None

    def objExists(self, node):
        return node not in self.deleted

    def delete(self, node):
        self.deleted.append(node)


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
    getShaderManager = MagicMock()
    drawAPI = MagicMock(return_value=kDirectX11)
    needEvaluateAllLights = MagicMock()
    setLightRequiresShadows = MagicMock(return_value=True)


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
    stub.MUserRenderOperation = _MUserRenderOperation
    stub.MQuadRender = _MQuadRender
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
        _MRenderer.getShaderManager.reset_mock()
        _MRenderer.drawAPI.reset_mock()
        _MRenderer.drawAPI.return_value = _MRenderer.kDirectX11
        _MRenderer.needEvaluateAllLights.reset_mock()
        _MRenderer.setLightRequiresShadows.reset_mock()
        _MRenderer.setLightRequiresShadows.return_value = True

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

    def test_native_shadow_receiver_is_opt_in_and_queued_after_scene(self):
        with mock.patch.dict(
            os.environ,
            {render_override.NATIVE_SHADOW_RECEIVER_ENV: "1"},
            clear=False,
        ):
            override = render_override.PassthroughRenderOverride()

        self.assertEqual(
            override.operation_names,
            (
                render_override.SCENE_OPERATION_NAME,
                render_override.NATIVE_SHADOW_RECEIVER_OPERATION_NAME,
                "standardHUD",
                render_override.PRESENT_OPERATION_NAME,
            ),
        )
        receiver = override.operations[1]
        self.assertIsInstance(receiver, render_override.NativeShadowReceiverRender)
        self.assertTrue(receiver.requiresLightData())
        self.assertEqual(receiver.renderFilterOverride(), _MSceneRender.kRenderShadedItems)
        self.assertIsNone(receiver.targetOverrideList())
        self.assertFalse(receiver.report()["claimsSelfShadow"])
        override.cleanup()

    def test_native_shadow_receiver_discovery_uses_receive_bit(self):
        class _ReceiverCmds(_CasterCmds):
            def __init__(self):
                super().__init__()
                self.values["matCast.mmd_draw_flags"] = 0x0C

        selection = render_override.discover_self_shadow_receiver_components(
            _ReceiverCmds()
        )

        self.assertEqual(selection.flagged_materials, ("matCast", "matNoCast"))
        self.assertEqual(
            selection.components,
            (
                "|Mmd_root|bodyShape.f[0:9]",
                "|Mmd_root|faceTransform.f[10:19]",
            ),
        )

    def test_light_space_camera_aligns_to_directional_light_and_releases_owned_nodes(self):
        cmds = _LightSpaceCmds()
        api = types.SimpleNamespace(MSelectionList=_CameraSelectionList)
        camera = render_override.LightSpaceCasterCamera(
            cmds_module=cmds,
            om_module=api,
            render_module=_CameraRenderModule,
        )

        configured = camera.configure()

        self.assertEqual(configured["status"], "configured")
        self.assertTrue(configured["createSucceeded"])
        self.assertEqual(configured["boundsSource"], "mmd-root-bounds")
        self.assertEqual(configured["directionalLight"], "|renderOverrideParityLight")
        self.assertEqual(
            camera.camera_override().mCameraPath.fullPathName(),
            "|mmdToolsR32FLightSpaceCamera|mmdToolsR32FLightSpaceCameraShape",
        )
        self.assertTrue(camera.camera_override().mUseNearClippingPlane)
        self.assertTrue(camera.camera_override().mUseFarClippingPlane)

        released = camera.release()

        self.assertEqual(released["status"], "released")
        self.assertTrue(released["releaseSucceeded"])
        self.assertFalse(camera.has_owned_camera())
        self.assertEqual(cmds.deleted, ["|mmdToolsR32FLightSpaceCamera"])

    def test_light_space_camera_fails_closed_without_directional_light(self):
        camera = render_override.LightSpaceCasterCamera(
            cmds_module=_LightSpaceCmds(directional=False),
            om_module=types.SimpleNamespace(MSelectionList=_CameraSelectionList),
            render_module=_CameraRenderModule,
        )

        report = camera.configure()

        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(report["reason"], "no-directional-light")
        self.assertFalse(camera.has_owned_camera())

    def test_native_shadow_request_owns_and_releases_directional_light_requests(self):
        request = render_override.NativeShadowRequest(
            cmds_module=_LightSpaceCmds(),
            om_module=types.SimpleNamespace(MSelectionList=_MSelectionList),
            render_module=types.SimpleNamespace(MRenderer=_MRenderer),
        )

        requested = request.request()

        self.assertEqual(requested["status"], "requested")
        self.assertTrue(requested["requestSucceeded"])
        self.assertEqual(requested["lightCount"], 1)
        self.assertTrue(request.has_owned_requests())
        requested_object = _MRenderer.setLightRequiresShadows.call_args.args[0]
        self.assertEqual(_MRenderer.setLightRequiresShadows.call_args.args[1], True)

        released = request.release()

        self.assertEqual(released["status"], "released")
        self.assertTrue(released["releaseSucceeded"])
        self.assertFalse(request.has_owned_requests())
        self.assertEqual(
            _MRenderer.setLightRequiresShadows.call_args_list,
            [mock.call(requested_object, True), mock.call(requested_object, False)],
        )

    def test_native_shadow_request_retains_ownership_when_release_is_rejected(self):
        request = render_override.NativeShadowRequest(
            cmds_module=_LightSpaceCmds(),
            om_module=types.SimpleNamespace(MSelectionList=_MSelectionList),
            render_module=types.SimpleNamespace(MRenderer=_MRenderer),
        )
        _MRenderer.setLightRequiresShadows.side_effect = [True, False]

        self.assertEqual(request.request()["status"], "requested")
        released = request.release()

        self.assertEqual(released["status"], "unsupported")
        self.assertEqual(released["reason"], "native-shadow-release-failed")
        self.assertFalse(released["releaseSucceeded"])
        self.assertTrue(request.has_owned_requests())
        _MRenderer.setLightRequiresShadows.side_effect = None

    def test_native_shadow_request_uses_active_draw_context_light_object(self):
        request = render_override.NativeShadowRequest(
            cmds_module=_LightSpaceCmds(),
            om_module=types.SimpleNamespace(MSelectionList=_MSelectionList),
            render_module=types.SimpleNamespace(MRenderer=_MRenderer),
        )

        report = request.request(_NativeShadowDrawContext())

        self.assertEqual(report["status"], "requested")
        self.assertEqual(report["lights"][0]["objectType"], "draw-context")
        self.assertEqual(
            _MRenderer.setLightRequiresShadows.call_args.args[0],
            ("context-light", 0),
        )
        _MRenderer.needEvaluateAllLights.assert_called_once_with()
        request.release()

    def test_native_shadow_binding_probe_binds_active_directional_map(self):
        class _LightSemantics:
            kShadowMap = 1
            kShadowViewProj = 2
            kGlobalShadowOn = 3
            kShadowOn = 4
            kWorldDirection = 5

        class _Texture:
            def resourceHandle(self):
                return 17

        class _TextureManager:
            def __init__(self):
                self.released = []

            def releaseTexture(self, texture):
                self.released.append(texture)

        texture = _Texture()
        texture_manager = _TextureManager()
        renderer = types.SimpleNamespace(
            kDirectX11=_MRenderer.kDirectX11,
            drawAPI=MagicMock(return_value=_MRenderer.kDirectX11),
            getShaderManager=MagicMock(return_value=_MShaderManager()),
            needEvaluateAllLights=MagicMock(),
            getTextureManager=MagicMock(return_value=texture_manager),
        )

        class _Info:
            def lightType(self):
                return "directionalLight"

            def lightPath(self):
                return _ContextLightPath()

            def parameterList(self):
                return ["shadow", "viewProj", "global", "local", "direction"]

            def parameterSemantic(self, name):
                return {
                    "shadow": 1,
                    "viewProj": 2,
                    "global": 3,
                    "local": 4,
                    "direction": 5,
                }[name]

            def getParameter(self, name):
                return {
                    "shadow": texture,
                    "viewProj": "shadow-matrix",
                    "global": [1],
                    "local": [1],
                    "direction": [0.0, -1.0, 0.0],
                }[name]

        class _Context:
            def numberOfActiveLights(self, _light_filter=None):
                return 1

            def getLightParameterInformation(self, _index, _light_filter=None):
                return _Info()

            def getLightingMode(self):
                return "scene-lights"

        render_module = types.SimpleNamespace(
            MRenderer=renderer,
            MDrawContext=types.SimpleNamespace(kFilteredIgnoreLightLimit=9),
            MLightParameterInformation=_LightSemantics,
        )
        manager = _MRenderTargetManager()
        _MRenderer.getRenderTargetManager.return_value = manager
        resources = render_override.NativeShadowBindingProbeResources()
        resources.acquire()
        probe = render_override.NativeShadowBindingProbe(resources, render_module=render_module)

        self.assertEqual(probe.create()["status"], "created")
        shader = renderer.getShaderManager.return_value.shader
        probe._pre_draw(_Context(), None, shader)

        report = probe.report()
        self.assertEqual(report["status"], "bound")
        self.assertTrue(report["bindingSucceeded"])
        self.assertEqual(report["resourceHandle"], 17)
        self.assertEqual(
            [name for name, _value in shader.parameter_calls],
            [
                render_override.NATIVE_SHADOW_BINDING_PROBE_MAP_PARAMETER,
                render_override.NATIVE_SHADOW_BINDING_PROBE_VIEWPROJ_PARAMETER,
                render_override.NATIVE_SHADOW_BINDING_PROBE_ENABLED_PARAMETER,
            ],
        )
        self.assertEqual(texture_manager.released, [texture])

        probe.capture_output()
        released = probe.release_shader()
        self.assertTrue(released["releaseSucceeded"])
        resources.release()
        self.assertTrue(probe.report()["output"]["balanced"])

    def test_registration_is_idempotent_and_owned(self):
        first = render_override.initializePlugin(object())
        second = render_override.initializePlugin(object())
        self.assertIs(first, second)
        _MRenderer.registerOverride.assert_called_once_with(first)
        self.assertTrue(render_override.is_registered())

        with mock.patch.object(first, "cleanup", wraps=first.cleanup) as cleanup:
            render_override.uninitializePlugin(object())
        cleanup.assert_called_once()
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

    def test_deregister_failure_retains_owned_override_for_retry(self):
        first = render_override.initializePlugin(object())
        _MRenderer.deregisterOverride.side_effect = RuntimeError("deregister rejected")

        with self.assertRaisesRegex(RuntimeError, "Failed to deregister"):
            render_override.uninitializePlugin(object())

        self.assertIs(render_override.registered_override(), first)
        _MRenderer.deregisterOverride.side_effect = None
        render_override.uninitializePlugin(object())
        self.assertFalse(render_override.is_registered())

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

    def test_r32f_binding_probe_sets_only_its_owned_shader_parameter_then_releases(self):
        shader = _BindingProbeShader()
        shader_manager = _MShaderManager(shader)
        _MRenderer.getShaderManager.return_value = shader_manager
        probe = render_override.R32FTargetBindingProbe()
        target = object()

        bound = probe.bind(target)

        self.assertEqual(bound["status"], "bound")
        self.assertTrue(bound["bindingSucceeded"])
        self.assertFalse(bound["drawsReceiver"])
        self.assertEqual(
            shader.parameter_calls,
            [(render_override.R32F_BINDING_PROBE_PARAMETER, target)],
        )
        self.assertEqual(len(shader_manager.requests), 1)
        self.assertEqual(shader_manager.released, [])

        released = probe.release()

        self.assertEqual(released["status"], "released")
        self.assertTrue(released["releaseSucceeded"])
        self.assertEqual(shader_manager.released, [shader])

    def test_r32f_binding_probe_fails_closed_without_target_parameter_or_manager(self):
        missing_parameter_shader = _BindingProbeShader(parameters=[])
        shader_manager = _MShaderManager(missing_parameter_shader)
        _MRenderer.getShaderManager.return_value = shader_manager
        probe = render_override.R32FTargetBindingProbe()

        missing_parameter = probe.bind(object())

        self.assertEqual(missing_parameter["status"], "unsupported")
        self.assertEqual(missing_parameter["reason"], "target-parameter-unavailable")
        self.assertEqual(shader_manager.released, [missing_parameter_shader])

        _MRenderer.getShaderManager.return_value = None
        unavailable = render_override.R32FTargetBindingProbe().bind(object())

        self.assertEqual(unavailable["status"], "unsupported")
        self.assertEqual(unavailable["reason"], "shader-manager-unavailable")

        _MRenderer.getShaderManager.return_value = _MShaderManager()
        _MRenderer.drawAPI.return_value = _MRenderer.kOpenGL
        unsupported_api = render_override.R32FTargetBindingProbe().bind(object())
        self.assertEqual(unsupported_api["status"], "unsupported")
        self.assertEqual(unsupported_api["reason"], "directx11-only-shader-probe")

    def test_r32f_binding_probe_clears_previous_success_before_failed_rebind(self):
        first_shader_manager = _MShaderManager()
        _MRenderer.getShaderManager.return_value = first_shader_manager
        probe = render_override.R32FTargetBindingProbe()
        self.assertEqual(probe.bind(object())["status"], "bound")

        second_shader_manager = _MShaderManager(_BindingProbeShader(parameters=[]))
        _MRenderer.getShaderManager.return_value = second_shader_manager
        failed = probe.bind(object())

        self.assertEqual(failed["status"], "unsupported")
        self.assertFalse(failed["bindingSucceeded"])
        self.assertEqual(failed["reason"], "target-parameter-unavailable")
        self.assertEqual(len(second_shader_manager.released), 1)

    def test_r32f_binding_probe_retains_shader_when_release_fails(self):
        class FailingShaderManager(_MShaderManager):
            def releaseShader(self, shader):
                super().releaseShader(shader)
                raise RuntimeError("release rejected")

        shader_manager = FailingShaderManager()
        _MRenderer.getShaderManager.return_value = shader_manager
        probe = render_override.R32FTargetBindingProbe()
        self.assertEqual(probe.bind(object())["status"], "bound")

        failed = probe.release()

        self.assertEqual(failed["status"], "unsupported")
        self.assertEqual(failed["reason"], "shader-release-failed")
        self.assertTrue(probe.has_owned_shader())
        self.assertEqual(failed["releaseAttemptCount"], 1)

        rebound = probe.bind(object())

        self.assertEqual(rebound["reason"], "previous-shader-release-failed")
        self.assertTrue(probe.has_owned_shader())

    def test_r32f_binding_probe_records_release_on_set_parameter_failure(self):
        shader = _BindingProbeShader(set_parameter_error=RuntimeError("bind rejected"))
        shader_manager = _MShaderManager(shader)
        _MRenderer.getShaderManager.return_value = shader_manager
        probe = render_override.R32FTargetBindingProbe()

        failed = probe.bind(object())

        self.assertEqual(failed["status"], "unsupported")
        self.assertEqual(failed["reason"], "target-binding-failed")
        self.assertTrue(failed["releaseSucceeded"])
        self.assertEqual(failed["releaseAttemptCount"], 1)
        self.assertEqual(shader_manager.released, [shader])

    def test_r32f_caster_shader_pass_creates_dedicated_shader_and_releases_before_target(self):
        shader = _BindingProbeShader(
            parameters=[render_override.R32F_CASTER_PASS_PARAMETER]
        )
        shader_manager = _MShaderManager(shader)
        _MRenderer.getShaderManager.return_value = shader_manager
        caster = render_override.R32FCasterShaderPass()

        created = caster.create((object(), object()))

        self.assertEqual(created["status"], "created")
        self.assertTrue(created["createSucceeded"])
        self.assertFalse(created["drawsReceiver"])
        self.assertFalse(created["claimsSelfShadow"])
        self.assertIs(caster.shader_instance(), shader)
        self.assertEqual(shader_manager.requests[0][1], render_override.R32F_CASTER_PASS_TECHNIQUE)

        released = caster.release()

        self.assertEqual(released["status"], "released")
        self.assertTrue(released["releaseSucceeded"])
        self.assertTrue(released["releaseBeforeTarget"])
        self.assertEqual(shader_manager.released, [shader])

    def test_r32f_caster_shader_pass_accepts_semantic_only_world_view_projection(self):
        shader_manager = _MShaderManager(_BindingProbeShader(parameters=[]))
        _MRenderer.getShaderManager.return_value = shader_manager
        caster = render_override.R32FCasterShaderPass()

        report = caster.create((object(), object()))

        self.assertEqual(report["status"], "created")
        self.assertEqual(report["reason"], "shader-created")
        self.assertTrue(report["createSucceeded"])
        self.assertIs(caster.shader_instance(), shader_manager.shader)

    def test_r32f_caster_shader_pass_retain_on_release_failure(self):
        class FailingShaderManager(_MShaderManager):
            def releaseShader(self, shader):
                super().releaseShader(shader)
                raise RuntimeError("release rejected")

        shader_manager = FailingShaderManager(
            _BindingProbeShader(parameters=[render_override.R32F_CASTER_PASS_PARAMETER])
        )
        _MRenderer.getShaderManager.return_value = shader_manager
        caster = render_override.R32FCasterShaderPass()
        self.assertEqual(caster.create((object(), object()))["status"], "created")

        failed = caster.release()

        self.assertEqual(failed["status"], "unsupported")
        self.assertEqual(failed["reason"], "shader-release-failed")
        self.assertFalse(failed["releaseBeforeTarget"])
        self.assertTrue(caster.has_owned_shader())

    def test_r32f_receiver_probe_binds_caster_target_and_reads_separate_output(self):
        events = []
        manager = _MRenderTargetManager(events=events)
        shader_manager = _MShaderManager(events=events)
        _MRenderer.getRenderTargetManager.return_value = manager
        _MRenderer.getShaderManager.return_value = shader_manager
        with mock.patch.dict(
            os.environ,
            {
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE": "1",
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_CASTER_PASS": "1",
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_RECEIVER_PROBE": "1",
            },
            clear=False,
        ):
            override = render_override.PassthroughRenderOverride()
            with mock.patch.object(override._scene_operation, "configure_panel_background"):
                override.setup("modelPanel4")
            receiver_report = override.target_probe_report()["r32fReceiverProbe"]
            self.assertEqual(receiver_report["status"], "created")
            self.assertTrue(receiver_report["bindSucceeded"])
            self.assertEqual(override.operations[1].operationType(), 2)
            self.assertEqual(
                shader_manager.shader.parameter_calls[-1][0],
                render_override.R32F_RECEIVER_PROBE_PARAMETER,
            )
            override._receiver_probe._post_draw(None, None, shader_manager.shader)
            self.assertEqual(
                override.target_probe_report()["r32fReceiverProbe"]["output"]["status"],
                "sampled",
            )
            override.cleanup()

        receiver_report = override.target_probe_report()["r32fReceiverProbe"]
        self.assertEqual(receiver_report["status"], "released")
        self.assertTrue(receiver_report["releaseBeforeTarget"])
        self.assertTrue(receiver_report["output"]["balanced"])
        self.assertEqual([event[0] for event in events], ["shader", "target", "shader", "target", "target"])

    def test_override_binding_probe_is_opt_in_and_releases_before_target_resources(self):
        events = []
        manager = _MRenderTargetManager(events=events)
        shader_manager = _MShaderManager(events=events)
        _MRenderer.getRenderTargetManager.return_value = manager
        _MRenderer.getShaderManager.return_value = shader_manager
        with mock.patch.dict(
            os.environ,
            {
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE": "1",
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_BINDING_PROBE": "1",
            },
            clear=False,
        ):
            override = render_override.PassthroughRenderOverride()
            with mock.patch.object(override._scene_operation, "configure_panel_background"):
                override.setup("modelPanel4")
            bound = override.target_probe_report()["r32fBindingProbe"]
            self.assertEqual(bound["status"], "bound")
            override.cleanup()

        released = override.target_probe_report()["r32fBindingProbe"]
        self.assertEqual(released["status"], "released")
        self.assertEqual(len(shader_manager.released), 1)
        self.assertEqual(len(manager.released), 2)
        self.assertEqual([event[0] for event in events], ["shader", "target", "target"])
        self.assertIs(events[0][1], shader_manager.released[0])

    def test_override_caster_pass_is_opt_in_and_releases_before_target_resources(self):
        events = []
        manager = _MRenderTargetManager(events=events)
        shader = _BindingProbeShader(
            parameters=[render_override.R32F_CASTER_PASS_PARAMETER]
        )
        shader_manager = _MShaderManager(shader, events=events)
        _MRenderer.getRenderTargetManager.return_value = manager
        _MRenderer.getShaderManager.return_value = shader_manager
        with mock.patch.dict(
            os.environ,
            {
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE": "1",
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_CASTER_PASS": "1",
            },
            clear=False,
        ):
            override = render_override.PassthroughRenderOverride()
            with mock.patch.object(override._scene_operation, "configure_panel_background"):
                override.setup("modelPanel4")
            caster_report = override.target_probe_report()["r32fCasterPass"]
            self.assertEqual(caster_report["status"], "created")
            self.assertIs(override._shadow_target_operation.shaderOverride(), shader)
            override.cleanup()

        released = override.target_probe_report()["r32fCasterPass"]
        self.assertEqual(released["status"], "released")
        self.assertTrue(released["releaseBeforeTarget"])
        self.assertEqual([event[0] for event in events], ["shader", "target", "target"])

    def test_override_caster_cleanup_defers_target_release_when_shader_release_fails(self):
        class FailingShaderManager(_MShaderManager):
            def releaseShader(self, shader):
                super().releaseShader(shader)
                raise RuntimeError("release rejected")

        manager = _MRenderTargetManager()
        shader_manager = FailingShaderManager(
            _BindingProbeShader(parameters=[render_override.R32F_CASTER_PASS_PARAMETER])
        )
        _MRenderer.getRenderTargetManager.return_value = manager
        _MRenderer.getShaderManager.return_value = shader_manager
        with mock.patch.dict(
            os.environ,
            {
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE": "1",
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_CASTER_PASS": "1",
            },
            clear=False,
        ):
            override = render_override.PassthroughRenderOverride()
            with mock.patch.object(override._scene_operation, "configure_panel_background"):
                override.setup("modelPanel4")
            with self.assertRaisesRegex(RuntimeError, "caster shader release failed"):
                override.cleanup()

        self.assertEqual(manager.released, [])
        self.assertTrue(override._r32f_caster_shader_pass.has_owned_shader())

    def test_override_cleanup_defers_target_release_when_shader_release_fails(self):
        class FailingShaderManager(_MShaderManager):
            def releaseShader(self, shader):
                super().releaseShader(shader)
                raise RuntimeError("release rejected")

        manager = _MRenderTargetManager()
        shader_manager = FailingShaderManager()
        _MRenderer.getRenderTargetManager.return_value = manager
        _MRenderer.getShaderManager.return_value = shader_manager
        with mock.patch.dict(
            os.environ,
            {
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE": "1",
                "MMD_TOOLS_ENABLE_RENDER_OVERRIDE_R32F_BINDING_PROBE": "1",
            },
            clear=False,
        ):
            override = render_override.PassthroughRenderOverride()
            with mock.patch.object(override._scene_operation, "configure_panel_background"):
                override.setup("modelPanel4")
            with self.assertRaisesRegex(RuntimeError, "target release deferred"):
                override.cleanup()

        self.assertEqual(manager.released, [])

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

    def test_packed_color_occupancy_is_authoritative_when_d24s8_is_not_decoded(self):
        resources = render_override.ShadowTargetResources()
        resources._descriptions["color"] = _MRenderTargetDescription(
            render_override.SHADOW_COLOR_TARGET_NAME,
            2,
            2,
            1,
            49,
            0,
            False,
        )
        resources._descriptions["depth"] = _MRenderTargetDescription(
            render_override.SHADOW_DEPTH_TARGET_NAME,
            2,
            2,
            1,
            _MRenderer.kD32_FLOAT,
            0,
            False,
        )
        resources._targets["color"] = _PackedOccupancyRenderTarget(
            [
                255,
                0,
                0,
                255,
                255,
                0,
                0,
                255,
                64,
                0,
                0,
                255,
                255,
                0,
                0,
                255,
            ],
            2,
            2,
        )
        resources._targets["depth"] = _OccupancyRenderTarget(
            [1.0, 1.0, 1.0, 1.0], 2, 2
        )

        with mock.patch.object(render_override, "SHADOW_PACKED_COLOR_FORMAT", 49):
            report = resources.capture_target_occupancy(
                {"status": "ok", "reason": "components-added", "count": 1}
            )

        self.assertEqual(report["status"], "occupied")
        self.assertEqual(report["evidenceTarget"], render_override.SHADOW_COLOR_TARGET_NAME)
        self.assertEqual(report["reason"], "rgba8-color-below-clear")
        self.assertEqual(report["colorOccupancy"]["sampleCount"], 4)
        self.assertEqual(report["colorOccupancy"]["nonClearSampleCount"], 1)
        self.assertEqual(
            report["colorOccupancy"]["encoding"], "normalized-depth-r8"
        )
        self.assertEqual(
            report["depthOccupancy"]["reason"], "all-clear-depth-after-caster-selection"
        )

    def test_d24s8_readback_decodes_the_24_bit_depth_component(self):
        resources = render_override.ShadowTargetResources()
        resources._descriptions["depth"] = _MRenderTargetDescription(
            render_override.SHADOW_DEPTH_TARGET_NAME,
            2,
            2,
            1,
            0,
            0,
            False,
        )
        resources._targets["depth"] = _PackedOccupancyRenderTarget(
            [
                0xFF,
                0xFF,
                0xFF,
                0x00,
                0x00,
                0x00,
                0x80,
                0x00,
                0xFF,
                0xFF,
                0xFF,
                0x00,
                0xFF,
                0xFF,
                0xFF,
                0x00,
            ],
            2,
            2,
        )

        with mock.patch.object(_MRenderer, "kD24S8", 0, create=True):
            report = resources.capture_depth_occupancy(
                {"status": "ok", "reason": "components-added", "count": 1}
            )

        self.assertEqual(report["status"], "occupied")
        self.assertEqual(report["reason"], "depth-below-clear")
        self.assertEqual(report["encoding"], "d24s8")
        self.assertEqual(report["sampleCount"], 4)
        self.assertEqual(report["nonClearSampleCount"], 1)
        self.assertAlmostEqual(report["minSample"], 0.5, places=6)

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

    def test_target_operation_fails_closed_when_opt_in_caster_shader_is_unavailable(self):
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
            caster = render_override.R32FCasterShaderPass()
            operation = render_override.ShadowTargetClearRender(
                object(),
                selection_provider=lambda: render_override.ShadowCasterSelection(
                    components=("|Mmd_root|bodyShape.f[0:9]",),
                    roots=("|Mmd_root",),
                    flagged_materials=("matCast",),
                    skipped_materials=(),
                ),
                caster_shader_pass=caster,
            )

            selected = operation.objectSetOverride()

            self.assertEqual(selected.items, [])
            self.assertEqual(
                operation.selection_report()["reason"], "caster-shader-unavailable"
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

    def test_plugin_unload_retains_gate_when_override_cleanup_fails(self):
        os.environ["MMD_TOOLS_ENABLE_RENDER_OVERRIDE"] = "1"
        self.plugin_main.initializePlugin(object())
        self.fake_render.uninitializePlugin.side_effect = RuntimeError("cleanup rejected")

        with self.assertRaisesRegex(RuntimeError, "cleanup rejected"):
            self.plugin_main.uninitializePlugin(object())

        self.assertTrue(self.plugin_main._render_override_registered)
        self.fake_render.uninitializePlugin.side_effect = None
        self.plugin_main.uninitializePlugin(object())
        self.assertFalse(self.plugin_main._render_override_registered)


if __name__ == "__main__":
    unittest.main()
