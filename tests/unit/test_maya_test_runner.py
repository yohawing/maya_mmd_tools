"""Verify the mayapy runner restores only the plugin instance it loaded."""

from unittest import TestCase
from unittest.mock import MagicMock

from tests import maya_test_runner


class TestGlobalTestPluginLifecycle(TestCase):
    """Test runner-owned plugin setup and cleanup."""

    def setUp(self):
        self.cmds = MagicMock()
        self.original_cmds = maya_test_runner.cmds
        maya_test_runner.cmds = self.cmds

    def tearDown(self):
        maya_test_runner.cmds = self.original_cmds

    def test_load_returns_name_when_runner_loads_plugin(self):
        self.cmds.pluginInfo.return_value = False
        self.cmds.loadPlugin.return_value = ["plugin_main"]

        plugin_name = maya_test_runner._load_global_test_plugin()

        self.assertEqual(plugin_name, "plugin_main")
        self.cmds.loadPlugin.assert_called_once_with(
            str(maya_test_runner.ROOT_DIR / "mmd_tools" / "plugin_main.py"), quiet=True
        )

    def test_load_does_not_claim_preloaded_plugin(self):
        self.cmds.pluginInfo.return_value = True

        self.assertIsNone(maya_test_runner._load_global_test_plugin())
        self.cmds.loadPlugin.assert_not_called()

    def test_cleanup_resets_scene_and_unloads_runner_owned_plugin(self):
        maya_test_runner._unload_global_test_plugin("plugin_main")

        self.cmds.file.assert_called_once_with(new=True, force=True)
        self.cmds.unloadPlugin.assert_called_once_with("plugin_main", force=True)

    def test_cleanup_preserves_preloaded_plugin(self):
        maya_test_runner._unload_global_test_plugin(None)

        self.cmds.file.assert_not_called()
        self.cmds.unloadPlugin.assert_not_called()
