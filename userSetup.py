from maya import cmds


def mmd_tools_setup():
    # プラグインを自動読み込み（.modファイルで管理）
    try:
        if not cmds.pluginInfo("mmd_tools_plugin.py", query=True, loaded=True):
            cmds.loadPlugin("mmd_tools_plugin.py")
        else:
            # Import only after Maya has finished initializing its UI.  Importing
            # plugin_main at userSetup module load time initializes Qt/PySide too
            # early and can crash Maya 2027 on macOS.
            from mmd_tools.plugin_main import install_mmd_menu

            install_mmd_menu()
    except Exception:
        pass  # Silently fail in testing environment


def mmd_tools_schedule_setup():
    # Maya 2027 on macOS can still be constructing Qt/WebEngine UI when its
    # lowest-priority deferred queue starts.  Loading the plug-in in that window
    # can crash the host after initializePlugin returns, so wait for the main UI
    # event loop to settle first.
    from PySide6.QtCore import QTimer

    QTimer.singleShot(5000, mmd_tools_setup)


# Schedule the timer after Maya's other startup-time deferred work.
cmds.evalDeferred(mmd_tools_schedule_setup, lowestPriority=True)
