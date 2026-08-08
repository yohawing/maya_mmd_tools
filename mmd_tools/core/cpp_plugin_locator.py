"""Canonical discovery and loading helpers for the native MMD Tools plug-in.

The startup hook and the optional C++ fast importer intentionally share this
module so version-specific environment overrides and packaged plug-in layout
cannot drift between the two entry points.  The caller supplies search roots
because a Maya ``userSetup.py`` and an installed Python package have different
ways of locating their own source tree.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, Optional, Union

PLUGIN_BASENAME = "mmd_tools_cpp"
PLUGIN_EXTENSIONS = (".mll", ".bundle", ".so")
_DLL_DIRECTORY_HANDLES: dict[str, object] = {}


def running_maya_major_version(cmds_module=None, *, default: str = "") -> str:
    """Return the running Maya major version, falling back to ``MAYA_VERSION``.

    ``cmds_module`` is injectable for startup/unit tests.  Importing Maya is
    deliberately lazy so this locator remains usable by headless tooling.
    """
    try:
        if cmds_module is None:
            import maya.cmds as cmds_module
        about = getattr(cmds_module, "about", None)
        if about is not None:
            value = str(about(version=True)).strip()
            if value and value[0].isdigit():
                return value.split()[0].split(".")[0]
    except Exception:
        pass

    value = os.environ.get("MAYA_VERSION", "").strip() or str(default).strip()
    return value.split()[0].split(".")[0] if value else ""


def _first_non_empty_environment(*names: str) -> Optional[str]:
    """Return the first non-empty environment value in the supplied order."""
    for name in names:
        if not name:
            continue
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def plugin_configurations(maya_version: str) -> tuple[str, ...]:
    """Return config candidates with version-specific precedence.

    ``MMD_TOOLS_CPP_CONFIG_<version>`` wins over the generic variable.  The
    selected configuration is followed by Release and Debug fallback builds,
    de-duplicated while preserving order.
    """
    selected = _first_non_empty_environment(
        f"MMD_TOOLS_CPP_CONFIG_{maya_version}" if maya_version else "",
        "MMD_TOOLS_CPP_CONFIG",
    ) or "Debug"
    configs = []
    for config in (selected, "Release", "Debug"):
        if config not in configs:
            configs.append(config)
    return tuple(configs)


def plugin_candidate_paths(
    roots: Iterable[Union[Path, str]],
    maya_version: Optional[str] = None,
) -> list[Path]:
    """Return native plug-in candidates in canonical precedence order.

    An explicit version-specific path wins over the generic path and is
    returned as the sole candidate even when the file is not present.  This
    lets callers report a deterministic missing-artifact diagnostic instead of
    silently selecting a different build.  Packaged artifacts use
    ``<root>/plug-ins/<maya>/<config>/mmd_tools_cpp.<ext>``.
    """
    version = maya_version
    if version is None:
        version = running_maya_major_version()
    version = str(version).strip().split()[0].split(".")[0] if str(version).strip() else ""

    explicit = _first_non_empty_environment(
        f"MMD_TOOLS_CPP_PLUGIN_{version}" if version else "",
        "MMD_TOOLS_CPP_PLUGIN",
    )
    if explicit:
        return [Path(explicit).expanduser()]
    if not version:
        return []

    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            root_path = Path(root).expanduser()
        except (TypeError, ValueError):
            continue
        # Preserve caller-visible spelling while de-duplicating equivalent
        # roots.  ``resolve`` may fail for an unavailable network package.
        try:
            key = root_path.resolve()
        except OSError:
            key = root_path.absolute()
        if key in seen:
            continue
        seen.add(key)
        for config in plugin_configurations(version):
            for extension in PLUGIN_EXTENSIONS:
                candidates.append(
                    root_path
                    / "plug-ins"
                    / version
                    / config
                    / f"{PLUGIN_BASENAME}{extension}"
                )
    return candidates


def find_plugin_path(candidates: Iterable[Union[Path, str]]) -> Optional[Path]:
    """Return the first existing regular-file candidate, if any."""
    for candidate in candidates:
        try:
            path = Path(candidate)
        except (TypeError, ValueError):
            continue
        if path.is_file():
            return path
    return None


def is_plugin_loaded(plugin_path: Union[Path, str], cmds_module) -> bool:
    """Return whether the exact resolved plug-in path is already loaded."""
    try:
        expected = Path(plugin_path).expanduser().resolve()
    except (OSError, TypeError, ValueError):
        return False

    try:
        loaded_plugins = cmds_module.pluginInfo(query=True, listPlugins=True) or []
    except Exception:
        return False
    for name in loaded_plugins:
        try:
            if not cmds_module.pluginInfo(name, query=True, loaded=True):
                continue
            loaded_path = Path(cmds_module.pluginInfo(name, query=True, path=True)).expanduser().resolve()
            if loaded_path == expected:
                return True
        except (OSError, TypeError, ValueError):
            continue
        except Exception:
            continue
    return False


def prepare_plugin_directory(plugin_path: Union[Path, str]) -> None:
    """Expose the native plug-in directory to dependent DLL/SO loaders."""
    plugin_dir = Path(plugin_path).expanduser().resolve().parent
    directory = str(plugin_dir)
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if directory not in path_entries:
        os.environ["PATH"] = os.pathsep.join([directory, *[entry for entry in path_entries if entry]])

    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        if directory in _DLL_DIRECTORY_HANDLES:
            return
        try:
            _DLL_DIRECTORY_HANDLES[directory] = os.add_dll_directory(directory)
        except OSError:
            # Maya may already have registered the directory, or this may be
            # a host where the optional API rejects a non-native path.
            pass


def load_plugin(plugin_path: Union[Path, str], cmds_module, *, prepare: bool = True) -> bool:
    """Load *plugin_path* once and return whether this call loaded it.

    Exact-path reuse is checked before calling ``loadPlugin``.  ``prepare`` is
    false only when the caller has already prepared the directory and wants to
    retain a testable compatibility seam around that operation.
    """
    path = Path(plugin_path).expanduser()
    if is_plugin_loaded(path, cmds_module):
        return False
    if prepare:
        prepare_plugin_directory(path)
    cmds_module.loadPlugin(str(path), quiet=True)
    return True
