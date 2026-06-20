"""Pure-Python checks for PMX material backface-culling helpers."""

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters.mesh_converter import (  # noqa: E402
    _material_is_double_sided,
    _set_mesh_double_sided,
)


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


class TestSetMeshDoubleSided(unittest.TestCase):
    def test_enabled_sets_shape_double_sided_to_one(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute:
            mock_cmds.listRelatives.return_value = ["meshShape1", "meshShape2"]

            _set_mesh_double_sided("meshTransform", True)

        mock_cmds.listRelatives.assert_called_once_with("meshTransform", shapes=True, type="mesh")
        self.assertEqual(
            mock_set_attribute.call_args_list,
            [
                call("meshShape1", "doubleSided", 1, "bool"),
                call("meshShape2", "doubleSided", 1, "bool"),
            ],
        )

    def test_disabled_sets_shape_double_sided_to_zero(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute:
            mock_cmds.listRelatives.return_value = ["meshShape1"]

            _set_mesh_double_sided("meshTransform", False)

        mock_cmds.listRelatives.assert_called_once_with("meshTransform", shapes=True, type="mesh")
        mock_set_attribute.assert_called_once_with("meshShape1", "doubleSided", 0, "bool")


if __name__ == "__main__":
    unittest.main()
