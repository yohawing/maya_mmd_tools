import maya.cmds as cmds

def mmd_tools_setup():
    print("Maya MMD Tools: Initializing...")

    # Load the plugin
    # cmds.loadPlugin("plugin_main.py") # This will be handled by .mod file

    # Create a custom menu for MMD Tools
    import mmd_tools.ui as mmd_ui
    mmd_ui.create_mmd_tools_menu()

    print("MMD Tools: userSetup initialized.")

# Defer execution until Maya is fully initialized
cmds.evalDeferred(mmd_tools_setup)