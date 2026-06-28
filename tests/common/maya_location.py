"""Shared Maya installation path discovery helpers for test tooling."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Union


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
