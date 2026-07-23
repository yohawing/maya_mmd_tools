import os
import traceback

from maya import cmds
import maya.api.OpenMaya as om
from mmd_tools import __version__
from mmd_tools.view import shader_override as mmd_shader
from mmd_tools.ui.drag_drop_importer import (
    install_drag_drop_importer,
    uninstall_drag_drop_importer,
)
from mmd_tools.nodes import mmd_append_node
from mmd_tools.nodes import mmd_bone_morph_accum_node
from mmd_tools.nodes import mmd_ccd_ik_node
from mmd_tools.nodes import mmd_material_morph_eval_node
from mmd_tools.nodes import mmd_morph_controller_node
from mmd_tools.nodes import mmd_rigid_body_shape
from mmd_tools.nodes import mmd_rigid_body_draw_override
from mmd_tools.nodes import mmd_physics_joint_shape
from mmd_tools.nodes import mmd_physics_solver_node
from mmd_tools.nodes import mmd_physics_bone_driver_node
from mmd_tools.nodes import mmd_physics_world_shape

_main_window = None
_animator_toolset_window = None
# Track whether Python actually registered rig nodes (mmdAppend / mmdCcdIk).
# Used at deregister time instead of re-checking _cpp_plugin_loaded(), which
# is fragile if the C++ plugin loads or unloads between init and uninit.
_python_rig_nodes_registered = False
_shader_override_registered = False
_physics_nodes_registered = False
_python_physics_solver_registered = False
_python_physics_driver_registered = False
_after_open_callback_id = None
_after_new_callback_id = None
_active_view_callback_id = None
_MAIN_WINDOW_NAME = "MMDToolsMainWindow"
_MAIN_WINDOW_WORKSPACE_CONTROL_NAME = "MMDToolsWorkspaceControl"


def _trace_initialize_step(step):
    """Write an opt-in plugin initialization trace for GUI gate diagnosis."""
    path = os.environ.get("MMD_TOOLS_INIT_TRACE_PATH")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"{step}\n")
    except Exception:
        pass


def _load_main_window_class():
    """Load the Qt UI only when a caller explicitly requests it."""
    from mmd_tools.ui.main_window import MainWindow

    return MainWindow


def maya_useNewAPI():
    """Tell Maya to use the Python API 2.0"""
    pass


def _delete_qt_widget(widget):
    """Close and schedule deletion for a Qt widget without letting cleanup fail plugin flow."""
    if widget is None:
        return
    try:
        widget.close()
    except Exception:
        pass
    try:
        widget.setParent(None)
    except Exception:
        pass
    try:
        widget.deleteLater()
    except Exception:
        pass


def close_main_window():
    """Close the Python-owned MMD Tools window and stale Qt instances."""
    global _main_window

    _delete_qt_widget(_main_window)
    _main_window = None

    try:
        from mmd_tools.ui.qt_compat import QApplication

        app = QApplication.instance()
        if app is not None:
            for widget in list(app.allWidgets()):
                try:
                    if widget.objectName() == _MAIN_WINDOW_NAME:
                        _delete_qt_widget(widget)
                except Exception:
                    pass
            app.processEvents()
    except Exception:
        pass


def open_main_window(dockable=False):
    """Open the main MMD Tools window."""
    global _main_window

    close_main_window()

    try:
        main_window_class = _load_main_window_class()
    except ImportError as exc:
        om.MGlobal.displayWarning(f"MMD Tools UI is unavailable: {exc}")
        return None

    # 既存のウィンドウを削除
    if cmds.window(_MAIN_WINDOW_NAME, exists=True):
        cmds.deleteUI(_MAIN_WINDOW_NAME)

    # workspaceControlがあれば削除
    workspace_control_name = getattr(
        main_window_class,
        "WORKSPACE_CONTROL_NAME",
        _MAIN_WINDOW_WORKSPACE_CONTROL_NAME,
    )
    if cmds.workspaceControl(workspace_control_name, exists=True):
        cmds.deleteUI(workspace_control_name, control=True)

    # 新しいインスタンスを作成して表示
    main_window = main_window_class()
    main_window.show_window(dockable=dockable)
    _main_window = main_window
    return main_window


def close_animator_toolset():
    """Close the standalone Animator Toolset window."""
    global _animator_toolset_window

    if _animator_toolset_window is not None:
        try:
            _animator_toolset_window.close_window()
        except Exception:
            _delete_qt_widget(_animator_toolset_window)
        _animator_toolset_window = None

    ws = "MMDAnimatorToolsetWorkspaceControl"
    try:
        if cmds.workspaceControl(ws, exists=True):
            cmds.deleteUI(ws)
    except Exception:
        pass


def open_animator_toolset(dockable=True):
    """Open the standalone Animator Toolset window."""
    global _animator_toolset_window

    try:
        close_animator_toolset()

        from mmd_tools.ui.animator_toolset_window import AnimatorToolsetWindow

        window = AnimatorToolsetWindow()
        window.show_window(dockable=dockable)
        _animator_toolset_window = window
        return window
    except Exception as exc:
        message = f"Animator Toolset failed to open: {exc}"
        om.MGlobal.displayError(message)
        om.MGlobal.displayError(traceback.format_exc())
        raise


def repair_current_model_texture_paths():
    """Repair texture paths for the model selected in the MMD Tools window."""
    window = _main_window
    if window is None:
        window = open_main_window(dockable=False)
        app_state = getattr(window, "app_state", None)
        if app_state is not None and hasattr(app_state, "emit_status"):
            from mmd_tools.ui.translations.translator import UITranslator

            app_state.emit_status(
                UITranslator.instance().translate("status_select_model", "texture_issues")
            )
        return None
    presenter = getattr(window, "import_export_presenter", None)
    if presenter is None:
        om.MGlobal.displayWarning("MMD Tools texture repair is unavailable")
        return None
    return presenter.fix_texture_paths()


def _dispatch_humanik_action(action_name):
    """Lazy-dispatch a HumanIK menu action to the UI module."""
    from mmd_tools.ui import humanik_menu_actions

    return humanik_menu_actions.dispatch_action(action_name)


def _reset_humanik_menu_session():
    """Restore HumanIK-owned scene state before plugin unload when possible."""
    try:
        from mmd_tools.ui import humanik_menu_actions

        return humanik_menu_actions.reset_humanik_session(restore=True)
    except Exception as exc:
        om.MGlobal.displayWarning(f"HumanIK session reset during unload failed: {exc}")
        return False


def _close_humanik_window():
    """Close the standalone HumanIK Editor window before plugin unload.

    Soft-fails like the rest of unload cleanup: an already-torn-down window
    (or an environment where the Qt UI never loaded) must never abort
    ``uninitializePlugin``. This runs after ``_reset_humanik_menu_session()``
    (see ``uninitializePlugin``), which already restored any HumanIK-owned
    scene state, so this is purely UI/window cleanup -- and drops the
    presenter's ``humanik_control_rig_watch`` callback subscription via the
    window's own ``hideEvent``/``closeEvent`` handling.
    """
    try:
        from mmd_tools.ui import humanik_window

        humanik_window.close_humanik_window()
    except Exception as exc:
        om.MGlobal.displayWarning(f"HumanIK window close during unload failed: {exc}")


def install_mmd_menu():
    """Install the MMD menu in Maya."""
    if not cmds.menu("MMD", exists=True):
        cmds.menu("MMD", label="MMD", parent="MayaWindow", tearOff=True)
    else:
        cmds.menu("MMD", edit=True, label="MMD")

    _LABELS = ("MMD Tools", "MMD Editor", "Repair Texture Paths", "Animator Toolset")
    for item in cmds.menu("MMD", query=True, itemArray=True) or []:
        if cmds.menuItem(item, query=True, label=True) in _LABELS:
            cmds.deleteUI(item)

    cmds.menuItem(
        "MMDToolsMenuItem",
        label="MMD Editor",
        command=lambda *args: open_main_window(dockable=False),
        parent="MMD",
    )
    cmds.menuItem(
        "MMDRepairTexturePathsMenuItem",
        label="Repair Texture Paths",
        command=lambda *args: repair_current_model_texture_paths(),
        parent="MMD",
    )

    from mmd_tools.services.settings_service import SettingsService

    if SettingsService().is_development_mode():
        cmds.menuItem(
            "MMDAnimatorToolsetMenuItem",
            label="Animator Toolset",
            command=lambda *args: open_animator_toolset(dockable=True),
            parent="MMD",
        )

    from mmd_tools.ui.humanik_menu_actions import install_humanik_menu

    install_humanik_menu(
        parent="MMD",
        cmds_module=cmds,
        callback_dispatcher=_dispatch_humanik_action,
    )


def uninstall_mmd_menu():
    """Uninstall the MMD menu from Maya."""
    if cmds.menu("MMD", exists=True):
        cmds.deleteUI("MMD", menu=True)


def _cpp_plugin_loaded() -> bool:
    """Return True if the C++ plugin (mmd_tools_cpp) is already loaded."""
    try:
        loaded = cmds.pluginInfo(query=True, listPlugins=True) or []
    except Exception:
        loaded = []
    return "mmd_tools_cpp" in loaded


def _node_type_registered(type_name: str) -> bool:
    """Return True if Maya already has the given node type registered."""
    try:
        return type_name in (cmds.allNodeTypes() or [])
    except Exception:
        return False


def _soft_check_bone_morph_accum_availability():
    """Soft plugin postcondition for mmdBoneMorphAccum contract.

    Creates a temporary probe via the runtime helper. Never raises: plugin load
    must succeed even when the probe fails, because import-time graph building
    re-probes and fails soft with structured warnings.
    """
    try:
        from mmd_tools.converters.bone_morph_runtime import (
            log_bone_morph_accum_availability_postcondition,
        )

        availability = log_bone_morph_accum_availability_postcondition()
        if not availability.get("available"):
            om.MGlobal.displayWarning(
                "mmdBoneMorphAccum is unavailable after registration; "
                "bone morph runtime graphs will be skipped ({0})".format(
                    availability.get("detail") or "node_type_unavailable"
                )
            )
    except Exception:
        # Runtime probing remains the enforcement boundary for fail-soft import.
        pass


def _soft_sync_existing_glsl_diffuse_contracts():
    """Migrate strict legacy GLSL contracts in the scene without polluting undo.

    The synchronizer is signature-gated and idempotent.  A real migration keeps
    the scene dirty so Maya can prompt the user to save it; an empty/no-op scan
    does not issue any scene edit.
    """
    undo_was_enabled = None
    try:
        undo_was_enabled = bool(cmds.undoInfo(query=True, state=True))
        if undo_was_enabled:
            cmds.undoInfo(stateWithoutFlush=False)
    except Exception:
        undo_was_enabled = None
    try:
        from mmd_tools.converters.mesh_converter import migrate_legacy_glsl_diffuse_contracts

        migrate_legacy_glsl_diffuse_contracts()
    except Exception:
        # Existing-scene compatibility must never make plugin loading fail.
        pass
    finally:
        if undo_was_enabled:
            try:
                cmds.undoInfo(stateWithoutFlush=True)
            except Exception:
                pass


def _after_scene_open(*_args):
    """Run strict existing-scene migration after Maya opens a scene."""
    _reset_humanik_session_after_scene_change()
    try:
        _soft_sync_existing_glsl_diffuse_contracts()
    except Exception:
        pass
    _soft_sync_dx11_device_pixel_ratio(force=True)


def _after_scene_new(*_args):
    """Drop process-owned HumanIK state after Maya creates a new scene."""
    _reset_humanik_session_after_scene_change()
    _soft_sync_dx11_device_pixel_ratio(force=True)


def _soft_sync_dx11_device_pixel_ratio(*_args, force=False):
    """Refresh screen-space shader scaling without making callbacks fatal."""
    try:
        from mmd_tools.core import maya_viewport_utils

        maya_viewport_utils.sync_dx11_shader_device_pixel_ratio(force=force)
    except Exception:
        pass


def _register_active_view_callback():
    """Track active-view changes that can cross monitor DPI boundaries."""
    global _active_view_callback_id
    if _active_view_callback_id is not None:
        return
    try:
        _active_view_callback_id = om.MEventMessage.addEventCallback(
            "ActiveViewChanged",
            _soft_sync_dx11_device_pixel_ratio,
        )
        _soft_sync_dx11_device_pixel_ratio(force=True)
    except Exception:
        _active_view_callback_id = None


def _remove_active_view_callback():
    """Remove the owned active-view callback if it exists."""
    global _active_view_callback_id
    callback_id = _active_view_callback_id
    _active_view_callback_id = None
    if callback_id is None:
        return
    try:
        om.MMessage.removeCallback(callback_id)
    except Exception:
        pass


def _reset_humanik_session_after_scene_change():
    """Replace stale frontend state and refresh an open HumanIK Editor."""
    try:
        from mmd_tools.ui import humanik_menu_actions

        # The old scene has already been replaced at kAfterOpen/kAfterNew;
        # attempting Restore here would act on the new scene with stale names.
        humanik_menu_actions.reset_humanik_session(restore=False)
    except Exception:
        pass
    try:
        from mmd_tools.ui import humanik_window

        humanik_window.refresh_humanik_window_for_scene_change()
    except Exception:
        pass


def _scene_file_is_being_read():
    """Return Maya's file-read state, conservatively treating query failure as reading."""
    try:
        return bool(om.MFileIO.isReadingFile())
    except Exception:
        return True


def _register_humanik_control_rig_watch():
    """Register the out-of-band HumanIK Control Rig detection/adoption watch.

    Best-effort: ``humanik_control_rig_watch`` itself swallows and logs
    ``OpenMaya`` failures (mayapy/batch hosts with no HumanIK UI simply never
    fire the callback), so this never raises during plugin initialization.
    """
    try:
        from mmd_tools.core import humanik_control_rig_watch

        humanik_control_rig_watch.register_humanik_control_rig_watch()
    except Exception:
        pass


def _deregister_humanik_control_rig_watch():
    """Deregister the HumanIK Control Rig watch callback, if registered."""
    try:
        from mmd_tools.core import humanik_control_rig_watch

        humanik_control_rig_watch.deregister_humanik_control_rig_watch()
    except Exception:
        pass


def _register_after_open_callback():
    """Register scene-open/new callbacks, tolerating host limitations."""
    global _after_open_callback_id, _after_new_callback_id
    if _after_open_callback_id is None:
        try:
            _after_open_callback_id = om.MSceneMessage.addCallback(
                om.MSceneMessage.kAfterOpen,
                _after_scene_open,
            )
        except Exception:
            _after_open_callback_id = None
    if _after_new_callback_id is None:
        try:
            _after_new_callback_id = om.MSceneMessage.addCallback(
                om.MSceneMessage.kAfterNew,
                _after_scene_new,
            )
        except Exception:
            _after_new_callback_id = None


def _remove_after_open_callback():
    """Remove the owned scene-open/new callbacks if they exist."""
    global _after_open_callback_id, _after_new_callback_id
    callback_ids = (_after_open_callback_id, _after_new_callback_id)
    _after_open_callback_id = None
    _after_new_callback_id = None
    for callback_id in callback_ids:
        if callback_id is None:
            continue
        try:
            om.MMessage.removeCallback(callback_id)
        except Exception:
            pass


def initializePlugin(mobject):
    """
    Plugin entry point.
    """
    vendor = "yohawing"
    version = __version__

    plugin_fn = om.MFnPlugin(mobject, vendor, version)

    try:
        _trace_initialize_step("initialize:start")
        install_mmd_menu()
        _trace_initialize_step("menu:done")
        try:
            install_drag_drop_importer()
        except ImportError as exc:
            om.MGlobal.displayWarning(f"MMD Tools drag-and-drop UI is unavailable: {exc}")
        _trace_initialize_step("drag-drop:done")
        global _shader_override_registered
        if os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE") != "1":
            mmd_shader.initializePlugin(mobject)
            _shader_override_registered = True
        _trace_initialize_step("shader-override:done")
        mmd_bone_morph_accum_node.register(plugin_fn)
        _trace_initialize_step("bone-morph-register:done")
        _soft_check_bone_morph_accum_availability()
        _trace_initialize_step("bone-morph-check:done")
        mmd_material_morph_eval_node.register(plugin_fn)
        _trace_initialize_step("material-morph:done")
        mmd_morph_controller_node.register(plugin_fn)
        _trace_initialize_step("morph-controller:done")
        if not _scene_file_is_being_read():
            _soft_sync_existing_glsl_diffuse_contracts()
        _trace_initialize_step("scene-sync:done")
        # Skip Python rig-node registration when C++ plugin already provides them
        global _python_rig_nodes_registered
        if not _cpp_plugin_loaded():
            mmd_append_node.register(plugin_fn)
            mmd_ccd_ik_node.register(plugin_fn)
            _python_rig_nodes_registered = True
        _trace_initialize_step("rig-nodes:done")
        global _physics_nodes_registered
        global _python_physics_solver_registered
        global _python_physics_driver_registered
        mmd_physics_world_shape.register(plugin_fn)
        _trace_initialize_step("physics-world:done")
        mmd_rigid_body_shape.register(plugin_fn)
        _trace_initialize_step("rigid-body-shape:done")
        mmd_rigid_body_draw_override.register()
        _trace_initialize_step("rigid-body-draw:done")
        mmd_physics_joint_shape.register(plugin_fn)
        _trace_initialize_step("physics-joint:done")
        if not _node_type_registered("mmdPhysicsSolver"):
            mmd_physics_solver_node.register(plugin_fn)
            _python_physics_solver_registered = True
        if not _node_type_registered("mmdPhysicsBoneDriver"):
            mmd_physics_bone_driver_node.register(plugin_fn)
            _python_physics_driver_registered = True
        _trace_initialize_step("physics-solver:done")
        _physics_nodes_registered = True
        _register_after_open_callback()
        _register_active_view_callback()
        _register_humanik_control_rig_watch()
        _trace_initialize_step("initialize:done")
    except Exception as e:
        _trace_initialize_step(f"initialize:error:{type(e).__name__}:{e}")
        om.MGlobal.displayError(f"Plugin initialization failed: {str(e)}")
        raise


def uninitializePlugin(mobject):
    """
    Plugin exit point.
    """
    plugin_fn = om.MFnPlugin(mobject)

    try:
        if not _reset_humanik_menu_session():
            raise RuntimeError("HumanIK session restore failed; plugin unload was aborted")
        _close_humanik_window()
        _deregister_humanik_control_rig_watch()
        _remove_active_view_callback()
        _remove_after_open_callback()
        close_animator_toolset()
        close_main_window()
        uninstall_mmd_menu()
        uninstall_drag_drop_importer()
        global _shader_override_registered
        if _shader_override_registered:
            try:
                mmd_shader.uninitializePlugin(mobject)
            finally:
                _shader_override_registered = False
        global _physics_nodes_registered
        global _python_physics_solver_registered
        global _python_physics_driver_registered
        if _physics_nodes_registered:
            if _python_physics_driver_registered:
                mmd_physics_bone_driver_node.deregister(plugin_fn)
                _python_physics_driver_registered = False
            if _python_physics_solver_registered:
                mmd_physics_solver_node.deregister(plugin_fn)
                _python_physics_solver_registered = False
            mmd_physics_joint_shape.deregister(plugin_fn)
            mmd_rigid_body_draw_override.deregister()
            mmd_rigid_body_shape.deregister(plugin_fn)
            mmd_physics_world_shape.deregister(plugin_fn)
            _physics_nodes_registered = False
        # Only deregister rig nodes that Python actually registered
        global _python_rig_nodes_registered
        if _python_rig_nodes_registered:
            mmd_ccd_ik_node.deregister(plugin_fn)
            mmd_append_node.deregister(plugin_fn)
            _python_rig_nodes_registered = False
        mmd_morph_controller_node.deregister(plugin_fn)
        mmd_material_morph_eval_node.deregister(plugin_fn)
        mmd_bone_morph_accum_node.deregister(plugin_fn)
    except Exception as e:
        om.MGlobal.displayError(f"Plugin uninitialization failed: {str(e)}")
        raise
