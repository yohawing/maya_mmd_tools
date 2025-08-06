from maya import cmds
from mmd_tools.plugin_main import install_mmd_menu


def mmd_tools_setup():
    # プラグインを自動読み込み（.modファイルで管理）
    try:
        if not cmds.pluginInfo("plugin_main.py", query=True, loaded=True):
            cmds.loadPlugin("plugin_main.py")
    except Exception:
        pass  # Silently fail in testing environment

    install_mmd_menu()


# Defer execution until Maya is fully initialized
cmds.evalDeferred(mmd_tools_setup)
