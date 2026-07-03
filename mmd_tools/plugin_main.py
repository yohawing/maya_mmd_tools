import os

from maya import cmds
import maya.api.OpenMaya as om
from mmd_tools import __version__
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.view import shader_override as mmd_shader
from mmd_tools.ui.drag_drop_importer import (
    install_drag_drop_importer,
    uninstall_drag_drop_importer,
)
from mmd_tools.nodes import mmd_append_node
from mmd_tools.nodes import mmd_bone_morph_accum_node
from mmd_tools.nodes import mmd_ccd_ik_node
from mmd_tools.nodes import mmd_material_morph_eval_node

_main_window = None


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
                    if widget.objectName() == MainWindow.WINDOW_NAME:
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

    # 既存のウィンドウを削除
    if cmds.window(MainWindow.WINDOW_NAME, exists=True):
        cmds.deleteUI(MainWindow.WINDOW_NAME)

    # workspaceControlがあれば削除
    if hasattr(MainWindow, "WORKSPACE_CONTROL_NAME"):
        if cmds.workspaceControl(MainWindow.WORKSPACE_CONTROL_NAME, exists=True):
            cmds.deleteUI(MainWindow.WORKSPACE_CONTROL_NAME, control=True)

    # 新しいインスタンスを作成して表示
    main_window = MainWindow()
    main_window.show_window(dockable=dockable)
    _main_window = main_window


def install_mmd_menu():
    """Install the MMD menu in Maya."""
    if not cmds.menu("MMD", exists=True):
        cmds.menu("MMD", parent="MayaWindow")

    # Keep menu installation idempotent across userSetup, reloads, and plug-in toggles.
    for item in cmds.menu("MMD", query=True, itemArray=True) or []:
        if cmds.menuItem(item, query=True, label=True) == "MMD Tools":
            cmds.deleteUI(item)

    cmds.menuItem(
        "MMDToolsMenuItem",
        label="MMD Tools",
        command=lambda *args: open_main_window(dockable=False),
        parent="MMD",
    )


def uninstall_mmd_menu():
    """Uninstall the MMD menu from Maya."""
    if cmds.menu("MMD", exists=True):
        cmds.deleteUI("MMD", menu=True)


def initializePlugin(mobject):
    """
    Plugin entry point.
    """
    vendor = "yohawing"
    version = __version__

    plugin_fn = om.MFnPlugin(mobject, vendor, version)

    try:
        install_mmd_menu()
        install_drag_drop_importer()
        if os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE") != "1":
            mmd_shader.initializePlugin(mobject)
        mmd_bone_morph_accum_node.register(plugin_fn)
        mmd_material_morph_eval_node.register(plugin_fn)
        mmd_append_node.register(plugin_fn)
        mmd_ccd_ik_node.register(plugin_fn)
    except Exception as e:
        om.MGlobal.displayError(f"Plugin initialization failed: {str(e)}")
        raise


def uninitializePlugin(mobject):
    """
    Plugin exit point.
    """
    plugin_fn = om.MFnPlugin(mobject)

    try:
        close_main_window()
        uninstall_mmd_menu()
        uninstall_drag_drop_importer()
        if os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE") != "1":
            mmd_shader.uninitializePlugin(mobject)
        mmd_ccd_ik_node.deregister(plugin_fn)
        mmd_append_node.deregister(plugin_fn)
        mmd_material_morph_eval_node.deregister(plugin_fn)
        mmd_bone_morph_accum_node.deregister(plugin_fn)
    except Exception as e:
        om.MGlobal.displayError(f"Plugin uninitialization failed: {str(e)}")
        raise
