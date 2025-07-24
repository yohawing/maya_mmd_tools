from maya import cmds

import maya.OpenMaya as om

import maya.OpenMayaMPx as ommpx
from mmd_tools.mmd_file_translator import register_file_translators, unregister_file_translators
from mmd_tools.ui.main_window import MainWindow

# Store the window instance to avoid it being garbage collected
main_window_instance = None

def open_main_window():
    """Open the main MMD Tools window."""
    global main_window_instance
    if main_window_instance is None:
        main_window_instance = MainWindow()
    main_window_instance.show()

def install_mmd_menu():
    """Install the MMD menu in Maya."""
    if not cmds.menu("MMD", exists=True):
        cmds.menu("MMD", parent="MayaWindow")
    cmds.menuItem(label="MMD Tools", command=lambda *args: open_main_window(), parent="MMD")

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
        om.MGlobal.displayInfo("maya_mmd_tools plugin unloaded!")
    except Exception as e:
        om.MGlobal.displayError(f"Plugin uninitialization failed: {str(e)}")
        raise
