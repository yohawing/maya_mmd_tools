"""Maya process, path, and environment helpers for the Nox task runner."""

from __future__ import annotations

import os
from pathlib import Path

from tests.common.maya_location import convert_path_options_for_maya_process as _convert_maya_path_options
from tests.common.maya_location import path_for_maya_process as _maya_process_path
from tests.common.maya_location import pythonpath_for_maya_process as _maya_pythonpath
from tests.common.maya_location import resolve_path_for_maya_process as _resolve_maya_path


def _mayapy_env(mayapy: Path, root: Path, preserve_pythonpath: bool = False, **extra: str) -> dict[str, str]:
    """Return environment values with repository paths suitable for mayapy."""
    env = {
        **os.environ,
        "PYTHONPATH": _maya_pythonpath(
            mayapy,
            root,
            os.environ.get("PYTHONPATH"),
            preserve_existing=preserve_pythonpath,
        ),
    }
    env.update(extra)
    return env


def _mayapy_script(mayapy: Path, root: Path, relative_script: str) -> str:
    """Return an absolute script path suitable for the resolved mayapy."""
    return _maya_process_path(mayapy, root / relative_script)


def _mayapy_arg_path(mayapy: Path, root: Path, value: str | Path) -> str:
    """Return a path argument suitable for the resolved mayapy."""
    return _resolve_maya_path(mayapy, root, value)


def _convert_mayapy_path_options(
    mayapy: Path,
    root: Path,
    args: list[str],
    path_options: set[str],
) -> list[str]:
    """Convert values following path-like options for a mayapy child process."""
    return _convert_maya_path_options(mayapy, root, args, path_options)
