"""Cross-platform development task runner for maya_mmd_tools.

Nox is used as a thin orchestration layer around existing project tools:
Maya tests still run through mayapy, C++ builds still run through CMake, and
mmd-anim still builds through Cargo. Sessions use the current Python process
instead of creating a separate virtual environment.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import nox


ROOT = Path(__file__).resolve().parent
DEFAULT_MAYA_VERSION = "2024"
DEFAULT_CMAKE_CONFIG = "Debug"

nox.options.sessions = ["tests"]


def _option(args: list[str], name: str, default: str) -> str:
    """Return a string option value from nox positional arguments."""
    try:
        index = args.index(name)
    except ValueError:
        return default
    try:
        return args[index + 1]
    except IndexError as exc:
        raise ValueError(f"{name} requires a value") from exc


def _maya_location(version: str) -> Path:
    """Return Maya installation root for the current platform."""
    version_env = os.environ.get(f"MAYA_LOCATION_{version}")
    if version_env:
        return Path(version_env)

    common_env = os.environ.get("MAYA_LOCATION")
    if common_env:
        return Path(common_env)

    system = platform.system()
    if system == "Windows":
        return Path(f"C:/Program Files/Autodesk/Maya{version}")
    if system == "Darwin":
        return Path(f"/Applications/Autodesk/maya{version}/Maya.app/Contents")

    return Path(f"/usr/autodesk/maya{version}")


def _maya_devkit_root(version: str) -> Path:
    """Return the Maya devkit root, allowing environment overrides."""
    version_env = os.environ.get(f"MAYA_DEVKIT_ROOT_{version}")
    if version_env:
        return Path(version_env)

    common_env = os.environ.get("MAYA_DEVKIT_ROOT")
    if common_env:
        return Path(common_env)

    return _maya_location(version) / "devkit"


def _mayapy(version: str) -> Path:
    """Return mayapy executable path for the current platform."""
    executable = _maya_location(version) / "bin" / "mayapy"
    if platform.system() == "Windows":
        executable = executable.with_suffix(".exe")
    return executable


def _cpp_build_dir(version: str) -> Path:
    """Return the CMake build directory for a Maya version."""
    return ROOT / "build" / "cpp" / f"maya{version}"


def _cmake_configure(session: nox.Session, version: str) -> None:
    """Configure the Maya C++ plugin build."""
    args = [
        "-S",
        "cpp/src",
        "-B",
        str(_cpp_build_dir(version)),
        f"-DMAYA_VERSION={version}",
        f"-DREPO_ROOT={ROOT}",
        f"-DMAYA_DEVKIT_ROOT={_maya_devkit_root(version)}",
    ]

    if platform.system() == "Windows" and not os.environ.get("CMAKE_GENERATOR"):
        args.extend(["-G", "Visual Studio 17 2022", "-A", "x64"])

    session.run("cmake", *args, external=True)


@nox.session(venv_backend="none")
def tests(session: nox.Session) -> None:
    """Run existing mayapy-backed unit/integration tests.

    Examples:
        uvx nox -s tests
        uvx nox -s tests -- --type integration --test test_maya_utils
    """
    args = session.posargs or ["--type", "unit"]
    session.run(sys.executable, "tests/run_tests.py", *args, external=True)


@nox.session(venv_backend="none")
def gui_tests(session: nox.Session) -> None:
    """Run existing Maya GUI tests."""
    args = session.posargs or ["--maya_version", DEFAULT_MAYA_VERSION]
    session.run(sys.executable, "tests/run_gui_tests.py", *args, external=True)


@nox.session(venv_backend="none")
def ffi_build(session: nox.Session) -> None:
    """Build the mmd-anim FFI library used by Python and C++ integrations."""
    args = session.posargs or ["--release"]
    session.run(
        "cargo",
        "build",
        "-p",
        "mmd-anim-ffi",
        "--manifest-path",
        "external/mmd-anim/Cargo.toml",
        *args,
        external=True,
    )


@nox.session(venv_backend="none")
def native_smoke(session: nox.Session) -> None:
    """Verify that Python can load mmd-anim-ffi and read its ABI version."""
    code = (
        "from mmd_tools.core.native.mmd_anim_runtime import "
        "get_mmd_runtime_library, get_runtime_library_path; "
        "lib = get_mmd_runtime_library(); "
        "print(get_runtime_library_path()); "
        "raise SystemExit(0 if lib and lib.mmd_runtime_abi_version() == 1 else 1)"
    )
    session.run(sys.executable, "-c", code, external=True)


@nox.session(venv_backend="none")
def cpp_config(session: nox.Session) -> None:
    """Configure the Maya C++ plugin build."""
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    _cmake_configure(session, version)


@nox.session(venv_backend="none")
def cpp_build(session: nox.Session) -> None:
    """Configure and build the Maya C++ plugin."""
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)
    _cmake_configure(session, version)
    session.run(
        "cmake",
        "--build",
        str(_cpp_build_dir(version)),
        "--config",
        config,
        external=True,
    )


@nox.session(venv_backend="none")
def maya_smoke(session: nox.Session) -> None:
    """Load the C++ plugin in mayapy and create the runtime node."""
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)
    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    env = {
        **os.environ,
        "MAYA_VERSION": version,
        "MMD_TOOLS_CPP_CONFIG": config,
        "PYTHONPATH": str(ROOT),
    }
    session.run(
        str(mayapy),
        "tests/cpp/smoke_runtime_node.py",
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def cpp_verify(session: nox.Session) -> None:
    """Run the CLI-only C++/native verification chain."""
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)

    session.run(
        "cargo",
        "build",
        "-p",
        "mmd-anim-ffi",
        "--manifest-path",
        "external/mmd-anim/Cargo.toml",
        "--release",
        external=True,
    )

    code = (
        "from mmd_tools.core.native.mmd_anim_runtime import "
        "get_mmd_runtime_library, get_runtime_library_path; "
        "lib = get_mmd_runtime_library(); "
        "print(get_runtime_library_path()); "
        "raise SystemExit(0 if lib and lib.mmd_runtime_abi_version() == 1 else 1)"
    )
    session.run(sys.executable, "-c", code, external=True)

    _cmake_configure(session, version)
    session.run(
        "cmake",
        "--build",
        str(_cpp_build_dir(version)),
        "--config",
        config,
        external=True,
    )

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    env = {
        **os.environ,
        "MAYA_VERSION": version,
        "MMD_TOOLS_CPP_CONFIG": config,
        "PYTHONPATH": str(ROOT),
    }
    session.run(
        str(mayapy),
        "tests/cpp/smoke_runtime_node.py",
        env=env,
        external=True,
    )
