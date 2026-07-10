"""plugin_main の MMD Tools ウィンドウ所有権管理を検証するテスト。"""

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()


class _FakeMainWindow:
    WINDOW_NAME = "MMDToolsMainWindow"
    WORKSPACE_CONTROL_NAME = "MMDToolsWorkspaceControl"

    instances = []

    def __init__(self):
        self.closed = False
        self.deleted = False
        self.parent = "maya"
        self.show_calls = []
        self.instances.append(self)

    def show_window(self, dockable=False):
        self.show_calls.append(dockable)

    def close(self):
        self.closed = True

    def setParent(self, parent):
        self.parent = parent

    def deleteLater(self):
        self.deleted = True


class _FakeQApplication:
    _instance = None

    def __init__(self):
        self.processed = False

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def allWidgets(self):
        return []

    def processEvents(self):
        self.processed = True


class TestPluginMainWindowLifecycle(unittest.TestCase):
    def setUp(self):
        _FakeMainWindow.instances = []
        _FakeQApplication._instance = None
        sys.modules.pop("mmd_tools.plugin_main", None)

        main_window_mod = types.ModuleType("mmd_tools.ui.main_window")
        main_window_mod.MainWindow = _FakeMainWindow
        sys.modules["mmd_tools.ui.main_window"] = main_window_mod

        qt_compat_mod = types.ModuleType("mmd_tools.ui.qt_compat")
        qt_compat_mod.QApplication = _FakeQApplication
        sys.modules["mmd_tools.ui.qt_compat"] = qt_compat_mod

        shader_mod = types.ModuleType("mmd_tools.view.shader_override")
        shader_mod.initializePlugin = MagicMock()
        shader_mod.uninitializePlugin = MagicMock()
        sys.modules["mmd_tools.view.shader_override"] = shader_mod

        drag_drop_mod = types.ModuleType("mmd_tools.ui.drag_drop_importer")
        drag_drop_mod.install_drag_drop_importer = MagicMock()
        drag_drop_mod.uninstall_drag_drop_importer = MagicMock()
        sys.modules["mmd_tools.ui.drag_drop_importer"] = drag_drop_mod

        for name in (
            "mmd_tools.nodes.mmd_append_node",
            "mmd_tools.nodes.mmd_bone_morph_accum_node",
            "mmd_tools.nodes.mmd_ccd_ik_node",
            "mmd_tools.nodes.mmd_material_morph_eval_node",
        ):
            mod = types.ModuleType(name)
            mod.register = MagicMock()
            mod.deregister = MagicMock()
            sys.modules[name] = mod

        locator_mod = types.ModuleType("mmd_tools.nodes.mmd_rigid_body_locator_node")
        locator_mod.register = MagicMock()
        locator_mod.deregister = MagicMock()
        locator_mod.MmdRigidBodyLocatorNode = types.SimpleNamespace(kTypeName="mmdRigidBodyLocator")
        sys.modules["mmd_tools.nodes.mmd_rigid_body_locator_node"] = locator_mod

        self.plugin_main = importlib.import_module("mmd_tools.plugin_main")
        self.plugin_main.mmd_shader = shader_mod
        self.plugin_main.cmds = MagicMock()
        self.plugin_main.cmds.window.return_value = False
        self.plugin_main.cmds.workspaceControl.return_value = False
        self.plugin_main.om.MFnPlugin = MagicMock(return_value=MagicMock())

    def test_open_main_window_deletes_previous_python_owned_window(self):
        self.plugin_main.open_main_window(dockable=False)
        first = _FakeMainWindow.instances[0]

        self.plugin_main.open_main_window(dockable=True)
        second = _FakeMainWindow.instances[1]

        self.assertTrue(first.closed)
        self.assertIsNone(first.parent)
        self.assertTrue(first.deleted)
        self.assertEqual(second.show_calls, [True])
        self.assertIs(self.plugin_main._main_window, second)

    def test_uninitialize_closes_python_owned_window(self):
        self.plugin_main.open_main_window(dockable=False)
        window = _FakeMainWindow.instances[0]

        self.plugin_main.uninitializePlugin(MagicMock())

        self.assertTrue(window.closed)
        self.assertTrue(window.deleted)
        self.assertIsNone(self.plugin_main._main_window)

    def test_initialize_skips_locator_registration_when_already_registered(self):
        self.plugin_main.install_mmd_menu = MagicMock()
        self.plugin_main.install_drag_drop_importer = MagicMock()
        self.plugin_main.cmds.allNodeTypes.return_value = ["mmdRigidBodyLocator"]
        self.plugin_main.cmds.pluginInfo.return_value = []

        self.plugin_main.initializePlugin(MagicMock())

        locator_mod = sys.modules["mmd_tools.nodes.mmd_rigid_body_locator_node"]
        locator_mod.register.assert_not_called()

    def test_initialize_calls_soft_bone_morph_postcondition(self):
        """initializePlugin invokes soft postcondition after bone morph register."""
        self.plugin_main.install_mmd_menu = MagicMock()
        self.plugin_main.install_drag_drop_importer = MagicMock()
        # Keep locator registration skipped so mayapy cannot hit draw registry.
        self.plugin_main.cmds.allNodeTypes.return_value = ["mmdRigidBodyLocator"]
        self.plugin_main.cmds.pluginInfo.return_value = []
        soft_check = MagicMock()
        self.plugin_main._soft_check_bone_morph_accum_availability = soft_check

        self.plugin_main.initializePlugin(MagicMock())

        soft_check.assert_called_once_with()

    def test_soft_bone_morph_postcondition_warns_without_raising(self):
        """Unavailable probe emits a warning and never aborts plugin load."""
        display_warning = MagicMock()
        fake_om = MagicMock()
        fake_om.MGlobal.displayWarning = display_warning
        self.plugin_main.om = fake_om

        unavailable = {
            "available": False,
            "code": "node_type_unavailable",
            "reason": "node_type_unavailable",
            "detail": "create_failed: simulated",
        }
        postcondition = MagicMock(return_value=unavailable)
        fake_runtime = types.ModuleType("mmd_tools.converters.bone_morph_runtime")
        fake_runtime.log_bone_morph_accum_availability_postcondition = postcondition
        sys.modules["mmd_tools.converters.bone_morph_runtime"] = fake_runtime

        # Ensure parent package exists without executing converters package import.
        converters_pkg = types.ModuleType("mmd_tools.converters")
        converters_pkg.__path__ = []
        sys.modules.setdefault("mmd_tools.converters", converters_pkg)

        self.plugin_main._soft_check_bone_morph_accum_availability()

        postcondition.assert_called_once_with()
        display_warning.assert_called_once()
        warning_text = display_warning.call_args[0][0]
        self.assertIn("mmdBoneMorphAccum", warning_text)
        self.assertIn("create_failed: simulated", warning_text)

    def test_soft_bone_morph_postcondition_swallows_probe_errors(self):
        """Probe import/runtime errors must not fail plugin initialization."""
        display_warning = MagicMock()
        fake_om = MagicMock()
        fake_om.MGlobal.displayWarning = display_warning
        self.plugin_main.om = fake_om

        fake_runtime = types.ModuleType("mmd_tools.converters.bone_morph_runtime")

        def _boom():
            raise RuntimeError("probe exploded")

        fake_runtime.log_bone_morph_accum_availability_postcondition = _boom
        sys.modules["mmd_tools.converters.bone_morph_runtime"] = fake_runtime
        converters_pkg = types.ModuleType("mmd_tools.converters")
        converters_pkg.__path__ = []
        sys.modules.setdefault("mmd_tools.converters", converters_pkg)

        # Must not raise.
        self.plugin_main._soft_check_bone_morph_accum_availability()
        display_warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
