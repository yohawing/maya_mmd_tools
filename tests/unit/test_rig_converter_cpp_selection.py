"""Tests for RigConverter C++ rig-node selection guards."""

import unittest
from unittest.mock import patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.converters import rig_converter  # noqa: E402
from mmd_tools.converters.rig_converter import RigConverter  # noqa: E402
from mmd_tools.core.settings import settings  # noqa: E402


class TestRigConverterCppNodeSelection(unittest.TestCase):
    def setUp(self):
        self._saved_use_cpp = settings.get("import.native.use_cpp_rig_nodes", False)

    def tearDown(self):
        settings.set("import.native.use_cpp_rig_nodes", self._saved_use_cpp)

    def _make_converter(
        self,
        *,
        use_cpp_setting,
        loaded_plugins=None,
        available_types=(),
    ):
        settings.set("import.native.use_cpp_rig_nodes", use_cpp_setting)
        loaded_plugins = [] if loaded_plugins is None else list(loaded_plugins)
        available_types = set(available_types)

        def _fake_node_type_available(node_type):
            return node_type in available_types

        with patch.object(rig_converter.cmds, "pluginInfo", return_value=loaded_plugins):
            with patch.object(
                rig_converter,
                "_node_type_available",
                side_effect=_fake_node_type_available,
            ):
                return RigConverter()

    def test_cpp_setting_disabled_uses_python_node_types_even_when_plugin_available(self):
        converter = self._make_converter(
            use_cpp_setting=False,
            loaded_plugins=["mmd_tools_cpp"],
            available_types=("mmdAppendNode", "mmdCcdIkNode"),
        )

        self.assertEqual(converter._append_node_type(), "mmdAppend")
        self.assertEqual(converter._ccd_ik_node_type(), "mmdCcdIk")

    def test_cpp_setting_enabled_falls_back_when_plugin_is_not_loaded(self):
        converter = self._make_converter(
            use_cpp_setting=True,
            loaded_plugins=["plugin_main"],
            available_types=("mmdAppendNode", "mmdCcdIkNode"),
        )

        self.assertEqual(converter._append_node_type(), "mmdAppend")
        self.assertEqual(converter._ccd_ik_node_type(), "mmdCcdIk")

    def test_cpp_setting_enabled_falls_back_when_any_cpp_node_type_is_missing(self):
        converter = self._make_converter(
            use_cpp_setting=True,
            loaded_plugins=["mmd_tools_cpp"],
            available_types=("mmdAppendNode",),
        )

        self.assertEqual(converter._append_node_type(), "mmdAppend")
        self.assertEqual(converter._ccd_ik_node_type(), "mmdCcdIk")

    def test_cpp_setting_enabled_uses_cpp_node_types_when_plugin_and_nodes_are_available(self):
        converter = self._make_converter(
            use_cpp_setting=True,
            loaded_plugins=["mmd_tools_cpp"],
            available_types=("mmdAppendNode", "mmdCcdIkNode"),
        )

        self.assertEqual(converter._append_node_type(), "mmdAppendNode")
        self.assertEqual(converter._ccd_ik_node_type(), "mmdCcdIkNode")


if __name__ == "__main__":
    unittest.main()
