from maya import cmds


def mmd_tools_setup():
    print("Maya MMD Tools: Initializing...")

    # プラグインを自動読み込み（.modファイルで管理）
    try:
        if not cmds.pluginInfo("plugin_main.py", query=True, loaded=True):
            cmds.loadPlugin("plugin_main.py")
            print("MMD Tools: Plugin loaded successfully.")
    except Exception as e:
        print(f"MMD Tools: Failed to load plugin: {e}")

    print("MMD Tools: userSetup initialized.")

# Defer execution until Maya is fully initialized
cmds.evalDeferred(mmd_tools_setup)
