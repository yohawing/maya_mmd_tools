"""Maya startup hook native-plugin discovery and loading contracts."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


def _load_user_setup_module():
    """Load userSetup.py with the smallest possible Maya module stub."""
    fake_cmds = MagicMock(name="maya.cmds")
    fake_om = SimpleNamespace(MGlobal=MagicMock(name="MGlobal"))
    fake_maya = ModuleType("maya")
    fake_maya.cmds = fake_cmds
    fake_api = ModuleType("maya.api")
    fake_api.OpenMaya = fake_om

    module_name = "maya_mmd_tools_test_user_setup"
    module_path = Path(__file__).resolve().parents[2] / "userSetup.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "maya": fake_maya,
            "maya.cmds": fake_cmds,
            "maya.api": fake_api,
            "maya.api.OpenMaya": fake_om,
        },
    ):
        spec.loader.exec_module(module)
    return module, fake_cmds


class TestUserSetupCppAutoload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user_setup, cls.cmds = _load_user_setup_module()

    def setUp(self):
        self.cmds.about.return_value = "2026"

    def test_version_specific_environment_path_has_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            explicit = Path(directory) / "mmd_tools_cpp.mll"
            explicit.write_bytes(b"plugin")
            with patch.dict(
                os.environ,
                {
                    "MMD_TOOLS_CPP_PLUGIN_2026": str(explicit),
                    "MMD_TOOLS_CPP_PLUGIN": str(Path(directory) / "other.mll"),
                },
                clear=False,
            ):
                actual = self.user_setup._mmd_tools_cpp_plugin_path()

        self.assertEqual(actual, explicit)

    def test_debug_then_release_candidate_order_matches_fast_importer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "plug-ins" / "2026" / "Release" / "mmd_tools_cpp.mll"
            release.parent.mkdir(parents=True)
            release.write_bytes(b"plugin")
            with patch.dict(
                os.environ,
                {
                    "MMD_TOOLS_CPP_PLUGIN_2026": "",
                    "MMD_TOOLS_CPP_PLUGIN": "",
                    "MMD_TOOLS_CPP_CONFIG_2026": "Debug",
                    "MMD_TOOLS_CPP_CONFIG": "Debug",
                },
                clear=False,
            ):
                with patch.object(self.user_setup, "_mmd_tools_roots", return_value=[root]):
                    actual = self.user_setup._mmd_tools_cpp_plugin_path()

        self.assertEqual(actual, release)

    def test_autoload_prepares_directory_and_loads_selected_plugin(self):
        plugin = Path("F:/mmd_tools/plug-ins/2026/Debug/mmd_tools_cpp.mll")
        self.cmds.loadPlugin.reset_mock()
        with patch.dict(os.environ, {"MMD_TOOLS_CPP_AUTOLOAD": "1"}, clear=False):
            with patch.object(
                self.user_setup, "_mmd_tools_cpp_plugin_path", return_value=plugin
            ):
                with patch.object(
                    self.user_setup, "_mmd_tools_cpp_plugin_loaded", return_value=False
                ):
                    with patch.object(
                        self.user_setup, "_prepare_mmd_tools_cpp_plugin_directory"
                    ) as prepare:
                        self.user_setup._load_mmd_tools_cpp_plugin()

        prepare.assert_called_once_with(plugin)
        self.cmds.loadPlugin.assert_called_once_with(str(plugin), quiet=True)


if __name__ == "__main__":
    unittest.main()
