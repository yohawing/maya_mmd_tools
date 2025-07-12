from maya import cmds

import maya.api.OpenMaya as om

import maya.OpenMayaMPx as ommpx
from .mmd_file_translator import register_file_translators, unregister_file_translators
from .ui import install_mmd_menu, uninstall_mmd_menu


def maya_useNewAPI():
    """Maya API 2.0を使用することを宣言"""
    pass


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
