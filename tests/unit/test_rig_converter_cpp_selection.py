"""Tests for RigConverter unified node type names.

After the typeName unification (C++ and Python both register as mmdAppend /
mmdCcdIk), the converter always returns the unified names regardless of
which plugin is loaded.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.converters.rig_converter import RigConverter  # noqa: E402


class TestRigConverterUnifiedNodeTypes(unittest.TestCase):
    def test_append_node_type_always_returns_unified_name(self):
        converter = RigConverter()
        self.assertEqual(converter._append_node_type(), "mmdAppend")

    def test_ccd_ik_node_type_always_returns_unified_name(self):
        converter = RigConverter()
        self.assertEqual(converter._ccd_ik_node_type(), "mmdCcdIk")

    def test_setup_pmx_rig_records_native_unavailable_fallback_warning(self):
        converter = RigConverter()
        pmx_data = SimpleNamespace(bones=[])

        with patch("mmd_tools.converters.rig_converter.is_rig_primitive_available", return_value=False):
            result = converter.setup_pmx_rig(
                pmx_data,
                maya_joints=[],
                bone_map={},
                skeleton_group="skeleton",
                pmx_filepath="model.pmx",
            )

        self.assertIsNone(result["native_rig"])
        self.assertEqual(result["warnings"][0]["source"], "rig_converter")
        self.assertEqual(result["warnings"][0]["code"], "native_rig_unavailable")
        self.assertEqual(result["warnings"][0]["severity"], "warning")
        self.assertEqual(result["warnings"][0]["fallback"], "python_constraints")


if __name__ == "__main__":
    unittest.main()
