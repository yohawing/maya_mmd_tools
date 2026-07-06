"""Tests for RigConverter unified node type names.

After the typeName unification (C++ and Python both register as mmdAppend /
mmdCcdIk), the converter always returns the unified names regardless of
which plugin is loaded.
"""

import unittest

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


if __name__ == "__main__":
    unittest.main()
