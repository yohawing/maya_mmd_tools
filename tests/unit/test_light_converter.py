"""Unit coverage for MMD light hardware-shader wiring."""

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.converters import light_converter  # noqa: E402


class TestMmdLightShaderWiring(unittest.TestCase):
    def test_wires_dx11_and_glsl_uniforms(self):
        cmds = mock.Mock()
        cmds.objExists.return_value = True
        cmds.nodeType.side_effect = {"dx": "dx11Shader", "gl": "GLSLShader", "lambert1": "lambert"}.get
        cmds.attributeQuery.return_value = True

        with mock.patch.object(light_converter, "cmds", cmds), mock.patch.object(
            light_converter,
            "_get_or_create_light_direction_node",
            return_value="mmd_light_dirVP",
        ):
            wired = light_converter.wire_mmd_shaders_to_mmd_light(
                ["dx", "gl", "lambert1"],
                "mmd_light",
            )

        self.assertEqual(wired, 2)
        expected_attrs = {
            "dx": ("MMDLightDirection", "MMDLightColor"),
            "gl": ("MmdControllerLightVector", "MmdControllerLightRgb"),
        }
        for shader, (direction_attr, color_attr) in expected_attrs.items():
            cmds.connectAttr.assert_any_call(
                "mmd_light_dirVP.output",
                f"{shader}.{direction_attr}",
                force=True,
            )
            cmds.connectAttr.assert_any_call(
                "mmd_light.mmd_light_color",
                f"{shader}.{color_attr}",
                force=True,
            )

    def test_does_not_count_shader_without_light_uniforms(self):
        cmds = mock.Mock()
        cmds.objExists.return_value = True
        cmds.nodeType.return_value = "GLSLShader"
        cmds.attributeQuery.return_value = False

        with mock.patch.object(light_converter, "cmds", cmds), mock.patch.object(
            light_converter,
            "_get_or_create_light_direction_node",
            return_value="mmd_light_dirVP",
        ):
            wired = light_converter.wire_mmd_shaders_to_mmd_light(["gl"], "mmd_light")

        self.assertEqual(wired, 0)
        cmds.connectAttr.assert_not_called()


if __name__ == "__main__":
    unittest.main()
