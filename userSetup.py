import maya.cmds as cmds
import maya.mel as mel
import sys
import os

def mmd_tools_setup():
    print("MMD Tools: Initializing userSetup...")

    # Add plugin directory to Python path if not already there
    # This might be redundant if .mod file is used correctly, but good for robustness
    plugin_root = os.path.dirname(__file__)
    if plugin_root not in sys.path:
        sys.path.append(plugin_root)

    # Load the plugin
    # cmds.loadPlugin("plugin_main.py") # This will be handled by .mod file

    # Create a custom menu for MMD Tools
    if not cmds.menu('MMDToolsMenu', exists=True):
        gMainWindow = mel.eval('$temp1 = $gMainWindow')
        cmds.menu('MMDToolsMenu', parent=gMainWindow, tearOff=True, label='MMD Tools')
        cmds.menuItem('MMDTools_Import', parent='MMDToolsMenu', label='Import MMD Model', command='import maya_mmd_tools.src.io.mmd_importer as mmd_importer; mmd_importer.import_mmd_file("dummy_path.pmx")') # Placeholder command
        cmds.menuItem('MMDTools_Export', parent='MMDToolsMenu', label='Export MMD Model', command='import maya_mmd_tools.src.io.pmx_exporter as pmx_exporter; pmx_exporter.export_pmx_file("dummy_path.pmx", None)') # Placeholder command

    print("MMD Tools: userSetup initialized.")

# Defer execution until Maya is fully initialized
cmds.evalDeferred(mmd_tools_setup)