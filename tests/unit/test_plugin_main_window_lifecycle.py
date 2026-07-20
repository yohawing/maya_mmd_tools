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
        self._saved_modules = {}
        self._saved_parent_attrs = {}
        self._injected_module_names = []
        _FakeMainWindow.instances = []
        _FakeQApplication._instance = None
        plugin_parent = importlib.import_module("mmd_tools")
        self._plugin_main_parent_state = (
            plugin_parent,
            hasattr(plugin_parent, "plugin_main"),
            getattr(plugin_parent, "plugin_main", None),
        )
        self._plugin_main_module_original = sys.modules.pop("mmd_tools.plugin_main", None)

        main_window_mod = types.ModuleType("mmd_tools.ui.main_window")
        main_window_mod.MainWindow = _FakeMainWindow
        self._inject_module("mmd_tools.ui.main_window", main_window_mod)

        qt_compat_mod = types.ModuleType("mmd_tools.ui.qt_compat")
        qt_compat_mod.QApplication = _FakeQApplication
        self._inject_module("mmd_tools.ui.qt_compat", qt_compat_mod)

        shader_mod = types.ModuleType("mmd_tools.view.shader_override")
        shader_mod.initializePlugin = MagicMock()
        shader_mod.uninitializePlugin = MagicMock()
        self._inject_module("mmd_tools.view.shader_override", shader_mod)

        drag_drop_mod = types.ModuleType("mmd_tools.ui.drag_drop_importer")
        drag_drop_mod.install_drag_drop_importer = MagicMock()
        drag_drop_mod.uninstall_drag_drop_importer = MagicMock()
        self._inject_module("mmd_tools.ui.drag_drop_importer", drag_drop_mod)

        for name in (
            "mmd_tools.nodes.mmd_append_node",
            "mmd_tools.nodes.mmd_bone_morph_accum_node",
            "mmd_tools.nodes.mmd_ccd_ik_node",
            "mmd_tools.nodes.mmd_material_morph_eval_node",
            "mmd_tools.nodes.mmd_morph_controller_node",
            "mmd_tools.nodes.mmd_rigid_body_shape",
            "mmd_tools.nodes.mmd_physics_joint_shape",
            "mmd_tools.nodes.mmd_physics_solver_node",
            "mmd_tools.nodes.mmd_physics_bone_driver_node",
            "mmd_tools.nodes.mmd_physics_world_shape",
            "mmd_tools.nodes.mmd_rigid_body_draw_override",
        ):
            mod = types.ModuleType(name)
            mod.register = MagicMock()
            mod.deregister = MagicMock()
            self._inject_module(name, mod)

        self.plugin_main = importlib.import_module("mmd_tools.plugin_main")
        self.plugin_main.mmd_shader = shader_mod
        self.plugin_main.cmds = MagicMock()
        self.plugin_main.cmds.window.return_value = False
        self.plugin_main.cmds.workspaceControl.return_value = False
        self.plugin_main.om.MFnPlugin = MagicMock(return_value=MagicMock())

    def test_plugin_main_does_not_import_main_window_eagerly(self):
        self.assertFalse(hasattr(self.plugin_main, "MainWindow"))

    def test_open_main_window_soft_fails_when_qt_is_unavailable(self):
        self.plugin_main._load_main_window_class = MagicMock(
            side_effect=ImportError("No module named PySide2")
        )

        result = self.plugin_main.open_main_window()

        self.assertIsNone(result)
        self.plugin_main.cmds.window.assert_not_called()

    def _inject_module(self, name, module):
        if name not in self._saved_modules:
            self._saved_modules[name] = sys.modules.get(name)
            self._injected_module_names.append(name)
            parent_name, _, child_name = name.rpartition(".")
            try:
                parent = importlib.import_module(parent_name)
            except Exception:
                parent = sys.modules.get(parent_name)
            if parent is not None:
                self._saved_parent_attrs[name] = (
                    parent,
                    child_name,
                    hasattr(parent, child_name),
                    getattr(parent, child_name, None),
                )
        sys.modules[name] = module
        if parent is not None:
            setattr(parent, child_name, module)

    def tearDown(self):
        for name in reversed(self._injected_module_names):
            original = self._saved_modules[name]
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
            parent_state = self._saved_parent_attrs.get(name)
            if parent_state is not None:
                parent, child_name, existed, value = parent_state
                if existed:
                    setattr(parent, child_name, value)
                elif hasattr(parent, child_name):
                    delattr(parent, child_name)
        if self._plugin_main_module_original is None:
            sys.modules.pop("mmd_tools.plugin_main", None)
        else:
            sys.modules["mmd_tools.plugin_main"] = self._plugin_main_module_original
        parent, existed, value = self._plugin_main_parent_state
        if existed:
            parent.plugin_main = value
        elif hasattr(parent, "plugin_main"):
            delattr(parent, "plugin_main")

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

    def test_repair_texture_paths_uses_main_window_presenter(self):
        presenter = MagicMock()
        window = types.SimpleNamespace(import_export_presenter=presenter)
        self.plugin_main._main_window = window

        result = self.plugin_main.repair_current_model_texture_paths()

        presenter.fix_texture_paths.assert_called_once_with()
        self.assertIs(result, presenter.fix_texture_paths.return_value)

    def test_repair_texture_paths_first_opens_window_without_mutating(self):
        presenter = MagicMock()
        app_state = MagicMock()
        window = types.SimpleNamespace(import_export_presenter=presenter, app_state=app_state)
        self.plugin_main._main_window = None
        self.plugin_main.open_main_window = MagicMock(return_value=window)

        result = self.plugin_main.repair_current_model_texture_paths()

        self.assertIsNone(result)
        self.plugin_main.open_main_window.assert_called_once_with(dockable=False)
        presenter.fix_texture_paths.assert_not_called()
        app_state.emit_status.assert_called_once()

    def test_install_menu_adds_texture_repair_action(self):
        self.plugin_main.cmds.menu.side_effect = lambda *_args, **kwargs: (
            False if kwargs.get("exists") else [] if kwargs.get("query") else "MMD"
        )

        self.plugin_main.install_mmd_menu()

        repair_calls = [
            call
            for call in self.plugin_main.cmds.menuItem.call_args_list
            if call[1].get("label") == "Repair Texture Paths"
        ]
        self.assertEqual(len(repair_calls), 1)

    def test_install_menu_creates_tearoff_top_menu_and_installs_humanik_submenu(self):
        self.plugin_main.cmds.menu.side_effect = lambda *_args, **kwargs: (
            False if kwargs.get("exists") else [] if kwargs.get("query") else "MMD"
        )
        with unittest.mock.patch(
            "mmd_tools.ui.humanik_menu_actions.install_humanik_menu"
        ) as install_humanik:
            self.plugin_main.install_mmd_menu()

        self.assertIn(
            unittest.mock.call(
                "MMD",
                label="MMD Tools",
                parent="MayaWindow",
                tearOff=True,
            ),
            self.plugin_main.cmds.menu.call_args_list,
        )
        install_humanik.assert_called_once_with(
            parent="MMD",
            cmds_module=self.plugin_main.cmds,
            callback_dispatcher=self.plugin_main._dispatch_humanik_action,
        )

    def test_humanik_action_dispatch_is_lazy(self):
        with unittest.mock.patch(
            "mmd_tools.ui.humanik_menu_actions.dispatch_action", return_value="ok"
        ) as dispatch:
            self.assertEqual(self.plugin_main._dispatch_humanik_action("diagnostics"), "ok")

        dispatch.assert_called_once_with("diagnostics")

    def test_humanik_submenu_has_seven_direct_dispatch_callbacks_and_reinstalls(self):
        humanik = importlib.import_module("mmd_tools.ui.humanik_menu_actions")
        existing = False

        def menu_item(*args, **kwargs):
            nonlocal existing
            if kwargs.get("exists"):
                return existing
            if kwargs.get("subMenu"):
                existing = True
            return args[0] if args else "menuItem"

        self.plugin_main.cmds.menu.return_value = False
        self.plugin_main.cmds.menuItem.side_effect = menu_item
        dispatch = MagicMock()
        humanik.install_humanik_menu(
            parent="MMD",
            cmds_module=self.plugin_main.cmds,
            callback_dispatcher=dispatch,
        )
        callbacks = [
            call.kwargs["command"]
            for call in self.plugin_main.cmds.menuItem.call_args_list
            if call.kwargs.get("parent") == "MMDHumanIKMenu"
        ]
        self.assertEqual(len(callbacks), 7)
        for callback in callbacks:
            callback("menu-click")
        self.assertEqual(
            [call.args[0] for call in dispatch.call_args_list],
            [action for action, _label in humanik.ACTION_LABELS],
        )

        humanik.install_humanik_menu(
            parent="MMD",
            cmds_module=self.plugin_main.cmds,
            callback_dispatcher=dispatch,
        )
        self.plugin_main.cmds.deleteUI.assert_called_once_with(humanik.HUMANIK_MENU_NAME)

    def test_teardown_restores_parent_package_module_attribute(self):
        parent, child_name, existed, original = self._saved_parent_attrs["mmd_tools.ui.main_window"]
        setattr(parent, child_name, sys.modules["mmd_tools.ui.main_window"])

        self.tearDown()

        if existed:
            self.assertIs(getattr(parent, child_name), original)
        else:
            self.assertFalse(hasattr(parent, child_name))

    def test_teardown_restores_plugin_main_parent_attribute(self):
        parent, existed, original = self._plugin_main_parent_state
        parent.plugin_main = self.plugin_main

        self.tearDown()

        if existed:
            self.assertIs(parent.plugin_main, original)
        else:
            self.assertFalse(hasattr(parent, "plugin_main"))

    def test_uninitialize_closes_python_owned_window(self):
        self.plugin_main.open_main_window(dockable=False)
        window = _FakeMainWindow.instances[0]
        reset = MagicMock(return_value=True)
        self.plugin_main._reset_humanik_menu_session = reset

        self.plugin_main.uninitializePlugin(MagicMock())

        reset.assert_called_once_with()
        self.assertTrue(window.closed)
        self.assertTrue(window.deleted)
        self.assertIsNone(self.plugin_main._main_window)

    def test_uninitialize_aborts_before_close_when_humanik_restore_fails(self):
        self.plugin_main.open_main_window(dockable=False)
        window = _FakeMainWindow.instances[0]
        self.plugin_main._reset_humanik_menu_session = MagicMock(return_value=False)
        self.plugin_main._remove_after_open_callback = MagicMock()

        with self.assertRaisesRegex(RuntimeError, "restore failed"):
            self.plugin_main.uninitializePlugin(MagicMock())

        self.assertFalse(window.closed)
        self.assertFalse(window.deleted)
        self.plugin_main._remove_after_open_callback.assert_not_called()

    def test_initialize_calls_soft_bone_morph_postcondition(self):
        """initializePlugin invokes soft postcondition after bone morph register."""
        self.plugin_main.install_mmd_menu = MagicMock()
        self.plugin_main.install_drag_drop_importer = MagicMock()
        self.plugin_main.cmds.allNodeTypes.return_value = []
        self.plugin_main.cmds.pluginInfo.return_value = []
        soft_check = MagicMock()
        self.plugin_main._soft_check_bone_morph_accum_availability = soft_check

        self.plugin_main.initializePlugin(MagicMock())

        soft_check.assert_called_once_with()

    def test_initialize_while_reading_registers_after_open_without_immediate_migration(self):
        self.plugin_main.install_mmd_menu = MagicMock()
        self.plugin_main.install_drag_drop_importer = MagicMock()
        self.plugin_main.cmds.allNodeTypes.return_value = []
        self.plugin_main.cmds.pluginInfo.return_value = []
        migration = MagicMock()
        self.plugin_main._soft_sync_existing_glsl_diffuse_contracts = migration
        register_callback = MagicMock()
        self.plugin_main._register_after_open_callback = register_callback
        self.plugin_main._scene_file_is_being_read = MagicMock(return_value=True)

        self.plugin_main.initializePlugin(MagicMock())

        migration.assert_not_called()
        register_callback.assert_called_once_with()
        self.plugin_main._after_scene_open("loaded_scene.ma")
        migration.assert_called_once_with()

    def test_initialize_manual_load_migrates_complete_scene_and_registers_callback(self):
        self.plugin_main.install_mmd_menu = MagicMock()
        self.plugin_main.install_drag_drop_importer = MagicMock()
        self.plugin_main.cmds.allNodeTypes.return_value = []
        self.plugin_main.cmds.pluginInfo.return_value = []
        migration = MagicMock()
        self.plugin_main._soft_sync_existing_glsl_diffuse_contracts = migration
        register_callback = MagicMock()
        self.plugin_main._register_after_open_callback = register_callback
        self.plugin_main._scene_file_is_being_read = MagicMock(return_value=False)

        self.plugin_main.initializePlugin(MagicMock())

        migration.assert_called_once_with()
        register_callback.assert_called_once_with()

    def test_scene_read_state_query_failure_is_conservative(self):
        self.plugin_main.om = types.SimpleNamespace(
            MFileIO=types.SimpleNamespace(isReadingFile=MagicMock(side_effect=RuntimeError("unavailable")))
        )

        self.assertTrue(self.plugin_main._scene_file_is_being_read())

    def test_existing_scene_glsl_migration_noop_restores_scene_and_undo(self):
        fake_mesh = types.ModuleType("mmd_tools.converters.mesh_converter")
        fake_mesh.migrate_legacy_glsl_diffuse_contracts = MagicMock(return_value=0)
        self._inject_module("mmd_tools.converters.mesh_converter", fake_mesh)
        self.plugin_main.cmds.undoInfo.side_effect = lambda **kwargs: True if kwargs.get("query") else None

        self.plugin_main._soft_sync_existing_glsl_diffuse_contracts()

        fake_mesh.migrate_legacy_glsl_diffuse_contracts.assert_called_once_with()
        self.plugin_main.cmds.file.assert_not_called()
        self.assertIn(
            unittest.mock.call(stateWithoutFlush=False),
            self.plugin_main.cmds.undoInfo.call_args_list,
        )
        self.assertIn(
            unittest.mock.call(stateWithoutFlush=True),
            self.plugin_main.cmds.undoInfo.call_args_list,
        )

    def test_after_open_callback_registers_once_invokes_and_removes(self):
        callback_id = 42
        add_callback = MagicMock(return_value=callback_id)
        remove_callback = MagicMock()
        self.plugin_main.om = types.SimpleNamespace(
            MSceneMessage=types.SimpleNamespace(kAfterOpen=7, addCallback=add_callback),
            MMessage=types.SimpleNamespace(removeCallback=remove_callback),
        )
        migrate = MagicMock()
        self.plugin_main._soft_sync_existing_glsl_diffuse_contracts = migrate

        self.plugin_main._register_after_open_callback()
        self.plugin_main._register_after_open_callback()
        registered_callback = add_callback.call_args.args[1]
        registered_callback("scene.ma")
        self.plugin_main._remove_after_open_callback()

        add_callback.assert_called_once_with(7, self.plugin_main._after_scene_open)
        migrate.assert_called_once_with()
        remove_callback.assert_called_once_with(callback_id)
        self.assertIsNone(self.plugin_main._after_open_callback_id)

    def test_after_open_callback_registration_failure_is_soft(self):
        add_callback = MagicMock(side_effect=RuntimeError("callback unavailable"))
        self.plugin_main.om = types.SimpleNamespace(
            MSceneMessage=types.SimpleNamespace(kAfterOpen=7, addCallback=add_callback),
            MMessage=types.SimpleNamespace(removeCallback=MagicMock()),
        )

        self.plugin_main._register_after_open_callback()

        self.assertIsNone(self.plugin_main._after_open_callback_id)

    def test_after_open_callback_migration_failure_is_soft(self):
        self.plugin_main._soft_sync_existing_glsl_diffuse_contracts = MagicMock(
            side_effect=RuntimeError("migration failed")
        )

        self.plugin_main._after_scene_open("scene.ma")

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
        self._inject_module("mmd_tools.converters.bone_morph_runtime", fake_runtime)

        # Ensure parent package exists without executing converters package import.
        converters_pkg = types.ModuleType("mmd_tools.converters")
        converters_pkg.__path__ = []
        if "mmd_tools.converters" not in sys.modules:
            self._inject_module("mmd_tools.converters", converters_pkg)

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
        self._inject_module("mmd_tools.converters.bone_morph_runtime", fake_runtime)
        converters_pkg = types.ModuleType("mmd_tools.converters")
        converters_pkg.__path__ = []
        if "mmd_tools.converters" not in sys.modules:
            self._inject_module("mmd_tools.converters", converters_pkg)

        # Must not raise.
        self.plugin_main._soft_check_bone_morph_accum_availability()
        display_warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
