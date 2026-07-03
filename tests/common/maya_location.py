"""Shared Maya installation path discovery helpers for test tooling."""

from __future__ import annotations

import os
import platform
import re
import ntpath
from pathlib import Path
from typing import Iterable, List, Optional, Set, Union


Version = Union[str, int]


def maya_location(version: Version) -> Path:
    """Return Maya installation root for *version*.

    Environment overrides are resolved before platform defaults:
    ``MAYA_LOCATION_<version>`` first, then common ``MAYA_LOCATION``.
    """
    version_text = str(version)
    version_env = os.environ.get(f"MAYA_LOCATION_{version_text}")
    if version_env:
        return Path(version_env)

    common_env = os.environ.get("MAYA_LOCATION")
    if common_env:
        return Path(common_env)

    system = platform.system()
    if system == "Windows":
        return Path(f"C:/Program Files/Autodesk/Maya{version_text}")
    if system == "Darwin":
        return Path(f"/Applications/Autodesk/maya{version_text}/Maya.app/Contents")

    wsl_maya_path = Path(f"/mnt/c/Program Files/Autodesk/Maya{version_text}")
    if wsl_maya_path.exists():
        return wsl_maya_path

    location = f"/usr/autodesk/maya{version_text}"
    try:
        if int(version_text) < 2016:
            location += "-x64"
    except ValueError:
        pass
    return Path(location)


def maya_binary(version: Version, executable: str) -> Path:
    """Return a Maya binary path below the resolved installation root."""
    location = maya_location(version)
    binary = location / "bin" / executable
    if platform.system() == "Windows" or str(location).startswith("/mnt/"):
        binary = binary.with_suffix(".exe")
    return binary


def mayapy(version: Version) -> Path:
    """Return the mayapy executable path for *version*."""
    return maya_binary(version, "mayapy")


def wsl_to_windows_path(path: Union[str, Path]) -> str:
    """Convert a WSL ``/mnt/<drive>/...`` path to a Windows path string."""
    path_text = str(path)
    match = re.match(r"/mnt/([a-zA-Z])(?:/|$)", path_text)
    if not match:
        return path_text
    drive_letter = match.group(1).upper()
    if path_text == f"/mnt/{match.group(1)}":
        return f"{drive_letter}:\\"
    converted = path_text.replace(f"/mnt/{match.group(1)}/", f"{drive_letter}:/", 1)
    return converted.replace("/", "\\")


def path_for_maya_process(mayapy_path: Union[str, Path], path: Union[str, Path]) -> str:
    """Return *path* in the form expected by the resolved mayapy process."""
    path_text = str(path)
    if str(mayapy_path).startswith("/mnt/"):
        return wsl_to_windows_path(path_text)
    return path_text


def is_windows_absolute_path(path: Union[str, Path]) -> bool:
    """Return True for drive-letter or UNC absolute Windows paths."""
    path_text = str(path)
    if path_text.startswith("/"):
        return False
    return ntpath.isabs(path_text)


def resolve_path_for_maya_process(
    mayapy_path: Union[str, Path],
    root: Union[str, Path],
    path: Union[str, Path],
) -> str:
    """Resolve a path argument relative to *root* and format it for mayapy."""
    path_text = str(path)
    if path_text.startswith("/mnt/"):
        return path_for_maya_process(mayapy_path, path_text)
    if is_windows_absolute_path(path_text):
        return path_text

    root_text = str(root)
    if root_text.startswith("/mnt/"):
        if path_text.startswith("/"):
            return path_for_maya_process(mayapy_path, path_text)
        return path_for_maya_process(mayapy_path, f"{root_text.rstrip('/')}/{path_text}")

    resolved = Path(path_text)
    if not resolved.is_absolute():
        resolved = (Path(root) / resolved).resolve()
    return path_for_maya_process(mayapy_path, resolved)


def convert_path_options_for_maya_process(
    mayapy_path: Union[str, Path],
    root: Union[str, Path],
    args: Iterable[str],
    path_options: Set[str],
) -> List[str]:
    """Convert values following path-like options for a mayapy child process."""
    arg_list = list(args)
    converted: List[str] = []
    i = 0
    while i < len(arg_list):
        arg = arg_list[i]
        matched_inline = False
        for option in path_options:
            prefix = f"{option}="
            if arg.startswith(prefix):
                value = arg[len(prefix):]
                converted.append(f"{option}={resolve_path_for_maya_process(mayapy_path, root, value)}")
                matched_inline = True
                break
        if matched_inline:
            i += 1
            continue
        converted.append(arg)
        if arg in path_options and i + 1 < len(arg_list):
            converted.append(resolve_path_for_maya_process(mayapy_path, root, arg_list[i + 1]))
            i += 2
            continue
        i += 1
    return converted


def pythonpath_for_maya_process(
    mayapy_path: Union[str, Path],
    root: Union[str, Path],
    existing_pythonpath: Optional[str],
    host_pathsep: str = os.pathsep,
    preserve_existing: bool = False,
) -> str:
    """Return PYTHONPATH preserving existing entries for the target mayapy."""
    root_entry = path_for_maya_process(mayapy_path, root)
    if not preserve_existing or not existing_pythonpath:
        return root_entry

    mayapy_path_str = str(mayapy_path)
    target_is_windows = (
        mayapy_path_str.startswith("/mnt/")
        or platform.system() == "Windows"
        or re.match(r"^[A-Za-z]:\\", mayapy_path_str) is not None
    )
    target_sep = ";" if target_is_windows else os.pathsep
    source_sep = host_pathsep
    if str(mayapy_path).startswith("/mnt/") and re.search(r"(?:^|;)[A-Za-z]:\\", existing_pythonpath):
        source_sep = ";"
    entries = [
        path_for_maya_process(mayapy_path, entry)
        for entry in existing_pythonpath.split(source_sep)
        if entry
    ]
    return target_sep.join([root_entry, *entries])
