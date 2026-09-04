"""Unit coverage for MMD light hardware-shader wiring."""

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.converters import light_converter  # noqa: E402


class TestMmdLightShaderWiring(unittest.TestCase):
    def test_new_self_shadow_mode_defaults_to_mode1(self):
        cmds = mock.Mock()
        cmds.objExists.return_value = True
        cmds.attributeQuery.return_value = False

        with mock.patch.object(light_converter, "cmds", cmds):
            light_converter.ensure_mmd_light_shadow_attrs("mmd_light")

        cmds.addAttr.assert_any_call(
            "mmd_light",
            longName="mmd_self_shadow_mode",
            attributeType="enum",
            enumName="OFF:MODE1:MODE2",
            defaultValue=1,
            keyable=True,
        )

    def test_reused_light_preserves_existing_off_and_mode2(self):
        for mode in (0, 2):
            with self.subTest(mode=mode):
                cmds = mock.Mock()
                cmds.objExists.return_value = True
                cmds.attributeQuery.return_value = True
                cmds.getAttr.return_value = mode
                with mock.patch.object(light_converter, "cmds", cmds), mock.patch.object(
                    light_converter, "find_mmd_light", return_value="mmd_light"
                ):
                    self.assertEqual(light_converter.create_mmd_light_controller(), "mmd_light")
                cmds.addAttr.assert_not_called()
                cmds.setAttr.assert_not_called()

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
