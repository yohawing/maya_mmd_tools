from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock

from tests.common.maya_plugin_setup import (
    REQUIRED_MMD_NODE_TYPES,
    load_mmd_tools_plugin,
)


class TestMayaPluginSetup(TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.plugin = str((self.root / "mmd_tools" / "plugin_main.py").resolve())
        self.cmds = MagicMock()
        self.cmds.allNodeTypes.return_value = list(REQUIRED_MMD_NODE_TYPES)
        self.loaded_plugins = {}

        def plugin_info(name=None, *, query=False, listPlugins=False, loaded=False, path=False):
            if listPlugins:
                return list(self.loaded_plugins)
            if loaded:
                return name in self.loaded_plugins
            if path and name in self.loaded_plugins:
                return self.loaded_plugins[name]
            return None

        def load_plugin(_path, *, quiet=False):
            self.loaded_plugins["plugin_main"] = self.plugin
            return ["plugin_main"]

        self.cmds.pluginInfo.side_effect = plugin_info
        self.cmds.loadPlugin.side_effect = load_plugin

    def test_loads_canonical_plugin_and_accepts_complete_registration(self):
        actual = load_mmd_tools_plugin(self.root, cmds_module=self.cmds)

        self.assertEqual(actual, Path(self.plugin))
        self.cmds.loadPlugin.assert_called_once_with(self.plugin, quiet=True)

    def test_reuses_loaded_plugin_without_reloading(self):
        self.loaded_plugins["plugin_main"] = self.plugin

        load_mmd_tools_plugin(self.root, cmds_module=self.cmds)

        self.cmds.loadPlugin.assert_not_called()

    def test_foreign_plugin_path_does_not_satisfy_canonical_load(self):
        self.loaded_plugins["mmd_tools_plugin"] = str(
            self.root / "installed" / "mmd_tools_plugin.py"
        )

        load_mmd_tools_plugin(self.root, cmds_module=self.cmds)

        self.cmds.loadPlugin.assert_called_once_with(self.plugin, quiet=True)

    def test_fails_when_plugin_does_not_remain_loaded(self):
        self.cmds.loadPlugin.side_effect = lambda *_args, **_kwargs: []

        with self.assertRaisesRegex(RuntimeError, "did not remain loaded"):
            load_mmd_tools_plugin(self.root, cmds_module=self.cmds)

    def test_fails_with_all_missing_required_node_types(self):
        self.loaded_plugins["plugin_main"] = self.plugin
        self.cmds.allNodeTypes.return_value = ["mmdMorphController"]

        with self.assertRaisesRegex(RuntimeError, "mmdAppend.*mmdPhysicsWorldShape"):
            load_mmd_tools_plugin(self.root, cmds_module=self.cmds)
