"""Unit coverage for material morph runtime evaluator and colour-route guards."""

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.converters import material_morph_runtime  # noqa: E402


def _attr_query_side_effect(exists=None, writable=None, **_extra):
    """Build an attributeQuery side_effect from attr-name maps.

    Args:
        exists: Mapping of short attr name -> bool (default False for unknown).
        writable: Mapping of short attr name -> bool (default True for unknown).
    """
    exists = exists or {}
    writable = writable or {}

    def _query(attr, node=None, **kwargs):  # noqa: ARG001
        if kwargs.get("exists"):
            return exists.get(attr, False)
        if kwargs.get("writable"):
            return writable.get(attr, True)
        return False

    return _query


def _rgb_safe_get_attr(*, locked=False, attr_type="double3", value=(1.0, 0.0, 0.0)):
    """getAttr side_effect for a safe/unsafe RGB plug probe."""

    def _get_attr(plug, **kwargs):  # noqa: ARG001
        if kwargs.get("lock"):
            return locked
        if kwargs.get("type"):
            return attr_type
        return [value]

    return _get_attr


class TestMaterialMorphRuntimeGuard(unittest.TestCase):
    def test_build_graph_does_not_connect_shader_by_default(self):
        cmds = mock.Mock()
        cmds.objExists.return_value = True
        with mock.patch.object(material_morph_runtime, "cmds", cmds), mock.patch.object(
            material_morph_runtime,
            "_collect_shaders_by_material_index",
        ) as collect_mock, mock.patch.object(
            material_morph_runtime,
            "_reroute_shader_color",
        ) as reroute_mock:
            result = material_morph_runtime.build_material_morph_graph("root")

        self.assertTrue(result["success"])
        self.assertEqual(result["evaluator_nodes"], [])
        self.assertEqual(result["contributions"], 0)
        self.assertEqual(result["skipped"], ["material_morph_shader_routing_disabled"])
        collect_mock.assert_not_called()
        reroute_mock.assert_not_called()

    def test_alpha_only_offsets_are_retained_for_full_channel_evaluation(self):
        additive_alpha_only = {
            "material_index": 0,
            "operation_type": 1,
            "diffuse": [0.0, 0.0, 0.0, -1.0],
        }
        multiply_alpha_only = {
            "material_index": 0,
            "operation_type": 0,
            "diffuse": [1.0, 1.0, 1.0, 0.0],
        }
        rgb_change = {
            "material_index": 0,
            "operation_type": 1,
            "diffuse": [0.25, 0.0, 0.0, 0.0],
        }

        self.assertTrue(material_morph_runtime._is_neutral_diffuse_rgb_offset(additive_alpha_only))
        self.assertTrue(material_morph_runtime._is_neutral_diffuse_rgb_offset(multiply_alpha_only))
        self.assertFalse(material_morph_runtime._is_neutral_diffuse_rgb_offset(rgb_change))

        skipped = []
        with mock.patch.object(
            material_morph_runtime,
            "_parse_offsets_json",
            return_value=[additive_alpha_only, multiply_alpha_only, rgb_change],
        ), mock.patch.object(material_morph_runtime, "_get_morph_order", return_value=7):
            contributions = material_morph_runtime._collect_contributions_by_shader(
                ["materialMorph1"],
                {0: "shader1"},
                skipped,
            )

        self.assertEqual(len(contributions["shader1"]), 3)
        self.assertEqual(
            contributions["shader1"][0]["diffuse"],
            (0.0, 0.0, 0.0, -1.0),
        )
        self.assertEqual(
            contributions["shader1"][1]["diffuse"],
            (1.0, 1.0, 1.0, 0.0),
        )
        self.assertEqual(
            contributions["shader1"][2]["diffuse"],
            (0.25, 0.0, 0.0, 0.0),
        )
        self.assertEqual(
            contributions["shader1"][0]["texture_factor"],
            (0.0, 0.0, 0.0, 0.0),
        )
        self.assertEqual(
            contributions["shader1"][1]["texture_factor"],
            (1.0, 1.0, 1.0, 1.0),
        )
        self.assertEqual(skipped, [])

    def test_create_evaluator_rejects_node_without_required_attrs(self):
        cmds = mock.Mock()
        cmds.createNode.return_value = "bad_materialMorphEval"
        cmds.objExists.return_value = True
        cmds.nodeType.return_value = material_morph_runtime.EVAL_NODE_TYPE
        cmds.attributeQuery.return_value = False

        with mock.patch.object(material_morph_runtime, "cmds", cmds):
            node = material_morph_runtime._create_evaluator("shader1")

        self.assertIsNone(node)
        cmds.delete.assert_called_once_with("bad_materialMorphEval")

    def test_create_evaluator_accepts_node_with_required_attrs(self):
        cmds = mock.Mock()
        cmds.createNode.return_value = "good_materialMorphEval"
        cmds.objExists.return_value = True
        cmds.nodeType.return_value = material_morph_runtime.EVAL_NODE_TYPE
        cmds.attributeQuery.return_value = True

        with mock.patch.object(material_morph_runtime, "cmds", cmds):
            node = material_morph_runtime._create_evaluator("shader1")

        self.assertEqual(node, "good_materialMorphEval")
        cmds.delete.assert_not_called()


class TestDetectEffectiveVp2DrawApi(unittest.TestCase):
    """Isolated effective VP2 draw-API detector (ogs + optionVar only)."""

    def test_prefers_ogs_device_information_directx(self):
        cmds = mock.Mock()
        cmds.ogs.return_value = "API : DirectX V.11\nAdapter : Fake GPU"
        cmds.optionVar.side_effect = AssertionError("optionVar must not run when ogs succeeds")

        with mock.patch.object(material_morph_runtime, "cmds", cmds):
            api = material_morph_runtime.detect_effective_vp2_draw_api()

        self.assertEqual(api, material_morph_runtime.VP2_API_DIRECTX11)
        cmds.ogs.assert_called_once_with(deviceInformation=True)

    def test_ogs_opengl_core_before_plain_opengl(self):
        cmds = mock.Mock()
        cmds.ogs.return_value = "OpenGL Core Profile"
        with mock.patch.object(material_morph_runtime, "cmds", cmds):
            self.assertEqual(
                material_morph_runtime.detect_effective_vp2_draw_api(),
                material_morph_runtime.VP2_API_OPENGL_CORE,
            )

    def test_falls_back_to_option_var_when_ogs_empty(self):
        cmds = mock.Mock()
        cmds.ogs.return_value = ""
        cmds.optionVar.return_value = "OpenGL"

        with mock.patch.object(material_morph_runtime, "cmds", cmds):
            api = material_morph_runtime.detect_effective_vp2_draw_api()

        self.assertEqual(api, material_morph_runtime.VP2_API_OPENGL)
        cmds.optionVar.assert_called_once_with(query="vp2RenderingEngine")

    def test_unknown_when_both_probes_fail(self):
        cmds = mock.Mock()
        cmds.ogs.side_effect = RuntimeError("no ogs")
        cmds.optionVar.side_effect = RuntimeError("no optionVar")

        with mock.patch.object(material_morph_runtime, "cmds", cmds):
            api = material_morph_runtime.detect_effective_vp2_draw_api()

        self.assertEqual(api, material_morph_runtime.VP2_API_UNKNOWN)

    def test_classify_helper_tokens(self):
        classify = material_morph_runtime._classify_vp2_draw_api_text
        self.assertEqual(classify("Direct3D11"), material_morph_runtime.VP2_API_DIRECTX11)
        self.assertEqual(classify("VirtualDeviceGLCore"), material_morph_runtime.VP2_API_OPENGL_CORE)
        self.assertEqual(classify("VirtualDeviceGL"), material_morph_runtime.VP2_API_OPENGL)
        self.assertEqual(classify(""), material_morph_runtime.VP2_API_UNKNOWN)


class TestResolveShaderColorRoute(unittest.TestCase):
    """Backend- and VP2-API-aware colour plug contract resolver."""

    def _patch_cmds(self, **overrides):
        cmds = mock.Mock()
        cmds.objExists.return_value = True
        for key, value in overrides.items():
            setattr(cmds, key, value)
        return cmds

    def _rgb_exists(self, *attrs):
        return {name: True for name in attrs}

    def _resolve(self, cmds, shader, **route_kwargs):
        with mock.patch.object(material_morph_runtime, "cmds", cmds):
            return material_morph_runtime.resolve_shader_color_route(shader, **route_kwargs)

    def test_standard_lambert_is_api_independent(self):
        cmds = self._patch_cmds()
        cmds.nodeType.return_value = "lambert"
        cmds.attributeQuery.side_effect = _attr_query_side_effect(
            exists=self._rgb_exists("color", "colorR", "colorG", "colorB"),
            writable=self._rgb_exists("color", "colorR", "colorG", "colorB"),
        )
        cmds.getAttr.side_effect = _rgb_safe_get_attr(value=(1, 1, 1))

        # Even with unknown VP2, standard materials still route.
        route = self._resolve(
            cmds,
            "mat_lambert",
            vp2_api=material_morph_runtime.VP2_API_UNKNOWN,
        )

        self.assertTrue(route.is_usable)
        self.assertEqual(route.backend, material_morph_runtime.BACKEND_STANDARD)
        self.assertEqual(route.attr_name, "color")
        self.assertIsNone(route.skip_reason)

    def test_standard_surface_routes_to_base_color(self):
        cmds = self._patch_cmds()
        cmds.nodeType.return_value = "standardSurface"
        cmds.attributeQuery.side_effect = _attr_query_side_effect(
            exists=self._rgb_exists("baseColor", "baseColorR", "baseColorG", "baseColorB"),
            writable=self._rgb_exists("baseColor", "baseColorR", "baseColorG", "baseColorB"),
        )
        cmds.getAttr.side_effect = _rgb_safe_get_attr(value=(0.8, 0.8, 0.8))

        route = self._resolve(cmds, "mat_ss", vp2_api=material_morph_runtime.VP2_API_OPENGL)

        self.assertTrue(route.is_usable)
        self.assertEqual(route.backend, material_morph_runtime.BACKEND_STANDARD)
        self.assertEqual(route.attr_name, "baseColor")

    def test_dx11_routes_only_on_directx11_with_rgb_plug(self):
        cmds = self._patch_cmds()
        cmds.nodeType.return_value = "dx11Shader"
        cmds.attributeQuery.side_effect = _attr_query_side_effect(
            exists={"DiffuseColorRGB": True, "DiffuseColor": True},
            writable={"DiffuseColorRGB": True, "DiffuseColor": True},
        )
        cmds.getAttr.side_effect = _rgb_safe_get_attr()

        route = self._resolve(
            cmds,
            "mat_dx11",
            vp2_api=material_morph_runtime.VP2_API_DIRECTX11,
        )

        self.assertTrue(route.is_usable)
        self.assertEqual(route.backend, material_morph_runtime.BACKEND_DX11)
        self.assertEqual(route.attr_name, "DiffuseColorRGB")

    def test_dx11_skips_on_non_directx_api(self):
        cmds = self._patch_cmds()
        cmds.nodeType.return_value = "dx11Shader"
        # Plug would be safe, but OpenGL must not route dx11Shader.
        cmds.attributeQuery.side_effect = _attr_query_side_effect(
            exists={"DiffuseColorRGB": True},
            writable={"DiffuseColorRGB": True},
        )
        cmds.getAttr.side_effect = _rgb_safe_get_attr()

        for api in (
            material_morph_runtime.VP2_API_OPENGL,
            material_morph_runtime.VP2_API_OPENGL_CORE,
            material_morph_runtime.VP2_API_UNKNOWN,
        ):
            route = self._resolve(cmds, "mat_dx11_gl", vp2_api=api)
            self.assertFalse(route.is_usable, api)
            self.assertEqual(route.backend, material_morph_runtime.BACKEND_DX11)
            self.assertIsNone(route.attr_name)
            self.assertEqual(route.skip_reason, "dx11_vp2_not_directx11:mat_dx11_gl")

    def test_dx11_unknown_api_fails_closed_even_with_usable_rgb_plug(self):
        cmds = self._patch_cmds()
        cmds.nodeType.return_value = "dx11Shader"
        cmds.attributeQuery.side_effect = _attr_query_side_effect(
            exists={"DiffuseColorRGB": True},
            writable={"DiffuseColorRGB": True},
        )
        cmds.getAttr.side_effect = _rgb_safe_get_attr()

        route = self._resolve(
            cmds,
            "mat_dx11_standalone",
            vp2_api=material_morph_runtime.VP2_API_UNKNOWN,
        )

        self.assertFalse(route.is_usable)
        self.assertEqual(route.backend, material_morph_runtime.BACKEND_DX11)
        self.assertIsNone(route.attr_name)
        self.assertEqual(
            route.skip_reason,
            "dx11_vp2_not_directx11:mat_dx11_standalone",
        )

    def test_dx11_skips_when_rgb_plug_missing_locked_or_wrong_type(self):
        cases = (
            ({"DiffuseColorRGB": False}, True, "double3", "missing"),
            ({"DiffuseColorRGB": True}, False, "double3", "locked"),
            ({"DiffuseColorRGB": True}, True, "double", "badtype"),
        )
        for exists_map, writable_ok, attr_type, label in cases:
            cmds = self._patch_cmds()
            cmds.nodeType.return_value = "dx11Shader"
            writable = {"DiffuseColorRGB": writable_ok} if "DiffuseColorRGB" in exists_map else {}
            # For locked case, writable stays True but lock probe fails.
            if label == "locked":
                writable = {"DiffuseColorRGB": True}
                locked = True
            else:
                locked = False
            if label == "missing":
                exists_map = {}
            cmds.attributeQuery.side_effect = _attr_query_side_effect(
                exists=exists_map if label != "missing" else {},
                writable=writable,
            )
            cmds.getAttr.side_effect = _rgb_safe_get_attr(locked=locked, attr_type=attr_type)

            route = self._resolve(
                cmds,
                f"mat_dx11_{label}",
                vp2_api=material_morph_runtime.VP2_API_DIRECTX11,
            )
            self.assertFalse(route.is_usable, label)
            self.assertEqual(
                route.skip_reason,
                f"dx11_diffuse_unroutable:mat_dx11_{label}",
                label,
            )

    def test_glsl_routes_only_on_opengl_with_rgb_not_vec4(self):
        cmds = self._patch_cmds()
        cmds.nodeType.return_value = "GLSLShader"
        # Both RGB contract and legacy vec4 exist; must choose RGB only.
        cmds.attributeQuery.side_effect = _attr_query_side_effect(
            exists={
                "DiffuseColorRGB": True,
                "DiffuseColor": True,
                "DiffuseColorR": True,
                "DiffuseColorG": True,
                "DiffuseColorB": True,
            },
            writable={
                "DiffuseColorRGB": True,
                "DiffuseColor": True,
                "DiffuseColorR": True,
                "DiffuseColorG": True,
                "DiffuseColorB": True,
            },
        )
        cmds.getAttr.side_effect = _rgb_safe_get_attr(attr_type="float3", value=(0.8, 0.8, 0.8))

        for api in (
            material_morph_runtime.VP2_API_OPENGL,
            material_morph_runtime.VP2_API_OPENGL_CORE,
        ):
            route = self._resolve(cmds, "mat_glsl", vp2_api=api)
            self.assertTrue(route.is_usable, api)
            self.assertEqual(route.backend, material_morph_runtime.BACKEND_GLSL)
            self.assertEqual(route.attr_name, "DiffuseColorRGB")
            self.assertNotEqual(route.attr_name, "DiffuseColor")

    def test_glsl_skips_on_non_opengl_api(self):
        cmds = self._patch_cmds()
        cmds.nodeType.return_value = "GLSLShader"
        cmds.attributeQuery.side_effect = _attr_query_side_effect(
            exists={"DiffuseColorRGB": True},
            writable={"DiffuseColorRGB": True},
        )
        cmds.getAttr.side_effect = _rgb_safe_get_attr()

        for api in (
            material_morph_runtime.VP2_API_DIRECTX11,
            material_morph_runtime.VP2_API_UNKNOWN,
        ):
            route = self._resolve(cmds, "mat_glsl_dx", vp2_api=api)
            self.assertFalse(route.is_usable, api)
            self.assertEqual(route.skip_reason, "glsl_vp2_not_opengl:mat_glsl_dx")

    def test_glsl_unknown_api_fails_closed_even_with_usable_rgb_plug(self):
        cmds = self._patch_cmds()
        cmds.nodeType.return_value = "GLSLShader"
        cmds.attributeQuery.side_effect = _attr_query_side_effect(
            exists={"DiffuseColorRGB": True},
            writable={"DiffuseColorRGB": True},
        )
        cmds.getAttr.side_effect = _rgb_safe_get_attr(attr_type="float3")

        route = self._resolve(
            cmds,
            "mat_glsl_standalone",
            vp2_api=material_morph_runtime.VP2_API_UNKNOWN,
        )

        self.assertFalse(route.is_usable)
        self.assertEqual(route.backend, material_morph_runtime.BACKEND_GLSL)
        self.assertIsNone(route.attr_name)
        self.assertEqual(
            route.skip_reason,
            "glsl_vp2_not_opengl:mat_glsl_standalone",
        )

    def test_glsl_skips_when_only_legacy_vec4_or_plug_unsafe(self):
        # Only vec4 DiffuseColor present — must fail closed (never reconnect vec4).
        cmds = self._patch_cmds()
        cmds.nodeType.return_value = "GLSLShader"
        cmds.attributeQuery.side_effect = _attr_query_side_effect(
            exists={
                "DiffuseColor": True,
                "DiffuseColorR": True,
                "DiffuseColorG": True,
                "DiffuseColorB": True,
            },
            writable={
                "DiffuseColor": True,
                "DiffuseColorR": True,
                "DiffuseColorG": True,
                "DiffuseColorB": True,
            },
        )
        cmds.getAttr.side_effect = _rgb_safe_get_attr(attr_type="float3")

        route = self._resolve(
            cmds,
            "mat_glsl_vec4only",
            vp2_api=material_morph_runtime.VP2_API_OPENGL,
        )
        self.assertFalse(route.is_usable)
        self.assertEqual(route.skip_reason, "glsl_diffuse_unroutable:mat_glsl_vec4only")

        # RGB exists but not writable.
        cmds.attributeQuery.side_effect = _attr_query_side_effect(
            exists={"DiffuseColorRGB": True},
            writable={"DiffuseColorRGB": False},
        )
        route = self._resolve(
            cmds,
            "mat_glsl_ro",
            vp2_api=material_morph_runtime.VP2_API_OPENGL,
        )
        self.assertFalse(route.is_usable)
        self.assertEqual(route.skip_reason, "glsl_diffuse_unroutable:mat_glsl_ro")

    def test_build_graph_records_skip_without_rerouting_unusable_dx11(self):
        """Graph builder must leave shader colour untouched and record skip."""
        cmds = mock.Mock()
        with mock.patch.object(material_morph_runtime, "cmds", cmds), mock.patch.object(
            material_morph_runtime,
            "detect_effective_vp2_draw_api",
            return_value=material_morph_runtime.VP2_API_OPENGL,
        ), mock.patch.object(
            material_morph_runtime,
            "_collect_shaders_by_material_index",
            return_value={0: "dx11_shader"},
        ), mock.patch.object(
            material_morph_runtime,
            "_collect_contributions_by_shader",
            return_value={
                "dx11_shader": [
                    {
                        "morph_node": "morph1",
                        "morph_order": 0,
                        "material_index": 0,
                        "operation_type": 1,
                        "diffuse_rgb": (1.0, 0.0, 0.0),
                    }
                ]
            },
        ), mock.patch.object(
            material_morph_runtime,
            "_collect_existing_evaluators",
            return_value={},
        ), mock.patch.object(
            material_morph_runtime,
            "_create_evaluator",
            return_value="dx11_shader_materialMorphEval",
        ), mock.patch.object(
            material_morph_runtime,
            "_mark_evaluator",
        ), mock.patch.object(
            material_morph_runtime,
            "_refresh_contributions",
        ), mock.patch.object(
            material_morph_runtime,
            "resolve_shader_color_route",
            return_value=material_morph_runtime.ShaderColorRoute(
                backend=material_morph_runtime.BACKEND_DX11,
                skip_reason="dx11_vp2_not_directx11:dx11_shader",
            ),
        ) as resolve_mock, mock.patch.object(
            material_morph_runtime,
            "_reroute_shader_color",
        ) as reroute_mock, mock.patch.object(
            material_morph_runtime,
            "_iter_material_morph_nodes",
            return_value=["morph1"],
        ):
            cmds.objExists.return_value = True
            result = material_morph_runtime.build_material_morph_graph("root", connect_shader=True)

        self.assertTrue(result["success"])
        self.assertIn("dx11_vp2_not_directx11:dx11_shader", result["skipped"])
        self.assertEqual(result["evaluator_nodes"], ["dx11_shader_materialMorphEval"])
        self.assertEqual(result["contributions"], 1)
        # Public summary keys stay compatible.
        for key in ("success", "evaluator_nodes", "created", "reused", "contributions", "skipped"):
            self.assertIn(key, result)
        resolve_mock.assert_called_once_with(
            "dx11_shader",
            vp2_api=material_morph_runtime.VP2_API_OPENGL,
        )
        reroute_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
