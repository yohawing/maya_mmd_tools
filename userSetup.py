from maya import cmds
from mmd_tools.plugin_main import install_mmd_menu


def mmd_tools_setup():
    # プラグインを自動読み込み（.modファイルで管理）
    try:
        if not cmds.pluginInfo("mmd_tools_plugin.py", query=True, loaded=True):
            cmds.loadPlugin("mmd_tools_plugin.py")
        else:
            install_mmd_menu()
    except Exception:
        pass  # Silently fail in testing environment


# Defer execution until Maya is fully initialized
cmds.evalDeferred(mmd_tools_setup)
