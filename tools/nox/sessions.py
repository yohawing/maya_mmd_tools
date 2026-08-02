"""Reusable implementations for the public Nox session registry.

The entrypoint keeps the public ``@nox.session`` names and delegates the
session bodies here.  Dependencies that are commonly patched by unit tests
are passed explicitly so this module does not import or depend on
``noxfile.py``.
"""

from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union


_MMD_ANIM_BINDING_RELATIVE_DIR = Path("external") / "mmd-anim" / "bindings" / "python"
_MMD_ANIM_RUNTIME_RELATIVE_DIR = Path("external") / "mmd-anim" / "target" / "release"


def _mmd_anim_runtime_library_names(platform_name: str) -> tuple[str, ...]:
    """Return the supported release-library names for a host platform."""
    if platform_name == "Windows":
        return ("mmd_runtime_ffi.dll", "libmmd_runtime_ffi.dll")
    if platform_name == "Darwin":
        return ("libmmd_runtime_ffi.dylib", "mmd_runtime_ffi.dylib")
    return ("libmmd_runtime_ffi.so", "mmd_runtime_ffi.so")


def _resolve_mmd_anim_runtime_library(
    session,
    *,
    root: Path,
    runtime_library: str,
    platform_name: str,
) -> Path:
    """Resolve and validate the runtime library used by binding tests."""
    if runtime_library:
        candidate = Path(runtime_library)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            session.error(f"mmd_anim_python_tests: runtime library not found: {candidate}")
        return candidate

    runtime_dir = (root / _MMD_ANIM_RUNTIME_RELATIVE_DIR).resolve()
    for library_name in _mmd_anim_runtime_library_names(platform_name):
        candidate = runtime_dir / library_name
        if candidate.is_file():
            return candidate.resolve()
    expected = ", ".join(str(runtime_dir / name) for name in _mmd_anim_runtime_library_names(platform_name))
    session.error(f"mmd_anim_python_tests: runtime library not found; expected one of: {expected}")
    raise AssertionError("session.error must terminate the Nox session")


def run_mmd_anim_python_tests(
    session,
    *,
    posargs: Sequence[str],
    option,
    root: Path,
    python_executable: Union[PathLike, str],
    environment: Mapping[str, str],
    platform_name: Optional[str] = None,
) -> None:
    """Run the external mmd-anim Python binding contract tests."""
    binding_dir = (root / _MMD_ANIM_BINDING_RELATIVE_DIR).resolve()
    tests_dir = binding_dir / "tests"
    if not binding_dir.is_dir():
        session.error(f"mmd_anim_python_tests: binding directory not found: {binding_dir}")
    if not tests_dir.is_dir():
        session.error(f"mmd_anim_python_tests: binding tests directory not found: {tests_dir}")

    try:
        runtime_library_arg = option(list(posargs), "--runtime-library", "")
    except ValueError as exc:
        session.error(f"mmd_anim_python_tests: {exc}")
    remaining = list(posargs)
    if "--runtime-library" in remaining:
        index = remaining.index("--runtime-library")
        del remaining[index : index + 2]
    if remaining:
        session.error(
            "mmd_anim_python_tests: unsupported arguments; only --runtime-library is accepted: "
            + " ".join(remaining)
        )

    runtime_library = _resolve_mmd_anim_runtime_library(
        session,
        root=root,
        runtime_library=runtime_library_arg,
        platform_name=platform_name or "",
    )
    env = dict(environment)
    env["PYTHONPATH"] = str(binding_dir)
    env["MMD_RUNTIME_LIBRARY"] = str(runtime_library)
    session.run(
        str(python_executable),
        "-m",
        "unittest",
        "discover",
        "-s",
        str(tests_dir),
        env=env,
        external=True,
    )


def run_ci_unit(
    session,
    *,
    root: Path,
    run_process,
    glob_files,
    is_expected_environment_import_failure,
    run_logged_subprocess,
) -> None:
    """Run importable pure-Python unit modules and retain the test log."""
    unit_dir = root / "tests" / "unit"
    importable: list[str] = []
    skipped: list[str] = []

    for py_file in sorted(glob_files(unit_dir, "test_*.py")):
        module_name = f"tests.unit.{py_file.stem}"
        probe = run_process(
            [
                "uvx",
                "--with",
                "pytest",
                "--",
                "python",
                "-c",
                f"import {module_name}",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if probe.returncode == 0:
            importable.append(module_name)
            continue
        stderr = probe.stderr or ""
        if is_expected_environment_import_failure(stderr):
            skipped.append(py_file.name)
        else:
            session.error(
                f"ci_unit: {py_file.name} failed to import for a non-environment reason; "
                "update _EXPECTED_ENVIRONMENT_MODULE_PREFIXES only for an intentional dependency:\n"
                + stderr.strip()[-2000:]
            )

    if skipped:
        session.log(
            f"Skipping {len(skipped)} test file(s) that require environment-only dependencies: "
            + ", ".join(skipped)
        )

    if not importable:
        session.error("No importable pure-python unit tests found in tests/unit/")

    session.log(f"Running {len(importable)} pure-python unit test module(s)")
    command = ["uvx", "--with", "pytest", "--", "python", "-m", "pytest", "--pyargs", *importable]
    returncode, log_path, (_, repeated_warnings) = run_logged_subprocess(
        command,
        log_path=root / "build" / "reports" / "ci_unit_tests.log",
        cwd=root,
        verbose=False,
    )
    if returncode != 0:
        session.error(f"ci_unit failed with exit code {returncode}; full log: {log_path}")
    detail = f"; repeated warnings suppressed: {repeated_warnings}" if repeated_warnings else ""
    session.log(f"ci_unit passed; full log: {log_path}{detail}")


def run_release_version(session, *, option, version_check) -> None:
    """Validate release markers and optionally compare them with a version."""
    expected_version = option(session.posargs, "--version", "") or None
    version_check(expected_version=expected_version)
    session.log(f"Release version markers match {expected_version or 'the project version'}")


def run_tests(session, *, posargs: list[str], python_executable: str) -> None:
    """Run the existing mayapy-backed unit or integration test runner."""
    args = posargs or ["--type", "unit"]
    session.run(python_executable, "tests/run_tests.py", *args, external=True)


def run_python_module(session, *, module: str, posargs: list[str], python_executable: str, environment) -> None:
    """Run a repository Python module with the caller's environment unchanged."""
    session.run(
        python_executable,
        "-m",
        module,
        *posargs,
        env=environment,
        external=True,
    )


def run_control_rig_vmd_roundtrip(session, *, posargs: list[str], option, default_maya_version: str, python_executable: str) -> None:
    """Run the focused Control Rig import/edit/bake/VMD round-trip test."""
    maya_version = option(posargs, "--maya", default_maya_version)
    session.run(
        python_executable,
        "tests/run_tests.py",
        "--type",
        "integration",
        "--test",
        "test_mmd_control_rig_analyzer",
        "--maya",
        maya_version,
        external=True,
    )


def run_gui_tests(session, *, posargs: list[str], python_executable: str, default_maya_version: str) -> None:
    """Run the existing Maya GUI test runner."""
    args = posargs or ["--maya_version", default_maya_version]
    session.run(python_executable, "tests/run_gui_tests.py", *args, external=True)


def run_release_package(
    session,
    *,
    posargs: list[str],
    root: Path,
    package_manifest_path: Path,
    option,
    resolve_existing_or_repo_path,
    build_release_package,
) -> None:
    """Build and validate the release ZIP from the package manifest."""
    manifest = resolve_existing_or_repo_path(option(posargs, "--manifest", str(package_manifest_path)))
    output_dir = resolve_existing_or_repo_path(option(posargs, "--out-dir", "dist"))
    resolved_root = root.resolve()
    if output_dir != resolved_root and resolved_root not in output_dir.parents:
        session.error(f"--out-dir must stay inside the repository: {output_dir}")
    result = build_release_package(
        resolved_root,
        manifest_path=manifest,
        output_dir=output_dir,
        expected_version=option(posargs, "--version", "") or None,
    )
    session.log(f"Release package: {result['archive']}")
    session.log("Release package evidence: build/reports/release_package.json and .md")
