"""Session implementations for native, CMake, and mayapy smoke tasks.

These functions own native-session command construction while ``noxfile.py``
retains the public Nox decorators and injects repository-specific helpers.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path


def run_ffi_build(
    session,
    *,
    posargs: list[str],
    root: Path,
    option,
    without_option,
    cargo_args_with_physics_feature,
    require_build_path,
    windows_processes_locking_module,
    configure_bullet3_dir,
    platform_name: str,
) -> None:
    """Build the mmd-anim FFI library with the requested Cargo profile."""
    args = posargs or ["--release"]
    cargo_target_dir_raw = option(args, "--cargo-target-dir", "")
    cargo_args = without_option(args, "--cargo-target-dir") if cargo_target_dir_raw else list(args)
    cargo_args = cargo_args_with_physics_feature(cargo_args)
    cargo_target_dir = None
    if cargo_target_dir_raw:
        cargo_target_dir = require_build_path(session, cargo_target_dir_raw, "--cargo-target-dir")
    profile = "release" if "--release" in args else "debug"
    library_name = {
        "Windows": "mmd_runtime_ffi.dll",
        "Darwin": "libmmd_runtime_ffi.dylib",
    }.get(platform_name, "libmmd_runtime_ffi.so")
    output_root = cargo_target_dir or (root / "external" / "mmd-anim" / "target")
    locked_by = windows_processes_locking_module(output_root / profile / library_name)
    if locked_by:
        session.error(
            "mmd-anim FFI output DLL is currently loaded and cannot be replaced: "
            + "; ".join(locked_by)
        )
    env = os.environ.copy()
    if cargo_target_dir is not None:
        env["CARGO_TARGET_DIR"] = str(cargo_target_dir)
    configure_bullet3_dir(session, env)
    session.run(
        "cargo",
        "build",
        "-p",
        "mmd-anim-ffi",
        "--manifest-path",
        "external/mmd-anim/Cargo.toml",
        *cargo_args,
        env=env,
        external=True,
    )


def run_native_smoke(
    session,
    *,
    posargs: list[str],
    option,
    resolve_existing_or_repo_path,
    runtime_smoke_code: str,
) -> None:
    """Verify that Python can load the native runtime and inspect its ABI."""
    args = list(posargs)
    ffi_path = option(args, "--ffi-path", "")
    env = os.environ.copy()
    if ffi_path:
        env["MMD_ANIM_FFI_PATH"] = str(resolve_existing_or_repo_path(ffi_path))
    session.run(sys.executable, "-c", runtime_smoke_code, env=env, external=True)


def run_reduction_abi_probe(
    session,
    *,
    posargs: list[str],
    option,
    resolve_existing_or_repo_path,
    require_build_path,
) -> None:
    """Run the dense-pose reduction probe and write its JSON/Markdown reports."""
    args = list(posargs)
    ffi_path = resolve_existing_or_repo_path(
        option(args, "--ffi-path", "external/mmd-anim/target/release")
    )
    out_json = require_build_path(
        session,
        option(args, "--out-json", "build/reports/reduction_abi_probe.json"),
        "--out-json",
    )
    out_md = require_build_path(
        session,
        option(args, "--out-md", "build/reports/reduction_abi_probe.md"),
        "--out-md",
    )
    session.run(
        sys.executable,
        "tests/release/reduction_abi_probe.py",
        "--ffi-path",
        str(ffi_path),
        "--out-json",
        str(out_json),
        "--out-md",
        str(out_md),
        external=True,
    )


def run_bundled_native_smoke(session, *, posargs: list[str], root: Path, option, require_build_path) -> None:
    """Verify native binaries that are already present in release paths."""
    out_json = require_build_path(
        session,
        option(posargs, "--out-json", "build/reports/bundled_native_smoke.json"),
        "--out-json",
    )
    out_md = require_build_path(
        session,
        option(posargs, "--out-md", "build/reports/bundled_native_smoke.md"),
        "--out-md",
    )
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    session.run(
        sys.executable,
        "tests/release/bundled_native_smoke.py",
        "--root",
        str(root),
        "--expected-version",
        project["project"]["version"],
        "--out-json",
        str(out_json),
        "--out-md",
        str(out_md),
        external=True,
    )


def run_native_export_smoke(
    session,
    *,
    posargs: list[str],
    option,
    without_option,
    resolve_existing_or_repo_path,
) -> None:
    """Verify native export writer symbols when the selected DLL is current."""
    args = list(posargs)
    ffi_path = option(args, "--ffi-path", "")
    smoke_args = without_option(args, "--ffi-path") if ffi_path else args
    env = os.environ.copy()
    if ffi_path:
        env["MMD_ANIM_FFI_PATH"] = str(resolve_existing_or_repo_path(ffi_path))
    session.run(sys.executable, "tests/native_export_smoke.py", *smoke_args, env=env, external=True)


def run_cpp_config(session, *, posargs: list[str], option, default_maya_version: str, default_config: str, configure) -> None:
    """Configure the Maya C++ plugin build."""
    version = option(posargs, "--maya", default_maya_version)
    config = option(posargs, "--config", default_config)
    configure(session, version, config)


def run_cpp_build(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    default_config: str,
    configure,
    build,
) -> None:
    """Configure and build the Maya C++ plugin."""
    version = option(posargs, "--maya", default_maya_version)
    config = option(posargs, "--config", default_config)
    configure(session, version, config)
    build(session, version, config)


def run_maya_smoke(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    default_config: str,
    mayapy,
    mayapy_env,
    mayapy_script,
) -> None:
    """Load the C++ plugin in mayapy and create the runtime node."""
    version = option(posargs, "--maya", default_maya_version)
    config = option(posargs, "--config", default_config)
    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")
    env = mayapy_env(mayapy_path, MAYA_VERSION=version, MMD_TOOLS_CPP_CONFIG=config)
    for script in (
        "tests/cpp/smoke_python_rig_fallback.py",
        "tests/cpp/smoke_runtime_node.py",
        "tests/cpp/focused_physics_solver_world_toggle.py",
    ):
        session.run(str(mayapy_path), mayapy_script(mayapy_path, script), env=env, external=True)


def run_cpp_cli_smoke(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    default_config: str,
    run_cli_smoke,
) -> None:
    """Run the standalone C++ runtime smoke against a required manifest."""
    version = option(posargs, "--maya", default_maya_version)
    config = option(posargs, "--config", default_config)
    manifest = option(posargs, "--manifest", "")
    case_name = option(posargs, "--case", "")
    limit = option(posargs, "--limit", "")
    if not manifest:
        session.error("--manifest <path> is required for cpp_cli_smoke")
    run_cli_smoke(session, version, config, manifest, case_name, limit)


def run_cpp_verify(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    default_config: str,
    root: Path,
    configure_bullet3_dir,
    native_runtime_smoke_code,
    configure,
    build,
    run_cli_smoke,
    mayapy,
    mayapy_env,
    mayapy_script,
    python_executable: str = sys.executable,
) -> None:
    """Run the CLI-only native verification chain before the mayapy checks."""
    version = option(posargs, "--maya", default_maya_version)
    config = option(posargs, "--config", default_config)

    env = os.environ.copy()
    configure_bullet3_dir(session, env)
    session.run(
        "cargo",
        "build",
        "-p",
        "mmd-anim-ffi",
        "--manifest-path",
        "external/mmd-anim/Cargo.toml",
        "--release",
        "--features",
        "physics-bullet-native",
        env=env,
        external=True,
    )

    runtime_env = os.environ.copy()
    runtime_env["MMD_ANIM_FFI_PATH"] = str((root / "external" / "mmd-anim" / "target" / "release").resolve())
    session.run(python_executable, "-c", native_runtime_smoke_code(), env=runtime_env, external=True)

    configure(session, version, config)
    build(session, version, config, clean_first=True)

    # Keep the standalone CLI step before mayapy when a manifest is supplied.
    manifest = option(posargs, "--manifest", "")
    case_name = option(posargs, "--case", "")
    limit = option(posargs, "--limit", "")
    run_cli_smoke(session, version, config, manifest, case_name, limit)

    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")

    env = mayapy_env(
        mayapy_path,
        MAYA_VERSION=version,
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_CPP_CONFIG=config,
        MMD_ANIM_FFI_PATH=runtime_env["MMD_ANIM_FFI_PATH"],
    )
    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/cpp/smoke_runtime_node.py"),
        env=env,
        external=True,
    )
    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/cpp/focused_physics_solver_world_toggle.py"),
        env=env,
        external=True,
    )
