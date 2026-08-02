"""Session implementations for Maya-hosted fixture and plugin smokes."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def run_cpp_plugin_smoke(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    default_config: str,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
    scripts: tuple[str, ...],
    require_plugin: bool,
) -> None:
    """Run one or more mayapy probes with the selected C++ plugin environment."""
    version = option(posargs, "--maya", default_maya_version)
    config = option(posargs, "--config", default_config)
    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")

    env_values = {"MAYA_VERSION": version, "MMD_TOOLS_CPP_CONFIG": config}
    if require_plugin:
        plugin = root / "plug-ins" / version / config / "mmd_tools_cpp.mll"
        if not plugin.exists():
            session.error(
                f"C++ plugin not found at {plugin}; run 'uvx nox -s cpp_build "
                f"-- --maya {version} --config {config}' first."
            )
        env_values["MMD_TOOLS_CPP_PLUGIN"] = mayapy_arg_path(mayapy_path, plugin)
    env = mayapy_env(mayapy_path, **env_values)
    for script in scripts:
        session.run(
            str(mayapy_path),
            mayapy_script(mayapy_path, script),
            env=env,
            external=True,
        )


def run_yw_test_model_fixture_gate(
    session,
    *,
    posargs: list[str],
    options,
    option,
    default_maya_versions: tuple[str, ...],
    root: Path,
    require_build_path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
) -> None:
    """Run the checked-in YW test-model fixture gate for each requested Maya version."""
    requested_versions = options(posargs, "--maya")
    versions = requested_versions or list(default_maya_versions)
    unsupported = [version for version in versions if version not in set(default_maya_versions)]
    if unsupported:
        session.error(
            "--maya must be one of "
            + ", ".join(default_maya_versions)
            + " for the YW test-model gate"
        )
    manifest = Path(option(posargs, "--manifest", "tests/data/yw_test_model.fixture.json"))
    if not manifest.is_absolute():
        manifest = root / manifest
    manifest = manifest.resolve()
    if not manifest.is_file():
        session.error(f"Fixture manifest not found: {manifest}")
    out_dir = require_build_path(
        session,
        option(posargs, "--out-dir", "build/yw-test-model-fixture"),
        "--out-dir",
    )
    for version in versions:
        mayapy_path = mayapy(version)
        if not mayapy_path.exists():
            session.error(f"mayapy not found for Maya {version}: {mayapy_path}")
        out_path = out_dir / f"maya-{version}.json"
        env = mayapy_env(mayapy_path, MAYA_VERSION=version, preserve_pythonpath=True)
        session.run(
            str(mayapy_path),
            mayapy_script(mayapy_path, "tests/viewport/yw_test_model_fixture_gate.py"),
            "--manifest",
            mayapy_arg_path(mayapy_path, manifest),
            "--out",
            mayapy_arg_path(mayapy_path, out_path),
            env=env,
            external=True,
        )
        if not out_path.is_file():
            session.error(f"Fixture gate did not write report: {out_path}")


def run_viewport_capture(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
) -> None:
    """Run the plugin-free offscreen Maya viewport capture smoke."""
    version = option(posargs, "--maya", default_maya_version)
    out = option(posargs, "--out", str(root / "build/captures/viewport_smoke.png"))
    frame = option(posargs, "--frame", "1")
    width = option(posargs, "--width", "640")
    height = option(posargs, "--height", "480")
    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")
    env = mayapy_env(mayapy_path, MAYA_VERSION=version)
    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/smoke_viewport_capture.py"),
        "--out",
        mayapy_arg_path(mayapy_path, out),
        "--frame",
        frame,
        "--width",
        width,
        "--height",
        height,
        env=env,
        external=True,
    )


def run_model_readme_dialog_e2e(
    session,
    *,
    posargs: list[str],
    options,
    option,
    root: Path,
    require_build_path,
    python_executable: str = sys.executable,
) -> None:
    """Run and validate the Maya model-readme GUI gate for each version."""
    versions = options(posargs, "--maya") or ["2024", "2026"]
    unsupported = [version for version in versions if version not in {"2024", "2026"}]
    if unsupported:
        session.error("--maya must be 2024 or 2026 for the model-readme GUI gate")
    model = option(posargs, "--model", "tests/data/yw_test_model.pmx")
    out_dir = require_build_path(
        session,
        option(posargs, "--out-dir", "build/reports/model-readme-dialog-e2e"),
        "--out-dir",
    )
    for index, version in enumerate(versions):
        report = out_dir / f"maya-{version}.json"
        session.run(
            python_executable,
            str(root / "tests/viewport/model_readme_dialog_e2e.py"),
            "--maya",
            version,
            "--model",
            model,
            "--out",
            str(report),
            "--port",
            str(7731 + index),
            external=True,
        )
        result = json.loads(report.read_text(encoding="utf-8"))
        if result.get("status") != "pass":
            session.error(f"Maya {version} model-readme GUI gate failed: {result}")
