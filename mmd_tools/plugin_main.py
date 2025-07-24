from maya import cmds

import maya.OpenMaya as om

import maya.OpenMayaMPx as ommpx
from mmd_tools.mmd_file_translator import (
    register_file_translators,
    unregister_file_translators,
)
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.view import shader_override as mmd_shader

def open_main_window(dockable=False):
    """Open the main MMD Tools window."""
    from mmd_tools.ui.main_window import MainWindow

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


def install_mmd_menu():
    """Install the MMD menu in Maya."""
    if not cmds.menu("MMD", exists=True):
        cmds.menu("MMD", parent="MayaWindow")
    cmds.menuItem(
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
    version = "1.0.0"

    # プラグインオブジェクトを作成
    plugin = ommpx.MFnPlugin(mobject, vendor, version)

    try:
        register_file_translators(plugin)
        install_mmd_menu()
        mmd_shader.initializePlugin(plugin)  # Register shader
        om.MGlobal.displayInfo("maya_mmd_tools plugin loaded!")
    except Exception as e:
        om.MGlobal.displayError(f"Plugin initialization failed: {str(e)}")
        raise


def uninitializePlugin(mobject):
    """
    Plugin exit point.
    """
    # プラグインオブジェクトを作成
    plugin = ommpx.MFnPlugin(mobject)

    try:
        unregister_file_translators(plugin)
        uninstall_mmd_menu()
        mmd_shader.uninitializePlugin(plugin)  # Deregister shader
        om.MGlobal.displayInfo("maya_mmd_tools plugin unloaded!")
    except Exception as e:
        om.MGlobal.displayError(f"Plugin uninitialization failed: {str(e)}")
        raise
