"""Maya startup hook for loading the Python and native MMD Tools plugins.

The Python entry point owns the MMD Tools UI and Python-side node fallback.
The versioned C++ plugin is loaded separately when a matching build is
available.  Both paths are fail-soft so an unavailable native build does not
prevent Maya from starting with the Python UI.
"""

from pathlib import Path
import os
import sys
import traceback

from maya import cmds
from maya.api import OpenMaya as om


_CPP_DLL_DIRECTORY_HANDLE = None


def _mmd_tools_roots():
    """Return candidate source or installed package roots in search order."""
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
        yield root


def _mmd_tools_plugin_path():
    """Return the source-tree Python plugin path when this hook can find it."""
    for root in _mmd_tools_roots():
        candidate = root / "plug-ins" / "mmd_tools_plugin.py"
        if candidate.is_file():
            return str(candidate)
    return "mmd_tools_plugin.py"


def _maya_major_version():
    """Return the running Maya major version used by the native plugin layout."""
    try:
        value = cmds.about(version=True)
    except Exception:
        value = os.environ.get("MAYA_VERSION", "")
    value = str(value).strip()
    if not value:
        value = os.environ.get("MAYA_VERSION", "")
    return value.split()[0].split(".")[0] if value else ""


def _mmd_tools_cpp_plugin_candidates():
    """Return native plugin candidates using the fast-importer conventions."""
    version = _maya_major_version()
    for variable in (
        f"MMD_TOOLS_CPP_PLUGIN_{version}" if version else "",
        "MMD_TOOLS_CPP_PLUGIN",
    ):
        if variable:
            explicit = os.environ.get(variable)
            if explicit:
                return [Path(explicit).expanduser()]

    if not version:
        return []

    config = (
        os.environ.get(f"MMD_TOOLS_CPP_CONFIG_{version}")
        or os.environ.get("MMD_TOOLS_CPP_CONFIG")
        or "Debug"
    )
    configs = [config]
    for fallback in ("Release", "Debug"):
        if fallback not in configs:
            configs.append(fallback)

    candidates = []
    for root in _mmd_tools_roots():
        for selected_config in configs:
            for suffix in (".mll", ".bundle", ".so"):
                candidates.append(
                    root
                    / "plug-ins"
                    / version
                    / selected_config
                    / f"mmd_tools_cpp{suffix}"
                )
    return candidates


def _mmd_tools_cpp_plugin_path():
    """Return the first existing version-matched native plugin artifact."""
    for candidate in _mmd_tools_cpp_plugin_candidates():
        if candidate.is_file():
            return candidate
    return None


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


def _mmd_tools_cpp_plugin_loaded(plugin_path):
    """Check whether the native plugin at *plugin_path* is already loaded."""
    try:
        expected = Path(plugin_path).resolve()
    except Exception:
        return False
    for name in cmds.pluginInfo(query=True, listPlugins=True) or []:
        try:
            if not cmds.pluginInfo(name, query=True, loaded=True):
                continue
            loaded_path = Path(cmds.pluginInfo(name, query=True, path=True)).resolve()
            if loaded_path == expected:
                return True
        except Exception:
            continue
    return False


def _prepare_mmd_tools_cpp_plugin_directory(plugin_path):
    """Make native runtime dependencies beside *plugin_path* discoverable."""
    global _CPP_DLL_DIRECTORY_HANDLE

    plugin_dir = str(Path(plugin_path).parent)
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if plugin_dir not in path_entries:
        os.environ["PATH"] = os.pathsep.join(
            [plugin_dir] + [entry for entry in path_entries if entry]
        )

    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        try:
            _CPP_DLL_DIRECTORY_HANDLE = os.add_dll_directory(plugin_dir)
        except OSError:
            pass


def _load_mmd_tools_cpp_plugin():
    """Load the matching C++ plugin when native auto-load is enabled."""
    if os.environ.get("MMD_TOOLS_CPP_AUTOLOAD", "1").lower() in {
        "0",
        "false",
        "off",
        "no",
    }:
        return

    plugin_path = _mmd_tools_cpp_plugin_path()
    if plugin_path is None:
        return
    if _mmd_tools_cpp_plugin_loaded(plugin_path):
        return

    _prepare_mmd_tools_cpp_plugin_directory(plugin_path)
    cmds.loadPlugin(str(plugin_path), quiet=True)


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

    try:
        _load_mmd_tools_cpp_plugin()
    except Exception as exc:
        try:
            om.MGlobal.displayWarning(f"[MMD] C++ plugin auto-load failed: {exc}")
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
