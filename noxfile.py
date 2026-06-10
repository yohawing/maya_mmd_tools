"""Cross-platform development task runner for maya_mmd_tools.

Nox is used as a thin orchestration layer around existing project tools:
Maya tests still run through mayapy, C++ builds still run through CMake, and
mmd-anim still builds through Cargo. Sessions use the current Python process
instead of creating a separate virtual environment.
"""

from __future__ import annotations

import os
import platform
import subprocess
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


def _run_in_vs_dev_cmd(session: nox.Session, command: list[str]) -> None:
    """Run a Windows command after initializing Visual Studio C++ tools."""
    vsdevcmd = _find_vsdevcmd()
    if vsdevcmd is None or os.environ.get("MMD_TOOLS_SKIP_VSDEVCMD"):
        session.run(*command, external=True)
        return

    body = subprocess.list2cmdline(command)
    session.log(f"Using Visual Studio developer environment: {vsdevcmd}")
    result = subprocess.run(
        f'"{vsdevcmd}" -arch=x64 -host_arch=x64 >nul && {body}',
        cwd=ROOT,
        shell=True,
    )
    if result.returncode != 0:
        session.error(f"Command failed with exit code {result.returncode}: {body}")


def _cmake_configure(session: nox.Session, version: str, config: str = DEFAULT_CMAKE_CONFIG) -> None:
    """Configure the Maya C++ plugin build."""
    args = [
        "cmake",
        "-S",
        "cpp/src",
        "-B",
        str(_cpp_build_dir(version)),
        f"-DMAYA_VERSION={version}",
        f"-DREPO_ROOT={ROOT}",
        f"-DMAYA_DEVKIT_ROOT={_maya_devkit_root(version)}",
    ]

    if platform.system() == "Windows" and not os.environ.get("CMAKE_GENERATOR"):
        args.extend(["-G", "Ninja", f"-DCMAKE_BUILD_TYPE={config}"])

    if platform.system() == "Windows":
        _run_in_vs_dev_cmd(session, args)
    else:
        session.run(*args, external=True)


def _cmake_build(session: nox.Session, version: str, config: str) -> None:
    """Build the Maya C++ plugin."""
    command = [
        "cmake",
        "--build",
        str(_cpp_build_dir(version)),
        "--config",
        config,
    ]
    if platform.system() == "Windows":
        _run_in_vs_dev_cmd(session, command)
    else:
        session.run(*command, external=True)


def _cpp_smoke_exe(version: str, config: str) -> Path:
    """Return path to the standalone mmd_runtime_smoke exe produced by cpp build."""
    build_dir = _cpp_build_dir(version) / config
    exe = build_dir / "mmd_runtime_smoke"
    if platform.system() == "Windows":
        exe = exe.with_suffix(".exe")
    return exe


def _run_cli_smoke(
    session: nox.Session,
    version: str,
    config: str,
    manifest: str,
    case: str = "",
    limit: str = "",
) -> None:
    """Run the CLI smoke exe (if manifest provided). Used by cpp_cli_smoke and conditionally by cpp_verify."""
    if not manifest:
        return
    exe = _cpp_smoke_exe(version, config)
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
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)
    _cmake_configure(session, version, config)


@nox.session(venv_backend="none")
def cpp_build(session: nox.Session) -> None:
    """Configure and build the Maya C++ plugin."""
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)
    _cmake_configure(session, version, config)
    _cmake_build(session, version, config)


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
def cpp_cli_smoke(session: nox.Session) -> None:
    """Run the C++ standalone (no-Maya, no-mayapy) runtime smoke against a GoldenOracle-style manifest.

    Reads JSON manifest subset, resolves relative assets.model / assets.motion, uses
    mmd::RuntimeBridge (via built exe) to load PMX (+VMD), create model/clip/instance,
    evaluate frame(s), and report basic sanity counts (bones, morphs, matrix floats, etc).
    Fails on missing files, creation/eval errors, empty matrices, or NaN/Inf.

    Does NOT compare against oracle JSONL (v1).

    Examples:
        uvx nox -s cpp_cli_smoke -- --manifest F:\\Develop\\MMDDev\\GoldenOracle\\manifests\\fixture.motion.json
        uvx nox -s cpp_cli_smoke -- --manifest ... --case fixture-motion-generated-rest-pose-ik-chain --limit 1

    The exe is produced by 'cpp_build' (placed under build/cpp/maya<ver>/<config>/).
    Use --maya/--config to select which built exe to invoke (defaults 2024/Debug).
    """
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)
    manifest = _option(session.posargs, "--manifest", "")
    case_name = _option(session.posargs, "--case", "")
    limit = _option(session.posargs, "--limit", "")
    if not manifest:
        session.error("--manifest <path> is required for cpp_cli_smoke")
    _run_cli_smoke(session, version, config, manifest, case_name, limit)


@nox.session(venv_backend="none")
def cpp_verify(session: nox.Session) -> None:
    """Run the CLI-only C++/native verification chain.

    Always: ffi (release) + native python smoke + cpp_configure + cpp_build.
    If --manifest is supplied: also runs cpp_cli_smoke (C++ standalone exe path)
    *before* the maya_smoke (mayapy) step.
    """
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

    _cmake_configure(session, version, config)
    _cmake_build(session, version, config)

    # Insert cpp_cli_smoke before maya_smoke when a manifest is supplied.
    # This exercises the pure C++ CLI path (no mayapy) for runtime eval.
    manifest = _option(session.posargs, "--manifest", "")
    case_name = _option(session.posargs, "--case", "")
    limit = _option(session.posargs, "--limit", "")
    _run_cli_smoke(session, version, config, manifest, case_name, limit)

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
