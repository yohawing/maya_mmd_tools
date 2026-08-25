"""Nox-independent native build and CLI helpers for the task runner."""

from __future__ import annotations

import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

from tests.common.maya_location import maya_location as _maya_location


def _maya_devkit_root(version: str) -> Path:
    """Return the Maya devkit root, allowing environment overrides."""
    version_env = os.environ.get(f"MAYA_DEVKIT_ROOT_{version}")
    if version_env:
        return Path(version_env)

    common_env = os.environ.get("MAYA_DEVKIT_ROOT")
    if common_env:
        return Path(common_env)

    return _maya_location(version) / "devkit"


def _cpp_build_dir(root: Path, version: str) -> Path:
    """Return the CMake build directory for a Maya version."""
    return root / "build" / "cpp" / f"maya{version}"


def _vswhere_path() -> Path:
    """Return the default vswhere path."""
    explicit = os.environ.get("VSWHERE_PATH")
    if explicit:
        return Path(explicit)
    return Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")


def _find_vsdevcmd() -> Path | None:
    """Find VsDevCmd.bat for Windows C++ builds."""
    explicit = os.environ.get("VSDEVCMD_PATH")
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None

    vswhere = _vswhere_path()
    if vswhere.exists():
        try:
            result = subprocess.run(
                [
                    str(vswhere),
                    "-latest",
                    "-prerelease",
                    "-products",
                    "*",
                    "-requires",
                    "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                    "-property",
                    "installationPath",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            for line in result.stdout.splitlines():
                candidate = Path(line.strip()) / "Common7" / "Tools" / "VsDevCmd.bat"
                if candidate.exists():
                    return candidate
        except OSError:
            pass

    for root in (
        Path("C:/Program Files/Microsoft Visual Studio/18"),
        Path("C:/Program Files/Microsoft Visual Studio/2022"),
        Path("C:/Program Files (x86)/Microsoft Visual Studio/2022"),
    ):
        if not root.exists():
            continue
        for candidate in root.glob("*/Common7/Tools/VsDevCmd.bat"):
            if candidate.exists():
                return candidate

    return None


def _run_in_vs_dev_cmd(session: Any, root: Path, command: list[str]) -> None:
    """Run a Windows command after initializing Visual Studio C++ tools."""
    vsdevcmd = _find_vsdevcmd()
    if vsdevcmd is None or os.environ.get("MMD_TOOLS_SKIP_VSDEVCMD"):
        session.run(*command, external=True)
        return

    body = subprocess.list2cmdline(command)
    session.log(f"Using Visual Studio developer environment: {vsdevcmd}")
    result = subprocess.run(
        f'"{vsdevcmd}" -arch=x64 -host_arch=x64 >nul && {body}',
        cwd=root,
        shell=True,
    )
    if result.returncode != 0:
        session.error(f"Command failed with exit code {result.returncode}: {body}")


def _cmake_configure(session: Any, root: Path, version: str, config: str) -> None:
    """Configure the Maya C++ plugin build."""
    args = [
        "cmake",
        "-S",
        "cpp/src",
        "-B",
        str(_cpp_build_dir(root, version)),
        f"-DMAYA_VERSION={version}",
        f"-DREPO_ROOT={root}",
        f"-DMAYA_DEVKIT_ROOT={_maya_devkit_root(version)}",
    ]

    if platform.system() == "Windows" and not os.environ.get("CMAKE_GENERATOR"):
        args.extend(["-G", "Ninja", f"-DCMAKE_BUILD_TYPE={config}"])

    if platform.system() == "Windows":
        _run_in_vs_dev_cmd(session, root, args)
    else:
        session.run(*args, external=True)


def _cmake_build(
    session: Any,
    root: Path,
    version: str,
    config: str,
    *,
    clean_first: bool = False,
) -> None:
    """Build the Maya C++ plugin, optionally forcing fresh tracked artifacts."""
    command = [
        "cmake",
        "--build",
        str(_cpp_build_dir(root, version)),
        "--config",
        config,
    ]
    if clean_first:
        command.append("--clean-first")
    if platform.system() == "Windows":
        _run_in_vs_dev_cmd(session, root, command)
    else:
        session.run(*command, external=True)


def _cpp_smoke_exe(root: Path, version: str, config: str) -> Path:
    """Return path to the standalone mmd_runtime_smoke exe produced by cpp build."""
    build_dir = _cpp_build_dir(root, version) / config
    exe = build_dir / "mmd_runtime_smoke"
    if platform.system() == "Windows":
        exe = exe.with_suffix(".exe")
    return exe


def _run_cli_smoke(
    session: Any,
    root: Path,
    version: str,
    config: str,
    manifest: str,
    case: str = "",
    limit: str = "",
) -> None:
    """Run the CLI smoke exe (if manifest provided). Used by C++ sessions."""
    if not manifest:
        return
    exe = _cpp_smoke_exe(root, version, config)
    if not exe.exists():
        raise FileNotFoundError(
            f"mmd_runtime_smoke not found at {exe}. "
            f"Run 'uvx nox -s cpp_build -- --maya {version} --config {config}' first."
        )
    smoke_args: list[str] = ["--manifest", manifest]
    if case:
        smoke_args.extend(["--case", case])
    if limit:
        smoke_args.extend(["--limit", limit])
    session.run(str(exe), *smoke_args, external=True)


_EXPECTED_ENVIRONMENT_MODULE_PREFIXES = ("maya", "PySide2", "PySide6")
_TERMINAL_EXCEPTION_RE = re.compile(r"^(?P<type>[\w.]+(?:Error|Exception)):\s*(?P<message>.*)$")
_MISSING_MODULE_RE = re.compile(
    r"No module named ['\"](?P<module>[^'\"]+)['\"](?:;.*)?\.?$"
)


def _is_expected_environment_import_failure(stderr: str) -> bool:
    """Return whether the final exception is an allowlisted missing environment module."""
    for line in reversed(stderr.splitlines()):
        match = _TERMINAL_EXCEPTION_RE.match(line.strip())
        if not match:
            continue
        if match.group("type") != "ModuleNotFoundError":
            return False
        missing = _MISSING_MODULE_RE.fullmatch(match.group("message"))
        if not missing:
            return False
        prefix = missing.group("module").split(".", 1)[0]
        return prefix in _EXPECTED_ENVIRONMENT_MODULE_PREFIXES
    return False
