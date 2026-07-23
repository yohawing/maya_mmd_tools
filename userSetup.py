"""Maya startup hook for loading the canonical MMD Tools plugin."""

from pathlib import Path
import os
import sys
import traceback

from maya import cmds
from maya.api import OpenMaya as om


def _mmd_tools_plugin_path():
    """Return the source-tree plugin path when this hook belongs to a checkout."""
    roots = []
    source_file = globals().get("__file__")
    if source_file:
        try:
            roots.append(Path(source_file).resolve().parent)
        except Exception:
            pass
    for variable in ("MMD_TOOLS_ROOT", "MAYA_MODULE_PATH", "PYTHONPATH"):
        roots.extend(Path(value) for value in os.environ.get(variable, "").split(os.pathsep) if value)
    roots.extend(Path(value) for value in sys.path if value)
    try:
        roots.append(Path.cwd())
    except Exception:
        pass
    seen = set()
    for root in roots:
        try:
            root = root.resolve()
        except Exception:
            continue
        if root in seen:
            continue
        seen.add(root)
        candidate = root / "plug-ins" / "mmd_tools_plugin.py"
        if candidate.is_file():
            return str(candidate)
    return "mmd_tools_plugin.py"


def _mmd_tools_plugin_loaded(plugin_path):
    """Check the loaded plugin by resolved path to avoid stale module matches."""
    try:
        expected = Path(plugin_path).resolve() if Path(plugin_path).is_absolute() else None
    except Exception:
        return False
    for name in cmds.pluginInfo(query=True, listPlugins=True) or []:
        try:
            if not cmds.pluginInfo(name, query=True, loaded=True):
                continue
            if expected is None:
                if name in {"mmd_tools_plugin", "mmd_tools_plugin.py"}:
                    return True
                continue
            loaded_path = Path(cmds.pluginInfo(name, query=True, path=True)).resolve()
            if loaded_path == expected:
                return True
        except Exception:
            continue
    return False


def mmd_tools_setup():
    # Plugin load failures must remain visible; otherwise the UI can be left
    # partially available while custom node types are silently absent.
    try:
        plugin_path = _mmd_tools_plugin_path()
        was_loaded = _mmd_tools_plugin_loaded(plugin_path)
        if not was_loaded:
            cmds.loadPlugin(plugin_path, quiet=True)
        else:
            # Import only after Maya has finished initializing its UI.  Importing
            # plugin_main at userSetup module load time initializes Qt/PySide too
            # early and can crash Maya 2027 on macOS.
            from mmd_tools.plugin_main import install_mmd_menu

            install_mmd_menu()
    except Exception as exc:
        message = (
            f"[MMD] Plugin auto-load failed: {exc}. "
            "Load mmd_tools_plugin.py manually and inspect the Script Editor."
        )
        try:
            om.MGlobal.displayError(message)
            om.MGlobal.displayError(traceback.format_exc())
        except Exception:
            pass


def mmd_tools_schedule_setup():
    # Maya 2027 on macOS can still be constructing Qt/WebEngine UI when its
    # lowest-priority deferred queue starts.  Loading the plug-in in that window
    # can crash the host after initializePlugin returns, so wait for the main UI
    # event loop to settle first.
    from PySide6.QtCore import QTimer

    QTimer.singleShot(5000, mmd_tools_setup)


# Schedule the timer after Maya's other startup-time deferred work.
cmds.evalDeferred(mmd_tools_schedule_setup, lowestPriority=True)
