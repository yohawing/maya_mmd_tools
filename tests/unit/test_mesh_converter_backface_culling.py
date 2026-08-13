"""Pure-Python checks for PMX material backface-culling helpers."""

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters.mesh_converter import (  # noqa: E402
    MeshConverter,
    TRANSPARENCY_MODE_BLEND,
    TRANSPARENCY_MODE_CUTOUT,
    TRANSPARENCY_MODE_OPAQUE,
    _dx11_rendering_from_technique,
    _material_is_double_sided,
    _set_mesh_double_sided,
    _technique_for_transparency,
    apply_shader_outline,
    apply_transparency_mode,
    get_shader_outline_enabled,
    get_transparency_mode,
)
from mmd_tools.converters.mesh_material_properties import material_has_outline  # noqa: E402


class TestMaterialIsDoubleSided(unittest.TestCase):
    def test_draw_flag_bit0_enabled(self):
        self.assertTrue(_material_is_double_sided(SimpleNamespace(draw_flag=0x01)))

    def test_draw_flag_zero_disabled(self):
        self.assertFalse(_material_is_double_sided(SimpleNamespace(draw_flag=0x00)))

    def test_draw_flag_other_bits_only_disabled(self):
        self.assertFalse(_material_is_double_sided(SimpleNamespace(draw_flag=0x10)))

    def test_draw_flag_bit0_plus_other_bits_enabled(self):
        self.assertTrue(_material_is_double_sided(SimpleNamespace(draw_flag=0x11)))

    def test_missing_draw_flag_disabled(self):
        self.assertFalse(_material_is_double_sided(SimpleNamespace()))

    def test_outline_uses_pmx_edge_bit(self):
        self.assertTrue(material_has_outline(SimpleNamespace(draw_flag=0x10)))
        self.assertFalse(material_has_outline(SimpleNamespace(draw_flag=0x01)))

    def test_outline_uses_pmd_edge_flag(self):
        self.assertTrue(material_has_outline(SimpleNamespace(edge_flag=1), is_pmd=True))
        self.assertFalse(material_has_outline(SimpleNamespace(edge_flag=0), is_pmd=True))

    def test_imported_custom_outline_state_defaults_off_without_losing_pmx_edge_data(self):
        material = SimpleNamespace(
            material_index=0,
            name="outline",
            name_english="outline",
            diffuse=(1.0, 1.0, 1.0, 1.0),
            ambient=(0.0, 0.0, 0.0),
            specular=(0.0, 0.0, 0.0),
            specular_coefficient=0.0,
            toon_texture_index=-1,
            draw_flag=0x11,
            edge_color=(0.0, 0.0, 0.0, 1.0),
            edge_size=2.5,
            sphere_mode=0,
            sphere_texture_index=-1,
            texture_index=-1,
            memo="",
            shared_toon_flag=1,
        )
        converter = MeshConverter.__new__(MeshConverter)

        with patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ) as set_custom_attributes:
            converter._apply_custom_attributes("shader1", material, [], is_pmd=False)

        attrs = set_custom_attributes.call_args[0][1]
        self.assertEqual(attrs["mmd_edge_size"], 2.5)
        self.assertEqual(attrs["mmd_draw_flags"], 0x11)
        self.assertFalse(attrs["mmd_shader_outline_enabled"])

    def test_imported_setup_disables_hardware_outline_by_default(self):
        material = SimpleNamespace(
            diffuse=(1.0, 1.0, 1.0, 1.0),
            ambient=(0.0, 0.0, 0.0),
            specular=(0.0, 0.0, 0.0),
            specular_coefficient=0.0,
            draw_flag=0x11,
            edge_color=(0.0, 0.0, 0.0, 1.0),
            edge_size=2.5,
            sphere_mode=0,
            sphere_texture_index=-1,
            texture_index=-1,
            toon_texture_index=-1,
            shared_toon_flag=1,
        )
        converter = MeshConverter.__new__(MeshConverter)
        converter._transparency_modes = {}
        converter.texture_dir = ""

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter._ensure_dx11_uniform_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter._set_dx11_color_uniform"
        ), patch.object(converter, "_connect_dx11_main_texture"), patch.object(
            converter, "_apply_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as set_attribute:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.listConnections.return_value = []
            converter._setup_dx11_shader(
                "shader1",
                material,
                texture_path=None,
                all_textures=[],
                is_pmd=False,
            )

        set_attribute.assert_any_call("shader1", "EdgeSize", 0.0, "float")

    def test_imported_dx11_material_starts_opaque_even_when_classified_blend(self):
        """Automatic import must not mix a subset of materials into VP2 transparency."""
        material = SimpleNamespace(
            diffuse=(1.0, 1.0, 1.0, 0.5),
            ambient=(0.0, 0.0, 0.0),
            specular=(0.0, 0.0, 0.0),
            specular_coefficient=0.0,
            draw_flag=0x01,
            edge_color=(0.0, 0.0, 0.0, 1.0),
            edge_size=0.0,
            sphere_mode=0,
            sphere_texture_index=-1,
            texture_index=-1,
            toon_texture_index=-1,
            shared_toon_flag=1,
        )
        converter = MeshConverter.__new__(MeshConverter)
        converter._transparency_modes = {0: TRANSPARENCY_MODE_BLEND}
        converter.texture_dir = ""

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter._ensure_dx11_uniform_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter._set_dx11_color_uniform"
        ), patch.object(converter, "_connect_dx11_main_texture"), patch.object(
            converter, "_apply_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ):
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.listConnections.return_value = []
            converter._setup_dx11_shader(
                "shader1",
                material,
                texture_path=None,
                all_textures=[],
                is_pmd=False,
                material_index=0,
            )

        mock_cmds.setAttr.assert_any_call("shader1.technique", "MMDTechniqueDoubleSided", type="string")
        mock_cmds.setAttr.assert_any_call("shader1.mmdTransparencyMode", TRANSPARENCY_MODE_OPAQUE, type="string")


class TestSetMeshDoubleSided(unittest.TestCase):
    def test_enabled_sets_shape_double_sided_to_one(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute:
            mock_cmds.listRelatives.return_value = ["meshShape1", "meshShape2"]

            _set_mesh_double_sided("meshTransform", True)

        mock_cmds.listRelatives.assert_called_once_with(
            "meshTransform", shapes=True, type="mesh", fullPath=True
        )
        self.assertEqual(
            mock_set_attribute.call_args_list,
            [
                call("meshShape1", "doubleSided", 1, "bool"),
                call("meshShape2", "doubleSided", 1, "bool"),
            ],
        )

    def test_disabled_sets_shape_double_sided_to_zero(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute:
            mock_cmds.listRelatives.return_value = ["meshShape1"]

            _set_mesh_double_sided("meshTransform", False)

        mock_cmds.listRelatives.assert_called_once_with(
            "meshTransform", shapes=True, type="mesh", fullPath=True
        )
        mock_set_attribute.assert_called_once_with("meshShape1", "doubleSided", 0, "bool")


class TestDx11TechniqueSelection(unittest.TestCase):
    def test_technique_matrix_uses_explicit_names(self):
        cases = [
            (TRANSPARENCY_MODE_OPAQUE, True, False, "MMDTechnique"),
            (TRANSPARENCY_MODE_CUTOUT, True, False, "MMDTechnique"),
            (TRANSPARENCY_MODE_BLEND, True, False, "MMDTechniqueTranslucent"),
            (TRANSPARENCY_MODE_OPAQUE, False, False, "MMDTechnique"),
            (TRANSPARENCY_MODE_CUTOUT, False, False, "MMDTechnique"),
            (TRANSPARENCY_MODE_BLEND, False, False, "MMDTechniqueTranslucent"),
            (TRANSPARENCY_MODE_OPAQUE, True, True, "MMDTechniqueDoubleSided"),
            (TRANSPARENCY_MODE_CUTOUT, True, True, "MMDTechniqueDoubleSided"),
            (TRANSPARENCY_MODE_BLEND, True, True, "MMDTechniqueTranslucentDoubleSided"),
            (TRANSPARENCY_MODE_OPAQUE, False, True, "MMDTechniqueDoubleSided"),
            (TRANSPARENCY_MODE_CUTOUT, False, True, "MMDTechniqueDoubleSided"),
            (TRANSPARENCY_MODE_BLEND, False, True, "MMDTechniqueTranslucentDoubleSided"),
        ]

        for mode, edge_enabled, double_sided, expected in cases:
            with self.subTest(mode=mode, edge_enabled=edge_enabled, double_sided=double_sided):
                self.assertEqual(_technique_for_transparency(mode, edge_enabled, double_sided), expected)
                expected_mode = TRANSPARENCY_MODE_BLEND if mode == TRANSPARENCY_MODE_BLEND else TRANSPARENCY_MODE_OPAQUE
                self.assertEqual(_dx11_rendering_from_technique(expected), (expected_mode, True, double_sided))

    def test_get_transparency_mode_accepts_double_sided_suffix(self):
        cases = [
            ("MMDTechniqueDoubleSided", TRANSPARENCY_MODE_OPAQUE),
            ("MMDTechniqueTransparentDoubleSided", TRANSPARENCY_MODE_CUTOUT),
            ("MMDTechniqueTranslucentDoubleSided", TRANSPARENCY_MODE_BLEND),
            ("MMDTechniqueNoEdgeTransparentDoubleSided", TRANSPARENCY_MODE_CUTOUT),
            ("MMDTechniqueNoEdgeTranslucentDoubleSided", TRANSPARENCY_MODE_BLEND),
        ]

        for technique, expected in cases:
            with self.subTest(technique=technique), patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds:
                mock_cmds.attributeQuery.side_effect = lambda *args, **kwargs: args[0] == "technique"
                mock_cmds.getAttr.return_value = technique

                self.assertEqual(get_transparency_mode("shader1"), expected)

    def test_apply_transparency_mode_preserves_double_sided_technique_state(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ) as mock_set_custom_attributes:
            mock_cmds.attributeQuery.side_effect = lambda *args, **kwargs: args[0] == "technique"
            mock_cmds.getAttr.return_value = "MMDTechniqueNoEdgeDoubleSided"

            technique = apply_transparency_mode("shader1", TRANSPARENCY_MODE_CUTOUT)

        self.assertEqual(technique, "MMDTechniqueDoubleSided")
        mock_cmds.setAttr.assert_any_call("shader1.technique", technique, type="string")
        mock_set_custom_attributes.assert_called_once_with("shader1", {"mmdDoubleSided": True})

    def test_apply_shader_outline_prefers_draw_flags_for_double_sided_state(self):
        def attribute_exists(*args, **kwargs):
            attr = args[0]
            return attr in {"technique", "mmd_draw_flags", "mmd_shader_outline_enabled"}

        def get_attr(plug):
            if plug.endswith(".technique"):
                return "MMDTechniqueNoEdge"
            if plug.endswith(".mmd_draw_flags"):
                return 0x01
            raise AssertionError(f"Unexpected getAttr plug: {plug}")

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ) as mock_set_custom_attributes, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ):
            mock_cmds.attributeQuery.side_effect = attribute_exists
            mock_cmds.getAttr.side_effect = get_attr

            technique = apply_shader_outline("shader1", True)

        self.assertEqual(technique, "MMDTechniqueDoubleSided")
        mock_cmds.setAttr.assert_any_call("shader1.technique", technique, type="string")
        mock_set_custom_attributes.assert_called_once_with("shader1", {"mmdDoubleSided": True})

    def test_disabling_outline_keeps_technique_and_suppresses_edge_size(self):
        def attribute_exists(*args, **kwargs):
            return args[0] in {"technique", "EdgeSize", "mmd_shader_outline_enabled"}

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ):
            mock_cmds.attributeQuery.side_effect = attribute_exists
            mock_cmds.getAttr.return_value = "MMDTechnique"

            technique = apply_shader_outline("shader1", False, edge_size=1.5)

        self.assertEqual(technique, "MMDTechnique")
        mock_cmds.setAttr.assert_any_call("shader1.EdgeSize", 0.0)
        mock_set_attribute.assert_any_call("shader1", "mmd_shader_outline_enabled", False, "bool")

    def test_disabling_outline_without_size_still_suppresses_edge(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ):
            mock_cmds.attributeQuery.side_effect = lambda *args, **kwargs: args[0] in {
                "technique",
                "EdgeSize",
                "mmd_shader_outline_enabled",
            }
            mock_cmds.getAttr.return_value = "MMDTechnique"

            apply_shader_outline("shader1", False)

        mock_cmds.setAttr.assert_any_call("shader1.EdgeSize", 0.0)

    def test_legacy_no_edge_technique_remains_disabled_without_marker(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds:
            mock_cmds.attributeQuery.side_effect = lambda *args, **kwargs: args[0] == "technique"
            mock_cmds.getAttr.return_value = "MMDTechniqueNoEdge"

            self.assertFalse(get_shader_outline_enabled("shader1"))


if __name__ == "__main__":
    unittest.main()
